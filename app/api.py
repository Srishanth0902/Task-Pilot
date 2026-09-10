"""FastAPI backend for the Task Pilot LangGraph calendar agent."""

from __future__ import annotations

import threading
import uuid
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from app.calendar_service import get_calendar_service, get_events
from app.config import (
    API_HOST,
    API_PORT,
    CREDENTIALS_FILE,
    OPENROUTER_API_KEY,
    OPENROUTER_MODEL,
    TIMEZONE,
    TOKEN_FILE,
)
from app.date_utils import coerce_datetime
from app.graph_agent import CalendarConversation
from app.llm import create_openrouter_model


class HealthResponse(BaseModel):
    status: str
    service: str
    model: str
    timezone: str
    openrouter_configured: bool
    google_credentials_configured: bool
    google_token_configured: bool


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    thread_id: str | None = Field(default=None, min_length=1, max_length=200)

    @field_validator("message")
    @classmethod
    def message_cannot_be_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("message cannot be blank")
        return stripped

    @field_validator("thread_id")
    @classmethod
    def thread_id_cannot_be_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("thread_id cannot be blank")
        return stripped


class ChatResponse(BaseModel):
    success: bool
    thread_id: str
    response: str
    intent: str | None = None
    requires_confirmation: bool = False
    confirmation_status: str | None = None
    events: list[dict[str, Any]] = Field(default_factory=list)
    affected_events: list[dict[str, Any]] = Field(default_factory=list)
    proposed_changes: list[dict[str, Any]] = Field(default_factory=list)
    conflicts: list[dict[str, Any]] = Field(default_factory=list)
    alternatives: list[dict[str, Any]] = Field(default_factory=list)
    tool_result: dict[str, Any] | None = None
    error: str | None = None


class EventsResponse(BaseModel):
    success: bool
    count: int = 0
    events: list[dict[str, Any]] = Field(default_factory=list)
    error: str | None = None


class AgentRuntime:
    """Lazily owns the authenticated service, model, graph, and checkpoint memory."""

    def __init__(self):
        self._service = None
        self._conversation = None
        self._initialization_lock = threading.RLock()
        self._conversation_lock = threading.RLock()

    def _get_service(self):
        if self._service is not None:
            return self._service
        with self._initialization_lock:
            if self._service is None:
                self._service = get_calendar_service()
        return self._service

    def _get_conversation(self):
        if self._conversation is not None:
            return self._conversation
        with self._initialization_lock:
            if self._conversation is None:
                self._conversation = CalendarConversation(
                    create_openrouter_model(), self._get_service()
                )
        return self._conversation

    def health(self) -> dict:
        """Report configuration without making network or OAuth calls."""
        return {
            "status": "ok",
            "service": "task-pilot",
            "model": OPENROUTER_MODEL,
            "timezone": TIMEZONE,
            "openrouter_configured": bool(OPENROUTER_API_KEY),
            "google_credentials_configured": CREDENTIALS_FILE.exists(),
            "google_token_configured": TOKEN_FILE.exists(),
        }

    def chat(self, message: str, thread_id: str) -> dict:
        # InMemorySaver is process-local; serialize access so concurrent HTTP
        # requests cannot interleave updates to the same conversation state.
        with self._conversation_lock:
            return self._get_conversation().ask(message, thread_id=thread_id)

    def events(
        self,
        *,
        max_results: int,
        time_min: str | None,
        time_max: str | None,
    ) -> dict:
        parsed_min = coerce_datetime(time_min) if time_min else None
        parsed_max = coerce_datetime(time_max) if time_max else None
        if parsed_min and parsed_max and parsed_max <= parsed_min:
            raise ValueError("time_max must be after time_min")
        return get_events(
            self._get_service(),
            max_results=max_results,
            time_min=parsed_min,
            time_max=parsed_max,
        )


def _chat_response(state: dict, thread_id: str) -> ChatResponse:
    tool_result = state.get("tool_result") or None
    events = tool_result.get("events", []) if isinstance(tool_result, dict) else []
    response = state.get("response") or "The agent returned no response."
    error = state.get("error")
    return ChatResponse(
        success=not bool(error),
        thread_id=thread_id,
        response=response,
        intent=state.get("intent"),
        requires_confirmation=bool(state.get("awaiting_confirmation")),
        confirmation_status=state.get("confirmation_status"),
        events=events,
        affected_events=state.get("affected_events", []),
        proposed_changes=state.get("proposed_changes", []),
        conflicts=state.get("conflict_events", []),
        alternatives=state.get("alternatives", []),
        tool_result=tool_result,
        error=error,
    )


def create_app(runtime=None) -> FastAPI:
    """Create an injectable application for production and isolated tests."""
    active_runtime = runtime or AgentRuntime()
    application = FastAPI(
        title="Task Pilot API",
        version="1.0.0",
        description="FastAPI gateway for the Task Pilot LangGraph calendar agent.",
    )

    @application.get("/health", response_model=HealthResponse)
    def health():
        return active_runtime.health()

    @application.get("/events", response_model=EventsResponse)
    def events(
        max_results: int = Query(default=10, ge=1, le=250),
        time_min: str | None = Query(default=None),
        time_max: str | None = Query(default=None),
    ):
        try:
            result = active_runtime.events(
                max_results=max_results,
                time_min=time_min,
                time_max=time_max,
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except Exception as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        if not result.get("success"):
            raise HTTPException(
                status_code=result.get("status_code", 502),
                detail=result.get("error", "Calendar request failed."),
            )
        return result

    @application.post("/chat", response_model=ChatResponse)
    def chat(request: ChatRequest):
        thread_id = request.thread_id or str(uuid.uuid4())
        try:
            state = active_runtime.chat(request.message, thread_id)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except Exception as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        return _chat_response(state, thread_id)

    return application


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.api:app", host=API_HOST, port=API_PORT, reload=False)

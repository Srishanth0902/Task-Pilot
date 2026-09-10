"""Stateful, conditional LangGraph workflow for Task Pilot.

The model understands the request and produces a structured plan. Deterministic
graph nodes then search, resolve ambiguity, execute exactly one calendar tool,
verify its structured result, and generate the response. Conversation state is
checkpointed by ``thread_id`` so a later answer such as ``8 PM`` can finish a
previously incomplete update.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Annotated, Literal, TypedDict

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field

from app.calendar_tools import build_calendar_tools
from app.config import TIMEZONE
from app.date_utils import local_now

Intent = Literal["create", "list", "search", "update", "delete", "unknown"]


class QueryPlan(BaseModel):
    """Structured interpretation returned by the LLM."""

    intent: Intent
    search_query: str | None = None
    event_id: str | None = None
    title: str | None = None
    start_time: str | None = None
    end_time: str | None = None
    time_min: str | None = None
    time_max: str | None = None
    description: str | None = None
    location: str | None = None
    max_results: int = Field(default=10, ge=1, le=250)
    continue_previous: bool = False


class CalendarAgentState(TypedDict, total=False):
    """Week 3 state contract shared by every graph node."""

    messages: Annotated[list[AnyMessage], add_messages]
    user_query: str
    intent: Intent
    selected_event: dict | None
    tool_result: dict | None
    pending_action: dict | None
    candidate_events: list[dict]
    clarification: str | None
    error: str | None
    verified: bool
    response: str | None


def calendar_planner_prompt() -> str:
    now = local_now()
    return f"""You plan exactly one Google Calendar action.
Current local time: {now.isoformat()}. User timezone: {TIMEZONE}.
Return the requested intent and only the fields the user supplied or that can
be safely inferred. Resolve relative dates to timezone-aware ISO-8601.

Rules:
- create needs title and start_time; end_time defaults to one hour later.
- list means list upcoming events. search means find events without changing.
- update/delete must never invent event_id. Supply search_query when no id is
  known so the graph searches first.
- For "delete my meeting tomorrow", intent is delete, search_query is meeting,
  and time_min/time_max bound tomorrow in {TIMEZONE}.
- If an update says move/reschedule but omits the new time, leave start_time and
  end_time empty; the graph will ask for it.
- Prior pending state and candidates are included below. If this message answers
  that question, set continue_previous=true and preserve/complete that action.
- If candidates are shown and the user identifies one, copy its exact event_id.
"""


def _action_from_plan(plan: QueryPlan) -> dict:
    values = plan.model_dump(exclude_none=True)
    values.pop("continue_previous", None)
    return values


def _clock_from_text(text: str) -> tuple[int, int] | None:
    match = re.search(
        r"\b(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<period>am|pm)\b",
        text,
        re.IGNORECASE,
    )
    if not match:
        return None
    hour = int(match.group("hour"))
    minute = int(match.group("minute") or 0)
    if not 1 <= hour <= 12 or minute > 59:
        return None
    hour = hour % 12 + (12 if match.group("period").lower() == "pm" else 0)
    return hour, minute


def _select_candidate(query: str, candidates: list[dict], event_id: str | None):
    if event_id:
        matches = [event for event in candidates if event.get("event_id") == event_id]
        if len(matches) == 1:
            return matches[0]

    clock = _clock_from_text(query)
    if clock:
        matches = []
        for event in candidates:
            start = event.get("start") or ""
            if "T" in start:
                value = datetime.fromisoformat(start)
                if (value.hour, value.minute) == clock:
                    matches.append(event)
        if len(matches) == 1:
            return matches[0]

    lowered = query.casefold()
    matches = [
        event
        for event in candidates
        if event.get("title") and event["title"].casefold() in lowered
    ]
    return matches[0] if len(matches) == 1 else None


def _apply_followup_time(action: dict, selected_event: dict | None, query: str):
    if action.get("intent") != "update" or action.get("start_time"):
        return
    if not selected_event:
        return
    clock = _clock_from_text(query)
    old_start = selected_event.get("start")
    old_end = selected_event.get("end")
    if not clock or not old_start or not old_end or "T" not in old_start:
        return
    start = datetime.fromisoformat(old_start).replace(
        hour=clock[0], minute=clock[1], second=0, microsecond=0
    )
    duration = datetime.fromisoformat(old_end) - datetime.fromisoformat(old_start)
    action["start_time"] = start.isoformat()
    action["end_time"] = (start + duration).isoformat()


def _needs_search(action: dict) -> bool:
    return action.get("intent") in {"update", "delete"} and not action.get("event_id")


def _action_ready(action: dict) -> bool:
    intent = action.get("intent")
    if intent == "create":
        return bool(action.get("title") and action.get("start_time"))
    if intent == "update":
        changes = any(
            action.get(field) is not None
            for field in ("title", "start_time", "description", "location")
        )
        times_valid = (action.get("start_time") is None) == (
            action.get("end_time") is None
        )
        return bool(action.get("event_id") and changes and times_valid)
    if intent == "delete":
        return bool(action.get("event_id"))
    if intent == "search":
        return bool(action.get("search_query"))
    return intent == "list"


def create_calendar_graph(model, service, *, checkpointer=None, planner=None):
    """Compile the Week 3 graph around an LLM and calendar service."""
    if model is None and planner is None:
        raise ValueError("A LangChain-compatible chat model must be supplied.")

    structured_planner = planner or model.with_structured_output(
        QueryPlan, method="function_calling"
    )
    tools = {tool.name: tool for tool in build_calendar_tools(service)}

    def understand_query(state: CalendarAgentState):
        query = state["user_query"].strip()
        if not query:
            return {"error": "Calendar request cannot be empty."}
        context = {
            "pending_action": state.get("pending_action"),
            "selected_event": state.get("selected_event"),
            "candidate_events": state.get("candidate_events", []),
        }
        try:
            raw = structured_planner.invoke(
                [
                    SystemMessage(content=calendar_planner_prompt()),
                    HumanMessage(
                        content=f"Context:\n{json.dumps(context, default=str)}\n\n"
                        f"User message:\n{query}"
                    ),
                ]
            )
            plan = raw if isinstance(raw, QueryPlan) else QueryPlan.model_validate(raw)
        except Exception as error:
            return {"error": f"Could not understand the request: {error}"}

        previous = state.get("pending_action")
        continuing = bool(previous and plan.continue_previous)
        if continuing:
            action = {**previous, **_action_from_plan(plan)}
            if plan.intent == "unknown":
                action["intent"] = previous["intent"]
            candidates = state.get("candidate_events", [])
            selected = state.get("selected_event")
        else:
            action = _action_from_plan(plan)
            candidates = []
            selected = None

        if candidates and not selected:
            selected = _select_candidate(query, candidates, action.get("event_id"))
            if selected:
                action["event_id"] = selected["event_id"]
        _apply_followup_time(action, selected, query)

        return {
            "intent": action.get("intent", "unknown"),
            "pending_action": action,
            "selected_event": selected,
            "candidate_events": candidates,
            "tool_result": None,
            "clarification": None,
            "error": None,
            "verified": False,
            "response": None,
        }

    def route_after_understand(state: CalendarAgentState) -> str:
        if state.get("error"):
            return "handle_error"
        action = state.get("pending_action") or {}
        if action.get("intent") == "unknown":
            return "generate_response"
        if state.get("candidate_events") and action.get("intent") in {"update", "delete"}:
            return "resolve_event"
        if _needs_search(action):
            return "search_calendar"
        return "execute_action" if _action_ready(action) else "generate_response"

    def search_calendar(state: CalendarAgentState):
        action = dict(state.get("pending_action") or {})
        query = action.get("search_query") or action.get("title")
        if not query:
            return {"clarification": "Which event should I find?"}
        args = {
            "query": query,
            "max_results": action.get("max_results", 10),
            "time_min": action.get("time_min"),
            "time_max": action.get("time_max"),
        }
        try:
            result = tools["search_calendar_events"].invoke(
                {key: value for key, value in args.items() if value is not None}
            )
        except Exception as error:
            return {"error": f"Calendar search failed: {error}"}
        if not result.get("success"):
            return {"error": result.get("error", "Calendar search failed.")}
        return {"tool_result": result, "candidate_events": result.get("events", [])}

    def route_after_search(state: CalendarAgentState) -> str:
        if state.get("error"):
            return "handle_error"
        if state.get("clarification"):
            return "generate_response"
        return "resolve_event"

    def resolve_event(state: CalendarAgentState):
        candidates = state.get("candidate_events", [])
        action = dict(state.get("pending_action") or {})
        selected = state.get("selected_event")
        if not selected:
            selected = _select_candidate(
                state.get("user_query", ""), candidates, action.get("event_id")
            )
        if not selected and len(candidates) == 1:
            selected = candidates[0]
        if not selected and not candidates:
            return {"clarification": "I could not find a matching calendar event."}
        if not selected:
            choices = "; ".join(
                f"{index}. {event.get('title')} at {event.get('start')}"
                for index, event in enumerate(candidates, start=1)
            )
            return {
                "clarification": f"I found multiple matching events: {choices}. "
                "Which one do you mean?"
            }
        action["event_id"] = selected["event_id"]
        _apply_followup_time(action, selected, state.get("user_query", ""))
        if action.get("intent") == "update" and not _action_ready(action):
            return {
                "selected_event": selected,
                "pending_action": action,
                "clarification": f"I found {selected.get('title')} at "
                f"{selected.get('start')}. What time should I move it to?",
            }
        return {"selected_event": selected, "pending_action": action}

    def route_after_resolve(state: CalendarAgentState) -> str:
        if state.get("error"):
            return "handle_error"
        if state.get("clarification"):
            return "generate_response"
        return "execute_action"

    def execute_action(state: CalendarAgentState):
        action = dict(state.get("pending_action") or {})
        intent = action.pop("intent", "unknown")
        action.pop("continue_previous", None)
        search_query = action.pop("search_query", None)
        tool_names = {
            "create": "create_calendar_event",
            "list": "list_calendar_events",
            "search": "search_calendar_events",
            "update": "update_calendar_event",
            "delete": "delete_calendar_event",
        }
        tool_name = tool_names.get(intent)
        if not tool_name:
            return {"clarification": "I could not determine the calendar action."}

        allowed = {
            "create": {"title", "start_time", "end_time", "description", "location"},
            "list": {"max_results", "time_min", "time_max"},
            "search": {"query", "max_results", "time_min", "time_max"},
            "update": {
                "event_id", "title", "start_time", "end_time", "description", "location"
            },
            "delete": {"event_id"},
        }[intent]
        if intent == "search" and "query" not in action:
            action["query"] = search_query
        args = {
            key: value
            for key, value in action.items()
            if key in allowed and value is not None
        }
        try:
            result = tools[tool_name].invoke(args)
        except Exception as error:
            return {"error": f"Calendar action failed: {error}"}
        return {"tool_result": result}

    def verify_result(state: CalendarAgentState):
        result = state.get("tool_result") or {}
        if not result.get("success"):
            return {"error": result.get("error", "The calendar action failed.")}
        intent = state.get("intent")
        if intent in {"create", "update"} and not result.get("event_id"):
            return {"error": "Calendar returned success without an event ID."}
        if intent == "delete" and "deleted" not in result:
            return {"error": "Calendar returned an invalid deletion result."}
        if intent in {"list", "search"} and "events" not in result:
            return {"error": "Calendar returned an invalid event list."}
        return {"verified": True}

    def route_after_verify(state: CalendarAgentState) -> str:
        return "handle_error" if state.get("error") else "generate_response"

    def generate_response(state: CalendarAgentState):
        clarification = state.get("clarification")
        result = state.get("tool_result") or {}
        intent = state.get("intent")
        if clarification:
            text = clarification
            clear = False
        elif intent == "unknown":
            text = "I could not determine the calendar action. Please rephrase the request."
            clear = False
        elif not state.get("verified") and intent == "create":
            missing = []
            action = state.get("pending_action") or {}
            if not action.get("title"):
                missing.append("a title")
            if not action.get("start_time"):
                missing.append("a start time")
            text = "Please provide " + " and ".join(missing) + "."
            clear = False
        elif not state.get("verified") and intent == "update":
            text = "What would you like to change about that event?"
            clear = False
        elif not state.get("verified") and intent == "search":
            text = "What event should I search for?"
            clear = False
        elif intent in {"list", "search"}:
            events = result.get("events", [])
            if not events:
                text = "I found no matching calendar events."
            else:
                rows = [
                    f"{index}. {event.get('title')} - {event.get('start')}"
                    for index, event in enumerate(events, start=1)
                ]
                text = "I found:\n" + "\n".join(rows)
            clear = True
        elif intent == "delete":
            event = state.get("selected_event") or {}
            title = event.get("title") or result.get("event_id")
            text = f"Deleted {title}." if result.get("deleted") else f"{title} was already absent."
            clear = True
        else:
            verb = "Created" if intent == "create" else "Updated"
            text = (
                f"{verb} {result.get('title')} from {result.get('start')} "
                f"to {result.get('end')}."
            )
            clear = True
        return {
            "messages": [AIMessage(content=text)],
            "response": text,
            "pending_action": None if clear else state.get("pending_action"),
            "selected_event": None if clear else state.get("selected_event"),
            "candidate_events": [] if clear else state.get("candidate_events", []),
        }

    def handle_error(state: CalendarAgentState):
        text = f"I could not complete that request: {state.get('error', 'unknown error')}"
        return {"messages": [AIMessage(content=text)], "response": text}

    workflow = StateGraph(CalendarAgentState)
    workflow.add_node("understand_query", understand_query)
    workflow.add_node("search_calendar", search_calendar)
    workflow.add_node("resolve_event", resolve_event)
    workflow.add_node("execute_action", execute_action)
    workflow.add_node("verify_result", verify_result)
    workflow.add_node("generate_response", generate_response)
    workflow.add_node("handle_error", handle_error)
    workflow.add_edge(START, "understand_query")
    workflow.add_conditional_edges("understand_query", route_after_understand)
    workflow.add_conditional_edges("search_calendar", route_after_search)
    workflow.add_conditional_edges("resolve_event", route_after_resolve)
    workflow.add_edge("execute_action", "verify_result")
    workflow.add_conditional_edges("verify_result", route_after_verify)
    workflow.add_edge("generate_response", END)
    workflow.add_edge("handle_error", END)
    return workflow.compile(checkpointer=checkpointer or InMemorySaver())


class CalendarConversation:
    """Reusable graph session supporting follow-ups through a stable thread ID."""

    def __init__(self, model, service, *, checkpointer=None, planner=None):
        self.graph = create_calendar_graph(
            model,
            service,
            checkpointer=checkpointer,
            planner=planner,
        )

    def ask(self, query: str, *, thread_id: str = "default") -> CalendarAgentState:
        if not query.strip():
            raise ValueError("Calendar request cannot be empty.")
        config = {"configurable": {"thread_id": thread_id}}
        return self.graph.invoke(
            {"messages": [HumanMessage(content=query)], "user_query": query},
            config=config,
        )

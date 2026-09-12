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
from datetime import datetime, timedelta
from typing import Annotated, Literal, TypedDict

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field

from app.calendar_tools import build_calendar_tools
from app.config import TIMEZONE, WORKDAY_END_HOUR, WORKDAY_START_HOUR
from app.date_utils import (
    ensure_aware,
    format_local_datetime,
    format_local_range,
    infer_local_time_window,
    local_now,
)
from app.observability import log_workflow, safe_error_detail
from app.rescheduling import relocation_plan
from app.scheduling import (
    build_bulk_changes,
    day_window,
    find_free_slots,
    overlapping_events,
    working_window,
)

Intent = Literal[
    "create",
    "list",
    "search",
    "update",
    "delete",
    "bulk_update",
    "bulk_delete",
    "free_slot",
    "unknown",
]


class QueryPlan(BaseModel):
    """Structured interpretation returned by the LLM."""

    intent: Intent
    conflict_strategy: Literal["alternatives", "propose_relocation", "relocate"] | None = None
    displacement_query: str | None = None
    relocation_date: str | None = None
    search_query: str | None = None
    event_id: str | None = None
    title: str | None = None
    start_time: str | None = None
    end_time: str | None = None
    time_min: str | None = None
    time_max: str | None = None
    description: str | None = None
    location: str | None = None
    shift_minutes: int | None = None
    target_date: str | None = None
    duration_minutes: int | None = Field(default=None, ge=1, le=1440)
    max_results: int = Field(default=10, ge=1, le=250)
    continue_previous: bool = False


class CalendarAgentState(TypedDict, total=False):
    """Week 3 state contract shared by every graph node."""

    messages: Annotated[list[AnyMessage], add_messages]
    thread_id: str
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
    affected_events: list[dict]
    proposed_changes: list[dict]
    conflict_events: list[dict]
    alternatives: list[dict]
    awaiting_confirmation: bool
    confirmation_status: Literal["pending", "approved", "declined"] | None


def calendar_planner_prompt() -> str:
    now = local_now()
    return f"""You plan exactly one Google Calendar action.
Current local time: {now.isoformat()}. User timezone: {TIMEZONE}.
Return the requested intent and only the fields the user supplied or that can
be safely inferred. Resolve relative dates to timezone-aware ISO-8601.

Rules:
- create needs title and start_time; end_time defaults to one hour later.
- list means list upcoming events. search means find events without changing.
- To fit a NEW urgent event into an occupied slot, use create for the new event.
  Set conflict_strategy=propose_relocation when urgency/rearranging is mentioned
  without explicit permission to move existing events. Set conflict_strategy=relocate
  only when the user explicitly instructs moving the existing conflicting event
  to the next available slot; displacement_query identifies that event (e.g. Yoga).
  This coordinated create-plus-relocate request IS supported as one workflow.
  Never delete an existing event to make room. Relocation searches later the same
  day within working hours, preserves duration and requires a new choice if full.
  If the user explicitly chooses another day for the displaced event, set
  relocation_date=YYYY-MM-DD and preserve the NEW event's original start/end.
- "Rename X to Y" is update, search_query=X, title=Y. Never use search
  as the final intent for a rename or location/description change.
- For vague or unsupported requests, return intent=unknown. Do not omit the
  structured response. This workflow supports one action per turn; if several
  independent actions are requested, return unknown rather than executing only one.
- update/delete must never invent event_id. Supply search_query when no id is
  known so the graph searches first.
- For "delete my meeting tomorrow", intent is delete, search_query is meeting,
  and time_min/time_max bound tomorrow in {TIMEZONE}.
- If an update says move/reschedule but omits the new time, leave start_time and
  end_time empty; the graph will ask for it.
- Prior pending state and candidates are included below. If this message answers
  that question, set continue_previous=true and preserve/complete that action.
- If candidates are shown and the user identifies one, copy its exact event_id.
- Use bulk_update for requests affecting every matching event. Set search_query,
  time_min/time_max, and either shift_minutes or target_date (YYYY-MM-DD).
  Moving today to tomorrow means shift_minutes=1440. Moving events "by 30
  minutes" means shift_minutes=30.
- Use bulk_delete for deleting every matching event and provide the same search
  and date bounds. For "all tasks", "all events", or "everything", omit
  search_query so every event in the requested range is listed. Bulk actions
  are only plans until the graph gets confirmation.
- Use free_slot for both availability questions and requests to find a time and
  schedule something. Always supply the requested day's time_min/time_max. For
  scheduling, also supply title and duration_minutes. The graph uses the
  configured {WORKDAY_START_HOUR:02d}:00-{WORKDAY_END_HOUR:02d}:00 local window.
- "What slots are available tomorrow after 6 PM?" means tomorrow at 18:00
  through the configured end of the workday. It is an availability question,
  not permission to create an event.
- If an availability question contains no day or time range, leave time_min
  and time_max empty and ask for the day. Never invent today's date.
- Search queries should contain the event's identifying words, e.g. study for
  "study sessions", rather than generic words such as sessions or tasks.
"""


def _action_from_plan(plan: QueryPlan) -> dict:
    values = plan.model_dump(exclude_none=True)
    values.pop("continue_previous", None)
    return values


def _coerce_query_plan(raw) -> QueryPlan:
    """Normalize standard and provider-quirky structured model responses."""
    if isinstance(raw, QueryPlan):
        return raw
    if isinstance(raw, dict) and "parsed" in raw:
        if raw.get("parsed") is not None:
            parsed = raw["parsed"]
            return parsed if isinstance(parsed, QueryPlan) else QueryPlan.model_validate(parsed)
        message = raw.get("raw")
        for call in getattr(message, "tool_calls", []) or []:
            name = call.get("name")
            if name in {
                "create", "list", "search", "update", "delete",
                "bulk_update", "bulk_delete", "free_slot", "unknown",
            }:
                return QueryPlan.model_validate({"intent": name, **call.get("args", {})})
        if getattr(message, "content", None) and not getattr(message, "tool_calls", None):
            # A conversational clarification without a tool call is not a plan
            # to mutate anything; route it through the normal clarification node.
            return QueryPlan(intent="unknown")
        parsing_error = raw.get("parsing_error")
        raise ValueError(str(parsing_error or "Model returned an invalid query plan."))
    return QueryPlan.model_validate(raw)


def _clock_from_text(text: str) -> tuple[int, int] | None:
    match = re.search(
        r"(?:\bat\s+|^)(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*"
        r"(?P<period>am|pm)?\b",
        text,
        re.IGNORECASE,
    )
    if not match:
        return None
    hour = int(match.group("hour"))
    minute = int(match.group("minute") or 0)
    period = (match.group("period") or "").lower()
    if minute > 59 or (period and not 1 <= hour <= 12) or (not period and hour > 23):
        return None
    if period:
        hour = hour % 12 + (12 if period == "pm" else 0)
    return hour, minute


def _plan_datetime(value: str) -> datetime:
    """Parse and localize a planner-produced ISO datetime."""
    return ensure_aware(datetime.fromisoformat(value))


def _normalise_action_times(action: dict, query: str) -> None:
    """Force planner timestamps and explicit user clock times into IST."""
    for field in ("start_time", "end_time", "time_min", "time_max"):
        value = action.get(field)
        if value:
            action[field] = _plan_datetime(value).isoformat()

    if action.get("intent") not in {"create", "update"} or not action.get("start_time"):
        return
    # Explicit foreign-zone times have already been converted to IST above.
    if re.search(r"\b(?:UTC|GMT|PST|PDT|EST|EDT|CET|CEST)\b|[+-]\d{2}:\d{2}", query, re.I):
        return
    destination = re.search(r"\bto\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\b", query, re.I)
    clock = _clock_from_text(destination.group(1) if destination else query)
    if not clock:
        return
    start = _plan_datetime(action["start_time"])
    end = _plan_datetime(action["end_time"]) if action.get("end_time") else None
    duration = end - start if end else timedelta(hours=1)
    corrected_start = start.replace(
        hour=clock[0], minute=clock[1], second=0, microsecond=0
    )
    action["start_time"] = corrected_start.isoformat()
    action["end_time"] = (corrected_start + duration).isoformat()


def _requests_free_slot_scheduling(query: str) -> bool:
    """Distinguish listing availability from permission to create an event."""
    return bool(
        re.search(r"\b(?:schedule|book|create|add|put)\b", query, re.IGNORECASE)
    )


def _complete_free_slot_action(action: dict, query: str, *, continuing: bool = False) -> None:
    """Fill model-omitted free-slot details from explicit user language."""
    if action.get("intent") != "free_slot":
        return

    if "availability_only" not in action:
        action["availability_only"] = not _requests_free_slot_scheduling(query)
    elif _requests_free_slot_scheduling(query):
        action["availability_only"] = False

    if not continuing and not re.search(r"\b(?:today|tomorrow|tonight|monday|tuesday|wednesday|thursday|friday|saturday|sunday|week|after|before|between|from|at|on)\b|\d", query, re.I):
        action.pop("time_min", None)
        action.pop("time_max", None)

    explicit_bounds = bool(re.search(r"\b(?:after|before|between|until|from)\s+\d", query, re.I))
    if not action.get("time_min") or not action.get("time_max") or explicit_bounds:
        range_query = query
        if continuing and action.get("time_min") and not infer_local_time_window(query, now=local_now()):
            range_query = action["time_min"][:10] + " " + query
        inferred = infer_local_time_window(
            range_query,
            now=local_now(),
            start_hour=WORKDAY_START_HOUR,
            end_hour=WORKDAY_END_HOUR,
        )
        if inferred:
            action["time_min"] = inferred[0].isoformat()
            action["time_max"] = inferred[1].isoformat()

    # Availability lists use one-hour blocks unless the user requested another
    # duration. Scheduling requests still require an explicit duration.
    if action.get("availability_only") and not action.get("duration_minutes"):
        action["duration_minutes"] = 60


def _is_free_slot_range_followup(query: str) -> bool:
    """Return whether a follow-up contains a usable day/range."""
    try:
        return bool(
            infer_local_time_window(
                query,
                now=local_now(),
                start_hour=WORKDAY_START_HOUR,
                end_hour=WORKDAY_END_HOUR,
            )
        )
    except ValueError:
        return False


def _generic_bulk_query(value: str | None) -> bool:
    """Return true when a bulk request means every event, not a title search."""
    if not value:
        return True
    words = set(re.findall(r"[a-z]+", value.casefold()))
    return bool(words) and words <= {
        "all", "calendar", "everything", "event", "events", "my", "task", "tasks"
    }


def _select_candidate(query: str, candidates: list[dict], event_id: str | None):
    ordinal = re.fullmatch(
        r"(?:the\s+|option\s+)?(\d+|first|second|third|fourth|fifth)(?:\s+one)?[.!]?",
        query.strip().casefold(),
    )
    if ordinal:
        token = ordinal.group(1)
        index = int(token) if token.isdigit() else ["first", "second", "third", "fourth", "fifth"].index(token) + 1
        return candidates[index - 1] if 1 <= index <= len(candidates) else None
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
    if action.get("intent") not in {"create", "update"} or action.get("start_time"):
        return
    clock = _clock_from_text(query)
    source = selected_event or {}
    old_start = source.get("start") or action.get("previous_start")
    old_end = source.get("end") or action.get("previous_end")
    if not clock or not old_start or not old_end or "T" not in old_start:
        return
    start = datetime.fromisoformat(old_start).replace(
        hour=clock[0], minute=clock[1], second=0, microsecond=0
    )
    duration = datetime.fromisoformat(old_end) - datetime.fromisoformat(old_start)
    action["start_time"] = start.isoformat()
    action["end_time"] = (start + duration).isoformat()


def _confirmation_value(query: str) -> bool | None:
    text = re.sub(r"[^a-z ]", "", query.casefold()).strip()
    text = " ".join(text.split())
    if text in {"yes", "y", "confirm", "confirmed", "proceed", "do it", "go ahead", "yes proceed", "yes please", "yes go ahead"}:
        return True
    if text in {"no", "n", "cancel", "stop", "never mind", "nevermind", "no cancel it", "no thanks", "cancel it"}:
        return False
    return None


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
    """Compile the stateful Week 3/4 graph around an LLM and calendar service."""
    if model is None and planner is None:
        raise ValueError("A LangChain-compatible chat model must be supplied.")

    structured_planner = planner or model.with_structured_output(
        QueryPlan, method="function_calling", include_raw=True
    )
    tools = {tool.name: tool for tool in build_calendar_tools(service)}

    def invoke_tool(state: CalendarAgentState, tool_name: str, args: dict):
        context = {
            "thread_id": state.get("thread_id"),
            "intent": state.get("intent"),
            "selected_tool": tool_name,
        }
        log_workflow("tool_input", **context, tool_input=args)
        try:
            result = tools[tool_name].invoke(args)
        except Exception as error:
            log_workflow("tool_error", **context, error=str(error))
            raise
        log_workflow("tool_output", **context, tool_output=result)
        return result

    def traced_node(name, handler):
        def run(state: CalendarAgentState):
            context = {
                "thread_id": state.get("thread_id"),
                "graph_node": name,
                "intent": state.get("intent"),
            }
            log_workflow("graph_node_started", **context)
            try:
                result = handler(state)
            except Exception as error:
                log_workflow("graph_node_failed", **context, error=str(error))
                raise
            log_workflow(
                "graph_node_finished",
                **context,
                resulting_intent=result.get("intent", state.get("intent")),
                has_error=bool(result.get("error")),
            )
            if result.get("response"):
                log_workflow(
                    "final_response",
                    thread_id=state.get("thread_id"),
                    intent=result.get("intent", state.get("intent")),
                    response=result["response"],
                )
            return result

        return run

    def understand_query(state: CalendarAgentState):
        query = state["user_query"].strip()
        if not query:
            return {"error": "Calendar request cannot be empty."}
        if state.get("awaiting_confirmation"):
            decision = _confirmation_value(query)
            if decision is None:
                return {
                    "clarification": "Please answer yes to proceed or no to cancel.",
                    "confirmation_status": "pending",
                    "error": None,
                }
            return {
                "intent": (state.get("pending_action") or {}).get(
                    "intent", state.get("intent", "unknown")
                ),
                "awaiting_confirmation": False,
                "confirmation_status": "approved" if decision else "declined",
                "clarification": None,
                "error": None,
                "verified": False,
                "tool_result": None,
            }
        context = {
            "pending_action": state.get("pending_action"),
            "selected_event": state.get("selected_event"),
            "candidate_events": state.get("candidate_events", []),
            "conflict_events": state.get("conflict_events", []),
            "alternatives": state.get("alternatives", []),
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
            plan = _coerce_query_plan(raw)
        except Exception as error:
            return {
                "error": f"Could not understand the request: {safe_error_detail(error)}",
                "verified": False,
            }

        previous = state.get("pending_action")
        if plan.intent == "unknown":
            if re.fullmatch(r"(?:please\s+)?(?:add|create|schedule)\s+(?:a|an)\s+(?:calendar\s+)?event[.!]?", query, re.I):
                plan = QueryPlan(intent="create")
            incomplete_move = re.fullmatch(
                r"(?:please\s+)?(?:move|reschedule)\s+(?:my\s+)?(.+?)(?:\s+(today|tomorrow))?[.!]?",
                query, re.I,
            )
            if incomplete_move and not re.search(r"\b(?:to|by|and|it|them)\b|\d", incomplete_move.group(1), re.I):
                plan = QueryPlan(intent="update", search_query=incomplete_move.group(1))
                if incomplete_move.group(2):
                    reference = local_now() + timedelta(days=incomplete_move.group(2).lower() == "tomorrow")
                    start, end = day_window(reference)
                    plan.time_min, plan.time_max = start.isoformat(), end.isoformat()
        rename = re.fullmatch(r"\s*rename\s+(.+?)\s+to\s+(.+?)\s*[.!]?", query, re.I)
        if rename:
            plan = QueryPlan(intent="update", search_query=rename.group(1), title=rename.group(2))
        deterministic_free_slot_followup = bool(
            previous
            and previous.get("intent") == "free_slot"
            and plan.intent in {"free_slot", "unknown"}
            and _is_free_slot_range_followup(query)
        )
        continuing = bool(
            previous and (plan.continue_previous or deterministic_free_slot_followup
                          or (state.get("candidate_events") and _select_candidate(query, state["candidate_events"], None)))
        )
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

        # An explicit follow-up can authorize relocating the preserved conflict.
        move_existing = bool(re.search(r"\b(?:move|shift|reschedule)\b", query, re.I))
        next_slot = bool(re.search(r"\b(?:next|available|free)\b.*\b(?:slot|time)\b", query, re.I))
        negated = bool(re.search(r"\b(?:not|don't|do not|never)\b", query, re.I))
        if previous and state.get("conflict_events") and move_existing and next_slot and not negated:
            action = {**previous, "conflict_strategy": "relocate"}
            if plan.relocation_date:
                action["relocation_date"] = plan.relocation_date
            action["start_time"] = previous.get("start_time") or previous.get("previous_start")
            action["end_time"] = previous.get("end_time") or previous.get("previous_end")
        if action.get("intent") == "create":
            if negated and move_existing:
                action["conflict_strategy"] = "alternatives"
            if move_existing and next_slot and not negated:
                action["conflict_strategy"] = "relocate"
                named = [e.get("title", "") for e in state.get("conflict_events", []) if e.get("title", "").casefold() in query.casefold()]
                if len(named) == 1:
                    action["displacement_query"] = named[0]
            if action.get("conflict_strategy") == "relocate":
                action["relocation_authorized"] = move_existing and next_slot and not negated
            if re.search(r"\burgent\b|\brearrange\b", query, re.I) and not action.get("conflict_strategy"):
                action["conflict_strategy"] = "propose_relocation"

        if action.get("search_query"):
            action["search_query"] = re.sub(
                r"\s+(?:sessions?|tasks?|events?)$", "", action["search_query"], flags=re.I
            )

        if candidates and not selected:
            selected = _select_candidate(query, candidates, action.get("event_id"))
            if selected:
                action["event_id"] = selected["event_id"]
        _apply_followup_time(action, selected, query)
        try:
            _normalise_action_times(action, query)
            _complete_free_slot_action(action, query, continuing=continuing)
        except ValueError as error:
            return {"error": f"Invalid event time: {error}"}

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
            "affected_events": [],
            "proposed_changes": [],
            "conflict_events": [],
            "alternatives": [],
            "awaiting_confirmation": False,
            "confirmation_status": None,
        }

    def route_after_understand(state: CalendarAgentState) -> str:
        if state.get("error"):
            return "handle_error"
        action = state.get("pending_action") or {}
        if state.get("confirmation_status") == "declined":
            return "generate_response"
        if state.get("confirmation_status") == "approved":
            if action.get("coordinated"):
                return "execute_relocation"
            if action.get("intent") in {"bulk_update", "bulk_delete"}:
                return "execute_bulk_action"
            if action.get("intent") in {"create", "update"} and action.get("start_time"):
                return "detect_conflicts"
            return "execute_action"
        if state.get("awaiting_confirmation") or state.get("clarification"):
            return "generate_response"
        if action.get("intent") == "unknown":
            return "generate_response"
        if action.get("intent") in {"bulk_update", "bulk_delete"}:
            return "search_calendar"
        if action.get("intent") == "free_slot":
            return "load_calendar_window"
        if state.get("candidate_events") and action.get("intent") in {"update", "delete"}:
            return "resolve_event"
        if _needs_search(action):
            return "search_calendar"
        if action.get("intent") == "create":
            return "detect_conflicts" if _action_ready(action) else "generate_response"
        if action.get("intent") == "update" and action.get("start_time"):
            return "detect_conflicts" if _action_ready(action) else "generate_response"
        if action.get("intent") == "delete" and _action_ready(action):
            return "request_confirmation"
        return "execute_action" if _action_ready(action) else "generate_response"

    def search_calendar(state: CalendarAgentState):
        action = dict(state.get("pending_action") or {})
        query = action.get("search_query") or action.get("title")
        is_bulk = action.get("intent") in {"bulk_update", "bulk_delete"}
        list_everything = is_bulk and _generic_bulk_query(query)
        if not query and not list_everything:
            return {"clarification": "Which event should I find?"}
        args = {
            "query": query,
            "max_results": (
                250
                if action.get("intent") in {"bulk_update", "bulk_delete"}
                else action.get("max_results", 10)
            ),
            "time_min": action.get("time_min"),
            "time_max": action.get("time_max"),
        }
        try:
            if list_everything:
                list_args = {
                    "max_results": args["max_results"],
                    "time_min": args["time_min"],
                    "time_max": args["time_max"],
                }
                result = invoke_tool(
                    state,
                    "list_calendar_events",
                    {key: value for key, value in list_args.items() if value is not None}
                )
            else:
                result = invoke_tool(
                    state,
                    "search_calendar_events",
                    {key: value for key, value in args.items() if value is not None}
                )
        except Exception as error:
            return {"error": f"Calendar search failed: {safe_error_detail(error)}"}
        if not result.get("success"):
            detail = result.get("error", "Unknown calendar error")
            return {"error": f"Calendar search failed: {detail}"}
        return {"tool_result": result, "candidate_events": result.get("events", [])}

    def route_after_search(state: CalendarAgentState) -> str:
        if state.get("error"):
            return "handle_error"
        if state.get("clarification"):
            return "generate_response"
        if state.get("intent") in {"bulk_update", "bulk_delete"}:
            return "plan_bulk_operation"
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
                f"{index}. {event.get('title')} at {format_local_datetime(event.get('start'))}"
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
                f"{format_local_datetime(selected.get('start'))}. What time should I move it to?",
            }
        result = {"selected_event": selected, "pending_action": action}
        if action.get("intent") == "delete":
            result.update(
                {
                    "affected_events": [selected],
                    "proposed_changes": [
                        {
                            "action": "delete",
                            "event_id": selected["event_id"],
                            "title": selected.get("title"),
                            "old_start": selected.get("start"),
                            "old_end": selected.get("end"),
                        }
                    ],
                }
            )
        return result

    def route_after_resolve(state: CalendarAgentState) -> str:
        if state.get("error"):
            return "handle_error"
        if state.get("clarification"):
            return "generate_response"
        action = state.get("pending_action") or {}
        if action.get("intent") == "delete":
            return "request_confirmation"
        if action.get("intent") == "update" and action.get("start_time"):
            return "detect_conflicts"
        return "execute_action"

    def load_calendar_window(state: CalendarAgentState):
        action = dict(state.get("pending_action") or {})
        if not action.get("time_min") or not action.get("time_max"):
            return {
                "clarification": "Which day or time range should I search for a free slot?"
            }
        args = {
            "max_results": 250,
            "time_min": action["time_min"],
            "time_max": action["time_max"],
        }
        try:
            result = invoke_tool(state, "list_calendar_events", args)
        except Exception as error:
            return {
                "error": f"Could not load the scheduling window: {safe_error_detail(error)}"
            }
        if not result.get("success"):
            return {"error": result.get("error", "Could not load calendar events.")}
        return {"tool_result": result, "candidate_events": result.get("events", [])}

    def route_after_window_load(state: CalendarAgentState) -> str:
        if state.get("error"):
            return "handle_error"
        if state.get("clarification"):
            return "generate_response"
        return "find_free_slot"

    def find_free_slot(state: CalendarAgentState):
        action = dict(state.get("pending_action") or {})
        availability_only = bool(action.get("availability_only"))
        if not availability_only and not action.get("title"):
            return {"clarification": "What should I call the event?"}
        duration_minutes = action.get("duration_minutes")
        if not duration_minutes:
            return {"clarification": "How long should the event be?"}
        try:
            requested_start = _plan_datetime(action["time_min"])
            requested_end = _plan_datetime(action["time_max"])
        except ValueError as error:
            return {"error": f"Invalid free-slot time range: {error}"}
        if requested_end <= requested_start:
            return {"clarification": "The search end must be after its start."}
        if requested_end - requested_start > timedelta(days=31):
            return {"clarification": "Please search an availability range of 31 days or less."}
        slots = []
        day = requested_start
        valid_window = False
        limit = 6 if availability_only else 3
        while day < requested_end and len(slots) < limit:
            work_start, work_end = working_window(day)
            window_start = max(requested_start, work_start)
            window_end = min(requested_end, work_end)
            reference = local_now()
            if window_start.date() == reference.date():
                window_start = max(window_start, reference.replace(microsecond=0))
            if window_end > window_start:
                valid_window = True
                slots.extend(find_free_slots(
                    state.get("candidate_events", []), window_start, window_end,
                    timedelta(minutes=duration_minutes), limit=limit - len(slots),
                ))
            day = day.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        if not valid_window:
            return {
                "clarification": "The requested range does not overlap the configured working hours."
            }
        if not slots:
            if availability_only:
                return {
                    "tool_result": {
                        "success": True,
                        "count": 0,
                        "slots": [],
                        "duration_minutes": duration_minutes,
                    },
                    "alternatives": [],
                    "verified": True,
                }
            return {"clarification": "I could not find a suitable free slot in that window."}
        if availability_only:
            return {
                "tool_result": {
                    "success": True,
                    "count": len(slots),
                    "slots": slots,
                    "duration_minutes": duration_minutes,
                },
                "alternatives": slots,
                "verified": True,
            }
        chosen = slots[0]
        create_action = {
            "intent": "create",
            "title": action["title"],
            "start_time": chosen["start"],
            "end_time": chosen["end"],
            "description": action.get("description"),
            "location": action.get("location"),
        }
        return {
            "intent": "create",
            "pending_action": create_action,
            "alternatives": slots,
            "proposed_changes": [
                {
                    "action": "create",
                    "title": action["title"],
                    "new_start": chosen["start"],
                    "new_end": chosen["end"],
                }
            ],
        }

    def plan_bulk_operation(state: CalendarAgentState):
        action = dict(state.get("pending_action") or {})
        events = state.get("candidate_events", [])
        if not events:
            return {"clarification": "I found no events affected by that bulk request."}
        try:
            changes = build_bulk_changes(events, action)
        except (TypeError, ValueError) as error:
            return {"clarification": str(error)}
        if not changes:
            return {"clarification": "I found no timed events that can be changed."}

        if action.get("intent") == "bulk_update":
            affected_ids = {change["event_id"] for change in changes}
            conflicts = []
            for change in changes:
                try:
                    window = invoke_tool(
                        state,
                        "list_calendar_events",
                        {
                            "max_results": 250,
                            "time_min": change["new_start"],
                            "time_max": change["new_end"],
                        }
                    )
                except Exception as error:
                    return {
                        "error": f"Bulk conflict check failed: {safe_error_detail(error)}"
                    }
                if not window.get("success"):
                    return {"error": window.get("error", "Bulk conflict check failed.")}
                matches = overlapping_events(
                    window.get("events", []),
                    datetime.fromisoformat(change["new_start"]),
                    datetime.fromisoformat(change["new_end"]),
                    exclude_event_ids=affected_ids,
                )
                conflicts.extend(matches)
            if conflicts:
                names = ", ".join(dict.fromkeys(event.get("title") for event in conflicts))
                return {
                    "affected_events": events,
                    "proposed_changes": changes,
                    "conflict_events": conflicts,
                    "clarification": f"The bulk move would conflict with: {names}. "
                    "Please choose a different shift or target date.",
                }

        return {"affected_events": events, "proposed_changes": changes}

    def route_after_planning(state: CalendarAgentState) -> str:
        if state.get("error"):
            return "handle_error"
        if state.get("clarification"):
            return "generate_response"
        return "request_confirmation"

    def route_after_free_slot(state: CalendarAgentState) -> str:
        if state.get("error"):
            return "handle_error"
        if state.get("clarification"):
            return "generate_response"
        if state.get("intent") == "free_slot":
            return "generate_response"
        return "request_confirmation"

    def detect_conflicts(state: CalendarAgentState):
        action = dict(state.get("pending_action") or {})
        start_text = action.get("start_time")
        if not start_text:
            return {"clarification": "What start time should I use?"}
        try:
            start = _plan_datetime(start_text)
            end = (
                _plan_datetime(action["end_time"])
                if action.get("end_time")
                else start + timedelta(hours=1)
            )
        except ValueError as error:
            return {"error": f"Invalid event time: {error}"}
        day_start, day_end = day_window(start)
        try:
            result = invoke_tool(
                state,
                "list_calendar_events",
                {"max_results": 250, "time_min": day_start, "time_max": day_end}
            )
        except Exception as error:
            return {"error": f"Conflict check failed: {safe_error_detail(error)}"}
        if not result.get("success"):
            return {"error": result.get("error", "Conflict check failed.")}
        excluded = {action["event_id"]} if action.get("event_id") else set()
        conflicts = overlapping_events(
            result.get("events", []), start, end, exclude_event_ids=excluded
        )
        if not conflicts:
            return {"conflict_events": [], "alternatives": []}

        if action.get("intent") == "create" and action.get("conflict_strategy") in {"relocate", "propose_relocation"}:
            if len(result.get("events", [])) >= 250:
                return {"clarification": "There are too many events to safely rearrange this window. Please narrow the request."}
            action["end_time"] = end.isoformat()
            try:
                planning_events = list(result.get("events", []))
                if action.get("relocation_date") and action["relocation_date"] != start.date().isoformat():
                    target_start, target_end = day_window(datetime.fromisoformat(action["relocation_date"]))
                    extra = invoke_tool(state, "list_calendar_events", {"max_results": 250, "time_min": target_start, "time_max": target_end})
                    if not extra.get("success") or len(extra.get("events", [])) >= 250:
                        return {"error": "Could not safely check the replacement day. Nothing was changed."}
                    planning_events.extend(extra.get("events", []))
                changes = relocation_plan(planning_events, conflicts, action)
            except Exception as error:
                return {"pending_action": action, "conflict_events": conflicts, "clarification": safe_error_detail(error)}
            action["coordinated"] = True
            # Permission to move Yoga does not authorize moving another event too.
            target = (action.get("displacement_query") or "").casefold()
            exact_scope = bool(target) and all(target in e.get("title", "").casefold() for e in conflicts)
            action["relocation_authorized"] = bool(action.get("relocation_authorized") and exact_scope)
            return {"pending_action": action, "conflict_events": conflicts,
                    "affected_events": conflicts, "proposed_changes": changes}

        window_start, window_end = working_window(start)
        alternative_start = max(end, window_start)
        alternatives = (
            find_free_slots(
                result.get("events", []),
                alternative_start,
                window_end,
                end - start,
                limit=3,
                exclude_event_ids=excluded,
            )
            if alternative_start < window_end
            else []
        )
        conflict_names = ", ".join(event.get("title") for event in conflicts)
        options = ", ".join(
            format_local_range(slot["start"], slot["end"]) for slot in alternatives
        ) or "none that day"
        action["previous_start"] = start.isoformat()
        action["previous_end"] = end.isoformat()
        action.pop("start_time", None)
        action.pop("end_time", None)
        return {
            "pending_action": action,
            "conflict_events": conflicts,
            "alternatives": alternatives,
            "clarification": f"That time conflicts with {conflict_names}. "
            f"Available alternatives: {options}. Which time should I use? "
            "You can also ask me to move the existing event to the next available slot.",
        }

    def route_after_conflict_check(state: CalendarAgentState) -> str:
        if state.get("error"):
            return "handle_error"
        if state.get("clarification"):
            return "generate_response"
        action = state.get("pending_action") or {}
        if action.get("coordinated"):
            return "execute_relocation" if action.get("relocation_authorized") else "request_confirmation"
        return "execute_action"

    def execute_relocation(state: CalendarAgentState):
        """Recheck the reviewed plan, then move blockers before creating.

        Google Calendar is not transactional. Stop at the first failure and
        report completed moves; never retry or claim an automatic rollback.
        """
        action = state.get("pending_action") or {}
        changes = state.get("proposed_changes", [])
        completed = []
        try:
            day_start, day_end = day_window(_plan_datetime(action["start_time"]))
            latest_end = max(_plan_datetime(c["new_end"]) for c in changes)
            day_end = max(day_end, day_window(latest_end)[1])
            current = invoke_tool(state, "list_calendar_events", {"max_results": 250, "time_min": day_start, "time_max": day_end})
            if not current.get("success"):
                raise ValueError("Could not recheck the calendar. No changes were made.")
            events = current.get("events", [])
            if len(events) >= 250:
                raise ValueError("The calendar window is too large to safely verify. No changes were made.")
            by_id = {e.get("event_id"): e for e in events}
            moving = {c["event_id"] for c in changes if c["action"] == "update"}
            for change in changes:
                if change["action"] == "update":
                    old = by_id.get(change["event_id"], {})
                    if old.get("start") != change["old_start"] or old.get("end") != change["old_end"]:
                        raise ValueError("The calendar changed since planning. Please request a fresh plan; nothing was changed.")
                if overlapping_events(events, _plan_datetime(change["new_start"]), _plan_datetime(change["new_end"]), exclude_event_ids=moving):
                    raise ValueError("A proposed time is now occupied. Please request a fresh plan; nothing was changed.")
            for change in changes:
                if change["action"] == "update":
                    result = invoke_tool(state, "update_calendar_event", {"event_id": change["event_id"], "start_time": change["new_start"], "end_time": change["new_end"]})
                else:
                    result = invoke_tool(state, "create_calendar_event", {k: v for k, v in action.items() if k in {"title", "start_time", "end_time", "description", "location"} and v is not None})
                if not result.get("success") or not result.get("event_id"):
                    raise ValueError("A calendar operation failed. Check your calendar before retrying.")
                completed.append(change)
            text = "Schedule updated:\n" + "\n".join(
                f"{'Moved' if c['action'] == 'update' else 'Created'} {c['title']} — {format_local_range(c['new_start'], c['new_end'])}" for c in completed)
            return {"messages": [AIMessage(content=text)], "response": text, "verified": True,
                    "pending_action": None, "proposed_changes": [], "conflict_events": [],
                    "awaiting_confirmation": False, "tool_result": {"success": True, "results": completed}}
        except Exception as error:
            detail = safe_error_detail(error)
            done = "; ".join(f"Moved {c['title']} to {format_local_range(c['new_start'], c['new_end'])}" for c in completed)
            text = f"Could not finish rearranging: {detail}" + (f" Completed: {done}. These moves have not been undone." if done else " No changes were confirmed; check the calendar if a request timed out.")
            return {"messages": [AIMessage(content=text)], "response": text, "error": detail,
                    "verified": False, "pending_action": None, "proposed_changes": [],
                    "awaiting_confirmation": False, "tool_result": {"success": False, "results": completed}}

    def request_confirmation(state: CalendarAgentState):
        changes = list(state.get("proposed_changes", []))
        action = state.get("pending_action") or {}
        if not changes and action.get("intent") == "delete":
            selected = state.get("selected_event") or {}
            changes = [
                {
                    "action": "delete",
                    "event_id": action.get("event_id"),
                    "title": selected.get("title") or action.get("title") or "calendar event",
                    "old_start": selected.get("start"),
                    "old_end": selected.get("end"),
                }
            ]
        rows = []
        for index, change in enumerate(changes, start=1):
            if change["action"] == "delete":
                rows.append(
                    f"{index}. Delete {change.get('title')} — "
                    f"{format_local_range(change.get('old_start'), change.get('old_end'))}"
                )
            elif change["action"] == "update":
                rows.append(
                    f"{index}. Move {change.get('title')} from "
                    f"{format_local_range(change.get('old_start'), change.get('old_end'))} "
                    f"to {format_local_range(change.get('new_start'), change.get('new_end'))}"
                )
            else:
                rows.append(
                    f"{index}. Create {change.get('title')} — "
                    f"{format_local_range(change.get('new_start'), change.get('new_end'))}"
                )
        text = "Proposed changes:\n" + "\n".join(rows) + "\nProceed? (yes/no)"
        return {
            "messages": [AIMessage(content=text)],
            "response": text,
            "awaiting_confirmation": True,
            "confirmation_status": "pending",
        }

    def execute_bulk_action(state: CalendarAgentState):
        results = []
        for change in state.get("proposed_changes", []):
            try:
                if change["action"] == "delete":
                    result = invoke_tool(
                        state,
                        "delete_calendar_event",
                        {"event_id": change["event_id"]}
                    )
                else:
                    result = invoke_tool(
                        state,
                        "update_calendar_event",
                        {
                            "event_id": change["event_id"],
                            "start_time": change["new_start"],
                            "end_time": change["new_end"],
                        }
                    )
            except Exception as error:
                result = {"success": False, "error": safe_error_detail(error)}
            results.append({"event_id": change.get("event_id"), **result})
        return {
            "tool_result": {
                "success": bool(results) and all(item.get("success") for item in results),
                "count": len(results),
                "error": (
                    None
                    if all(item.get("success") for item in results)
                    else "One or more bulk changes failed; inspect the individual results."
                ),
                "results": results,
            }
        }

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
            result = invoke_tool(state, tool_name, args)
        except Exception as error:
            return {"error": f"Calendar action failed: {safe_error_detail(error)}"}
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
        if state.get("confirmation_status") == "declined":
            text = "Cancelled. No calendar events were changed."
            clear = True
        elif clarification:
            text = clarification
            clear = False
        elif intent == "unknown":
            text = "I could not determine the calendar action. Please rephrase the request."
            clear = False
        elif intent == "free_slot":
            slots = result.get("slots", [])
            duration = result.get("duration_minutes", 60)
            if not slots:
                text = "I found no available slots in that time range."
            else:
                duration_label = (
                    f"{duration // 60}-hour" if duration % 60 == 0
                    else f"{duration}-minute"
                )
                rows = [
                    f"{index}. {format_local_range(slot.get('start'), slot.get('end'))}"
                    for index, slot in enumerate(slots, start=1)
                ]
                text = f"Available {duration_label} slots:\n" + "\n".join(rows)
            clear = True
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
                    f"{index}. {event.get('title')} — "
                    f"{format_local_range(event.get('start'), event.get('end'))}"
                    for index, event in enumerate(events, start=1)
                ]
                text = "I found:\n" + "\n".join(rows)
            clear = True
        elif intent == "delete":
            event = state.get("selected_event") or {}
            title = event.get("title") or result.get("event_id")
            text = f"Deleted {title}." if result.get("deleted") else f"{title} was already absent."
            clear = True
        elif intent in {"bulk_update", "bulk_delete"}:
            verb = "updated" if intent == "bulk_update" else "deleted"
            text = f"Successfully {verb} {result.get('count', 0)} events."
            clear = True
        else:
            verb = "Created" if intent == "create" else "Updated"
            text = (
                f"{verb} {result.get('title')} — "
                f"{format_local_range(result.get('start'), result.get('end'))}."
            )
            clear = True
        return {
            "messages": [AIMessage(content=text)],
            "response": text,
            "pending_action": None if clear else state.get("pending_action"),
            "selected_event": None if clear else state.get("selected_event"),
            "candidate_events": [] if clear else state.get("candidate_events", []),
            "affected_events": [] if clear else state.get("affected_events", []),
            "proposed_changes": [] if clear else state.get("proposed_changes", []),
            "awaiting_confirmation": False if clear else state.get("awaiting_confirmation", False),
            "confirmation_status": None if clear else state.get("confirmation_status"),
        }

    def handle_error(state: CalendarAgentState):
        text = f"I could not complete that request: {state.get('error', 'unknown error')}"
        return {
            "messages": [AIMessage(content=text)],
            "response": text,
            "verified": False,
            "awaiting_confirmation": False,
        }

    workflow = StateGraph(CalendarAgentState)
    workflow.add_node("understand_query", traced_node("understand_query", understand_query))
    workflow.add_node("search_calendar", traced_node("search_calendar", search_calendar))
    workflow.add_node("resolve_event", traced_node("resolve_event", resolve_event))
    workflow.add_node("load_calendar_window", traced_node("load_calendar_window", load_calendar_window))
    workflow.add_node("find_free_slot", traced_node("find_free_slot", find_free_slot))
    workflow.add_node("plan_bulk_operation", traced_node("plan_bulk_operation", plan_bulk_operation))
    workflow.add_node("detect_conflicts", traced_node("detect_conflicts", detect_conflicts))
    workflow.add_node("execute_relocation", traced_node("execute_relocation", execute_relocation))
    workflow.add_edge("execute_relocation", END)
    workflow.add_node("request_confirmation", traced_node("request_confirmation", request_confirmation))
    workflow.add_node("execute_bulk_action", traced_node("execute_bulk_action", execute_bulk_action))
    workflow.add_node("execute_action", traced_node("execute_action", execute_action))
    workflow.add_node("verify_result", traced_node("verify_result", verify_result))
    workflow.add_node("generate_response", traced_node("generate_response", generate_response))
    workflow.add_node("handle_error", traced_node("handle_error", handle_error))
    workflow.add_edge(START, "understand_query")
    workflow.add_conditional_edges("understand_query", route_after_understand)
    workflow.add_conditional_edges("search_calendar", route_after_search)
    workflow.add_conditional_edges("resolve_event", route_after_resolve)
    workflow.add_conditional_edges("load_calendar_window", route_after_window_load)
    workflow.add_conditional_edges("find_free_slot", route_after_free_slot)
    workflow.add_conditional_edges("plan_bulk_operation", route_after_planning)
    workflow.add_conditional_edges("detect_conflicts", route_after_conflict_check)
    workflow.add_edge("request_confirmation", END)
    workflow.add_edge("execute_bulk_action", "verify_result")
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
        log_workflow("user_query", thread_id=thread_id, user_query=query)
        config = {"configurable": {"thread_id": thread_id}}
        return self.graph.invoke(
            {
                "messages": [HumanMessage(content=query)],
                "user_query": query,
                "thread_id": thread_id,
            },
            config=config,
        )

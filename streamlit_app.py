"""Streamlit interface for the Task Pilot FastAPI backend."""

from __future__ import annotations

import uuid

import streamlit as st

from app.api_client import TaskPilotAPI, TaskPilotAPIError
from app.config import TASK_PILOT_API_URL
from app.date_utils import format_local_range


def _initialize_state():
    defaults = {
        "thread_id": str(uuid.uuid4()),
        "messages": [],
        "requires_confirmation": False,
        "upcoming_events": [],
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def _render_payload(payload: dict):
    result = payload.get("tool_result") or {}
    events = payload.get("events") or []
    changes = payload.get("proposed_changes") or []
    conflicts = payload.get("conflicts") or []
    alternatives = payload.get("alternatives") or []
    if result.get("event_id") and not events:
        st.markdown(f"**{result.get('title', 'Calendar event')}**")
        if result.get("start"):
            st.caption(format_local_range(result.get("start"), result.get("end")))
        if result.get("html_link"):
            st.link_button("Open in Google Calendar", result["html_link"])
    if events:
        with st.expander(f"Events ({len(events)})"):
            for event in events:
                st.markdown(f"**{event.get('title', '(no title)')}**")
                st.caption(format_local_range(event.get("start"), event.get("end")))
    if changes:
        with st.expander(f"Proposed changes ({len(changes)})", expanded=True):
            for change in changes:
                title = change.get("title", "Calendar event")
                if change.get("action") == "delete":
                    st.markdown(f"**Delete {title}**")
                    st.caption(format_local_range(change.get("old_start"), change.get("old_end")))
                elif change.get("action") == "update":
                    st.markdown(f"**Move {title}**")
                    st.caption(
                        f"From {format_local_range(change.get('old_start'), change.get('old_end'))}  \n"
                        f"To {format_local_range(change.get('new_start'), change.get('new_end'))}"
                    )
                else:
                    st.markdown(f"**Create {title}**")
                    st.caption(format_local_range(change.get("new_start"), change.get("new_end")))
    if conflicts:
        with st.expander(f"Conflicts ({len(conflicts)})", expanded=True):
            for event in conflicts:
                st.markdown(
                    f"- **{event.get('title')}** — "
                    f"{format_local_range(event.get('start'), event.get('end'))}"
                )
    if alternatives:
        with st.expander("Available alternatives", expanded=True):
            for slot in alternatives:
                st.markdown(f"- {format_local_range(slot.get('start'), slot.get('end'))}")


def _send_message(client: TaskPilotAPI, message: str):
    st.session_state.messages.append({"role": "user", "content": message})
    try:
        with st.spinner("Task Pilot is working…", show_time=True):
            payload = client.chat(message, st.session_state.thread_id)
    except TaskPilotAPIError as error:
        payload = {
            "response": str(error),
            "success": False,
            "requires_confirmation": st.session_state.requires_confirmation,
        }
    st.session_state.messages.append(
        {"role": "assistant", "content": payload["response"], "payload": payload}
    )
    st.session_state.requires_confirmation = bool(
        payload.get("requires_confirmation")
    )


def _render_sidebar(client: TaskPilotAPI):
    with st.sidebar:
        st.header("Calendar")
        st.caption(f"Backend: {TASK_PILOT_API_URL}")
        if st.button("Check backend", use_container_width=True):
            try:
                health = client.health()
                st.success(f"Connected · {health.get('model')}")
            except TaskPilotAPIError as error:
                st.error(str(error))
        if st.button("Refresh upcoming events", use_container_width=True):
            try:
                with st.spinner("Loading events…"):
                    st.session_state.upcoming_events = client.events(
                        max_results=10
                    ).get("events", [])
            except TaskPilotAPIError as error:
                st.error(str(error))
        for event in st.session_state.upcoming_events:
            st.markdown(f"**{event.get('title', '(no title)')}**")
            st.caption(format_local_range(event.get("start"), event.get("end")))
        if st.button("New conversation", use_container_width=True):
            st.session_state.thread_id = str(uuid.uuid4())
            st.session_state.messages = []
            st.session_state.requires_confirmation = False
            st.rerun()


def main():
    st.set_page_config(
        page_title="Task Pilot",
        page_icon="📅",
        layout="centered",
    )
    _initialize_state()
    client = TaskPilotAPI()
    _render_sidebar(client)

    st.title("📅 Agentic Calendar Assistant")
    st.caption("Plan, search, reschedule, and safely manage your Google Calendar · All times are IST")

    if not st.session_state.messages:
        st.info(
            "Try: “Find a 2-hour free slot tomorrow and schedule DSA practice.”"
        )
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])
            if message["role"] == "assistant" and message.get("payload"):
                _render_payload(message["payload"])

    if st.session_state.requires_confirmation:
        st.warning(
            "Review the proposed calendar change below. Nothing will be changed "
            "until you confirm."
        )
        yes_column, no_column = st.columns(2)
        if yes_column.button("Confirm", type="primary", use_container_width=True):
            _send_message(client, "yes")
            st.rerun()
        if no_column.button("Cancel", use_container_width=True):
            _send_message(client, "no")
            st.rerun()

    prompt = st.chat_input(
        "Type your calendar request…",
        disabled=st.session_state.requires_confirmation,
    )
    if prompt:
        _send_message(client, prompt)
        st.rerun()


if __name__ == "__main__":
    main()

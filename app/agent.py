"""Provider-independent LangChain agent assembly.

No OpenAI, Anthropic, or local-model package is imported here. Callers inject
any LangChain-compatible chat model when they are ready for live LLM testing.
"""

from langchain.agents import create_agent

from app.calendar_tools import build_calendar_tools
from app.config import TIMEZONE
from app.date_utils import local_now


def calendar_system_prompt() -> str:
    now = local_now()
    return f"""You are a careful calendar assistant.
The user's timezone is {TIMEZONE}. The current local time is {now.isoformat()}.
Select exactly the calendar tool that matches the request.
Resolve relative dates in the user's timezone. Tool date fields accept either
timezone-aware ISO-8601 or these phrases: tomorrow, next Monday, Friday at
6 PM, in two hours, and next week. If no duration is given when creating an
event, omit end_time so the tool uses the one-hour default. Never invent an
event_id: search for the event first when an update or deletion lacks one.
Report the structured tool result accurately, including any error.
Never calculate or display a second timezone conversion. The tool already
returns start and end in {TIMEZONE}; quote those values exactly when reporting
event times.
"""


def create_calendar_agent(model, service):
    """Create the Week 2 agent using an injected model and Calendar service."""
    if model is None:
        raise ValueError("A LangChain-compatible chat model must be supplied.")
    return create_agent(
        model=model,
        tools=build_calendar_tools(service),
        system_prompt=calendar_system_prompt(),
    )


def run_calendar_request(query: str, model, service):
    """Run one natural-language request through the configured agent."""
    if not query.strip():
        raise ValueError("Calendar request cannot be empty.")
    agent = create_calendar_agent(model, service)
    return agent.invoke({"messages": [{"role": "user", "content": query}]})

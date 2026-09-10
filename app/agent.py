"""Compatibility entry points for the stateful Week 3 LangGraph agent."""

from app.graph_agent import CalendarConversation, create_calendar_graph


def create_calendar_agent(model, service, *, checkpointer=None, planner=None):
    """Build the stateful graph (kept under the Week 2 public API name)."""
    return create_calendar_graph(
        model,
        service,
        checkpointer=checkpointer,
        planner=planner,
    )


def run_calendar_request(
    query: str,
    model,
    service,
    *,
    thread_id: str = "default",
    checkpointer=None,
    planner=None,
):
    """Run one request; reuse ``CalendarConversation`` for follow-up turns."""
    conversation = CalendarConversation(
        model,
        service,
        checkpointer=checkpointer,
        planner=planner,
    )
    return conversation.ask(query, thread_id=thread_id)

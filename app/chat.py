"""Run one natural-language request through OpenRouter and Google Calendar."""

import argparse
import sys
import uuid

from app.graph_agent import CalendarConversation
from app.calendar_service import get_calendar_service
from app.llm import create_openrouter_model


def _final_text(result: dict) -> str:
    messages = result.get("messages", [])
    if not messages:
        return str(result)
    content = messages[-1].content
    return content if isinstance(content, str) else str(content)


def main() -> int:
    parser = argparse.ArgumentParser(description="Task Pilot calendar agent")
    parser.add_argument("request", nargs="?", help="Natural-language calendar request")
    parser.add_argument(
        "--thread-id",
        default=None,
        help="Conversation identifier (generated automatically when omitted)",
    )
    args = parser.parse_args()

    try:
        model = create_openrouter_model()
        service = get_calendar_service()
        conversation = CalendarConversation(model, service)
    except (FileNotFoundError, RuntimeError, ValueError) as error:
        print(f"Task Pilot failed: {error}", file=sys.stderr)
        return 1

    thread_id = args.thread_id or str(uuid.uuid4())
    if args.request:
        print(_final_text(conversation.ask(args.request, thread_id=thread_id)))
        return 0

    print("Task Pilot interactive mode. Type 'exit' to stop.")
    while True:
        try:
            query = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if query.casefold() in {"exit", "quit"}:
            break
        if not query:
            continue
        print("Task Pilot:", _final_text(conversation.ask(query, thread_id=thread_id)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

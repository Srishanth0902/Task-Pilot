"""Run one natural-language request through OpenRouter and Google Calendar."""

import argparse
import sys

from app.agent import run_calendar_request
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
    parser.add_argument("request", help="Natural-language calendar request")
    args = parser.parse_args()

    try:
        model = create_openrouter_model()
        service = get_calendar_service()
        result = run_calendar_request(args.request, model=model, service=service)
    except (FileNotFoundError, RuntimeError, ValueError) as error:
        print(f"Task Pilot failed: {error}", file=sys.stderr)
        return 1

    print(_final_text(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

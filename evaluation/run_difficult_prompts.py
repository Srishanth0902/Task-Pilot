"""Run real OpenRouter + LangGraph against a synthetic Calendar API.

Run: python -m evaluation.run_difficult_prompts
No Google credentials are loaded; all writes are recorded in memory.
Each case has a fresh conversation, with a fixed IST date and known events.
"""
import json
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from app.graph_agent import CalendarConversation
from app.llm import create_openrouter_model
from tests.test_calendar_service import FakeEvents, FakeRequest


class SyntheticCalendar(FakeEvents):
    def __init__(self):
        super().__init__()
        self.items = [
            {"id": ident, "summary": title, "start": {"dateTime": f"2026-09-13T{hour:02}:00:00+05:30"},
             "end": {"dateTime": f"2026-09-13T{hour+1:02}:00:00+05:30"}, "status": "confirmed"}
            for ident, title, hour in [("yoga", "Yoga", 7), ("meeting1", "Project meeting", 10),
                                       ("meeting2", "Team meeting", 15), ("study1", "DSA study", 13),
                                       ("study2", "ML study", 16)]
        ]

    def events(self):
        return self

    def list(self, **kwargs):
        self.calls.append(("list", kwargs))
        rows = self.items
        query = kwargs.get("q", "").lower()
        if query:
            rows = [r for r in rows if query in r["summary"].lower()]
        for key, side in [("timeMin", "end"), ("timeMax", "start")]:
            if kwargs.get(key):
                bound = datetime.fromisoformat(kwargs[key])
                rows = [r for r in rows if (datetime.fromisoformat(r[side]["dateTime"]) > bound
                         if side == "end" else datetime.fromisoformat(r[side]["dateTime"]) < bound)]
        rows = sorted(rows, key=lambda r: r["start"]["dateTime"])
        return FakeRequest({"items": rows[:kwargs.get("maxResults", 250)]})

    def patch(self, **kwargs):
        self.calls.append(("patch", kwargs))
        row = next(r for r in self.items if r["id"] == kwargs["eventId"])
        row.update(kwargs["body"])
        return FakeRequest(dict(row))

    def delete(self, **kwargs):
        self.calls.append(("delete", kwargs))
        self.items = [r for r in self.items if r["id"] != kwargs["eventId"]]
        return FakeRequest("")


def run_case(case):
    service = SyntheticCalendar()
    chat = CalendarConversation(create_openrouter_model(), service)
    responses = []
    try:
        for prompt in case["prompts"]:
            state = chat.ask(prompt, thread_id=case["id"])
            responses.append(state["response"])
        mutations = [name for name, _ in service.calls if name in {"insert", "patch", "delete"}]
        expected = case["expected_mutation"]
        passed = (state["intent"] == case["expected_intent"] and not state.get("error")
                  and (mutations == [] if expected == "none" else mutations == [expected]))
        checks = {}
        if "confirmation" in case:
            checks["confirmation"] = bool(state.get("awaiting_confirmation")) == case["confirmation"]
        if "event_count" in case:
            checks["event_count"] = len((state.get("tool_result") or {}).get("events", [])) == case["event_count"]
        if "changes" in case:
            checks["changes"] = len(state.get("proposed_changes", [])) == case["changes"]
        if "response_contains" in case:
            checks["response"] = case["response_contains"].lower() in state["response"].lower()
        if "event_id" in case:
            checks["event_id"] = (state.get("pending_action") or {}).get("event_id") == case["event_id"]
        if "start" in case:
            checks["start"] = (state.get("tool_result") or {}).get("start") == case["start"]
        passed = passed and all(checks.values())
        result = {"id": case["id"], "passed": passed, "intent": state["intent"],
                  "checks": checks, "mutations": mutations, "responses": responses, "calls": service.calls}
    except Exception as error:
        result = {"id": case["id"], "passed": False, "error": type(error).__name__}
    print(f"{case['id']}: {'PASS' if result['passed'] else 'FAIL'}", flush=True)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ids", nargs="*", help="Run just these case IDs")
    args = parser.parse_args()
    root = Path(__file__).parent
    data = json.loads((root / "difficult_prompts.json").read_text(encoding="utf-8"))
    now = datetime.fromisoformat(data["reference_now"])
    with patch("app.graph_agent.local_now", return_value=now):
        with ThreadPoolExecutor(max_workers=3) as pool:
            cases = [c for c in data["cases"] if not args.ids or c["id"] in args.ids]
            results = list(pool.map(run_case, cases))
    if args.ids and (root / "difficult_results.json").exists():
        previous = json.loads((root / "difficult_results.json").read_text(encoding="utf-8"))
        replacements = {r["id"]: r for r in results}
        results = [replacements.get(r["id"], r) for r in previous["results"]]
    report = {"reference_now": data["reference_now"], "total": len(results),
              "passed": sum(r["passed"] for r in results), "results": results}
    (root / "difficult_results.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Passed {report['passed']}/{report['total']}")
    return 0 if report["passed"] == report["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

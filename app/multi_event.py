"""Validate and order an explicit set of calendar moves before any writes."""
import re
from datetime import datetime, timedelta

from app.date_utils import ensure_aware
from app.scheduling import overlapping_events


def plan_moves(events, moves):
    changes = []
    selected_ids = set()
    for move in moves:
        query = re.sub(r"^(?:my|the)\s+", "", move["search_query"], flags=re.I)
        query = re.sub(r"\s+(?:task|event|session)s?$", "", query, flags=re.I).strip().casefold()
        matches = [e for e in events if (e.get("event_id") == move["event_id"] if move.get("event_id") else query and query in e.get("title", "").casefold())]
        if not matches and move.get("create_if_missing"):
            start = ensure_aware(datetime.fromisoformat(move["start_time"]))
            changes.append({"action": "create", "title": move["search_query"], "new_start": start.isoformat(),
                            "new_end": (start + timedelta(minutes=move.get("duration_minutes", 60))).isoformat()})
            continue
        if len(matches) != 1:
            raise ValueError(f"I found {len(matches)} matches for {move['search_query']}. Please give an exact event title/date for each move. No events were changed.")
        event = matches[0]
        if event["event_id"] in selected_ids:
            raise ValueError("The same event has two destinations. Which destination should I use?")
        selected_ids.add(event["event_id"])
        if not event.get("start") or "T" not in event["start"]:
            raise ValueError("Please handle all-day events separately from timed moves.")
        old_start, old_end = [ensure_aware(datetime.fromisoformat(event[k])) for k in ("start", "end")]
        start = ensure_aware(datetime.fromisoformat(move["start_time"]))
        end = start + (old_end - old_start)
        changes.append({"action": "update", "event_id": event["event_id"], "title": event["title"],
                        "old_start": event["start"], "old_end": event["end"],
                        "new_start": start.isoformat(), "new_end": end.isoformat()})
    targets = [{"event_id": c.get("event_id", f"new-{i}"), "start": c["new_start"], "end": c["new_end"]} for i,c in enumerate(changes)]
    for target in targets:
        if overlapping_events(targets, datetime.fromisoformat(target["start"]), datetime.fromisoformat(target["end"]), exclude_event_ids={target["event_id"]}):
            raise ValueError("The requested destinations overlap each other. Choose non-overlapping times; nothing was changed.")
    # Move the blocker first. Do not silently overlap events to execute a cycle.
    remaining = list(changes)
    simulated = [dict(e) for e in events]
    ordered = []
    while remaining:
        ready = next((c for c in remaining if not overlapping_events(simulated,
            datetime.fromisoformat(c["new_start"]), datetime.fromisoformat(c["new_end"]), exclude_event_ids={c["event_id"]} if c.get("event_id") else set())), None)
        if ready is None:
            raise ValueError("These moves are blocked or require a temporary slot. Choose another destination; nothing was changed.")
        remaining.remove(ready)
        ordered.append(ready)
        for event in simulated:
            if ready.get("event_id") and event.get("event_id") == ready["event_id"]:
                event.update(start=ready["new_start"], end=ready["new_end"])
        if ready["action"] == "create":
            simulated.append({"start": ready["new_start"], "end": ready["new_end"]})
    return ordered

"""Deterministic plans for making room without deleting existing events."""
from datetime import datetime, timedelta

from app.date_utils import ensure_aware
from app.scheduling import find_free_slots, working_window


def relocation_plan(events, conflicts, action):
    """Reserve the urgent event, then fit each displaced event later that day."""
    start = ensure_aware(datetime.fromisoformat(action["start_time"]))
    end = ensure_aware(datetime.fromisoformat(action["end_time"]))
    if end <= start:
        raise ValueError("The new event must end after it starts.")
    target_day = datetime.fromisoformat(action["relocation_date"]) if action.get("relocation_date") else start
    if target_day.date() < start.date() or (target_day.date() - start.date()).days > 31:
        raise ValueError("Choose a replacement date within 31 days after the new event.")
    window_start, boundary = working_window(target_day)
    earliest = max(end, window_start)
    if earliest >= boundary:
        raise ValueError("There is no room later within working hours. Which other day should I search?")
    ids = {e["event_id"] for e in conflicts}
    occupied = [e for e in events if e.get("event_id") not in ids]
    occupied.append({"start": start.isoformat(), "end": end.isoformat()})
    changes = []
    for event in sorted(conflicts, key=lambda e: e["start"]):
        if "T" not in event.get("start", "") or "T" not in event.get("end", ""):
            raise ValueError("An all-day event occupies that date. Choose another date or edit it explicitly.")
        old_start = ensure_aware(datetime.fromisoformat(event["start"]))
        old_end = ensure_aware(datetime.fromisoformat(event["end"]))
        slots = find_free_slots(occupied, earliest, boundary, old_end - old_start, limit=1)
        if not slots:
            raise ValueError(f"No replacement slot for {event.get('title')} later that day. Which other day should I search?")
        slot = slots[0]
        changes.append({"action": "update", "event_id": event["event_id"],
                        "title": event.get("title"), "old_start": event["start"],
                        "old_end": event["end"], "new_start": slot["start"], "new_end": slot["end"]})
        occupied.append(slot)
    changes.append({"action": "create", "title": action["title"],
                    "new_start": start.isoformat(), "new_end": end.isoformat()})
    return changes

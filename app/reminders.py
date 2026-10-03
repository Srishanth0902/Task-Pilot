"""Email reminders for upcoming events and assignment deadlines.

The sweep is split from the transport on purpose: ``due_reminders`` decides
what should go out and is a pure function, while ``SmtpMailer`` is the only
piece that touches the network. Tests drive the whole pipeline through a
recording mailer, so reminder logic is verified without an SMTP server.

Each reminder is claimed in the database before it is sent, so a sweep that
runs twice — or two processes sweeping at once — cannot double-send.
"""

import os
import smtplib
import ssl
from datetime import datetime, timedelta
from email.message import EmailMessage

from app.date_utils import ensure_aware, format_local_range, local_now

# How far ahead of an event or deadline a reminder goes out. Two lead times so
# a student gets a day's warning and then a nudge on the day itself.
DEFAULT_EVENT_LEADS = (24 * 60, 60)
DEFAULT_DEADLINE_LEADS = (48 * 60, 24 * 60)

# A sweep only looks at reminders whose moment has arrived within this window.
# Without it, a service restarted after a week of downtime would flood every
# account with stale reminders.
STALE_AFTER_MINUTES = 180


class SmtpMailer:
    """Sends mail through SMTP. The only part of this module that does I/O."""

    def __init__(self, host=None, port=None, username=None, password=None,
                 sender=None, use_tls=None):
        self.host = host or os.getenv("SMTP_HOST", "").strip()
        self.port = int(port or os.getenv("SMTP_PORT", "587"))
        self.username = username if username is not None else os.getenv("SMTP_USERNAME", "").strip()
        self.password = password if password is not None else os.getenv("SMTP_PASSWORD", "")
        self.sender = sender or os.getenv("SMTP_SENDER", "").strip() or self.username
        self.use_tls = (
            use_tls if use_tls is not None
            else os.getenv("SMTP_USE_TLS", "true").strip().lower() not in {"0", "false", "no"}
        )

    @property
    def configured(self) -> bool:
        return bool(self.host and self.sender)

    def send(self, to, subject, body):
        if not self.configured:
            raise RuntimeError(
                "Email reminders need SMTP_HOST and SMTP_SENDER (and usually "
                "SMTP_USERNAME/SMTP_PASSWORD) in the environment."
            )
        message = EmailMessage()
        message["From"] = self.sender
        message["To"] = to
        message["Subject"] = subject
        message.set_content(body)
        with smtplib.SMTP(self.host, self.port, timeout=30) as server:
            if self.use_tls:
                server.starttls(context=ssl.create_default_context())
            if self.username:
                server.login(self.username, self.password)
            server.send_message(message)
        return True


def _lead_times(preferences, key, fallback):
    values = (preferences or {}).get(key)
    if not values:
        return fallback
    return tuple(sorted({int(v) for v in values if int(v) > 0}, reverse=True))


def due_reminders(events, assignments, *, now=None, preferences=None,
                  stale_after=STALE_AFTER_MINUTES):
    """Return the reminders whose moment has arrived.

    A reminder is due when ``now`` has passed its send moment but by no more
    than ``stale_after`` minutes, so a late sweep catches up without replaying
    a backlog from days ago.
    """
    moment = now or local_now()
    cutoff = timedelta(minutes=stale_after)
    pending = []

    for event in events or []:
        start = event.get("start")
        if not start or "T" not in str(start):
            continue  # All-day entries have no useful lead time.
        try:
            begins = ensure_aware(datetime.fromisoformat(start))
        except ValueError:
            continue
        for lead in _lead_times(preferences, "event_reminder_minutes", DEFAULT_EVENT_LEADS):
            send_at = begins - timedelta(minutes=lead)
            if send_at <= moment <= send_at + cutoff and begins > moment:
                pending.append({
                    "kind": "event",
                    "key": f"event:{event.get('event_id')}:{lead}",
                    "lead_minutes": lead,
                    "title": event.get("title") or "Untitled event",
                    "when": begins,
                    "detail": format_local_range(event.get("start"), event.get("end")),
                    "location": event.get("location") or "",
                })
                break  # One reminder per item per sweep.

    for work in assignments or []:
        if work.get("status") == "done":
            continue
        try:
            deadline = ensure_aware(datetime.fromisoformat(work["due"]))
        except (KeyError, TypeError, ValueError):
            continue
        for lead in _lead_times(preferences, "deadline_reminder_minutes", DEFAULT_DEADLINE_LEADS):
            send_at = deadline - timedelta(minutes=lead)
            if send_at <= moment <= send_at + cutoff and deadline > moment:
                pending.append({
                    "kind": "deadline",
                    "key": f"deadline:{work.get('id')}:{lead}",
                    "lead_minutes": lead,
                    "title": work.get("title") or "Untitled assignment",
                    "when": deadline,
                    "detail": format_local_range(work["due"], None),
                    "subject": work.get("subject") or "",
                    "priority": work.get("priority") or "medium",
                })
                break

    pending.sort(key=lambda item: item["when"])
    return pending


def _humanise(minutes):
    if minutes % (24 * 60) == 0:
        days = minutes // (24 * 60)
        return "tomorrow" if days == 1 else f"in {days} days"
    if minutes >= 60:
        hours = minutes // 60
        return "in an hour" if hours == 1 else f"in {hours} hours"
    return f"in {minutes} minutes"


def compose(reminder, name=""):
    """Build the subject and body for one reminder."""
    lead = _humanise(reminder["lead_minutes"])
    if reminder["kind"] == "deadline":
        subject = f"Due {lead}: {reminder['title']}"
        lines = [
            f"Hello{' ' + name if name else ''},",
            "",
            f"Your assignment \"{reminder['title']}\" is due {lead}.",
            f"Deadline: {reminder['detail']}",
        ]
        if reminder.get("subject"):
            lines.append(f"Subject: {reminder['subject']}")
        if reminder.get("priority") and reminder["priority"] != "medium":
            lines.append(f"Priority: {reminder['priority']}")
    else:
        subject = f"Starting {lead}: {reminder['title']}"
        lines = [
            f"Hello{' ' + name if name else ''},",
            "",
            f"\"{reminder['title']}\" starts {lead}.",
            f"When: {reminder['detail']}",
        ]
        if reminder.get("location"):
            lines.append(f"Where: {reminder['location']}")
    lines += ["", "— Task Pilot"]
    return subject, "\n".join(lines)


class ReminderService:
    """Finds due reminders for every account and emails them once each."""

    def __init__(self, store, mailer=None, event_reader=None):
        self.store = store
        self.mailer = mailer or SmtpMailer()
        # Injected so the sweep can run without a live Google connection.
        self.event_reader = event_reader

    def _events(self, user_id, horizon_hours=48):
        if not self.event_reader:
            return []
        try:
            return self.event_reader(user_id, horizon_hours) or []
        except Exception:
            # One account's broken calendar must not stop everyone's deadline
            # reminders, which do not need Google at all.
            return []

    def sweep(self, now=None):
        """Send every reminder that has come due. Returns a per-account tally."""
        moment = now or local_now()
        sent, failed, skipped = 0, 0, 0

        for user_id in self.store.user_ids():
            try:
                profile, _ = self.store.user(user_id)
            except KeyError:
                continue
            address = (profile or {}).get("email")
            if not address:
                continue
            preferences = self.store.preferences(user_id)
            if preferences.get("email_reminders") is False:
                skipped += 1
                continue

            reminders = due_reminders(
                self._events(user_id),
                self.store.assignments(user_id, include_done=False),
                now=moment,
                preferences=preferences,
            )
            for reminder in reminders:
                # Claim first: if the send then fails we do not retry, which is
                # the right trade for reminders. A duplicate is worse than a
                # miss, and the next lead time still catches the user.
                if not self.store.reminder_claim(user_id, reminder["key"]):
                    continue
                subject, body = compose(reminder, (profile or {}).get("name", ""))
                try:
                    self.mailer.send(address, subject, body)
                    sent += 1
                except Exception:
                    failed += 1
        return {"sent": sent, "failed": failed, "skipped_accounts": skipped}

"""Reminder worker: sweeps every account and emails what has come due.

Run it once from cron, or as a long-running loop beside the API:

    python -m app.reminder_worker --once
    python -m app.reminder_worker --interval 900

A single sweep is safe to run from several places at once: every reminder is
claimed in the database before it is sent, so only one sweep can win.
"""

import argparse
import logging
import os
import sys
import time

from app.config import PROJECT_ROOT
from app.date_utils import local_now
from app.reminders import ReminderService, SmtpMailer
from app.user_store import UserStore

log = logging.getLogger("task_pilot.reminders")


def calendar_reader(runtime):
    """Build an event reader backed by each account's own Google credentials."""
    from datetime import timedelta

    from app.calendar_service import get_events

    def read(user_id, horizon_hours=48):
        service = runtime.service(user_id)
        try:
            now = local_now()
            result = get_events(service, 250, now, now + timedelta(hours=horizon_hours))
            return result.get("events", []) if result.get("success") else []
        finally:
            if hasattr(service, "close"):
                service.close()

    return read


def build_service(store=None, mailer=None, with_calendar=True):
    from app.multiuser import UserRuntime

    store = store or UserStore(
        os.getenv("DATA_DIRECTORY", str(PROJECT_ROOT / "data")),
        os.getenv("TOKEN_ENCRYPTION_KEY"),
    )
    reader = calendar_reader(UserRuntime(store)) if with_calendar else None
    return ReminderService(store, mailer or SmtpMailer(), event_reader=reader)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Send Task Pilot email reminders")
    parser.add_argument("--once", action="store_true", help="Run a single sweep and exit")
    parser.add_argument("--interval", type=int, default=900,
                        help="Seconds between sweeps when looping (default 900)")
    parser.add_argument("--no-calendar", action="store_true",
                        help="Only send deadline reminders; do not read Google Calendar")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    service = build_service(with_calendar=not args.no_calendar)

    if not service.mailer.configured:
        print(
            "SMTP is not configured. Set SMTP_HOST and SMTP_SENDER (and usually "
            "SMTP_USERNAME/SMTP_PASSWORD) before running the reminder worker.",
            file=sys.stderr,
        )
        return 1

    while True:
        try:
            result = service.sweep()
            log.info("reminder sweep sent=%s failed=%s skipped_accounts=%s",
                     result["sent"], result["failed"], result["skipped_accounts"])
        except Exception:
            # A worker that dies on one bad sweep stops reminding everyone.
            log.exception("reminder sweep failed")
        if args.once:
            return 0
        time.sleep(max(args.interval, 60))


if __name__ == "__main__":
    sys.exit(main())

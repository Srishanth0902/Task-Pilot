"""Validated, per-account scheduling preferences."""
from datetime import timedelta
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.config import WORKDAY_START_HOUR, WORKDAY_END_HOUR
from app.date_utils import ensure_aware
from app.scheduling import working_window


class SchedulingPreferences(BaseModel):
    work_start: int = Field(default=WORKDAY_START_HOUR, ge=0, le=22)
    work_end: int = Field(default=WORKDAY_END_HOUR, ge=1, le=23)
    break_minutes: int = Field(default=0, ge=0, le=120)
    study_start: int | None = Field(default=None, ge=0, le=22)
    protected_titles: list[str] = Field(default_factory=list, max_length=30)
    preview_changes: bool = False

    # Which calendar this account works against. Kept with the account's other
    # settings because it is per-account, already encrypted, and already has
    # read/write plumbing; it is resolved on the server, never trusted from the
    # browser. An empty value means "decide from whether Google is connected".
    calendar_provider: Literal['', 'google', 'native'] = ''

    # Email reminders. Lead times are minutes before the event start or the
    # assignment deadline; an empty list falls back to the service defaults.
    email_reminders: bool = True
    event_reminder_minutes: list[int] = Field(default_factory=list, max_length=4)
    deadline_reminder_minutes: list[int] = Field(default_factory=list, max_length=4)

    @model_validator(mode='after')
    def valid_window(self):
        if self.work_start >= self.work_end:
            raise ValueError('Working hours must end after they start.')
        if self.study_start is not None and not self.work_start <= self.study_start < self.work_end:
            raise ValueError('Preferred study time must be within working hours.')
        self.protected_titles = list(dict.fromkeys(x.strip() for x in self.protected_titles if x.strip()))
        if any(len(x) > 100 for x in self.protected_titles):
            raise ValueError('Protected event titles must be 100 characters or fewer.')
        for name in ('event_reminder_minutes', 'deadline_reminder_minutes'):
            leads = sorted({int(v) for v in getattr(self, name)}, reverse=True)
            if any(not 5 <= v <= 20160 for v in leads):
                raise ValueError('Reminder lead times must be between 5 minutes and 14 days.')
            setattr(self, name, leads)
        return self

    def window(self, day, title=''):
        beginning = self.work_start
        if self.study_start is not None and any(word in title.casefold() for word in ('study', 'practice', 'revision', 'dsa', 'homework')):
            beginning = self.study_start
        return working_window(day, start_hour=beginning, end_hour=self.work_end)

    def protected(self, title):
        return any(value.casefold() in (title or '').casefold() for value in self.protected_titles)

    def busy_events(self, events):
        """Reserve a break on either side of occupied events for suggestions."""
        from datetime import datetime
        buffered = []
        for event in events:
            item = dict(event)
            if self.break_minutes and 'T' in (item.get('start') or '') and item.get('end'):
                delta = timedelta(minutes=self.break_minutes)
                item['start'] = (ensure_aware(datetime.fromisoformat(item['start'])) - delta).isoformat()
                item['end'] = (ensure_aware(datetime.fromisoformat(item['end'])) + delta).isoformat()
            buffered.append(item)
        return buffered

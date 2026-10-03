"""Assignments: coursework with a deadline, a priority, and a progress state.

Assignments are the unit the Smart Study Planner schedules against and the
reminder service emails about, so the model carries both a deadline and an
effort estimate.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.date_utils import coerce_datetime, ensure_aware, local_now

# Ordered from least to most pressing; the planner and the assignment list both
# sort on this, so the order matters as much as the membership.
PRIORITIES = ("low", "medium", "high", "urgent")
STATUSES = ("todo", "in_progress", "done")

# Weighting used when the planner has to decide which assignment gets the next
# free slot. Deadline proximity dominates, but priority breaks the ties.
PRIORITY_WEIGHT = {"low": 1.0, "medium": 1.5, "high": 2.25, "urgent": 3.5}


class Assignment(BaseModel):
    """One piece of coursework with a deadline."""

    title: str = Field(min_length=1, max_length=200)
    subject: str = Field(default="", max_length=100)
    due: datetime = Field(
        description="Deadline; accepts ISO text or a phrase such as 'next Friday 5 PM'"
    )
    priority: Literal["low", "medium", "high", "urgent"] = "medium"
    status: Literal["todo", "in_progress", "done"] = "todo"
    notes: str = Field(default="", max_length=2000)
    estimated_minutes: int = Field(
        default=60,
        ge=15,
        le=2400,
        description="Expected total effort, used to size study sessions",
    )

    @field_validator("due", mode="before")
    @classmethod
    def parse_due(cls, value):
        return coerce_datetime(value)

    @field_validator("title", "subject", "notes", mode="before")
    @classmethod
    def tidy(cls, value):
        """Collapse whitespace before the length limits are applied.

        Running this after validation would let a title of only spaces satisfy
        min_length and then be collapsed to nothing.
        """
        return " ".join(value.split()) if isinstance(value, str) else value

    @property
    def done(self) -> bool:
        return self.status == "done"

    def overdue(self, now: datetime | None = None) -> bool:
        """Past its deadline and not finished."""
        if self.done:
            return False
        return ensure_aware(self.due) < (now or local_now())

    def urgency(self, now: datetime | None = None) -> float:
        """Rank unfinished work: larger is more pressing.

        Hours remaining drives the score, scaled by priority. Overdue work is
        clamped rather than allowed to run away to infinity, so a forgotten
        assignment from last month cannot permanently crowd out this week's.
        """
        if self.done:
            return 0.0
        moment = now or local_now()
        hours = (ensure_aware(self.due) - moment).total_seconds() / 3600
        hours = max(hours, 1.0)
        return PRIORITY_WEIGHT[self.priority] * (1000.0 / hours)


def sort_key(record: dict):
    """Order assignments for display: unfinished first, then by deadline."""
    status_rank = 1 if record.get("status") == "done" else 0
    return (status_rank, record.get("due") or 0, record.get("title") or "")

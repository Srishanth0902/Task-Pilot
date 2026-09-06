"""Pydantic contracts shared by the CLI, LangChain tools, and tests."""

from datetime import datetime, timedelta

from pydantic import BaseModel, Field, field_validator, model_validator

from app.date_utils import coerce_datetime


class CreateEventInput(BaseModel):
    title: str = Field(min_length=1, description="Short title for the calendar event")
    start_time: datetime = Field(
        description="Timezone-aware ISO time or a phrase such as 'tomorrow at 6 PM'"
    )
    end_time: datetime | None = Field(
        default=None,
        description="Event end; defaults to one hour after the start",
    )
    description: str | None = None
    location: str | None = None

    @field_validator("start_time", "end_time", mode="before")
    @classmethod
    def parse_times(cls, value):
        return None if value is None else coerce_datetime(value)

    @model_validator(mode="after")
    def validate_range(self):
        if self.end_time is None:
            self.end_time = self.start_time + timedelta(hours=1)
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be after start_time")
        return self


class GetEventsInput(BaseModel):
    max_results: int = Field(default=10, ge=1, le=250)
    time_min: datetime | None = Field(
        default=None, description="Earliest event start; defaults to now"
    )
    time_max: datetime | None = Field(default=None, description="Latest event start")

    @field_validator("time_min", "time_max", mode="before")
    @classmethod
    def parse_times(cls, value):
        return None if value is None else coerce_datetime(value)

    @model_validator(mode="after")
    def validate_range(self):
        if self.time_min and self.time_max and self.time_max <= self.time_min:
            raise ValueError("time_max must be after time_min")
        return self


class SearchEventInput(GetEventsInput):
    query: str = Field(min_length=1, description="Words from the event to find")


class UpdateEventInput(BaseModel):
    event_id: str = Field(min_length=1)
    title: str | None = Field(default=None, min_length=1)
    start_time: datetime | None = None
    end_time: datetime | None = None
    description: str | None = None
    location: str | None = None

    @field_validator("start_time", "end_time", mode="before")
    @classmethod
    def parse_times(cls, value):
        return None if value is None else coerce_datetime(value)

    @model_validator(mode="after")
    def validate_update(self):
        fields = (
            self.title,
            self.start_time,
            self.end_time,
            self.description,
            self.location,
        )
        if all(value is None for value in fields):
            raise ValueError("At least one field must be supplied to update an event")
        if (self.start_time is None) != (self.end_time is None):
            raise ValueError("start_time and end_time must be supplied together")
        if self.start_time and self.end_time and self.end_time <= self.start_time:
            raise ValueError("end_time must be after start_time")
        return self


class DeleteEventInput(BaseModel):
    event_id: str = Field(min_length=1)

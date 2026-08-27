from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class OnceCadence(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    kind: Literal["once"] = "once"


class ScheduleCadence(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    kind: Literal["schedule"] = "schedule"
    datetimes: list[datetime] = Field(min_length=1, max_length=365)

    @field_validator("datetimes")
    @classmethod
    def _normalize(cls, v: list[datetime]) -> list[datetime]:
        out = []
        for dt in v:
            if dt.tzinfo is None:
                raise ValueError("schedule datetimes must be timezone-aware")
            out.append(dt.astimezone(UTC))
        if len(set(out)) != len(out):
            raise ValueError("schedule datetimes must be unique")
        return sorted(out)


class RecurringDaysCadence(BaseModel):
    """weekdays are Iranian indices: 0=Saturday .. 6=Friday."""
    model_config = ConfigDict(from_attributes=True)
    kind: Literal["recurring_days"] = "recurring_days"
    mode: Literal["weekdays", "every_n_days", "every_n_weeks", "every_n_months"]
    n: int = Field(default=1, ge=1, le=120)
    weekdays: list[int] = Field(default_factory=list)
    end_date: datetime | None = None

    @model_validator(mode="after")
    def _check(self):
        if self.mode == "weekdays":
            if not self.weekdays:
                raise ValueError("weekdays mode requires at least one weekday")
            if not all(0 <= d <= 6 for d in self.weekdays):
                raise ValueError("weekdays must be 0..6 (0=Saturday)")
            object.__setattr__(self, "weekdays", sorted(set(self.weekdays)))
        elif self.weekdays:
            raise ValueError("weekdays is only valid in weekdays mode")
        return self


class RecurringQuotaCadence(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    kind: Literal["recurring_quota"] = "recurring_quota"
    period: Literal["week", "month"]
    count: int = Field(ge=1, le=100)
    end_date: datetime | None = None


CadenceUnion = Annotated[
    OnceCadence | ScheduleCadence | RecurringDaysCadence | RecurringQuotaCadence,
    Field(discriminator="kind"),
]

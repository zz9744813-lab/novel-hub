"""v9.4: Pydantic request/response schemas for writing sessions (spec §38)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class WritingSessionCreateRequest(BaseModel):
    mode: Literal["duration", "until_time", "manual"] = "duration"
    duration_minutes: int | None = Field(
        default=240, ge=1, le=60 * 24 * 30, description="duration mode: session length"
    )
    until_time: str | None = Field(
        default=None, description="until_time mode: local wall-clock HH:MM"
    )

    # policy overrides (snapshot at creation, spec §8)
    max_unreviewed_ahead: int | None = Field(default=None, ge=0, le=100)
    quality_window_size: int | None = Field(default=None, ge=1, le=1000)
    quality_min_sample: int | None = Field(default=None, ge=1, le=1000)
    minimum_first_pass_yield: float | None = Field(default=None, ge=0, le=1)
    consecutive_bad_limit: int | None = Field(default=None, ge=1, le=100)
    stop_on_needs_human: bool = True
    stop_on_causal_failure: bool = True
    stop_on_quality_drop: bool = True
    stop_on_resource_block: bool = True

    @model_validator(mode="after")
    def _check_mode_fields(self):
        if self.mode == "duration" and not self.duration_minutes:
            raise ValueError("duration mode requires duration_minutes")
        if self.mode == "until_time" and not self.until_time:
            raise ValueError("until_time mode requires until_time (HH:MM)")
        return self


class SessionExtendRequest(BaseModel):
    extend_minutes: int = Field(default=120, ge=1, le=60 * 24 * 30)


class SessionControlResponse(BaseModel):
    id: str
    status: str
    control_requested: str
    message: str = ""

"""Pydantic schemas for the scheduler APIs (Step 7).

Structural validation lives here (Pydantic → automatic 422). Semantic
validation that needs APScheduler/zoneinfo (cron parsing, timezone
existence, cron frequency) lives in SchedulerService and is mapped to 422
by the API layer — never executed, only parsed.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.core import config
from app.schemas.research import MAX_QUESTION_LENGTH, MIN_QUESTION_LENGTH

ScheduleType = Literal["interval", "cron"]
ExecutionStatus = Literal["never_run", "running", "success", "failed"]
# Discord notification outcome of the latest execution (Step 8) — kept
# strictly separate from ExecutionStatus: research succeeds regardless.
NotificationStatus = Literal["not_configured", "sent", "failed"]

# Minimum spacing between runs — protects against excessive frequency
# (cost: each run is one GLM synthesis).
MIN_INTERVAL_MINUTES = 5
# One year — beyond this a schedule is meaningless for recurring research.
MAX_INTERVAL_MINUTES = 525_600

MAX_CRON_EXPRESSION_LENGTH = 100
MAX_TIMEZONE_LENGTH = 64


class ScheduleCreateRequest(BaseModel):
    """Request body for POST /api/research/schedules."""

    question: str = Field(
        min_length=MIN_QUESTION_LENGTH,
        max_length=MAX_QUESTION_LENGTH,
        description="The research question to run on a schedule.",
    )
    schedule_type: ScheduleType = Field(
        description="'interval' (every N minutes) or 'cron' (5-field expression).",
    )
    interval_minutes: int | None = Field(
        default=None,
        ge=MIN_INTERVAL_MINUTES,
        le=MAX_INTERVAL_MINUTES,
        description="Required for schedule_type='interval'; minimum 5 minutes.",
    )
    cron_expression: str | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_CRON_EXPRESSION_LENGTH,
        description=(
            "Required for schedule_type='cron'. Standard five-field cron "
            "(e.g. '0 8 * * *'); parsed by APScheduler, never executed."
        ),
    )
    timezone: str = Field(
        default_factory=lambda: config.DEFAULT_SCHEDULE_TIMEZONE,
        min_length=1,
        max_length=MAX_TIMEZONE_LENGTH,
        description="IANA timezone name, e.g. 'Asia/Jakarta' or 'UTC'.",
    )

    @field_validator("question", "cron_expression", "timezone", mode="before")
    @classmethod
    def strip_strings(cls, value: object) -> object:
        """Trim whitespace so blank values fail length validation."""
        if isinstance(value, str):
            return value.strip()
        return value

    @model_validator(mode="after")
    def schedule_fields_match_type(self) -> ScheduleCreateRequest:
        """Exactly one schedule definition, matching the chosen type."""
        if self.schedule_type == "interval":
            if self.interval_minutes is None:
                raise ValueError(
                    "interval_minutes is required for schedule_type='interval'."
                )
            if self.cron_expression is not None:
                raise ValueError(
                    "cron_expression must not be provided for "
                    "schedule_type='interval'."
                )
        else:
            if self.cron_expression is None:
                raise ValueError(
                    "cron_expression is required for schedule_type='cron'."
                )
            if self.interval_minutes is not None:
                raise ValueError(
                    "interval_minutes must not be provided for "
                    "schedule_type='cron'."
                )
        return self


class ScheduleInfo(BaseModel):
    """Public view of one scheduled research job.

    Datetimes are timezone-aware ISO 8601 (UTC-normalized). Internal
    APScheduler objects (triggers, job handles) are never exposed.
    """

    id: str
    question: str
    schedule_type: ScheduleType
    schedule_expression: str
    interval_minutes: int | None = None
    cron_expression: str | None = None
    timezone: str
    enabled: bool
    created_at: datetime
    last_run_at: datetime | None = None
    next_run_at: datetime | None = None
    last_status: ExecutionStatus
    last_error: str | None = None
    last_report_id: str | None = None
    # Outcome of the Discord notification for the latest execution. Reads
    # "not_configured" until a run happens with no webhook configured.
    # The webhook URL itself is a backend secret and is never exposed.
    notification_status: NotificationStatus = "not_configured"


class ScheduleCreateResponse(BaseModel):
    """Response body for POST /api/research/schedules."""

    status: Literal["success"] = "success"
    schedule: ScheduleInfo


class ScheduleListResponse(BaseModel):
    """Response body for GET /api/research/schedules."""

    status: Literal["success"] = "success"
    schedules: list[ScheduleInfo]
    total: int = Field(ge=0)


class ScheduleActionResponse(BaseModel):
    """Response body for GET one / pause / resume."""

    status: Literal["success"] = "success"
    schedule: ScheduleInfo


class ScheduleDeleteResponse(BaseModel):
    """Response body for DELETE /api/research/schedules/{id}."""

    status: Literal["success"] = "success"
    schedule_id: str
    deleted: Literal[True] = True


class ScheduleRunResponse(BaseModel):
    """Response body for POST /api/research/schedules/{id}/run."""

    status: Literal["accepted"] = "accepted"
    schedule_id: str
    message: str = "Research execution started."


# --------------------------------------------------- Step 9: execution history

TriggerType = Literal["scheduled", "manual"]
ExecutionOutcome = Literal["running", "success", "failed"]

# History listing bounds (validated → automatic 422 outside the range).
MIN_HISTORY_LIMIT = 1
MAX_HISTORY_LIMIT = 100
DEFAULT_HISTORY_LIMIT = 20


class ExecutionInfo(BaseModel):
    """One persisted research execution (Step 9).

    Datetimes are timezone-aware ISO 8601 (UTC-normalized). `error` is the
    same client-safe message the schedule surfaces — never a stack trace,
    API key, header, or environment value.
    """

    id: str
    schedule_id: str | None = None
    trigger_type: TriggerType
    started_at: datetime
    completed_at: datetime | None = None
    status: ExecutionOutcome
    error: str | None = None
    report_id: str | None = None
    source_count: int | None = None
    notification_status: NotificationStatus


class ExecutionListResponse(BaseModel):
    """Response body for GET /api/research/schedules/{id}/executions."""

    status: Literal["success"] = "success"
    schedule_id: str
    executions: list[ExecutionInfo]
    total: int = Field(ge=0)

"""API routes for scheduled research jobs (Steps 7 & 9).

Endpoints under /api/research/schedules manage the SQLite-backed schedule
registry and trigger manual runs. The scheduler loop itself is started
and stopped by the application lifespan (see app.main), never at import
time; startup restores persisted schedules (Step 9).
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query

from app.database.database import from_db, init_database
from app.database.models import ExecutionRow
from app.schemas.schedule import (
    DEFAULT_HISTORY_LIMIT,
    MAX_HISTORY_LIMIT,
    MIN_HISTORY_LIMIT,
    ExecutionInfo,
    ExecutionListResponse,
    ScheduleActionResponse,
    ScheduleCreateRequest,
    ScheduleCreateResponse,
    ScheduleDeleteResponse,
    ScheduleInfo,
    ScheduleListResponse,
    ScheduleRunResponse,
)
from app.services.scheduler_service import (
    ScheduleJob,
    ScheduleNotFoundError,
    SchedulerPersistenceError,
    SchedulerService,
    SchedulerValidationError,
    to_utc,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/research/schedules", tags=["schedules"])

# Shared service instance. Created lazily (never at import) and reset on
# lifespan shutdown so every application lifecycle starts clean.
_scheduler_service: SchedulerService | None = None


def get_scheduler_service() -> SchedulerService:
    """Provide the scheduler service (override in tests)."""
    global _scheduler_service
    if _scheduler_service is None:
        _scheduler_service = SchedulerService()
    return _scheduler_service


def stop_scheduler_service() -> None:
    """Stop the shared service and drop the singleton (lifespan shutdown)."""
    global _scheduler_service
    if _scheduler_service is not None:
        _scheduler_service.shutdown()
    _scheduler_service = None


@asynccontextmanager
async def scheduler_lifespan(_: object) -> AsyncIterator[None]:
    """Initialize persistence and start the scheduler with the app.

    Startup order (Step 9): database init (idempotent create-tables) →
    scheduler service → load persisted schedules → register enabled ones
    → start the APScheduler loop. Shutdown reverses this (stop loop; the
    SQLite data stays, of course). Lives here so all scheduler wiring
    stays in one module; app.main composes it into the app lifespan.
    """
    init_database()
    service = get_scheduler_service()
    service.start()
    try:
        yield
    finally:
        stop_scheduler_service()


def _to_info(service: SchedulerService, job: ScheduleJob) -> ScheduleInfo:
    """Map an internal ScheduleJob to its public API shape (UTC datetimes)."""
    return ScheduleInfo(
        id=job.id,
        question=job.question,
        schedule_type=job.schedule_type,
        schedule_expression=job.schedule_expression,
        interval_minutes=job.interval_minutes,
        cron_expression=job.cron_expression,
        timezone=job.timezone,
        enabled=job.enabled,
        created_at=to_utc(job.created_at),
        last_run_at=to_utc(job.last_run_at),
        next_run_at=to_utc(service.next_run_at(job)),
        last_status=job.last_status,
        last_error=job.last_error,
        last_report_id=job.last_report_id,
        notification_status=job.notification_status,
    )


def _to_execution_info(row: ExecutionRow) -> ExecutionInfo:
    """Map a persisted execution row to its public API shape."""
    return ExecutionInfo(
        id=row.id,
        schedule_id=row.schedule_id,
        trigger_type=row.trigger_type,
        started_at=from_db(row.started_at),
        completed_at=from_db(row.completed_at),
        status=row.status,
        error=row.error,
        report_id=row.report_id,
        source_count=row.source_count,
        notification_status=row.notification_status,
    )


@router.post("", response_model=ScheduleCreateResponse)
async def create_schedule(
    request: ScheduleCreateRequest,
    scheduler: SchedulerService = Depends(get_scheduler_service),
) -> ScheduleCreateResponse:
    """Create a scheduled research job (interval or cron, IANA timezone)."""
    try:
        job = scheduler.create_schedule(request)
    except SchedulerValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.message) from None
    except SchedulerPersistenceError as exc:
        raise HTTPException(status_code=503, detail=exc.message) from None
    return ScheduleCreateResponse(schedule=_to_info(scheduler, job))


@router.get("", response_model=ScheduleListResponse)
async def list_schedules(
    scheduler: SchedulerService = Depends(get_scheduler_service),
) -> ScheduleListResponse:
    """List all scheduled research jobs (persisted in SQLite, Step 9)."""
    try:
        jobs = scheduler.list_schedules()
    except SchedulerPersistenceError as exc:
        raise HTTPException(status_code=503, detail=exc.message) from None
    return ScheduleListResponse(
        schedules=[_to_info(scheduler, job) for job in jobs],
        total=len(jobs),
    )


@router.get("/{schedule_id}", response_model=ScheduleActionResponse)
async def get_schedule(
    schedule_id: str,
    scheduler: SchedulerService = Depends(get_scheduler_service),
) -> ScheduleActionResponse:
    """Inspect one scheduled research job."""
    try:
        job = scheduler.get_schedule(schedule_id)
    except ScheduleNotFoundError as exc:
        raise HTTPException(status_code=404, detail=exc.message) from None
    except SchedulerPersistenceError as exc:
        raise HTTPException(status_code=503, detail=exc.message) from None
    return ScheduleActionResponse(schedule=_to_info(scheduler, job))


@router.get(
    "/{schedule_id}/executions", response_model=ExecutionListResponse
)
async def list_schedule_executions(
    schedule_id: str,
    limit: int = Query(
        default=DEFAULT_HISTORY_LIMIT,
        ge=MIN_HISTORY_LIMIT,
        le=MAX_HISTORY_LIMIT,
        description="How many executions to return (most recent first).",
    ),
    scheduler: SchedulerService = Depends(get_scheduler_service),
) -> ExecutionListResponse:
    """Execution history for one schedule (Step 9).

    One row per pipeline run — manual and scheduled alike — newest first.
    Values outside 1–100 are rejected with 422 before any query runs.
    """
    try:
        rows = scheduler.list_executions(schedule_id, limit=limit)
    except ScheduleNotFoundError as exc:
        raise HTTPException(status_code=404, detail=exc.message) from None
    except SchedulerPersistenceError as exc:
        raise HTTPException(status_code=503, detail=exc.message) from None
    return ExecutionListResponse(
        schedule_id=schedule_id,
        executions=[_to_execution_info(row) for row in rows],
        total=len(rows),
    )


@router.post("/{schedule_id}/pause", response_model=ScheduleActionResponse)
async def pause_schedule(
    schedule_id: str,
    scheduler: SchedulerService = Depends(get_scheduler_service),
) -> ScheduleActionResponse:
    """Pause (disable) a schedule; in-flight executions finish untouched."""
    try:
        job = scheduler.pause_schedule(schedule_id)
    except ScheduleNotFoundError as exc:
        raise HTTPException(status_code=404, detail=exc.message) from None
    except SchedulerPersistenceError as exc:
        raise HTTPException(status_code=503, detail=exc.message) from None
    return ScheduleActionResponse(schedule=_to_info(scheduler, job))


@router.post("/{schedule_id}/resume", response_model=ScheduleActionResponse)
async def resume_schedule(
    schedule_id: str,
    scheduler: SchedulerService = Depends(get_scheduler_service),
) -> ScheduleActionResponse:
    """Resume (enable) a paused schedule."""
    try:
        job = scheduler.resume_schedule(schedule_id)
    except ScheduleNotFoundError as exc:
        raise HTTPException(status_code=404, detail=exc.message) from None
    except SchedulerValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.message) from None
    except SchedulerPersistenceError as exc:
        raise HTTPException(status_code=503, detail=exc.message) from None
    return ScheduleActionResponse(schedule=_to_info(scheduler, job))


@router.post(
    "/{schedule_id}/run", response_model=ScheduleRunResponse, status_code=202
)
async def run_schedule_now(
    schedule_id: str,
    background: BackgroundTasks,
    scheduler: SchedulerService = Depends(get_scheduler_service),
) -> ScheduleRunResponse:
    """Execute a schedule's research immediately, without touching it.

    The pipeline runs as a background task after the acknowledgement; the
    recurring schedule (trigger, next run) is never modified or deleted.
    Each run opens one persisted execution record (Step 9).
    """
    try:
        scheduler.get_schedule(schedule_id)
    except ScheduleNotFoundError as exc:
        raise HTTPException(status_code=404, detail=exc.message) from None
    except SchedulerPersistenceError as exc:
        raise HTTPException(status_code=503, detail=exc.message) from None
    if scheduler.is_running(schedule_id):
        raise HTTPException(
            status_code=409,
            detail="An execution is already running for this schedule.",
        )
    background.add_task(scheduler.run_now, schedule_id)
    return ScheduleRunResponse(schedule_id=schedule_id)


@router.delete("/{schedule_id}", response_model=ScheduleDeleteResponse)
async def delete_schedule(
    schedule_id: str,
    scheduler: SchedulerService = Depends(get_scheduler_service),
) -> ScheduleDeleteResponse:
    """Delete a schedule permanently; execution history is preserved."""
    try:
        job = scheduler.delete_schedule(schedule_id)
    except ScheduleNotFoundError as exc:
        raise HTTPException(status_code=404, detail=exc.message) from None
    except SchedulerPersistenceError as exc:
        raise HTTPException(status_code=503, detail=exc.message) from None
    return ScheduleDeleteResponse(schedule_id=job.id)

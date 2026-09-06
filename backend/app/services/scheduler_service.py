"""Scheduler service — recurring automated research jobs (Steps 7 & 9).

An in-process APScheduler (AsyncIOScheduler) drives the existing research
pipeline through ResearchExecutionService. All APScheduler-specific logic
lives here; nothing else in the codebase imports APScheduler.

Step 9 persistence model — SQLite is the SOURCE OF TRUTH:
- Every registry change (create / pause / resume / delete) is written to
  SQLite first; the in-memory dict is only a runtime cache of ScheduleJob
  objects, refreshed from the database on read.
- On startup, persisted schedules are loaded, re-validated, their
  triggers rebuilt, and only the ENABLED ones are attached to
  APScheduler. APScheduler itself remains a pure runtime engine (no
  persistent jobstore) — restarts reconstruct everything from SQLite.
- Every execution (manual or scheduled) writes ONE `research_executions`
  row at start and updates it at completion, alongside the schedule's
  last-run metadata and (on success) the report metadata.
- Deleting a schedule preserves execution history and report metadata
  (nullable SET NULL foreign keys); report files are never deleted.

Security posture:
- Cron expressions are only ever parsed by APScheduler's CronTrigger —
  never evaluated, never passed to a shell, no eval/exec anywhere.
- Timezones are resolved through zoneinfo (IANA names only).
- Frequency floors (>= 5 minutes) apply to both interval and cron
  schedules, protecting the GLM budget from excessive runs.
- Failures mark the job `failed` with a client-safe message; the
  scheduler itself keeps running and future fires still happen.
- Database problems during an execution are logged and skipped (the
  research run is the product); database problems during registry
  operations surface as a safe SchedulerPersistenceError (→ HTTP 503)
  after rollback. No SQL, paths, or stack traces reach clients.

Step 8: after each execution's outcome is known (success or failure),
exactly ONE Discord notification is dispatched via
DiscordNotificationService. Notification delivery problems are recorded
in `job.notification_status` and never change the research status.
"""

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apscheduler.jobstores.base import JobLookupError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.schedulers.base import SchedulerNotRunningError
from apscheduler.triggers.base import BaseTrigger
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from pydantic import ValidationError

from app.database.database import PersistenceError, from_db, session_scope
from app.database.models import ExecutionRow, ScheduleRow
from app.database.repositories import (
    ExecutionRepository,
    ReportRepository,
    ScheduleRepository,
)
from app.schemas.schedule import (
    MAX_INTERVAL_MINUTES,
    MIN_INTERVAL_MINUTES,
    ScheduleCreateRequest,
)
from app.services.discord_service import (
    STATUS_FAILED as NOTIFICATION_FAILED,
)
from app.services.discord_service import (
    STATUS_NOT_CONFIGURED as NOTIFICATION_NOT_CONFIGURED,
)
from app.services.discord_service import (
    DiscordNotificationService,
)
from app.services.research_execution_service import (
    ExecutionResult,
    ResearchExecutionError,
    ResearchExecutionService,
    default_research_execution_service,
)

logger = logging.getLogger(__name__)

STATUS_NEVER_RUN = "never_run"
STATUS_RUNNING = "running"
STATUS_SUCCESS = "success"
STATUS_FAILED = "failed"

# What caused an execution (shown in Discord notifications).
TRIGGER_SCHEDULED = "Scheduled"
TRIGGER_MANUAL = "Manual"
# Persisted trigger_type values (research_executions table, Step 9).
TRIGGER_TYPE_SCHEDULED = "scheduled"
TRIGGER_TYPE_MANUAL = "manual"

# Registry cap — also bounds worst-case concurrent GLM traffic.
MAX_SCHEDULES = 50
# How many pipeline executions may run at the same time, globally.
MAX_CONCURRENT_EXECUTIONS = 2
# Minimum spacing between cron fires (intervals get it via field bounds).
MIN_CRON_GAP_SECONDS = MIN_INTERVAL_MINUTES * 60
# Late fires are coalesced into one run instead of a burst of catch-ups.
MISFIRE_GRACE_SECONDS = 300
# Client-safe error messages are short by construction; hard cap anyway.
MAX_LAST_ERROR_LENGTH = 500

# Report filenames on disk are `<prefix><report_id>.md` / `.pdf` (mirrors
# report_service.FILENAME_PREFIX; duplicated so this module's import chain
# never loads ReportLab).
_REPORT_FILENAME_PREFIX = "researchflow_"


class SchedulerValidationError(Exception):
    """Invalid schedule definition. `message` is client-safe (→ HTTP 422)."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class ScheduleNotFoundError(Exception):
    """No schedule with that id (→ HTTP 404). `message` is client-safe."""

    def __init__(self, message: str = "Schedule not found.") -> None:
        super().__init__(message)
        self.message = message


class SchedulerPersistenceError(Exception):
    """SQLite could not be written (→ HTTP 503). `message` is client-safe."""

    def __init__(
        self, message: str = "Scheduling storage is temporarily unavailable."
    ) -> None:
        super().__init__(message)
        self.message = message


def utcnow() -> datetime:
    """Timezone-aware current UTC time (never naive)."""
    return datetime.now(UTC)


def to_utc(value: datetime | None) -> datetime | None:
    """Normalize an aware datetime to UTC; leave None as None."""
    if value is None:
        return None
    if value.tzinfo is None:  # defensive — APScheduler returns aware values
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


@dataclass
class ScheduleJob:
    """Internal schedule record (the runtime cache entry, not an API schema).

    `trigger`, `is_running`, and the persistence helpers treat this as the
    in-process working copy of one persisted `schedules` row. `trigger`,
    `is_running` are internal machinery and never serialized to API
    responses.
    """

    id: str
    question: str
    schedule_type: str
    interval_minutes: int | None
    cron_expression: str | None
    timezone: str
    enabled: bool = True
    created_at: datetime = field(default_factory=utcnow)
    last_run_at: datetime | None = None
    last_status: str = STATUS_NEVER_RUN
    last_error: str | None = None
    last_report_id: str | None = None
    # Discord notification outcome of the latest execution (Step 8):
    # not_configured / sent / failed — independent of `last_status`.
    notification_status: str = NOTIFICATION_NOT_CONFIGURED
    # --- internal (never exposed) ---
    trigger: BaseTrigger | None = None
    is_running: bool = False

    @property
    def schedule_expression(self) -> str:
        """Canonical human-readable expression for API responses."""
        if self.schedule_type == "interval":
            return f"every {self.interval_minutes} minutes"
        return self.cron_expression or ""


def resolve_timezone(name: str) -> ZoneInfo:
    """Resolve an IANA timezone name or raise SchedulerValidationError."""
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, KeyError, OSError):
        raise SchedulerValidationError(
            f"Unknown timezone {name!r}. Use an IANA name such as "
            "'Asia/Jakarta' or 'UTC'."
        ) from None


class SchedulerService:
    """Owns the APScheduler runtime and the SQLite-backed schedule registry."""

    def __init__(
        self,
        execution_service: ResearchExecutionService | None = None,
        notification_service: DiscordNotificationService | None = None,
    ) -> None:
        """`execution_service` and `notification_service` are injectable for
        tests; by default the real pipeline is wired lazily on first
        execution (listing schedules then never loads the embedding model
        or ChromaDB) and the notifier reads the backend env webhook.

        Persistence always goes through app.database.session_scope(), which
        resolves config.DATABASE_URL lazily (tests point it at a temporary
        SQLite file; production uses data/researchflow.db)."""
        self._execution = execution_service
        self._notifications = notification_service
        self._scheduler: AsyncIOScheduler | None = None
        self._jobs: dict[str, ScheduleJob] = {}
        self._slots: asyncio.Semaphore | None = None
        self._slots_loop: asyncio.AbstractEventLoop | None = None

    # ------------------------------------------------------- lifecycle

    @property
    def started(self) -> bool:
        """True while the APScheduler loop is running."""
        return self._scheduler is not None

    def start(self) -> None:
        """Start the scheduler and restore persisted schedules.

        Startup order (Step 9): load rows from SQLite → re-validate →
        rebuild triggers → start APScheduler → attach ENABLED schedules
        only. Idempotent (no-op if running); must be called from a running
        event loop (FastAPI lifespan is).
        """
        if self._scheduler is not None:
            return
        restored, skipped = self._restore_from_database()
        scheduler = AsyncIOScheduler(
            timezone=UTC,
            job_defaults={
                "coalesce": True,
                "max_instances": 1,
                "misfire_grace_time": MISFIRE_GRACE_SECONDS,
            },
        )
        scheduler.start()
        self._scheduler = scheduler
        attached = 0
        for job in self._jobs.values():
            if job.enabled and job.trigger is not None:
                self._attach(job)
                attached += 1
        logger.info(
            "Scheduler started (%d attached, %d restored, %d skipped)",
            attached,
            restored,
            skipped,
        )

    def shutdown(self) -> None:
        """Stop the scheduler and drop the runtime cache.

        SQLite rows are deliberately KEPT — restarting the backend
        restores every schedule from the database.
        """
        if self._scheduler is not None:
            scheduler, self._scheduler = self._scheduler, None
            try:
                scheduler.shutdown(wait=False)
            except SchedulerNotRunningError:
                pass  # already stopped (e.g. during interpreter teardown)
        self._jobs.clear()
        self._slots = None
        self._slots_loop = None
        logger.info("Scheduler shut down; runtime cache cleared (data kept)")

    # ------------------------------------------------------- registry ops

    def create_schedule(self, request: ScheduleCreateRequest) -> ScheduleJob:
        """Validate, persist, and register a new scheduled research job.

        Order: validate → write the SQLite row → attach to APScheduler.
        If APScheduler registration fails, the persisted row is rolled
        back so the database never describes a schedule that cannot run.
        """
        try:
            with session_scope() as session:
                existing = ScheduleRepository(session).count()
        except PersistenceError:
            raise SchedulerPersistenceError() from None
        if existing >= MAX_SCHEDULES:
            raise SchedulerValidationError(
                f"Schedule limit reached ({MAX_SCHEDULES}). Delete one first."
            )
        tz = resolve_timezone(request.timezone)
        trigger = self._build_trigger(request, tz)
        job = ScheduleJob(
            id=uuid.uuid4().hex,
            question=request.question,
            schedule_type=request.schedule_type,
            interval_minutes=request.interval_minutes,
            cron_expression=request.cron_expression,
            timezone=request.timezone,
            trigger=trigger,
        )
        try:
            with session_scope() as session:
                ScheduleRepository(session).create(
                    schedule_id=job.id,
                    question=job.question,
                    schedule_type=job.schedule_type,
                    interval_minutes=job.interval_minutes,
                    cron_expression=job.cron_expression,
                    timezone=job.timezone,
                    enabled=job.enabled,
                    created_at=job.created_at,
                    next_run_at=self.next_run_at(job),
                    last_status=job.last_status,
                    notification_status=job.notification_status,
                )
        except PersistenceError:
            raise SchedulerPersistenceError() from None
        self._jobs[job.id] = job
        if self._scheduler is not None:
            try:
                self._attach(job)
            except Exception:
                logger.exception(
                    "APScheduler registration failed for schedule %s",
                    job.id[:8],
                )
                try:
                    with session_scope() as session:
                        ScheduleRepository(session).delete(job.id)
                except PersistenceError:
                    logger.warning(
                        "Could not roll back schedule %s", job.id[:8]
                    )
                self._jobs.pop(job.id, None)
                raise SchedulerPersistenceError(
                    "Could not register the schedule. Please try again."
                ) from None
        logger.info(
            "Schedule %s created (%s, %s, tz=%s)",
            job.id[:8], job.schedule_type, job.schedule_expression, job.timezone,
        )
        return job

    def list_schedules(self) -> list[ScheduleJob]:
        """All schedules (read from SQLite), oldest first for the UI."""
        try:
            with session_scope() as session:
                rows = ScheduleRepository(session).list_all()
        except PersistenceError:
            raise SchedulerPersistenceError() from None
        jobs: list[ScheduleJob] = []
        for row in rows:
            job = self._jobs.get(row.id)
            if job is None:
                job = self._job_from_row(row)
                self._jobs[job.id] = job
            jobs.append(job)
        return jobs

    def get_schedule(self, schedule_id: str) -> ScheduleJob:
        """One schedule by id (runtime cache first, then SQLite)."""
        job = self._jobs.get(schedule_id)
        if job is not None:
            return job
        try:
            with session_scope() as session:
                row = ScheduleRepository(session).get(schedule_id)
        except PersistenceError:
            raise SchedulerPersistenceError() from None
        if row is None:
            raise ScheduleNotFoundError("Schedule not found.")
        job = self._job_from_row(row)
        self._jobs[job.id] = job
        return job

    def pause_schedule(self, schedule_id: str) -> ScheduleJob:
        """Disable a schedule (idempotent). Existing runs finish untouched."""
        job = self.get_schedule(schedule_id)
        self._persist_enabled(job, enabled=False, next_run_at=None)
        job.enabled = False
        if self._scheduler is not None:
            apscheduler_job = self._scheduler.get_job(job.id)
            if apscheduler_job is not None:
                apscheduler_job.pause()
        logger.info("Schedule %s paused", job.id[:8])
        return job

    def resume_schedule(self, schedule_id: str) -> ScheduleJob:
        """Re-enable a paused schedule (idempotent, re-validated)."""
        job = self.get_schedule(schedule_id)
        if job.trigger is None:
            # Defensive re-validation of a stored definition (spec: resume
            # must confirm the schedule still forms a valid trigger).
            job.trigger = self._rebuild_trigger(job)
        job.enabled = True
        try:
            self._persist_enabled(
                job, enabled=True, next_run_at=self.next_run_at(job)
            )
        except SchedulerPersistenceError:
            job.enabled = False
            raise
        if self._scheduler is not None:
            apscheduler_job = self._scheduler.get_job(job.id)
            if apscheduler_job is None:
                self._attach(job)
            else:
                apscheduler_job.resume()
        logger.info("Schedule %s resumed", job.id[:8])
        return job

    def delete_schedule(self, schedule_id: str) -> ScheduleJob:
        """Remove a schedule permanently; history and reports survive.

        Execution history rows and report metadata keep existing with a
        NULL schedule_id (ON DELETE SET NULL); report FILES are never
        deleted automatically.
        """
        job = self.get_schedule(schedule_id)
        if self._scheduler is not None:
            try:
                self._scheduler.remove_job(job.id)
            except JobLookupError:
                pass  # job already removed or was never registered
        try:
            with session_scope() as session:
                ScheduleRepository(session).delete(job.id)
        except PersistenceError:
            raise SchedulerPersistenceError() from None
        self._jobs.pop(job.id, None)
        logger.info(
            "Schedule %s deleted (execution history preserved)", job.id[:8]
        )
        return job

    def list_executions(
        self, schedule_id: str, *, limit: int
    ) -> list[ExecutionRow]:
        """Most recent execution records for one schedule (newest first)."""
        self.get_schedule(schedule_id)  # 404 when the schedule is unknown
        try:
            with session_scope() as session:
                rows = ExecutionRepository(session).list_for_schedule(
                    schedule_id, limit=limit
                )
        except PersistenceError:
            raise SchedulerPersistenceError() from None
        return list(rows)

    # ------------------------------------------------------- execution

    def is_running(self, schedule_id: str) -> bool:
        """True while an execution for this schedule is in flight."""
        job = self._jobs.get(schedule_id)
        return job is not None and job.is_running

    async def run_now(self, schedule_id: str) -> None:
        """Execute one schedule immediately (manual run).

        Does not touch the recurring schedule, the trigger, or next_run_at.
        """
        await self._execute(self.get_schedule(schedule_id), trigger=TRIGGER_MANUAL)

    def next_run_at(self, job: ScheduleJob) -> datetime | None:
        """Computed next fire time (UTC) — None when paused.

        Computed from the trigger rather than read back from APScheduler so
        the value is correct even before/without a running scheduler loop.
        """
        if not job.enabled or job.trigger is None:
            return None
        return to_utc(job.trigger.get_next_fire_time(None, utcnow()))

    async def _apscheduler_entry(self, schedule_id: str) -> None:
        """APScheduler entrypoint — must never raise (scheduler survives)."""
        job = self._jobs.get(schedule_id)
        if job is None:  # deleted between being scheduled and firing
            return
        await self._execute(job, trigger=TRIGGER_SCHEDULED)

    async def _execute(self, job: ScheduleJob, trigger: str = TRIGGER_SCHEDULED) -> None:
        """Run one pipeline execution with per-schedule and global guards.

        Concurrency contract: the check-and-set of `is_running` happens
        with no await between check and assignment, and every execution
        runs on the same event loop, so a second entry (scheduled fire,
        manual run, or both) always observes the flag and skips.

        Persistence (Step 9): ONE execution row is opened at start and
        completed at the end, together with the schedule's last-run
        metadata, the report metadata (on success), and — after the
        notification — the notification status. Each write is a short
        transaction; none is held across the pipeline call itself.
        """
        if job.is_running:
            logger.info(
                "Schedule %s skipped: previous execution still running",
                job.id[:8],
            )
            return
        job.is_running = True
        job.last_status = STATUS_RUNNING
        job.last_run_at = utcnow()
        execution_id = uuid.uuid4().hex
        self._persist_execution_start(job, execution_id, trigger)
        result: ExecutionResult | None = None
        try:
            async with self._execution_slots():
                result = await self._execution_service().run(job.question)
        except ResearchExecutionError as exc:
            job.last_status = STATUS_FAILED
            job.last_error = exc.message[:MAX_LAST_ERROR_LENGTH]
            logger.warning(
                "Scheduled execution failed for %s: %s", job.id[:8], exc.message
            )
        except Exception:
            # Log the traceback server-side; clients only get a generic text.
            job.last_status = STATUS_FAILED
            job.last_error = "Unexpected error during scheduled research."
            logger.exception("Unexpected error executing schedule %s", job.id)
        else:
            job.last_status = STATUS_SUCCESS
            job.last_error = None
            job.last_report_id = result.report_id
            logger.info(
                "Scheduled execution completed for %s (report %s)",
                job.id[:8],
                result.report_id,
            )
        finally:
            job.is_running = False
        self._persist_execution_outcome(job, execution_id, result)
        # Notification happens strictly AFTER the outcome is recorded, and
        # its own failures cannot touch `last_status` (see _notify).
        await self._notify(job, trigger, result)
        self._persist_notification(job, execution_id)

    async def _notify(
        self, job: ScheduleJob, trigger: str, result: ExecutionResult | None
    ) -> None:
        """Send the single Discord notification for one execution (Step 8).

        Exactly one message per execution — a success embed OR a failure
        embed, never both, never per-stage spam. The research status set
        by `_execute` is never modified here, no matter what happens.
        """
        notifier = self._notification_service()
        if not notifier.is_configured:
            job.notification_status = NOTIFICATION_NOT_CONFIGURED
            logger.info(
                "Discord notification skipped: not configured (schedule %s)",
                job.id[:8],
            )
            return
        completed_at = to_utc(job.last_run_at) or utcnow()
        try:
            if job.last_status == STATUS_SUCCESS:
                job.notification_status = await notifier.send_success(
                    question=job.question,
                    trigger=trigger,
                    report_id=job.last_report_id or "",
                    sources_found=result.sources_found if result else 0,
                    completed_at=completed_at,
                    timezone_name=job.timezone,
                )
            else:
                job.notification_status = await notifier.send_failure(
                    question=job.question,
                    trigger=trigger,
                    error=job.last_error or "",
                    occurred_at=completed_at,
                    timezone_name=job.timezone,
                )
        except Exception:
            # Defense in depth: notifier methods never raise by contract,
            # but a broken implementation must not corrupt the job record.
            job.notification_status = NOTIFICATION_FAILED
            logger.exception(
                "Notification dispatch failed for schedule %s", job.id[:8]
            )
            return
        logger.info(
            "Schedule %s notification status: %s", job.id[:8], job.notification_status
        )

    # ------------------------------------------------------- persistence

    def _restore_from_database(self) -> tuple[int, int]:
        """Load persisted schedules into the runtime cache. Never raises.

        Invalid stored definitions are cached without a trigger (visible
        via the API, `next_run_at: null`) but never registered — a single
        bad row must not crash startup.
        """
        try:
            with session_scope() as session:
                rows = ScheduleRepository(session).list_all()
        except PersistenceError:
            logger.warning(
                "Could not restore schedules from the database; starting "
                "with an empty registry"
            )
            return 0, 0
        restored = 0
        skipped = 0
        for row in rows:
            if row.id in self._jobs:
                continue  # already known (e.g. created before start())
            job = self._job_from_row(row)
            if job.trigger is None:
                skipped += 1
            self._jobs[job.id] = job
            restored += 1
        return restored, skipped

    def _job_from_row(self, row: ScheduleRow) -> ScheduleJob:
        """Rebuild a runtime job from a persisted row (re-validated)."""
        job = ScheduleJob(
            id=row.id,
            question=row.question,
            schedule_type=row.schedule_type,
            interval_minutes=row.interval_minutes,
            cron_expression=row.cron_expression,
            timezone=row.timezone,
            enabled=row.enabled,
            created_at=from_db(row.created_at) or utcnow(),
            last_run_at=from_db(row.last_run_at),
            last_status=row.last_status,
            last_error=row.last_error,
            last_report_id=row.last_report_id,
            notification_status=row.notification_status,
        )
        try:
            job.trigger = self._rebuild_trigger(job)
        except (SchedulerValidationError, ValidationError):
            job.trigger = None
            logger.warning(
                "Schedule %s not registered: stored definition failed "
                "validation",
                job.id[:8],
            )
        return job

    def _rebuild_trigger(self, job: ScheduleJob) -> BaseTrigger:
        """Rebuild (and thereby re-validate) a trigger from stored fields."""
        request = ScheduleCreateRequest(
            question=job.question,
            schedule_type=job.schedule_type,
            interval_minutes=job.interval_minutes,
            cron_expression=job.cron_expression,
            timezone=job.timezone,
        )
        return self._build_trigger(request, resolve_timezone(job.timezone))

    def _persist_enabled(
        self, job: ScheduleJob, *, enabled: bool, next_run_at: datetime | None
    ) -> None:
        try:
            with session_scope() as session:
                ScheduleRepository(session).set_enabled(
                    job.id,
                    enabled=enabled,
                    next_run_at=next_run_at,
                    updated_at=utcnow(),
                )
        except PersistenceError:
            raise SchedulerPersistenceError() from None

    def _persist_execution_start(
        self, job: ScheduleJob, execution_id: str, trigger: str
    ) -> None:
        """Open the execution history row (best-effort: a storage problem
        must never stop the research run itself; the scheduler stays alive)."""
        try:
            with session_scope() as session:
                ExecutionRepository(session).create(
                    execution_id=execution_id,
                    schedule_id=job.id,
                    trigger_type=(
                        TRIGGER_TYPE_MANUAL
                        if trigger == TRIGGER_MANUAL
                        else TRIGGER_TYPE_SCHEDULED
                    ),
                    started_at=job.last_run_at or utcnow(),
                    notification_status=job.notification_status,
                )
                ScheduleRepository(session).mark_started(
                    job.id,
                    last_run_at=job.last_run_at or utcnow(),
                    last_status=STATUS_RUNNING,
                    updated_at=utcnow(),
                )
        except PersistenceError:
            logger.warning(
                "Could not persist execution start for schedule %s", job.id[:8]
            )

    def _persist_execution_outcome(
        self, job: ScheduleJob, execution_id: str, result: ExecutionResult | None
    ) -> None:
        """Persist run outcome + report metadata (best-effort, short tx)."""
        try:
            with session_scope() as session:
                ScheduleRepository(session).record_outcome(
                    job.id,
                    last_status=job.last_status,
                    last_error=job.last_error,
                    last_report_id=job.last_report_id,
                    next_run_at=self.next_run_at(job),
                    updated_at=utcnow(),
                )
                ExecutionRepository(session).mark_completed(
                    execution_id,
                    status=job.last_status,
                    completed_at=utcnow(),
                    error=job.last_error,
                    report_id=job.last_report_id,
                    source_count=result.sources_found if result else None,
                )
                if result is not None and job.last_status == STATUS_SUCCESS:
                    ReportRepository(session).create(
                        report_id=result.report_id,
                        schedule_id=job.id,
                        execution_id=execution_id,
                        query=job.question,
                        markdown_filename=(
                            f"{_REPORT_FILENAME_PREFIX}{result.report_id}.md"
                        ),
                        pdf_filename=(
                            f"{_REPORT_FILENAME_PREFIX}{result.report_id}.pdf"
                        ),
                        created_at=utcnow(),
                        synthesis_status=result.synthesis_status,
                    )
        except PersistenceError:
            logger.warning(
                "Could not persist execution outcome for schedule %s",
                job.id[:8],
            )

    def _persist_notification(self, job: ScheduleJob, execution_id: str) -> None:
        """Persist the notification outcome (best-effort, short tx)."""
        try:
            with session_scope() as session:
                ScheduleRepository(session).set_notification_status(
                    job.id, job.notification_status, updated_at=utcnow()
                )
                ExecutionRepository(session).set_notification_status(
                    execution_id, job.notification_status
                )
        except PersistenceError:
            logger.warning(
                "Could not persist notification status for schedule %s",
                job.id[:8],
            )

    # ------------------------------------------------------- internals

    def _execution_service(self) -> ResearchExecutionService:
        """The pipeline to run — injected fake or the lazily-built real one."""
        if self._execution is None:
            self._execution = default_research_execution_service()
        return self._execution

    def _notification_service(self) -> DiscordNotificationService:
        """The notifier — injected fake or the env-configured real one."""
        if self._notifications is None:
            self._notifications = DiscordNotificationService()
        return self._notifications

    def _execution_slots(self) -> asyncio.Semaphore:
        """Global execution cap, rebound if the event loop changed.

        Production runs on one loop forever; the rebind only matters for
        test harnesses that drive the same service from fresh loops.
        """
        loop = asyncio.get_running_loop()
        if self._slots is None or self._slots_loop is not loop:
            self._slots = asyncio.Semaphore(MAX_CONCURRENT_EXECUTIONS)
            self._slots_loop = loop
        return self._slots

    def _attach(self, job: ScheduleJob) -> None:
        """Register an enabled job with the running APScheduler."""
        assert self._scheduler is not None
        self._scheduler.add_job(
            func=self._apscheduler_entry,
            trigger=job.trigger,
            id=job.id,
            name=f"research:{job.schedule_expression}",
            kwargs={"schedule_id": job.id},
            replace_existing=True,
        )

    @staticmethod
    def _build_trigger(
        request: ScheduleCreateRequest, tz: ZoneInfo
    ) -> BaseTrigger:
        """Build the APScheduler trigger from a validated request.

        Cron strings go ONLY through CronTrigger.from_crontab (parsing,
        never execution). A cron that fires more often than the frequency
        floor is rejected after measuring its first two fire times.
        """
        if request.schedule_type == "interval":
            minutes = request.interval_minutes
            if minutes is None or not (
                MIN_INTERVAL_MINUTES <= minutes <= MAX_INTERVAL_MINUTES
            ):
                raise SchedulerValidationError(
                    f"interval_minutes must be between {MIN_INTERVAL_MINUTES} "
                    f"and {MAX_INTERVAL_MINUTES}."
                )
            return IntervalTrigger(minutes=minutes, timezone=tz)

        try:
            trigger = CronTrigger.from_crontab(
                (request.cron_expression or ""), timezone=tz
            )
        except ValueError:
            raise SchedulerValidationError(
                "Invalid cron expression: expected exactly five fields like "
                "'0 8 * * *'."
            ) from None

        now = utcnow()
        first = trigger.get_next_fire_time(None, now)
        second = trigger.get_next_fire_time(first, first) if first else None
        if (
            first is not None
            and second is not None
            and (second - first).total_seconds() < MIN_CRON_GAP_SECONDS
        ):
            raise SchedulerValidationError(
                "Cron schedule is too frequent: runs must be at least "
                f"{MIN_INTERVAL_MINUTES} minutes apart."
            )
        return trigger

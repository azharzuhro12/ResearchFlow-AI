"""Tests for SchedulerService — registry, lifecycle, execution, concurrency.

No real pipeline runs: ResearchExecutionService is always a fake. The real
APScheduler loop is started only inside `asyncio.run(...)` blocks that also
shut it down, so no scheduler threads survive pytest (test isolation).
"""

import asyncio
from datetime import timedelta

import pytest

from app.schemas.schedule import ScheduleCreateRequest
from app.services.research_execution_service import (
    ExecutionResult,
    ResearchExecutionError,
    ResearchExecutionService,
)
from app.services.scheduler_service import (
    MAX_CONCURRENT_EXECUTIONS,
    ScheduleNotFoundError,
    SchedulerService,
    SchedulerValidationError,
    utcnow,
)

QUESTION = "What are the latest developments in Retrieval-Augmented Generation?"
REPORT_ID = "fake-report-0011aa22"


class FakeExecutionService(ResearchExecutionService):
    """Scripted pipeline: counts runs, can fail or block on demand."""

    def __init__(
        self,
        error: ResearchExecutionError | None = None,
        block: asyncio.Event | None = None,
    ) -> None:
        self.error = error
        self.block = block
        self.questions: list[str] = []

    async def run(self, question: str, top_k: int = 5) -> ExecutionResult:
        self.questions.append(question)
        if self.block is not None:
            await self.block.wait()
        if self.error is not None:
            raise self.error
        return ExecutionResult(
            report_id=REPORT_ID,
            synthesis_status="success",
            sources_found=4,
            indexed_sources=4,
            failed_sources=0,
            total_chunks=12,
        )


def make_service(
    execution: ResearchExecutionService | None = None,
) -> SchedulerService:
    return SchedulerService(execution_service=execution)


def interval_request(minutes: int = 30, question: str = QUESTION):
    return ScheduleCreateRequest(
        question=question,
        schedule_type="interval",
        interval_minutes=minutes,
    )


def cron_request(expression: str = "0 8 * * *", timezone_name: str = "Asia/Jakarta"):
    return ScheduleCreateRequest(
        question=QUESTION,
        schedule_type="cron",
        cron_expression=expression,
        timezone=timezone_name,
    )


@pytest.fixture
def service() -> SchedulerService:
    svc = make_service(FakeExecutionService())
    yield svc
    svc.shutdown()


# ------------------------------------------------------------ create & inspect


def test_create_interval_schedule() -> None:
    svc = make_service()
    job = svc.create_schedule(interval_request(minutes=30))
    try:
        assert job.schedule_type == "interval"
        assert job.interval_minutes == 30
        assert job.cron_expression is None
        assert job.timezone == "Asia/Jakarta"  # documented default
        assert job.enabled is True
        assert job.last_status == "never_run"
        assert job.last_run_at is None
        assert job.last_report_id is None
        assert job.schedule_expression == "every 30 minutes"
        assert job.id  # generated id
    finally:
        svc.shutdown()


def test_create_cron_schedule() -> None:
    svc = make_service()
    job = svc.create_schedule(cron_request())
    try:
        assert job.schedule_type == "cron"
        assert job.cron_expression == "0 8 * * *"
        assert job.interval_minutes is None
        assert job.schedule_expression == "0 8 * * *"
        assert job.timezone == "Asia/Jakarta"
    finally:
        svc.shutdown()


def test_list_schedules_oldest_first() -> None:
    svc = make_service()
    first = svc.create_schedule(interval_request(minutes=10, question="A question long enough"))
    second = svc.create_schedule(cron_request())
    try:
        jobs = svc.list_schedules()
        assert [job.id for job in jobs] == [first.id, second.id]
    finally:
        svc.shutdown()


def test_get_schedule_and_unknown_id() -> None:
    svc = make_service()
    job = svc.create_schedule(interval_request())
    try:
        assert svc.get_schedule(job.id) is job
        with pytest.raises(ScheduleNotFoundError):
            svc.get_schedule("does-not-exist")
    finally:
        svc.shutdown()


# ------------------------------------------------------------- next run calc


def test_next_run_for_interval_is_now_plus_interval() -> None:
    svc = make_service()
    job = svc.create_schedule(interval_request(minutes=15))
    try:
        before = utcnow()
        nxt = svc.next_run_at(job)
        after = utcnow()
        assert nxt is not None and nxt.tzinfo is not None  # timezone-aware
        assert before <= nxt <= after + timedelta(minutes=15)
        # APScheduler aligns interval fires to the trigger's start grid, so
        # allow sub-second skew around the nominal 15 minutes.
        elapsed = nxt - before
        assert timedelta(minutes=15) - timedelta(seconds=5) <= elapsed
    finally:
        svc.shutdown()


def test_next_run_for_cron_in_schedule_timezone() -> None:
    svc = make_service()
    # 01:00 UTC every day == 08:00 Asia/Jakarta (UTC+7, no DST).
    job = svc.create_schedule(cron_request("0 1 * * *", "UTC"))
    try:
        nxt = svc.next_run_at(job)
        assert nxt is not None
        assert nxt.tzinfo is not None
        assert nxt.minute == 0 and nxt.hour == 1
        assert nxt > utcnow()
        # A cron in Jakarta resolves against Jakarta wall clock but is
        # still returned as an aware UTC datetime.
        jakarta_job = svc.create_schedule(cron_request("0 8 * * *", "Asia/Jakarta"))
        nxt_jkt = svc.next_run_at(jakarta_job)
        assert nxt_jkt is not None and nxt_jkt.tzinfo is not None
        assert (nxt_jkt.hour, nxt_jkt.minute) in {(1, 0), (0, 0)}
    finally:
        svc.shutdown()


def test_paused_schedule_has_no_next_run() -> None:
    svc = make_service()
    job = svc.create_schedule(interval_request())
    svc.pause_schedule(job.id)
    try:
        assert svc.next_run_at(job) is None
    finally:
        svc.shutdown()


# ---------------------------------------------------------- pause/resume/delete


def test_pause_and_resume_toggle_enabled() -> None:
    svc = make_service()
    job = svc.create_schedule(interval_request())
    paused = svc.pause_schedule(job.id)
    assert paused.enabled is False
    resumed = svc.resume_schedule(job.id)
    assert resumed.enabled is True
    assert svc.next_run_at(resumed) is not None
    svc.shutdown()


def test_pause_is_idempotent() -> None:
    svc = make_service()
    job = svc.create_schedule(interval_request())
    svc.pause_schedule(job.id)
    svc.pause_schedule(job.id)
    try:
        assert job.enabled is False
    finally:
        svc.shutdown()


def test_pause_resume_unknown_id_raise() -> None:
    svc = make_service()
    try:
        with pytest.raises(ScheduleNotFoundError):
            svc.pause_schedule("missing")
        with pytest.raises(ScheduleNotFoundError):
            svc.resume_schedule("missing")
        with pytest.raises(ScheduleNotFoundError):
            svc.delete_schedule("missing")
    finally:
        svc.shutdown()


def test_delete_removes_schedule() -> None:
    svc = make_service()
    job = svc.create_schedule(interval_request())
    deleted = svc.delete_schedule(job.id)
    try:
        assert deleted.id == job.id
        assert svc.list_schedules() == []
        with pytest.raises(ScheduleNotFoundError):
            svc.get_schedule(job.id)
    finally:
        svc.shutdown()


# ------------------------------------------------------------------ validation


@pytest.mark.parametrize(
    "expression",
    [
        "not a cron",       # wrong field count
        "0 8 * * * *",      # six fields
        "0 8 *",            # three fields
        "* * * * *",        # every minute — below the frequency floor
        "*/2 * * * *",      # every 2 minutes — below the floor
        "0 8 * * *; rm -rf /",  # shell-looking junk must never parse
        "*/3 * * * *",
    ],
)
def test_invalid_cron_expressions_rejected(expression: str) -> None:
    svc = make_service()
    try:
        with pytest.raises(SchedulerValidationError):
            svc.create_schedule(cron_request(expression))
        assert svc.list_schedules() == []  # nothing registered on rejection
    finally:
        svc.shutdown()


@pytest.mark.parametrize("expression", ["0 8 * * *", "*/5 * * * *", "30 7 * * 1", "0 0 1 * *"])
def test_valid_cron_expressions_accepted(expression: str) -> None:
    svc = make_service()
    try:
        job = svc.create_schedule(cron_request(expression))
        assert job.cron_expression == expression
    finally:
        svc.shutdown()


@pytest.mark.parametrize(
    "timezone_name", ["Mars/Olympus", "Not/A/Zone", "UTC/Plus/8", "/etc/passwd"]
)
def test_invalid_timezones_rejected(timezone_name: str) -> None:
    svc = make_service()
    try:
        with pytest.raises(SchedulerValidationError):
            svc.create_schedule(cron_request(timezone_name))
    finally:
        svc.shutdown()


def test_blank_timezone_rejected_at_schema_level() -> None:
    # A whitespace-only timezone strips to empty and fails the schema's
    # min_length before the service is involved (still a 422 via the API).
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ScheduleCreateRequest(
            question=QUESTION,
            schedule_type="cron",
            cron_expression="0 8 * * *",
            timezone="   ",
        )


def test_schedule_limit_is_enforced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    svc = make_service()
    monkeypatch.setattr("app.services.scheduler_service.MAX_SCHEDULES", 2)
    try:
        svc.create_schedule(interval_request(minutes=10, question="Question number one here"))
        svc.create_schedule(interval_request(minutes=20, question="Question number two here"))
        with pytest.raises(SchedulerValidationError) as excinfo:
            svc.create_schedule(interval_request(minutes=30))
        assert "Batas jumlah jadwal" in str(excinfo.value)
    finally:
        svc.shutdown()


def test_interval_out_of_range_rejected_at_schema_level() -> None:
    # Interval bounds (>= 5 minutes, <= one year) are schema constraints:
    # too-frequent, zero, negative, and pathologically large values are
    # rejected by Pydantic before the service runs (→ 422 via the API).
    from pydantic import ValidationError

    for minutes in (4, 0, -10, 525_601, 10**9):
        with pytest.raises(ValidationError):
            interval_request(minutes=minutes)


def test_wrong_schedule_fields_rejected_at_schema_level() -> None:
    from pydantic import ValidationError

    # Missing interval / cron for the chosen type:
    with pytest.raises(ValidationError):
        ScheduleCreateRequest(question=QUESTION, schedule_type="interval")
    with pytest.raises(ValidationError):
        ScheduleCreateRequest(question=QUESTION, schedule_type="cron")
    # Providing the wrong definition for the type:
    with pytest.raises(ValidationError):
        ScheduleCreateRequest(
            question=QUESTION,
            schedule_type="interval",
            interval_minutes=30,
            cron_expression="0 8 * * *",
        )
    with pytest.raises(ValidationError):
        ScheduleCreateRequest(
            question=QUESTION,
            schedule_type="cron",
            cron_expression="0 8 * * *",
            interval_minutes=30,
        )
    # Unknown schedule type:
    with pytest.raises(ValidationError):
        ScheduleCreateRequest(
            question=QUESTION,
            schedule_type="hourly",
            interval_minutes=60,
        )
    # Question too short:
    with pytest.raises(ValidationError):
        ScheduleCreateRequest(
            question="too short",
            schedule_type="interval",
            interval_minutes=60,
        )


# ------------------------------------------------------------------ execution


def test_manual_run_executes_and_records_success(service: SchedulerService) -> None:
    job = service.create_schedule(interval_request())
    asyncio.run(service.run_now(job.id))

    assert job.last_status == "success"
    assert job.last_report_id == REPORT_ID
    assert job.last_error is None
    assert job.last_run_at is not None and job.last_run_at.tzinfo is not None
    assert service.is_running(job.id) is False

    fake = service._execution
    assert isinstance(fake, FakeExecutionService)
    assert fake.questions == [QUESTION]


def test_manual_run_does_not_change_the_schedule(service: SchedulerService) -> None:
    job = service.create_schedule(interval_request(minutes=45))
    before = service.next_run_at(job)
    asyncio.run(service.run_now(job.id))
    after = service.next_run_at(job)
    assert before is not None and after is not None
    assert before == after  # trigger untouched by a manual run
    assert job.enabled is True


def test_execution_failure_marks_failed_and_survives(service: SchedulerService) -> None:
    failing = FakeExecutionService(error=ResearchExecutionError("Synthesis failed: timeout"))
    failing_service = make_service(failing)
    try:
        job = failing_service.create_schedule(interval_request())
        asyncio.run(failing_service.run_now(job.id))

        assert job.last_status == "failed"
        assert job.last_error == "Synthesis failed: timeout"
        assert job.last_report_id is None
        # The schedule remains registered and can run again.
        assert failing_service.get_schedule(job.id) is job
        failing.error = None
        asyncio.run(failing_service.run_now(job.id))
        assert job.last_status == "success"
    finally:
        failing_service.shutdown()


def test_unexpected_error_is_wrapped_client_safe(service: SchedulerService) -> None:
    class ExplodingService(FakeExecutionService):
        async def run(self, question: str, top_k: int = 5) -> ExecutionResult:
            raise RuntimeError("secret traceback with GLM_API_KEY=abc")

    exploding = ExplodingService()
    exploding_service = make_service(exploding)
    try:
        job = exploding_service.create_schedule(interval_request())
        asyncio.run(exploding_service.run_now(job.id))
        assert job.last_status == "failed"
        assert job.last_error == "Error tak terduga saat riset terjadwal."
        assert "GLM_API_KEY" not in (job.last_error or "")
        assert "traceback" not in (job.last_error or "").lower()
    finally:
        exploding_service.shutdown()


def test_apscheduler_entry_with_deleted_job_does_not_raise(service: SchedulerService) -> None:
    # Simulates the race where a job is deleted between being scheduled
    # and firing: the callback must be a silent no-op, never an error.
    asyncio.run(service._apscheduler_entry("already-deleted"))


def test_same_schedule_cannot_run_concurrently() -> None:
    release = asyncio.Event()
    fake = FakeExecutionService(block=release)
    svc = make_service(fake)

    async def scenario() -> None:
        job = svc.create_schedule(interval_request())
        first = asyncio.create_task(svc.run_now(job.id))
        await asyncio.sleep(0.01)  # let the first execution enter `run`
        second = asyncio.create_task(svc.run_now(job.id))
        await second  # second entry returns without starting a run
        assert fake.questions == [QUESTION]  # exactly one execution started
        assert svc.is_running(job.id) is True
        release.set()
        await first
        assert svc.is_running(job.id) is False
        assert job.last_status == "success"
        # After completion a new execution can start again.
        await svc.run_now(job.id)
        assert len(fake.questions) == 2

    try:
        asyncio.run(scenario())
    finally:
        release.set()
        svc.shutdown()


def test_global_execution_cap_is_bounded() -> None:
    from app.services import scheduler_service

    assert MAX_CONCURRENT_EXECUTIONS >= 1
    assert scheduler_service.MAX_SCHEDULES > 0


def test_execution_slots_rebind_across_event_loops() -> None:
    # TestClient / pytest drive the same service from fresh event loops;
    # the concurrency semaphore must rebind instead of raising.
    fake = FakeExecutionService()
    svc = make_service(fake)
    try:
        job = svc.create_schedule(interval_request())
        asyncio.run(svc.run_now(job.id))
        asyncio.run(svc.run_now(job.id))  # second, different loop
        assert len(fake.questions) == 2
    finally:
        svc.shutdown()


# ------------------------------------------------------------------ lifecycle


def test_start_registers_jobs_and_shutdown_clears() -> None:
    svc = make_service(FakeExecutionService())

    async def scenario() -> None:
        job = svc.create_schedule(interval_request(minutes=10))
        assert svc.started is False  # nothing started at creation time
        svc.start()
        assert svc.started is True
        apscheduler_job = svc._scheduler.get_job(job.id)
        assert apscheduler_job is not None  # attached to the real scheduler
        svc.pause_schedule(job.id)
        assert svc._scheduler.get_job(job.id).next_run_time is None
        svc.resume_schedule(job.id)
        assert svc._scheduler.get_job(job.id).next_run_time is not None
        svc.delete_schedule(job.id)
        assert svc._scheduler.get_job(job.id) is None
        svc.shutdown()
        assert svc.started is False
        assert svc.list_schedules() == []  # in-memory registry cleared

    asyncio.run(scenario())


def test_start_is_idempotent() -> None:
    svc = make_service(FakeExecutionService())

    async def scenario() -> None:
        svc.start()
        scheduler = svc._scheduler
        svc.start()  # no second instance, no error
        assert svc._scheduler is scheduler
        svc.shutdown()
        svc.shutdown()  # also idempotent

    asyncio.run(scenario())


def test_next_run_matches_apscheduler_computation() -> None:
    svc = make_service(FakeExecutionService())

    async def scenario() -> None:
        job = svc.create_schedule(cron_request("0 8 * * *"))
        svc.start()
        apscheduler_next = svc._scheduler.get_job(job.id).next_run_time
        ours = svc.next_run_at(job)
        assert ours is not None and apscheduler_next is not None
        # Both computations agree to the second (allow sub-second skew).
        assert abs((ours - apscheduler_next).total_seconds()) < 1.0
        svc.shutdown()

    asyncio.run(scenario())


# ----------------------------------------------------- restart persistence (Step 9)
#
# The critical restart test from the Step 9 spec: two SEPARATE service
# instances over the SAME temporary SQLite database. Instance one shuts
# down (simulating a process exit — runtime cache gone, rows kept);
# instance two starts cold and must rebuild everything from SQLite alone.


def test_restart_restores_enabled_and_skips_paused_schedules() -> None:
    first = make_service(FakeExecutionService())
    second = make_service(FakeExecutionService())

    async def scenario() -> None:
        first.start()
        enabled = first.create_schedule(
            interval_request(30, question="Enabled survivor")
        )
        paused = first.create_schedule(
            interval_request(45, question="Paused survivor")
        )
        first.pause_schedule(paused.id)
        first.shutdown()  # process dies; SQLite rows remain

        second.start()  # cold start → restore from SQLite
        restored_enabled = second.get_schedule(enabled.id)
        assert restored_enabled.question == "Enabled survivor"
        assert restored_enabled.enabled is True
        # Enabled schedule is ACTIVE again: registered with a next run.
        ap_job = second._scheduler.get_job(enabled.id)
        assert ap_job is not None and ap_job.next_run_time is not None

        restored_paused = second.get_schedule(paused.id)
        assert restored_paused is not None  # still known…
        assert restored_paused.enabled is False  # …but NOT registered active
        paused_ap_job = second._scheduler.get_job(paused.id)
        assert paused_ap_job is None or paused_ap_job.next_run_time is None

        # Pause/resume survive the restart end-to-end.
        second.resume_schedule(paused.id)
        resumed = second._scheduler.get_job(paused.id)
        assert resumed is not None and resumed.next_run_time is not None
        second.shutdown()

    asyncio.run(scenario())


def test_execution_history_and_report_metadata_survive_restart() -> None:
    first = make_service(FakeExecutionService())
    second = make_service(FakeExecutionService())

    async def scenario() -> None:
        first.start()
        job = first.create_schedule(interval_request(60))
        await first.run_now(job.id)  # records execution + report metadata
        first.shutdown()

        second.start()  # cold start
        executions = second.list_executions(job.id, limit=10)
        assert len(executions) == 1
        row = executions[0]
        assert row.schedule_id == job.id
        assert row.trigger_type == "manual"
        assert row.status == "success"
        assert row.report_id == REPORT_ID
        assert row.source_count == 4

        # Report metadata survived the restart too (same DB, new session).
        from app.database.database import session_scope
        from app.database.repositories import ReportRepository

        with session_scope() as session:
            report = ReportRepository(session).get(REPORT_ID)
        assert report is not None
        assert report.schedule_id == job.id
        assert report.markdown_filename == f"researchflow_{REPORT_ID}.md"
        assert report.pdf_filename == f"researchflow_{REPORT_ID}.pdf"
        second.shutdown()

    asyncio.run(scenario())

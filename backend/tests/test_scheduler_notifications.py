"""Tests for the scheduler ↔ Discord notification integration (Step 8).

The pipeline is always a fake execution service and the notifier is always
a fake — no GLM, no HTTP, no scheduler threads left running. The core
invariant under test: ONE execution produces exactly ONE notification
(success XOR failure), and a notification problem can NEVER change the
recorded research status.
"""

import asyncio

import pytest

from app.schemas.schedule import ScheduleCreateRequest
from app.services.discord_service import DiscordNotificationService
from app.services.research_execution_service import (
    CitationSummary,
    ExecutionResult,
    ResearchExecutionError,
    ResearchExecutionService,
)
from app.services.scheduler_service import SchedulerService

QUESTION = "What are the latest developments in Retrieval-Augmented Generation?"
REPORT_ID = "notify-fake-report-5150beef"
SUMMARY = "RAG berkembang ke arah kurasi sumber dan evaluasi grounding."
FINDINGS = "- Kurasi sumber menaikkan kualitas jawaban.\n- Evaluasi grounding jadi standar."
CITATIONS = (
    CitationSummary(
        evidence_id="E1",
        title="RAG survey",
        url="https://example.com/rag-survey",
        domain="example.com",
    ),
)


class FakeExecutionService(ResearchExecutionService):
    """Scripted pipeline that succeeds or fails on demand."""

    def __init__(self, error: ResearchExecutionError | None = None) -> None:
        self.error = error

    async def run(self, question: str, top_k: int = 5) -> ExecutionResult:
        if self.error is not None:
            raise self.error
        return ExecutionResult(
            report_id=REPORT_ID,
            synthesis_status="success",
            sources_found=7,
            indexed_sources=7,
            failed_sources=0,
            total_chunks=21,
            evidence_count=18,
            summary=SUMMARY,
            findings=FINDINGS,
            citations=CITATIONS,
        )


class FakeNotificationService(DiscordNotificationService):
    """Records calls; returns configurable outcomes, can even explode."""

    def __init__(
        self,
        outcome: str = "sent",
        raise_error: Exception | None = None,
    ) -> None:
        # Not calling super().__init__: no config/transport is ever read.
        self.outcome = outcome
        self.raise_error = raise_error
        self.success_calls: list[dict] = []
        self.failure_calls: list[dict] = []

    @property
    def is_configured(self) -> bool:
        return self.outcome != "not_configured"

    async def send_success(self, **kwargs) -> str:
        self.success_calls.append(kwargs)
        if self.raise_error is not None:
            raise self.raise_error
        return self.outcome

    async def send_failure(self, **kwargs) -> str:
        self.failure_calls.append(kwargs)
        if self.raise_error is not None:
            raise self.raise_error
        return self.outcome


def make_request() -> ScheduleCreateRequest:
    return ScheduleCreateRequest(
        question=QUESTION,
        schedule_type="interval",
        interval_minutes=30,
    )


def make_service(
    execution: ResearchExecutionService | None = None,
    notifier: FakeNotificationService | None = None,
) -> SchedulerService:
    svc = SchedulerService(
        execution_service=execution or FakeExecutionService(),
        notification_service=notifier,
    )
    return svc


@pytest.fixture
def notifier() -> FakeNotificationService:
    return FakeNotificationService()


# --------------------------------------------------------- success executions


def test_success_sends_exactly_one_success_notification(
    notifier: FakeNotificationService,
) -> None:
    svc = make_service(notifier=notifier)
    try:
        job = svc.create_schedule(make_request())
        asyncio.run(svc.run_now(job.id))

        assert job.last_status == "success"
        assert job.notification_status == "sent"
        assert len(notifier.success_calls) == 1
        assert notifier.failure_calls == []  # never both for one execution
        call = notifier.success_calls[0]
        assert call["question"] == QUESTION
        assert call["trigger"] == "Manual"
        assert call["report_id"] == REPORT_ID
        assert call["sources_found"] == 7
        assert call["timezone_name"] == "Asia/Jakarta"
        assert call["completed_at"].tzinfo is not None  # aware UTC timestamp
    finally:
        svc.shutdown()


def test_success_notification_carries_the_executions_research_result(
    notifier: FakeNotificationService,
) -> None:
    # The embed must deliver THIS execution's own research result —
    # summary, findings, evidence count, and validated citations are
    # passed straight from the ExecutionResult the pipeline returned
    # (no re-research, no second pipeline run for Discord's benefit).
    svc = make_service(notifier=notifier)
    try:
        job = svc.create_schedule(make_request())
        asyncio.run(svc.run_now(job.id))

        call = notifier.success_calls[0]
        assert call["summary"] == SUMMARY
        assert call["findings"] == FINDINGS
        assert call["evidence_count"] == 18
        assert call["citations"] == CITATIONS
        assert call["sources_found"] == 7
    finally:
        svc.shutdown()


def test_scheduler_runs_the_pipeline_exactly_once_per_execution(
    notifier: FakeNotificationService,
) -> None:
    # One execution = one pipeline run = one Discord delivery carrying
    # that run's result. The notifier must never trigger more research.
    runs: list[str] = []

    class CountingExecution(FakeExecutionService):
        async def run(self, question: str, top_k: int = 5) -> ExecutionResult:
            runs.append(question)
            return await super().run(question, top_k)

    svc = make_service(execution=CountingExecution(), notifier=notifier)
    try:
        job = svc.create_schedule(make_request())
        asyncio.run(svc.run_now(job.id))
        assert runs == [QUESTION]
        assert len(notifier.success_calls) == 1
        assert notifier.failure_calls == []
    finally:
        svc.shutdown()


def test_failed_execution_sends_exactly_one_failure_notification() -> None:
    failing = FakeExecutionService(
        error=ResearchExecutionError("Synthesis failed: timeout")
    )
    notifier = FakeNotificationService()
    svc = make_service(execution=failing, notifier=notifier)
    try:
        job = svc.create_schedule(make_request())
        asyncio.run(svc.run_now(job.id))

        assert job.last_status == "failed"
        assert job.notification_status == "sent"
        assert len(notifier.failure_calls) == 1
        assert notifier.success_calls == []
        call = notifier.failure_calls[0]
        # Client-safe error only — this is the scheduler's sanitized text.
        assert call["error"] == "Synthesis failed: timeout"
        assert call["trigger"] == "Manual"
        assert call["timezone_name"] == "Asia/Jakarta"
    finally:
        svc.shutdown()


def test_unexpected_research_error_notified_with_generic_message() -> None:
    class Exploding(FakeExecutionService):
        async def run(self, question: str, top_k: int = 5) -> ExecutionResult:
            raise RuntimeError("traceback with GLM_API_KEY=secret")

    notifier = FakeNotificationService()
    svc = make_service(execution=Exploding(), notifier=notifier)
    try:
        job = svc.create_schedule(make_request())
        asyncio.run(svc.run_now(job.id))

        assert job.last_status == "failed"
        assert notifier.failure_calls[0]["error"] == (
            "Error tak terduga saat riset terjadwal."
        )
        assert "GLM_API_KEY" not in notifier.failure_calls[0]["error"]
    finally:
        svc.shutdown()


# ------------------------------------------------- notification failure paths


def test_discord_failure_does_not_fail_the_research() -> None:
    notifier = FakeNotificationService(outcome="failed")
    svc = make_service(notifier=notifier)
    try:
        job = svc.create_schedule(make_request())
        asyncio.run(svc.run_now(job.id))

        # Research succeeded; only the notification is marked failed.
        assert job.last_status == "success"
        assert job.last_report_id == REPORT_ID
        assert job.notification_status == "failed"
        assert len(notifier.success_calls) == 1
    finally:
        svc.shutdown()


def test_not_configured_notification_keeps_research_working() -> None:
    notifier = FakeNotificationService(outcome="not_configured")
    svc = make_service(notifier=notifier)
    try:
        job = svc.create_schedule(make_request())
        asyncio.run(svc.run_now(job.id))

        assert job.last_status == "success"
        assert job.notification_status == "not_configured"
        # The scheduler short-circuits: send_success is never even called.
        assert notifier.success_calls == []
        assert notifier.failure_calls == []
    finally:
        svc.shutdown()


def test_broken_notifier_cannot_corrupt_the_job_record() -> None:
    notifier = FakeNotificationService(raise_error=RuntimeError("boom"))
    svc = make_service(notifier=notifier)
    try:
        job = svc.create_schedule(make_request())
        asyncio.run(svc.run_now(job.id))  # must not raise

        assert job.last_status == "success"  # research outcome untouched
        assert job.notification_status == "failed"  # defensive fallback
        assert job.last_report_id == REPORT_ID
    finally:
        svc.shutdown()


# ------------------------------------------------------------- trigger types


def test_manual_run_reports_manual_trigger(notifier: FakeNotificationService) -> None:
    svc = make_service(notifier=notifier)
    try:
        job = svc.create_schedule(make_request())
        asyncio.run(svc.run_now(job.id))
        assert notifier.success_calls[0]["trigger"] == "Manual"
    finally:
        svc.shutdown()


def test_scheduled_fire_reports_scheduled_trigger(
    notifier: FakeNotificationService,
) -> None:
    svc = make_service(notifier=notifier)
    try:
        job = svc.create_schedule(make_request())
        asyncio.run(svc._apscheduler_entry(job.id))
        assert notifier.success_calls[0]["trigger"] == "Scheduled"
    finally:
        svc.shutdown()


def test_notification_happens_after_the_result_is_known(
    notifier: FakeNotificationService,
) -> None:
    # The notifier must only learn the outcome AFTER the pipeline finished:
    # record the job status at notification time and assert it is final.
    observed: list[str] = []

    class ObservingNotifier(FakeNotificationService):
        async def send_success(self, **kwargs) -> str:
            observed.append("success-path")
            return await super().send_success(**kwargs)

    class ObservingExecution(FakeExecutionService):
        async def run(self, question: str, top_k: int = 5) -> ExecutionResult:
            result = await super().run(question, top_k)
            observed.append("pipeline-done")
            return result

    observing = ObservingNotifier()
    svc = make_service(execution=ObservingExecution(), notifier=observing)
    try:
        job = svc.create_schedule(make_request())
        asyncio.run(svc.run_now(job.id))
        assert observed == ["pipeline-done", "success-path"]
    finally:
        svc.shutdown()


def test_no_duplicate_notifications_for_one_execution(
    notifier: FakeNotificationService,
) -> None:
    # One run (even with several internal stages) → exactly one message.
    svc = make_service(notifier=notifier)
    try:
        job = svc.create_schedule(make_request())
        asyncio.run(svc.run_now(job.id))
        total = len(notifier.success_calls) + len(notifier.failure_calls)
        assert total == 1
    finally:
        svc.shutdown()


def test_new_schedule_defaults_to_not_configured_status(
    notifier: FakeNotificationService,
) -> None:
    svc = make_service(notifier=notifier)
    try:
        job = svc.create_schedule(make_request())
        assert job.notification_status == "not_configured"  # before any run
        assert job.last_status == "never_run"
    finally:
        svc.shutdown()


def test_notification_status_updates_across_runs() -> None:
    notifier = FakeNotificationService()
    svc = make_service(notifier=notifier)
    try:
        job = svc.create_schedule(make_request())
        asyncio.run(svc.run_now(job.id))
        assert job.notification_status == "sent"
        # Next run the webhook breaks → status flips to failed, research OK.
        notifier.outcome = "failed"
        asyncio.run(svc.run_now(job.id))
        assert job.last_status == "success"
        assert job.notification_status == "failed"
    finally:
        svc.shutdown()

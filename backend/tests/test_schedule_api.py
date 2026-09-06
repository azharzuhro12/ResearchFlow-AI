"""API tests for the scheduler endpoints (Step 7).

The scheduler service is replaced with a fresh instance wired to a fake
execution service (dependency override), so no scheduler loop starts, no
GLM/web/embedding/Chroma is touched, and each test cleans up fully.
"""

import time

import pytest
from fastapi.testclient import TestClient

from app.api.schedules import get_scheduler_service
from app.main import app
from app.services.research_execution_service import (
    ExecutionResult,
    ResearchExecutionError,
    ResearchExecutionService,
)
from app.services.scheduler_service import SchedulerService

client = TestClient(app)

QUESTION = "What are the latest developments in Retrieval-Augmented Generation?"
REPORT_ID = "api-fake-report-99aa88bb"

VALID_CRON = {
    "question": QUESTION,
    "schedule_type": "cron",
    "cron_expression": "0 8 * * *",
    "timezone": "Asia/Jakarta",
}
VALID_INTERVAL = {
    "question": "How is the autonomous agents ecosystem evolving?",
    "schedule_type": "interval",
    "interval_minutes": 30,
}


class FakeExecutionService(ResearchExecutionService):
    """Instant scripted pipeline for manual-run tests."""

    def __init__(self, error: ResearchExecutionError | None = None) -> None:
        self.error = error
        self.questions: list[str] = []

    async def run(self, question: str, top_k: int = 5) -> ExecutionResult:
        self.questions.append(question)
        if self.error is not None:
            raise self.error
        return ExecutionResult(
            report_id=REPORT_ID,
            synthesis_status="success",
            sources_found=3,
            indexed_sources=3,
            failed_sources=0,
            total_chunks=11,
        )


@pytest.fixture
def fake_service() -> SchedulerService:
    service = SchedulerService(execution_service=FakeExecutionService())
    app.dependency_overrides[get_scheduler_service] = lambda: service
    yield service
    app.dependency_overrides.pop(get_scheduler_service, None)
    service.shutdown()


def wait_for_status(service: SchedulerService, schedule_id: str, timeout: float = 5.0):
    """Wait until a manual run's background task has finished (it is
    instantaneous with the fake; polling keeps the test deterministic)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = service.get_schedule(schedule_id)
        if job.last_status != "running":
            return job
        time.sleep(0.01)
    return service.get_schedule(schedule_id)


# ------------------------------------------------------------------- create


def test_create_cron_schedule(fake_service: SchedulerService) -> None:
    response = client.post("/api/research/schedules", json=VALID_CRON)
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    schedule = payload["schedule"]
    assert schedule["id"]
    assert schedule["question"] == QUESTION
    assert schedule["schedule_type"] == "cron"
    assert schedule["cron_expression"] == "0 8 * * *"
    assert schedule["schedule_expression"] == "0 8 * * *"
    assert schedule["timezone"] == "Asia/Jakarta"
    assert schedule["enabled"] is True
    assert schedule["last_status"] == "never_run"
    assert schedule["next_run_at"]  # aware ISO timestamp
    assert schedule["last_run_at"] is None
    assert schedule["last_report_id"] is None


def test_create_interval_schedule_defaults_timezone(fake_service: SchedulerService) -> None:
    response = client.post("/api/research/schedules", json=VALID_INTERVAL)
    assert response.status_code == 200
    schedule = response.json()["schedule"]
    assert schedule["timezone"] == "Asia/Jakarta"  # documented default
    assert schedule["interval_minutes"] == 30
    assert schedule["schedule_expression"] == "every 30 minutes"


@pytest.mark.parametrize(
    "body",
    [
        # Missing the definition for the chosen type:
        {"question": QUESTION, "schedule_type": "interval"},
        {"question": QUESTION, "schedule_type": "cron"},
        # Interval bounds:
        {"question": QUESTION, "schedule_type": "interval", "interval_minutes": 4},
        {"question": QUESTION, "schedule_type": "interval", "interval_minutes": 0},
        {"question": QUESTION, "schedule_type": "interval", "interval_minutes": -5},
        {"question": QUESTION, "schedule_type": "interval", "interval_minutes": 10**9},
        # Cron expression problems:
        {"question": QUESTION, "schedule_type": "cron", "cron_expression": "0 8 * * * *"},
        {"question": QUESTION, "schedule_type": "cron", "cron_expression": "garbage"},
        {"question": QUESTION, "schedule_type": "cron", "cron_expression": "* * * * *"},
        # Definition fields crossing types:
        {
            "question": QUESTION,
            "schedule_type": "interval",
            "interval_minutes": 30,
            "cron_expression": "0 8 * * *",
        },
        {
            "question": QUESTION,
            "schedule_type": "cron",
            "cron_expression": "0 8 * * *",
            "interval_minutes": 30,
        },
        # Unknown schedule type / bad question:
        {"question": QUESTION, "schedule_type": "hourly", "interval_minutes": 60},
        {"question": "short", "schedule_type": "interval", "interval_minutes": 60},
        {"question": "   ", "schedule_type": "interval", "interval_minutes": 60},
    ],
)
def test_create_validation_errors_return_422(
    fake_service: SchedulerService, body: dict
) -> None:
    response = client.post("/api/research/schedules", json=body)
    assert response.status_code == 422
    assert fake_service.list_schedules() == []  # nothing registered


def test_create_invalid_timezone_returns_422(fake_service: SchedulerService) -> None:
    body = {**VALID_CRON, "timezone": "Mars/Olympus"}
    response = client.post("/api/research/schedules", json=body)
    assert response.status_code == 422
    assert "timezone" in response.json()["detail"].lower()


# --------------------------------------------------------------------- list


def test_list_schedules(fake_service: SchedulerService) -> None:
    assert client.get("/api/research/schedules").json()["total"] == 0
    first = client.post("/api/research/schedules", json=VALID_CRON).json()["schedule"]
    second = client.post("/api/research/schedules", json=VALID_INTERVAL).json()["schedule"]

    response = client.get("/api/research/schedules")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["total"] == 2
    assert [s["id"] for s in payload["schedules"]] == [first["id"], second["id"]]


def test_get_one_schedule(fake_service: SchedulerService) -> None:
    created = client.post("/api/research/schedules", json=VALID_CRON).json()["schedule"]
    response = client.get(f"/api/research/schedules/{created['id']}")
    assert response.status_code == 200
    assert response.json()["schedule"]["id"] == created["id"]


def test_get_unknown_schedule_returns_404(fake_service: SchedulerService) -> None:
    assert client.get("/api/research/schedules/missing").status_code == 404


# ------------------------------------------------------------ pause / resume


def test_pause_and_resume(fake_service: SchedulerService) -> None:
    created = client.post("/api/research/schedules", json=VALID_CRON).json()["schedule"]
    schedule_id = created["id"]

    paused = client.post(f"/api/research/schedules/{schedule_id}/pause")
    assert paused.status_code == 200
    body = paused.json()["schedule"]
    assert body["enabled"] is False
    assert body["next_run_at"] is None  # paused → no upcoming run

    resumed = client.post(f"/api/research/schedules/{schedule_id}/resume")
    assert resumed.status_code == 200
    body = resumed.json()["schedule"]
    assert body["enabled"] is True
    assert body["next_run_at"] is not None


def test_pause_resume_unknown_return_404(fake_service: SchedulerService) -> None:
    assert client.post("/api/research/schedules/missing/pause").status_code == 404
    assert client.post("/api/research/schedules/missing/resume").status_code == 404


# ---------------------------------------------------------------- manual run


def test_manual_run_executes_and_records_success(fake_service: SchedulerService) -> None:
    created = client.post("/api/research/schedules", json=VALID_INTERVAL).json()["schedule"]
    schedule_id = created["id"]

    response = client.post(f"/api/research/schedules/{schedule_id}/run")
    assert response.status_code == 202
    payload = response.json()
    assert payload["status"] == "accepted"
    assert payload["schedule_id"] == schedule_id
    assert payload["message"]

    job = wait_for_status(fake_service, schedule_id)
    assert job.last_status == "success"
    assert job.last_report_id == REPORT_ID
    fake = fake_service._execution
    assert isinstance(fake, FakeExecutionService)
    assert fake.questions == [VALID_INTERVAL["question"]]


def test_manual_run_does_not_modify_the_schedule(fake_service: SchedulerService) -> None:
    created = client.post("/api/research/schedules", json=VALID_CRON).json()["schedule"]
    schedule_id = created["id"]

    response = client.post(f"/api/research/schedules/{schedule_id}/run")
    assert response.status_code == 202

    after = client.get(f"/api/research/schedules/{schedule_id}").json()["schedule"]
    assert after["enabled"] is True  # still active
    assert after["next_run_at"] == created["next_run_at"]  # trigger untouched
    assert after["last_report_id"] == REPORT_ID  # but the run did happen


def test_manual_run_records_failure(fake_service: SchedulerService) -> None:
    created = client.post("/api/research/schedules", json=VALID_INTERVAL).json()["schedule"]
    schedule_id = created["id"]
    fake_service._execution.error = ResearchExecutionError("Synthesis failed: timeout")

    response = client.post(f"/api/research/schedules/{schedule_id}/run")
    assert response.status_code == 202  # accepted; outcome recorded async

    job = wait_for_status(fake_service, schedule_id)
    assert job.last_status == "failed"
    assert job.last_error == "Synthesis failed: timeout"

    # The schedule survives a failed execution and stays listed.
    listing = client.get("/api/research/schedules").json()
    assert listing["total"] == 1


def test_manual_run_unknown_returns_404(fake_service: SchedulerService) -> None:
    assert client.post("/api/research/schedules/missing/run").status_code == 404


# -------------------------------------------------------------------- delete


def test_delete_schedule(fake_service: SchedulerService) -> None:
    created = client.post("/api/research/schedules", json=VALID_CRON).json()["schedule"]
    schedule_id = created["id"]

    response = client.delete(f"/api/research/schedules/{schedule_id}")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["schedule_id"] == schedule_id
    assert payload["deleted"] is True

    assert client.get(f"/api/research/schedules/{schedule_id}").status_code == 404
    assert client.get("/api/research/schedules").json()["total"] == 0


def test_delete_unknown_returns_404(fake_service: SchedulerService) -> None:
    assert client.delete("/api/research/schedules/missing").status_code == 404


# ----------------------------------------------------------------- lifecycle


def test_lifespan_starts_and_stops_scheduler_cleanly() -> None:
    """A real (unoverridden) application lifecycle starts APScheduler on
    startup and leaves no scheduler running after shutdown."""
    with TestClient(app) as with_lifespan:
        assert with_lifespan.get("/health").status_code == 200
        from app.api import schedules as schedules_api

        service = schedules_api.get_scheduler_service()
        assert service.started is True
    # After the context exits, the singleton is stopped and cleared.
    from app.api import schedules as schedules_api

    assert schedules_api._scheduler_service is None

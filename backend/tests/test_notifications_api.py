"""API tests for notifications (Step 8).

Covers the read-only status endpoint and the security contract: the
webhook URL (even a fake one injected via config) must never surface in
any API response. The manual-run test keeps the webhook unconfigured so
no test ever performs a real HTTP request.
"""

import time

import pytest
from fastapi.testclient import TestClient

from app.api.schedules import get_scheduler_service
from app.core import config
from app.main import app
from app.schemas.schedule import ScheduleCreateRequest
from app.services.research_execution_service import (
    ExecutionResult,
    ResearchExecutionService,
)
from app.services.scheduler_service import SchedulerService

client = TestClient(app)

QUESTION = "What are the latest developments in Retrieval-Augmented Generation?"
FAKE_WEBHOOK = "https://discord.com/api/webhooks/999999/TESTTOKEN-do-not-send"
VALID_INTERVAL = {
    "question": QUESTION,
    "schedule_type": "interval",
    "interval_minutes": 30,
}


class FakeExecutionService(ResearchExecutionService):
    """Instant scripted pipeline for the manual-run notification test."""

    def __init__(self) -> None:
        self.questions: list[str] = []

    async def run(self, question: str, top_k: int = 5) -> ExecutionResult:
        self.questions.append(question)
        return ExecutionResult(
            report_id="notify-api-report-77cc0011",
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


# ------------------------------------------------------------- status endpoint


def test_notification_status_unconfigured_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Force "no webhook" regardless of the developer's real backend/.env —
    # the ambient value must never leak into this test.
    monkeypatch.setattr(config, "DISCORD_WEBHOOK_URL", "")
    response = client.get("/api/research/notifications/status")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["discord"] == {"configured": False}


def test_notification_status_reports_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "DISCORD_WEBHOOK_URL", FAKE_WEBHOOK)
    response = client.get("/api/research/notifications/status")
    assert response.status_code == 200
    assert response.json()["discord"] == {"configured": True}


@pytest.mark.parametrize(
    "value",
    [
        "your_discord_webhook_url_here",  # placeholder
        "",  # empty
        "http://localhost/api/webhooks/1/a",  # HTTP → rejected
        "https://evil.com/api/webhooks/1/a",  # foreign host → rejected
        "not a url",  # malformed
    ],
)
def test_notification_status_fails_closed_on_bad_urls(
    value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "DISCORD_WEBHOOK_URL", value)
    response = client.get("/api/research/notifications/status")
    assert response.status_code == 200
    assert response.json()["discord"] == {"configured": False}


def test_status_endpoint_exists_in_openapi() -> None:
    paths = app.openapi()["paths"]
    assert "/api/research/notifications/status" in paths


# ------------------------------------------------- schedule response extension


def test_schedule_response_includes_notification_status(
    fake_service: SchedulerService,
) -> None:
    response = client.post("/api/research/schedules", json=VALID_INTERVAL)
    assert response.status_code == 200
    schedule = response.json()["schedule"]
    assert schedule["notification_status"] == "not_configured"  # before any run
    assert schedule["last_status"] == "never_run"


def test_manual_run_records_not_configured_notification(
    fake_service: SchedulerService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # No webhook configured (forced below, immune to the real backend/.env)
    # → the pipeline still succeeds and the notification outcome is honestly
    # "not_configured" (no HTTP made).
    monkeypatch.setattr(config, "DISCORD_WEBHOOK_URL", "")
    created = client.post("/api/research/schedules", json=VALID_INTERVAL).json()
    schedule_id = created["schedule"]["id"]

    run = client.post(f"/api/research/schedules/{schedule_id}/run")
    assert run.status_code == 202

    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        job = fake_service.get_schedule(schedule_id)
        if job.last_status != "running":
            break
        time.sleep(0.01)

    assert job.last_status == "success"
    assert job.notification_status == "not_configured"
    # …and the API surfaces it:
    fetched = client.get(f"/api/research/schedules/{schedule_id}").json()
    assert fetched["schedule"]["notification_status"] == "not_configured"
    assert fetched["schedule"]["last_status"] == "success"


# ------------------------------------------------------------- no URL leakage


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/api/research/notifications/status"),
        ("GET", "/api/research/schedules"),
    ],
)
def test_webhook_url_never_appears_in_api_responses(
    method: str,
    path: str,
    fake_service: SchedulerService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Even with a (fake) webhook configured, responses must not contain it.
    monkeypatch.setattr(config, "DISCORD_WEBHOOK_URL", FAKE_WEBHOOK)
    fake_service.create_schedule(
        ScheduleCreateRequest(
            question=QUESTION,
            schedule_type="interval",
            interval_minutes=30,
        )
    )
    response = client.request(method, path)
    assert response.status_code == 200
    blob = response.text
    assert FAKE_WEBHOOK not in blob
    assert "/api/webhooks/" not in blob
    assert "TESTTOKEN" not in blob

"""Tests for DiscordNotificationService (Step 8).

All HTTP goes through httpx.MockTransport — no real Discord request is
ever made, and no Discord credential is required. These tests verify URL
validation (SSRF guard), placeholder handling, payload structure/limits,
retry bounds, and that the webhook URL never leaks into payloads or logs.
"""

import asyncio
import json
import logging
from datetime import UTC, datetime

import httpx
import pytest

from app.services.discord_service import (
    COLOR_FAILURE,
    COLOR_SUCCESS,
    FAILURE_TITLE,
    MAX_ERROR_LENGTH,
    MAX_FIELD_LENGTH,
    REQUEST_TIMEOUT_SECONDS,
    RETRY_DELAY_SECONDS,
    STATUS_FAILED,
    STATUS_NOT_CONFIGURED,
    STATUS_SENT,
    SUCCESS_TITLE,
    DiscordNotificationService,
    is_valid_discord_webhook_url,
)

VALID_WEBHOOK = "https://discord.com/api/webhooks/1234567890/abcDEF123-_"

SUCCESS_KWARGS = {
    "question": "What are the latest developments in Retrieval-Augmented Generation?",
    "trigger": "Manual",
    "report_id": "latest-rag-developments_ab12cd34",
    "sources_found": 12,
    "completed_at": datetime(2026, 9, 6, 1, 0, tzinfo=UTC),
    "timezone_name": "Asia/Jakarta",
}

FAILURE_KWARGS = {
    "question": "What are the latest developments in Retrieval-Augmented Generation?",
    "trigger": "Scheduled",
    "error": "Synthesis failed: the AI could not answer.",
    "occurred_at": datetime(2026, 9, 6, 1, 0, tzinfo=UTC),
    "timezone_name": "Asia/Jakarta",
}


def make_transport(
    status: int = 204,
    requests: list[httpx.Request] | None = None,
    payloads: list[dict] | None = None,
    error: Exception | None = None,
) -> httpx.MockTransport:
    """MockTransport that records requests (and optionally raises)."""

    def handler(request: httpx.Request) -> httpx.Response:
        if requests is not None:
            requests.append(request)
        if payloads is not None:
            payloads.append(json.loads(request.content.decode("utf-8")))
        if error is not None:
            raise error
        return httpx.Response(status, request=request)

    return httpx.MockTransport(handler)


def make_service(
    webhook_url: str | None = VALID_WEBHOOK,
    **transport_kwargs,
) -> DiscordNotificationService:
    return DiscordNotificationService(
        webhook_url=webhook_url,
        transport=make_transport(**transport_kwargs),
        retry_delay=0.0,  # tests must not actually sleep between retries
    )


# ------------------------------------------------------------- URL validation


@pytest.mark.parametrize(
    "url",
    [
        "https://discord.com/api/webhooks/1234567890/abcDEF123-_",
        "https://discordapp.com/api/webhooks/1/a",
        "https://DISCORD.COM/api/webhooks/1/a",  # host is case-insensitive
        "https://discord.com:443/api/webhooks/1/a",  # standard HTTPS port
    ],
)
def test_valid_discord_webhook_urls_accepted(url: str) -> None:
    assert is_valid_discord_webhook_url(url) is True


@pytest.mark.parametrize(
    "url",
    [
        # Plain HTTP is rejected:
        "http://discord.com/api/webhooks/1/a",
        "http://localhost/api/webhooks/1/a",
        # Localhost / private IPs on any scheme:
        "https://localhost/api/webhooks/1/a",
        "https://127.0.0.1/api/webhooks/1/a",
        "https://192.168.1.10/api/webhooks/1/a",
        "https://10.0.0.2/api/webhooks/1/a",
        "https://172.16.0.5/api/webhooks/1/a",
        # Arbitrary / lookalike domains:
        "https://evil.com/api/webhooks/1/a",
        "https://discord.com.evil.com/api/webhooks/1/a",
        "https://evil-discord.com/api/webhooks/1/a",
        # Non-webhook path on a real Discord host:
        "https://discord.com/channels/1/2",
        # Explicit non-standard port and embedded credentials:
        "https://discord.com:8443/api/webhooks/1/a",
        "https://user:pass@discord.com/api/webhooks/1/a",
        # Malformed / junk:
        "not a url",
        "ftp://discord.com/api/webhooks/1/a",
        "",
        "https://",
        "https://discord.com/api/webhooks/1/a/../../admin",
    ],
)
def test_invalid_discord_webhook_urls_rejected(url: str) -> None:
    assert is_valid_discord_webhook_url(url) is False


# ------------------------------------------------------------ configuration


@pytest.mark.parametrize(
    ("webhook_url", "expected"),
    [
        (VALID_WEBHOOK, True),  # configured
        ("", False),  # unconfigured
        ("your_discord_webhook_url_here", False),  # placeholder
        ("http://localhost/api/webhooks/1/a", False),  # malformed → fail closed
        ("https://evil.com/api/webhooks/1/a", False),  # foreign host → fail closed
    ],
)
def test_is_configured_matrix(webhook_url: str, expected: bool) -> None:
    assert DiscordNotificationService(webhook_url=webhook_url).is_configured is expected


def test_webhook_url_defaults_to_backend_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core import config

    monkeypatch.setattr(config, "DISCORD_WEBHOOK_URL", VALID_WEBHOOK)
    assert DiscordNotificationService().is_configured is True
    monkeypatch.setattr(config, "DISCORD_WEBHOOK_URL", "your_discord_webhook_url_here")
    assert DiscordNotificationService().is_configured is False
    monkeypatch.setattr(config, "DISCORD_WEBHOOK_URL", "")
    assert DiscordNotificationService().is_configured is False


def test_unconfigured_send_makes_no_http_request() -> None:
    requests: list[httpx.Request] = []
    service = make_service(webhook_url="", requests=requests)

    result = asyncio.run(service.send_success(**SUCCESS_KWARGS))

    assert result == STATUS_NOT_CONFIGURED
    assert requests == []  # nothing was sent anywhere


# ------------------------------------------------------------------- payload


def field_map(payload: dict) -> dict[str, str]:
    return {f["name"]: f["value"] for f in payload["embeds"][0]["fields"]}


def test_success_payload_structure() -> None:
    payloads: list[dict] = []
    service = make_service(payloads=payloads)

    result = asyncio.run(service.send_success(**SUCCESS_KWARGS))

    assert result == STATUS_SENT
    assert len(payloads) == 1
    payload = payloads[0]
    embed = payload["embeds"][0]
    assert embed["title"] == SUCCESS_TITLE
    assert embed["color"] == COLOR_SUCCESS
    fields = field_map(payload)
    assert fields["Question"] == SUCCESS_KWARGS["question"]
    assert fields["Status"] == "SUCCESS"
    assert fields["Trigger"] == "Manual"
    assert fields["Sources"] == "12"
    assert "researchflow_latest-rag-developments_ab12cd34.md" in fields["Report"]
    assert "researchflow_latest-rag-developments_ab12cd34.pdf" in fields["Report"]
    # UTC 01:00 == Jakarta 08:00.
    assert fields["Completed"] == "2026-09-06 08:00 Asia/Jakarta"


def test_failure_payload_structure() -> None:
    payloads: list[dict] = []
    service = make_service(payloads=payloads)

    result = asyncio.run(service.send_failure(**FAILURE_KWARGS))

    assert result == STATUS_SENT
    payload = payloads[0]
    embed = payload["embeds"][0]
    assert embed["title"] == FAILURE_TITLE
    assert embed["color"] == COLOR_FAILURE
    fields = field_map(payload)
    assert fields["Question"] == FAILURE_KWARGS["question"]
    assert fields["Status"] == "FAILED"
    assert fields["Trigger"] == "Scheduled"
    assert fields["Error"] == "Synthesis failed: the AI could not answer."
    assert fields["Time"] == "2026-09-06 08:00 Asia/Jakarta"


def test_payloads_suppress_mentions() -> None:
    # Payload injection guard: even if a research question contains
    # @everyone / @here, the payload forbids Discord from pinging anyone.
    payloads: list[dict] = []
    service = make_service(payloads=payloads)

    asyncio.run(
        service.send_success(**{**SUCCESS_KWARGS, "question": "@everyone @here ping"})
    )

    assert payloads[0]["allowed_mentions"] == {"parse": []}


@pytest.mark.parametrize("which", ["success", "failure"])
def test_payloads_never_leak_secrets(which: str) -> None:
    payloads: list[dict] = []
    service = make_service(payloads=payloads)

    if which == "success":
        asyncio.run(service.send_success(**SUCCESS_KWARGS))
    else:
        asyncio.run(service.send_failure(**FAILURE_KWARGS))

    blob = json.dumps(payloads)
    assert VALID_WEBHOOK not in blob  # no webhook URL
    assert "/api/webhooks/" not in blob
    assert "GLM_API_KEY" not in blob  # no API keys
    assert "Authorization" not in blob  # no headers
    assert "Traceback" not in blob  # no stack traces


def test_long_question_is_capped() -> None:
    payloads: list[dict] = []
    service = make_service(payloads=payloads)

    asyncio.run(service.send_success(**{**SUCCESS_KWARGS, "question": "x" * 3000}))

    question_field = field_map(payloads[0])["Question"]
    assert len(question_field) <= MAX_FIELD_LENGTH


def test_long_error_is_capped() -> None:
    payloads: list[dict] = []
    service = make_service(payloads=payloads)

    asyncio.run(service.send_failure(**{**FAILURE_KWARGS, "error": "e" * 5000}))

    error_field = field_map(payloads[0])["Error"]
    assert len(error_field) <= MAX_ERROR_LENGTH


def test_missing_report_id_renders_placeholder() -> None:
    payloads: list[dict] = []
    service = make_service(payloads=payloads)

    asyncio.run(service.send_success(**{**SUCCESS_KWARGS, "report_id": ""}))

    assert field_map(payloads[0])["Report"] == "—"


@pytest.mark.parametrize(
    ("timezone_name", "expected"),
    [
        ("Asia/Jakarta", "2026-09-06 08:00 Asia/Jakarta"),
        ("UTC", "2026-09-06 01:00 UTC"),
        ("Not/A/Zone", "2026-09-06 01:00 UTC"),  # unknown tz falls back to UTC
    ],
)
def test_timestamp_rendering(timezone_name: str, expected: str) -> None:
    payloads: list[dict] = []
    service = make_service(payloads=payloads)

    asyncio.run(
        service.send_success(**{**SUCCESS_KWARGS, "timezone_name": timezone_name})
    )

    assert field_map(payloads[0])["Completed"] == expected


# ---------------------------------------------------------------- HTTP layer


@pytest.mark.parametrize("status", [204, 200])
def test_http_success_statuses(status: int) -> None:
    service = make_service(status=status)
    assert asyncio.run(service.send_success(**SUCCESS_KWARGS)) == STATUS_SENT


@pytest.mark.parametrize("status", [400, 404, 429])
def test_http_client_errors_fail_without_retry(status: int) -> None:
    # 4xx is permanent (bad or rate-limited webhook) — one request only.
    requests: list[httpx.Request] = []
    service = make_service(status=status, requests=requests)

    assert asyncio.run(service.send_success(**SUCCESS_KWARGS)) == STATUS_FAILED
    assert len(requests) == 1


def test_http_500_retries_once_then_fails() -> None:
    requests: list[httpx.Request] = []
    service = make_service(status=500, requests=requests)

    assert asyncio.run(service.send_failure(**FAILURE_KWARGS)) == STATUS_FAILED
    assert len(requests) == 2  # initial request + exactly one bounded retry


def test_timeout_retries_once_then_fails() -> None:
    requests: list[httpx.Request] = []
    service = make_service(
        requests=requests, error=httpx.TimeoutException("timed out")
    )

    assert asyncio.run(service.send_success(**SUCCESS_KWARGS)) == STATUS_FAILED
    assert len(requests) == 2


def test_connection_failure_retries_once_then_fails() -> None:
    requests: list[httpx.Request] = []
    service = make_service(requests=requests, error=httpx.ConnectError("no route"))

    assert asyncio.run(service.send_success(**SUCCESS_KWARGS)) == STATUS_FAILED
    assert len(requests) == 2


def test_transient_failure_then_success_on_retry() -> None:
    # First attempt 500, second attempt 204 → delivered on the retry.
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(500 if len(requests) == 1 else 204, request=request)

    service = DiscordNotificationService(
        webhook_url=VALID_WEBHOOK,
        transport=httpx.MockTransport(handler),
        retry_delay=0.0,
    )

    assert asyncio.run(service.send_success(**SUCCESS_KWARGS)) == STATUS_SENT
    assert len(requests) == 2


def test_retry_bounds_and_timeout_are_small() -> None:
    # The spec's resource-safety contract: fast timeout, bounded retries.
    from app.services import discord_service

    assert REQUEST_TIMEOUT_SECONDS <= 10.0
    assert discord_service.MAX_ATTEMPTS == 2  # initial + max ONE retry
    assert RETRY_DELAY_SECONDS <= 5.0


def test_webhook_url_never_appears_in_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    requests: list[httpx.Request] = []
    service = make_service(status=500, requests=requests)

    with caplog.at_level(logging.DEBUG, logger="app.services.discord_service"):
        asyncio.run(service.send_failure(**FAILURE_KWARGS))

    assert VALID_WEBHOOK not in caplog.text
    assert "/api/webhooks/" not in caplog.text

"""Discord notifications for scheduled research executions (Step 8).

Sends ONE compact embed per execution — after the scheduler knows the
research outcome (success or failure). The service is independent of
APScheduler: it exposes ``send_success`` / ``send_failure`` that return a
notification status string and never raise, so a Discord outage can never
change a research execution's status.

Security posture:
- The webhook URL comes ONLY from backend environment configuration and is
  validated against a strict allowlist (HTTPS + Discord hosts + webhook
  path). A malformed or foreign URL fails closed: notifications are
  treated as not configured, and no request is ever sent elsewhere (SSRF).
- Messages carry a short summary only — no answer text, no source
  snippets, no stack traces, no API keys, no environment values.
- ``allowed_mentions`` is emptied so research-question text can never
  ping ``@everyone`` / ``@here`` through the payload (payload injection).
- Failures are logged without the URL and retried at most once.
"""

import asyncio
import logging
from datetime import UTC, datetime
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from app.core import config

logger = logging.getLogger(__name__)

# Notification outcomes (job.notification_status mirrors these).
STATUS_SENT = "sent"
STATUS_FAILED = "failed"
STATUS_NOT_CONFIGURED = "not_configured"

# Short timeout — notifications must never stall the scheduler (this is
# deliberately NOT the 120 s GLM ceiling; Discord should answer in ~1 s).
REQUEST_TIMEOUT_SECONDS = 8.0
# Initial request plus at most ONE bounded retry (transient failures only;
# 4xx responses are permanent and never retried).
MAX_ATTEMPTS = 2
RETRY_DELAY_SECONDS = 1.5

# Strict webhook destination allowlist.
ALLOWED_WEBHOOK_HOSTS = {"discord.com", "discordapp.com"}
REQUIRED_WEBHOOK_PATH_PREFIX = "/api/webhooks/"

# Values that mean "not configured yet" (mirrors .env.example placeholders).
PLACEHOLDER_WEBHOOK_URLS = {"", "your_discord_webhook_url_here"}

# Discord embed field values cap at 1024 chars; stay comfortably below.
MAX_FIELD_LENGTH = 1000
# Client-safe error snippets are short by construction; hard cap anyway.
MAX_ERROR_LENGTH = 500

COLOR_SUCCESS = 0x22C55E  # green
COLOR_FAILURE = 0xEF4444  # red

# Report filenames are `<prefix><report_id>.md` / `.pdf` on disk (mirrors
# report_service.FILENAME_PREFIX; duplicated to keep this module's import
# chain light — pdf_reporter/reportlab must not load for notifications).
_REPORT_FILENAME_PREFIX = "researchflow_"

SUCCESS_TITLE = "ResearchFlow AI — Research Completed"
FAILURE_TITLE = "ResearchFlow AI — Research Failed"


def is_valid_discord_webhook_url(url: str) -> bool:
    """True only for HTTPS Discord incoming-webhook URLs.

    Rejects plain HTTP, non-Discord hosts (arbitrary domains, localhost,
    private IPs), explicit ports, embedded credentials, and anything
    malformed. The webhook is never user-controlled, but a mistyped value
    must fail closed instead of firing a request at a foreign destination.
    """
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        has_credentials = bool(parsed.username or parsed.password)
        port = parsed.port  # may raise ValueError for malformed ports
        path_segments = parsed.path.split("/")
        has_dot_segments = ".." in path_segments or "." in path_segments
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and host in ALLOWED_WEBHOOK_HOSTS
        and port in (None, 443)
        and not has_credentials
        and not has_dot_segments
        and parsed.path.startswith(REQUIRED_WEBHOOK_PATH_PREFIX)
    )


def _clip(text: str, limit: int) -> str:
    """Trim to `limit` characters on a single trailing ellipsis."""
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _format_timestamp(moment: datetime, timezone_name: str) -> str:
    """Render an aware datetime in the schedule's IANA timezone."""
    if moment.tzinfo is None:  # defensive — callers pass aware values
        moment = moment.replace(tzinfo=UTC)
    try:
        local = moment.astimezone(ZoneInfo(timezone_name))
    except (ZoneInfoNotFoundError, ValueError, KeyError, OSError):
        local = moment.astimezone(UTC)
        timezone_name = "UTC"
    return f"{local:%Y-%m-%d %H:%M} {timezone_name}"


def _field(name: str, value: str, *, inline: bool = True) -> dict:
    return {
        "name": name,
        "value": _clip(value, MAX_FIELD_LENGTH) or "—",
        "inline": inline,
    }


def _report_files_value(report_id: str) -> str:
    """Identify the report filenames without inventing public URLs.

    Discord recipients cannot reach a localhost backend, so the report is
    identified by id/filename only — no links are fabricated.
    """
    if not report_id:
        return "—"
    return (
        f"{_REPORT_FILENAME_PREFIX}{report_id}.md\n"
        f"{_REPORT_FILENAME_PREFIX}{report_id}.pdf"
    )


class DiscordNotificationService:
    """Posts compact result embeds to a Discord incoming webhook."""

    def __init__(
        self,
        webhook_url: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        retry_delay: float = RETRY_DELAY_SECONDS,
    ) -> None:
        """`webhook_url` defaults to the backend configuration;
        `transport` is injectable for tests (httpx.MockTransport)."""
        self._webhook_url = (
            webhook_url if webhook_url is not None else config.DISCORD_WEBHOOK_URL
        ).strip()
        self._transport = transport
        self._retry_delay = retry_delay

    @property
    def is_configured(self) -> bool:
        """True only when a real, structurally valid webhook URL is set."""
        return self._webhook_url not in PLACEHOLDER_WEBHOOK_URLS and (
            is_valid_discord_webhook_url(self._webhook_url)
        )

    # ------------------------------------------------------------- public API

    async def send_success(
        self,
        *,
        question: str,
        trigger: str,
        report_id: str,
        sources_found: int,
        completed_at: datetime,
        timezone_name: str,
    ) -> str:
        """Notify that a scheduled research run completed. Never raises."""
        payload = {
            # Empty parse list: question text can never trigger mentions.
            "allowed_mentions": {"parse": []},
            "embeds": [
                {
                    "title": SUCCESS_TITLE,
                    "color": COLOR_SUCCESS,
                    "fields": [
                        _field("Question", question, inline=False),
                        _field("Status", "SUCCESS"),
                        _field("Trigger", trigger),
                        _field("Sources", str(sources_found)),
                        _field("Report", _report_files_value(report_id), inline=False),
                        _field(
                            "Completed",
                            _format_timestamp(completed_at, timezone_name),
                            inline=False,
                        ),
                    ],
                }
            ],
        }
        return await self._post_payload(payload)

    async def send_failure(
        self,
        *,
        question: str,
        trigger: str,
        error: str,
        occurred_at: datetime,
        timezone_name: str,
    ) -> str:
        """Notify that a scheduled research run failed. Never raises."""
        payload = {
            "allowed_mentions": {"parse": []},
            "embeds": [
                {
                    "title": FAILURE_TITLE,
                    "color": COLOR_FAILURE,
                    "fields": [
                        _field("Question", question, inline=False),
                        _field("Status", "FAILED"),
                        _field("Trigger", trigger),
                        _field("Error", _clip(error, MAX_ERROR_LENGTH), inline=False),
                        _field(
                            "Time",
                            _format_timestamp(occurred_at, timezone_name),
                            inline=False,
                        ),
                    ],
                }
            ],
        }
        return await self._post_payload(payload)

    # -------------------------------------------------------------- internals

    async def _post_payload(self, payload: dict) -> str:
        """POST the JSON payload once (plus one bounded retry).

        Returns STATUS_SENT / STATUS_FAILED / STATUS_NOT_CONFIGURED and
        never raises — a notification problem must not affect research.
        """
        if not self.is_configured:
            logger.info("Discord notification skipped: not configured")
            return STATUS_NOT_CONFIGURED

        last_problem = ""
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                async with httpx.AsyncClient(
                    timeout=REQUEST_TIMEOUT_SECONDS, transport=self._transport
                ) as client:
                    response = await client.post(self._webhook_url, json=payload)
            except httpx.TimeoutException:
                last_problem = "timed out"
            except httpx.HTTPError:
                last_problem = "network error"
            else:
                if response.status_code < 400:  # Discord answers 204 (or 200)
                    logger.info("Discord notification sent")
                    return STATUS_SENT
                if response.status_code < 500:
                    # 4xx is permanent (bad/deleted webhook) — do not retry.
                    logger.warning(
                        "Discord notification rejected (HTTP %d, attempt %d)",
                        response.status_code,
                        attempt,
                    )
                    return STATUS_FAILED
                last_problem = f"HTTP {response.status_code}"
            if attempt < MAX_ATTEMPTS:
                await asyncio.sleep(self._retry_delay)

        logger.warning(
            "Discord notification failed after %d attempt(s): %s",
            MAX_ATTEMPTS,
            last_problem,
        )
        return STATUS_FAILED

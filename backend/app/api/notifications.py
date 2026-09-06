"""API routes for notification channels (Step 8).

Read-only status only: whether the Discord webhook is configured. There is
deliberately NO endpoint to configure a webhook from the browser — the
URL is a backend secret that lives only in backend/.env.
"""

from fastapi import APIRouter

from app.schemas.notification import (
    DiscordNotificationStatus,
    NotificationStatusResponse,
)
from app.services.discord_service import DiscordNotificationService

router = APIRouter(prefix="/api/research/notifications", tags=["notifications"])


@router.get("/status", response_model=NotificationStatusResponse)
async def notification_status() -> NotificationStatusResponse:
    """Which notification channels are configured (booleans only, no secrets)."""
    # The service validates the configured URL structurally (HTTPS +
    # Discord hosts); a malformed value fails closed to "not configured".
    return NotificationStatusResponse(
        discord=DiscordNotificationStatus(
            configured=DiscordNotificationService().is_configured,
        )
    )

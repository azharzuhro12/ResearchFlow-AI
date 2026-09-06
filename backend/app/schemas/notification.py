"""Pydantic schemas for notification channels (Step 8).

These responses deliberately expose ONLY whether a channel is configured.
Webhook URLs, tokens, and ids are backend secrets and can never appear in
an API response.
"""

from typing import Literal

from pydantic import BaseModel


class DiscordNotificationStatus(BaseModel):
    """Is the Discord webhook configured? (Yes/no — nothing else.)"""

    configured: bool


class NotificationStatusResponse(BaseModel):
    """Response body for GET /api/research/notifications/status."""

    status: Literal["success"] = "success"
    discord: DiscordNotificationStatus

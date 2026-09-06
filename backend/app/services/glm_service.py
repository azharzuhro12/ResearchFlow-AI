"""GLM API client (Anthropic Messages API format).

The only module that talks to the GLM API. Credentials come from
app.core.config (environment variables) and are never logged or returned.

Z.ai's GLM Coding Plan quota is exposed via the Anthropic-compatible
endpoint (`https://api.z.ai/api/anthropic`), so requests use the
Messages API shape: `x-api-key` auth, `system` as a top-level field,
and a `content` list of typed blocks in the response.
"""

import httpx

from app.core import config

# How long to wait for the GLM API before giving up. Synthesis requests
# carry a large evidence context and ask for a long, thinking-model answer,
# which can legitimately take longer than a planner call (Step 5 measured
# >60s), so the ceiling is generous — fast responses still return fast.
REQUEST_TIMEOUT_SECONDS = 120.0

# The Messages API requires an explicit output cap. Thinking-capable GLM
# models emit reasoning blocks before the text, so leave generous room.
MAX_OUTPUT_TOKENS = 4096


class GLMServiceError(Exception):
    """Raised when a GLM API call fails. `message` is safe to show clients."""

    def __init__(self, message: str, http_status: int = 502) -> None:
        super().__init__(message)
        self.message = message
        self.http_status = http_status


class GLMService:
    """Thin async client for the GLM Anthropic-compatible messages endpoint."""

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        """`transport` is injectable for tests (httpx.MockTransport)."""
        self._transport = transport

    async def complete(self, system_prompt: str, user_prompt: str) -> str:
        """Send a messages request and return the assistant text."""
        if not config.glm_is_configured():
            raise GLMServiceError(
                "GLM API is not configured. Set GLM_API_KEY and GLM_MODEL in backend/.env.",
                http_status=503,
            )

        payload = {
            "model": config.GLM_MODEL,
            "max_tokens": MAX_OUTPUT_TOKENS,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_prompt}],
            # No `temperature`: thinking-capable GLM models require default
            # sampling, and the planner already tolerates fenced JSON output.
        }
        headers = {
            "x-api-key": config.GLM_API_KEY,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        url = f"{config.GLM_BASE_URL}/v1/messages"

        try:
            async with httpx.AsyncClient(
                timeout=REQUEST_TIMEOUT_SECONDS, transport=self._transport
            ) as client:
                response = await client.post(url, json=payload, headers=headers)
        except httpx.TimeoutException:
            raise GLMServiceError(
                "GLM API request timed out. Unable to generate research plan.",
                http_status=504,
            ) from None
        except httpx.HTTPError:
            raise GLMServiceError(
                "Failed to reach the GLM API. Unable to generate research plan.",
                http_status=502,
            ) from None

        if response.status_code >= 400:
            raise GLMServiceError(
                f"GLM API returned an error (HTTP {response.status_code}). "
                "Unable to generate research plan.",
                http_status=502,
            ) from None

        try:
            # The response `content` is a list of typed blocks, e.g.
            # [{"type": "thinking", ...}, {"type": "text", "text": "..."}].
            # Only the text blocks carry the answer.
            blocks = response.json()["content"]
            text = "".join(
                block["text"]
                for block in blocks
                if isinstance(block, dict) and block.get("type") == "text"
            )
        except (ValueError, KeyError, TypeError):
            raise GLMServiceError(
                "GLM API returned an unexpected response.",
                http_status=502,
            ) from None

        if not text.strip():
            raise GLMServiceError(
                "GLM API returned an empty response.",
                http_status=502,
            ) from None
        return text

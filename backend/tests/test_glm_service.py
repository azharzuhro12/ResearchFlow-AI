"""Unit tests for the GLM service HTTP layer (Anthropic Messages format).

All HTTP traffic is mocked with httpx.MockTransport — no real requests,
no cost, no credentials involved.
"""

import asyncio
import json

import httpx
import pytest

from app.core import config
from app.services.glm_service import GLMService, GLMServiceError


def _make_service(handler) -> GLMService:
    return GLMService(transport=httpx.MockTransport(handler))


def _message_response(blocks: list) -> httpx.Response:
    return httpx.Response(200, json={"content": blocks})


@pytest.fixture(autouse=True)
def configured_glm(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pretend GLM is configured so the 503 guard does not trigger."""
    monkeypatch.setattr(config, "GLM_API_KEY", "test-key")
    monkeypatch.setattr(config, "GLM_MODEL", "test-model")
    monkeypatch.setattr(config, "GLM_BASE_URL", "https://api.test/api/anthropic")


def test_text_blocks_are_joined_and_thinking_ignored() -> None:
    """Only `text` blocks make up the answer; `thinking` blocks are dropped."""
    service = _make_service(
        lambda request: _message_response(
            [
                {"type": "thinking", "thinking": "let me reason..."},
                {"type": "text", "text": "part one, "},
                {"type": "text", "text": "part two"},
            ]
        )
    )

    result = asyncio.run(service.complete("system", "user"))

    assert result == "part one, part two"


def test_request_uses_anthropic_messages_shape() -> None:
    """The outgoing request must match the Anthropic Messages API format."""
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["api_key"] = request.headers.get("x-api-key")
        captured["anthropic_version"] = request.headers.get("anthropic-version")
        captured["body"] = json.loads(request.content.decode())
        return _message_response([{"type": "text", "text": "ok"}])

    asyncio.run(_make_service(handler).complete("be systematic", "hi"))

    assert captured["url"] == "https://api.test/api/anthropic/v1/messages"
    assert captured["api_key"] == "test-key"
    assert captured["anthropic_version"] == "2023-06-01"
    body = captured["body"]
    assert isinstance(body, dict)
    assert body["model"] == "test-model"
    assert body["system"] == "be systematic"
    assert body["messages"] == [{"role": "user", "content": "hi"}]
    assert isinstance(body["max_tokens"], int) and body["max_tokens"] > 0


def test_http_error_returns_502() -> None:
    service = _make_service(lambda request: httpx.Response(429, json={"error": {}}))

    with pytest.raises(GLMServiceError) as exc_info:
        asyncio.run(service.complete("system", "user"))

    assert exc_info.value.http_status == 502
    assert "HTTP 429" in exc_info.value.message


def test_malformed_response_returns_502() -> None:
    service = _make_service(lambda request: httpx.Response(200, text="not json"))

    with pytest.raises(GLMServiceError) as exc_info:
        asyncio.run(service.complete("system", "user"))

    assert exc_info.value.http_status == 502


def test_thinking_only_response_returns_empty_error() -> None:
    """A response with no text blocks (e.g. truncated) is an error."""
    service = _make_service(
        lambda request: _message_response(
            [{"type": "thinking", "thinking": "ran out of tokens"}]
        )
    )

    with pytest.raises(GLMServiceError) as exc_info:
        asyncio.run(service.complete("system", "user"))

    assert exc_info.value.http_status == 502
    assert "empty" in exc_info.value.message


def test_not_configured_returns_503(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "GLM_API_KEY", "")
    service = _make_service(lambda request: pytest.fail("must not call the API"))

    with pytest.raises(GLMServiceError) as exc_info:
        asyncio.run(service.complete("system", "user"))

    assert exc_info.value.http_status == 503

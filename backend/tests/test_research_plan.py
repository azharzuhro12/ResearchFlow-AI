"""Tests for POST /api/research/plan.

All GLM interaction is mocked — no real API calls, no cost.
"""

import pytest
from fastapi.testclient import TestClient

from app.api.research import get_research_planner
from app.core import config
from app.main import app
from app.services.glm_service import GLMService, GLMServiceError
from app.services.research_planner import (
    ResearchPlanner,
    ResearchPlannerError,
    parse_plan_payload,
)

client = TestClient(app)

VALID_QUESTION = "What are the latest RAG techniques in 2026?"


class FakeGLMService(GLMService):
    """In-memory GLM stub — never performs HTTP requests."""

    def __init__(
        self, content: str | None = None, error: GLMServiceError | None = None
    ) -> None:
        self._content = content
        self._error = error

    async def complete(self, system_prompt: str, user_prompt: str) -> str:
        assert system_prompt and user_prompt, "prompts must be non-empty"
        if self._error is not None:
            raise self._error
        assert self._content is not None
        return self._content


@pytest.fixture
def use_fake_planner():
    """Override the planner dependency with one backed by FakeGLMService."""

    def _install(fake: FakeGLMService) -> None:
        app.dependency_overrides[get_research_planner] = lambda: ResearchPlanner(fake)

    yield _install
    app.dependency_overrides.pop(get_research_planner, None)


# ---------------------------------------------------------------- API tests


def test_valid_question_returns_plan(use_fake_planner) -> None:
    use_fake_planner(
        FakeGLMService(
            content='{"plan": ["Identify techniques", "Analyze methods", "Summarize findings"]}'
        )
    )
    response = client.post("/api/research/plan", json={"question": VALID_QUESTION})
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["question"] == VALID_QUESTION
    assert data["plan"] == ["Identify techniques", "Analyze methods", "Summarize findings"]


def test_glm_json_with_code_fences_is_parsed(use_fake_planner) -> None:
    use_fake_planner(
        FakeGLMService(
            content='```json\n{"plan": ["Step one", "Step two", "Step three"]}\n```'
        )
    )
    response = client.post("/api/research/plan", json={"question": VALID_QUESTION})
    assert response.status_code == 200
    assert len(response.json()["plan"]) == 3


def test_empty_question_is_rejected() -> None:
    response = client.post("/api/research/plan", json={"question": "   "})
    assert response.status_code == 422


def test_question_too_short_is_rejected() -> None:
    response = client.post("/api/research/plan", json={"question": "too short"})
    assert response.status_code == 422


def test_glm_timeout_returns_504(use_fake_planner) -> None:
    use_fake_planner(
        FakeGLMService(
            error=GLMServiceError(
                "GLM API request timed out. Unable to generate research plan.",
                http_status=504,
            )
        )
    )
    response = client.post("/api/research/plan", json={"question": VALID_QUESTION})
    assert response.status_code == 504
    assert "timed out" in response.json()["detail"]


def test_malformed_glm_response_returns_502(use_fake_planner) -> None:
    use_fake_planner(FakeGLMService(content="Sorry, I cannot help with that."))
    response = client.post("/api/research/plan", json={"question": VALID_QUESTION})
    assert response.status_code == 502
    assert response.json()["detail"] == "Unable to generate research plan."


def test_missing_api_key_returns_503(monkeypatch) -> None:
    monkeypatch.setattr(config, "GLM_API_KEY", "")
    app.dependency_overrides[get_research_planner] = lambda: ResearchPlanner(GLMService())
    try:
        response = client.post("/api/research/plan", json={"question": VALID_QUESTION})
    finally:
        app.dependency_overrides.pop(get_research_planner, None)
    assert response.status_code == 503
    assert "not configured" in response.json()["detail"]


# ------------------------------------------------------- parser unit tests


class TestParsePlanPayload:
    def test_plain_json(self) -> None:
        assert parse_plan_payload('{"plan": ["a", "b"]}') == ["a", "b"]

    def test_json_with_prose_around_it(self) -> None:
        raw = 'Here is your plan:\n{"plan": ["a", "b", "c"]}\nHope that helps!'
        assert parse_plan_payload(raw) == ["a", "b", "c"]

    def test_code_fenced_json(self) -> None:
        assert parse_plan_payload('```json\n{"plan": ["a"]}\n```') == ["a"]

    def test_skips_non_string_and_empty_steps(self) -> None:
        assert parse_plan_payload('{"plan": ["a", "", 5, "b"]}') == ["a", "b"]

    @pytest.mark.parametrize(
        "bad",
        [
            "",
            "no json here",
            '["not", "an", "object"]',
            '{"no_plan": 1}',
            '{"plan": "not-a-list"}',
            '{"plan": []}',
            '{"plan": [42]}',
        ],
    )
    def test_invalid_payloads_raise(self, bad: str) -> None:
        with pytest.raises(ResearchPlannerError):
            parse_plan_payload(bad)

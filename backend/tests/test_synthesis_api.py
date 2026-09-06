"""API tests for POST /api/research/synthesize.

The whole synthesis pipeline is replaced via dependency overrides — no GLM,
no ChromaDB, no model download, no cost.
"""

import pytest
from fastapi.testclient import TestClient

from app.api.research import get_synthesis_service
from app.main import app
from app.services.synthesis_service import (
    CitationRecord,
    SynthesisOutcome,
    SynthesisService,
    SynthesisServiceError,
)

client = TestClient(app)

VALID_REQUEST = {"query": "What are the latest RAG techniques?", "top_k": 5}


class FakeSynthesisService(SynthesisService):
    """Scripted service — returns a preset outcome or raises."""

    def __init__(
        self,
        outcome: SynthesisOutcome | None = None,
        error: SynthesisServiceError | None = None,
    ) -> None:
        self._outcome = outcome
        self._error = error

    async def synthesize(self, query: str, top_k: int) -> SynthesisOutcome:
        if self._error:
            raise self._error
        assert self._outcome is not None
        return self._outcome


@pytest.fixture
def use_fake_synthesis():
    """Override the synthesis dependency with a scripted fake."""

    def _install(fake: FakeSynthesisService) -> None:
        app.dependency_overrides[get_synthesis_service] = lambda: fake

    yield _install
    app.dependency_overrides.pop(get_synthesis_service, None)


def _citation(evidence_id: str, position: int) -> CitationRecord:
    return CitationRecord(
        evidence_id=evidence_id,
        source_title=f"Research Source {position}",
        source_url=f"https://example.com/source-{position}",
        source_domain="example.com",
        chunk_index=position,
    )


def _grounded_outcome(**overrides) -> SynthesisOutcome:
    defaults: dict = {
        "status": "success",
        "query": VALID_REQUEST["query"],
        "answer": "Modern RAG systems increasingly use hybrid retrieval [E1][E2].",
        "citations": [_citation("E1", 1), _citation("E2", 2)],
        "evidence_count": 2,
    }
    defaults.update(overrides)
    return SynthesisOutcome(**defaults)


# ------------------------------------------------------------ request validation


def test_synthesize_valid_request(use_fake_synthesis) -> None:
    use_fake_synthesis(FakeSynthesisService(outcome=_grounded_outcome()))

    response = client.post("/api/research/synthesize", json=VALID_REQUEST)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert body["query"] == VALID_REQUEST["query"]
    assert body["evidence_count"] == 2
    assert "[E1]" in body["answer"]


def test_synthesize_invalid_query_is_422(use_fake_synthesis) -> None:
    use_fake_synthesis(FakeSynthesisService())

    for query in ("ab", "", "x" * 2001):
        response = client.post("/api/research/synthesize", json={"query": query})
        assert response.status_code == 422


@pytest.mark.parametrize("top_k", [0, -1, 21, 100])
def test_synthesize_invalid_top_k_is_422(use_fake_synthesis, top_k: int) -> None:
    use_fake_synthesis(FakeSynthesisService())

    response = client.post(
        "/api/research/synthesize", json={"query": "valid query here", "top_k": top_k}
    )

    assert response.status_code == 422


# ----------------------------------------------------------------- outcomes


def test_synthesize_no_evidence_returns_200_insufficient(use_fake_synthesis) -> None:
    use_fake_synthesis(
        FakeSynthesisService(
            outcome=SynthesisOutcome(
                status="insufficient_evidence",
                query=VALID_REQUEST["query"],
                answer="There is not enough indexed evidence to answer this question.",
                citations=[],
                evidence_count=0,
            )
        )
    )

    response = client.post("/api/research/synthesize", json=VALID_REQUEST)

    # Explicit status, not a server error (spec §13).
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "insufficient_evidence"
    assert body["citations"] == []
    assert body["evidence_count"] == 0


def test_synthesize_grounded_answer_with_citations(use_fake_synthesis) -> None:
    use_fake_synthesis(FakeSynthesisService(outcome=_grounded_outcome()))

    response = client.post("/api/research/synthesize", json=VALID_REQUEST)

    assert response.status_code == 200
    citations = response.json()["citations"]
    assert [c["evidence_id"] for c in citations] == ["E1", "E2"]
    assert citations[0]["source_url"] == "https://example.com/source-1"
    assert citations[0]["source_title"] == "Research Source 1"
    assert citations[0]["source_domain"] == "example.com"
    assert citations[0]["chunk_index"] == 1


def test_synthesize_glm_error_is_safe(use_fake_synthesis) -> None:
    use_fake_synthesis(
        FakeSynthesisService(error=SynthesisServiceError("GLM API request timed out.", 504))
    )

    response = client.post("/api/research/synthesize", json=VALID_REQUEST)

    assert response.status_code == 504
    detail = response.json()["detail"]
    assert "API key" not in detail
    assert "Traceback" not in detail


def test_synthesize_malformed_glm_json_is_502(use_fake_synthesis) -> None:
    # After both GLM attempts fail to parse, the service raises this.
    use_fake_synthesis(
        FakeSynthesisService(
            error=SynthesisServiceError(
                "The AI could not produce a valid grounded answer. Please try again.", 502
            )
        )
    )

    response = client.post("/api/research/synthesize", json=VALID_REQUEST)

    assert response.status_code == 502


def test_synthesize_invalid_citation_never_reaches_client(use_fake_synthesis) -> None:
    # Service-level policy strips E999 before the outcome is returned; the
    # API must pass exactly that through — no E999 in answer or citations.
    use_fake_synthesis(
        FakeSynthesisService(
            outcome=_grounded_outcome(
                answer="Grounded claim [E1] only.",
                citations=[_citation("E1", 1)],
            )
        )
    )

    response = client.post("/api/research/synthesize", json=VALID_REQUEST)

    assert response.status_code == 200
    body = response.json()
    assert "E999" not in body["answer"]
    assert [c["evidence_id"] for c in body["citations"]] == ["E1"]


def test_synthesize_multiple_citations_preserved(use_fake_synthesis) -> None:
    outcome = _grounded_outcome(
        answer="Claim one [E1]. Claim two [E2]. Both [E1][E2].",
        citations=[_citation("E1", 1), _citation("E2", 2)],
    )
    use_fake_synthesis(FakeSynthesisService(outcome=outcome))

    response = client.post("/api/research/synthesize", json=VALID_REQUEST)

    assert response.status_code == 200
    assert len(response.json()["citations"]) == 2


def test_synthesize_ungrounded_status_passes_through(use_fake_synthesis) -> None:
    use_fake_synthesis(
        FakeSynthesisService(
            outcome=_grounded_outcome(
                status="ungrounded", answer="Answer without citations.", citations=[]
            )
        )
    )

    response = client.post("/api/research/synthesize", json=VALID_REQUEST)

    assert response.status_code == 200
    assert response.json()["status"] == "ungrounded"
    assert response.json()["citations"] == []


# ------------------------------------------------------- existing contracts


def test_existing_endpoints_still_registered() -> None:
    """Steps 1–4 endpoints must remain untouched by Step 5."""
    assert client.get("/health").status_code == 200
    for path, payload in (
        ("/api/research/plan", {"question": "x"}),
        ("/api/research/search", {"question": "x"}),
        ("/api/research/index", {"sources": []}),
        ("/api/research/retrieve", {"query": "x"}),
    ):
        assert client.post(path, json=payload).status_code == 422

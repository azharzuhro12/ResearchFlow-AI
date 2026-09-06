"""API tests for POST /api/research/index and POST /api/research/retrieve.

The whole RAG pipeline is replaced via dependency overrides — no network,
no model download, no ChromaDB.
"""

import pytest
from fastapi.testclient import TestClient

from app.api.research import get_rag_service
from app.main import app
from app.services.rag_service import IndexStats, RAGService, RAGServiceError
from app.services.vector_store import RetrievedRecord

client = TestClient(app)

VALID_SOURCES = [
    {
        "title": "Example Article",
        "url": "https://example.com/article",
        "snippet": "A snippet",
        "source": "example.com",
    }
]
VALID_RETRIEVE = {"query": "What are the latest RAG techniques?", "top_k": 5}


class FakeRAGService(RAGService):
    """Scripted RAG service — never touches network, model, or disk."""

    def __init__(
        self,
        stats: IndexStats | None = None,
        records: list[RetrievedRecord] | None = None,
        error: RAGServiceError | None = None,
    ) -> None:
        self._stats = stats
        self._records = records
        self._error = error

    async def index_sources(self, sources):
        if self._error:
            raise self._error
        assert self._stats is not None
        return self._stats

    async def retrieve(self, query: str, top_k: int):
        if self._error:
            raise self._error
        return self._records or []


@pytest.fixture
def use_fake_rag():
    """Override the RAG dependency with a scripted fake."""

    def _install(fake: FakeRAGService) -> None:
        app.dependency_overrides[get_rag_service] = lambda: fake

    yield _install
    app.dependency_overrides.pop(get_rag_service, None)


def _record(**overrides) -> RetrievedRecord:
    defaults = {
        "text": "Retrieval-augmented generation combines search with LLMs.",
        "source_title": "Example Article",
        "source_url": "https://example.com/article",
        "source_domain": "example.com",
        "chunk_index": 2,
        "distance": 0.31,
    }
    defaults.update(overrides)
    return RetrievedRecord(**defaults)


# ------------------------------------------------------------- POST /index


def test_index_valid_request(use_fake_rag) -> None:
    use_fake_rag(FakeRAGService(stats=IndexStats(indexed_sources=1, total_chunks=8)))

    response = client.post("/api/research/index", json={"sources": VALID_SOURCES})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert body["indexed_sources"] == 1
    assert body["failed_sources"] == 0
    assert body["total_chunks"] == 8


def test_index_partial_failure_still_succeeds(use_fake_rag) -> None:
    use_fake_rag(
        FakeRAGService(stats=IndexStats(indexed_sources=0, failed_sources=1))
    )

    response = client.post("/api/research/index", json={"sources": VALID_SOURCES})

    assert response.status_code == 200
    body = response.json()
    assert body["indexed_sources"] == 0
    assert body["failed_sources"] == 1


def test_index_empty_sources_is_422(use_fake_rag) -> None:
    use_fake_rag(FakeRAGService(stats=IndexStats()))

    response = client.post("/api/research/index", json={"sources": []})

    assert response.status_code == 422


def test_index_bad_url_scheme_is_422(use_fake_rag) -> None:
    use_fake_rag(FakeRAGService(stats=IndexStats()))
    sources = [{"title": "Bad", "url": "file:///etc/passwd"}]

    response = client.post("/api/research/index", json={"sources": sources})

    assert response.status_code == 422


def test_index_missing_title_is_422(use_fake_rag) -> None:
    use_fake_rag(FakeRAGService(stats=IndexStats()))
    sources = [{"url": "https://example.com/a"}]

    response = client.post("/api/research/index", json={"sources": sources})

    assert response.status_code == 422


def test_index_infrastructure_error_is_503(use_fake_rag) -> None:
    use_fake_rag(FakeRAGService(error=RAGServiceError("Vector store is unavailable.")))

    response = client.post("/api/research/index", json={"sources": VALID_SOURCES})

    assert response.status_code == 503
    assert "unavailable" in response.json()["detail"]


# --------------------------------------------------------- POST /retrieve


def test_retrieve_valid_request(use_fake_rag) -> None:
    use_fake_rag(FakeRAGService(records=[_record()]))

    response = client.post("/api/research/retrieve", json=VALID_RETRIEVE)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert body["query"] == VALID_RETRIEVE["query"]
    assert body["total_results"] == 1
    chunk = body["results"][0]
    assert chunk["source_url"] == "https://example.com/article"
    assert chunk["source_domain"] == "example.com"
    assert chunk["chunk_index"] == 2
    assert chunk["distance"] == pytest.approx(0.31)
    assert "Retrieval-augmented" in chunk["text"]


def test_retrieve_empty_database_returns_empty_results(use_fake_rag) -> None:
    use_fake_rag(FakeRAGService(records=[]))

    response = client.post("/api/research/retrieve", json=VALID_RETRIEVE)

    assert response.status_code == 200
    body = response.json()
    assert body["results"] == []
    assert body["total_results"] == 0


@pytest.mark.parametrize("top_k", [0, -1, 21, 100])
def test_retrieve_invalid_top_k_is_422(use_fake_rag, top_k: int) -> None:
    use_fake_rag(FakeRAGService(records=[]))

    response = client.post(
        "/api/research/retrieve", json={"query": "valid query here", "top_k": top_k}
    )

    assert response.status_code == 422


def test_retrieve_short_query_is_422(use_fake_rag) -> None:
    use_fake_rag(FakeRAGService(records=[]))

    response = client.post("/api/research/retrieve", json={"query": "ab"})

    assert response.status_code == 422


def test_retrieve_infrastructure_error_is_503(use_fake_rag) -> None:
    use_fake_rag(
        FakeRAGService(error=RAGServiceError("Failed to load embedding model."))
    )

    response = client.post("/api/research/retrieve", json=VALID_RETRIEVE)

    assert response.status_code == 503


# ------------------------------------------------------- existing contracts


def test_existing_endpoints_still_registered() -> None:
    """Step 1–3 endpoints must remain untouched by Step 4."""
    for path in ("/health", "/"):
        assert client.get(path).status_code == 200
    # Plan/search routes exist and validate input the same way as before.
    assert client.post("/api/research/plan", json={"question": "x"}).status_code == 422
    assert (
        client.post("/api/research/search", json={"question": "x"}).status_code == 422
    )

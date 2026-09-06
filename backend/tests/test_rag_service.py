"""Tests for the RAG orchestrator: extract → clean → chunk → embed → store.

Every external component (page fetcher, embedding model, vector store)
is replaced with fakes — no network, no model, no ChromaDB.
"""

import asyncio

import pytest

from app.schemas.research import SourceToIndex
from app.services.content_extractor import (
    ContentExtractor,
    ContentExtractorError,
    ExtractedContent,
)
from app.services.embedding_service import (
    EmbeddingService,
    EmbeddingServiceError,
)
from app.services.rag_service import RAGService, RAGServiceError
from app.services.vector_store import (
    RetrievedRecord,
    VectorStore,
    VectorStoreError,
)


class FakeExtractor(ContentExtractor):
    """Preset extraction outcomes per URL."""

    def __init__(
        self,
        texts: dict[str, str] | None = None,
        failing_urls: set[str] | None = None,
    ) -> None:
        self._texts = texts or {}
        self._failing = failing_urls or set()

    async def extract(self, url: str) -> ExtractedContent:
        if url in self._failing:
            raise ContentExtractorError(f"cannot fetch {url}")
        if url not in self._texts:
            raise ContentExtractorError("unknown url")
        return ExtractedContent(text=self._texts[url])


class FakeEmbedder(EmbeddingService):
    """Deterministic embeddings; optional failure mode."""

    def __init__(self, error: EmbeddingServiceError | None = None) -> None:
        self.error = error
        self.document_calls: list[list[str]] = []
        self.query_calls: list[str] = []

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if self.error:
            raise self.error
        self.document_calls.append(texts)
        return [[float(len(text)), 1.0] for text in texts]

    def embed_query(self, text: str) -> list[float]:
        if self.error:
            raise self.error
        self.query_calls.append(text)
        return [float(len(text)), 1.0]


class FakeStore(VectorStore):
    """Records upserts; optional failure mode."""

    def __init__(self, error: VectorStoreError | None = None) -> None:
        self.error = error
        self.batches: list[list] = []

    def upsert_chunks(self, records: list) -> int:
        if self.error:
            raise self.error
        self.batches.append(list(records))
        return len(records)

    def query_similar(self, embedding: list[float], top_k: int) -> list[RetrievedRecord]:
        if self.error:
            raise self.error
        return [
            RetrievedRecord(
                text=f"chunk {i}",
                source_url="https://example.com/a",
                source_title="Example",
                source_domain="example.com",
                chunk_index=i,
                distance=0.1 * i,
            )
            for i in range(min(top_k, 3))
        ]


def _source(url: str, title: str = "Title", **kwargs) -> SourceToIndex:
    return SourceToIndex(url=url, title=title, **kwargs)


LONG_TEXT = "retrieval augmented generation " * 200  # ~ 6,000 chars → several chunks


def test_full_flow_indexes_chunks_with_metadata() -> None:
    extractor = FakeExtractor(texts={"https://example.com/a": LONG_TEXT})
    embedder, store = FakeEmbedder(), FakeStore()
    service = RAGService(extractor, embedder, store)

    stats = asyncio.run(
        service.index_sources(
            [_source("https://example.com/a", title="RAG Guide", source="example.com")]
        )
    )

    assert stats.indexed_sources == 1
    assert stats.failed_sources == 0
    assert stats.total_chunks > 1
    records = store.batches[0]
    first = records[0]
    assert first.source_url == "https://example.com/a"
    assert first.source_title == "RAG Guide"
    assert first.source_domain == "example.com"
    assert first.chunk_index == 0
    assert len(first.embedding) == 2
    # Every chunk embedded in order.
    assert embedder.document_calls[0] == [record.text for record in records]


def test_partial_failure_still_indexes_the_rest() -> None:
    extractor = FakeExtractor(
        texts={"https://good.com/a": LONG_TEXT},
        failing_urls={"https://bad.com/x"},
    )
    store = FakeStore()
    service = RAGService(extractor, FakeEmbedder(), store)

    stats = asyncio.run(
        service.index_sources(
            [_source("https://good.com/a"), _source("https://bad.com/x")]
        )
    )

    assert stats.indexed_sources == 1
    assert stats.failed_sources == 1
    assert stats.failed_urls == ["https://bad.com/x"]
    assert stats.total_chunks > 0


def test_blank_page_counts_as_failed_source() -> None:
    extractor = FakeExtractor(texts={"https://empty.com/a": "   \n  "})
    service = RAGService(extractor, FakeEmbedder(), FakeStore())

    stats = asyncio.run(service.index_sources([_source("https://empty.com/a")]))

    assert stats.indexed_sources == 0
    assert stats.failed_sources == 1


def test_embedding_failure_aborts_with_503() -> None:
    extractor = FakeExtractor(texts={"https://example.com/a": LONG_TEXT})
    embedder = FakeEmbedder(error=EmbeddingServiceError("model unavailable"))
    service = RAGService(extractor, embedder, FakeStore())

    with pytest.raises(RAGServiceError) as exc_info:
        asyncio.run(service.index_sources([_source("https://example.com/a")]))

    assert exc_info.value.http_status == 503


def test_store_failure_aborts_with_503() -> None:
    extractor = FakeExtractor(texts={"https://example.com/a": LONG_TEXT})
    store = FakeStore(error=VectorStoreError("chroma down"))
    service = RAGService(extractor, FakeEmbedder(), store)

    with pytest.raises(RAGServiceError) as exc_info:
        asyncio.run(service.index_sources([_source("https://example.com/a")]))

    assert exc_info.value.http_status == 503


def test_reindexing_same_source_is_idempotent() -> None:
    extractor = FakeExtractor(texts={"https://example.com/a": LONG_TEXT})
    store = FakeStore()
    service = RAGService(extractor, FakeEmbedder(), store)

    asyncio.run(service.index_sources([_source("https://example.com/a")]))
    first_ids = [record.chunk_id for record in store.batches[0]]
    asyncio.run(service.index_sources([_source("https://example.com/a")]))
    second_ids = [record.chunk_id for record in store.batches[1]]

    assert first_ids == second_ids  # deterministic ids → upsert replaces


def test_url_variations_map_to_same_ids() -> None:
    extractor = FakeExtractor(
        texts={
            "https://Example.com/article/": LONG_TEXT,
            "https://example.com/article": LONG_TEXT,
        }
    )
    store = FakeStore()
    service = RAGService(extractor, FakeEmbedder(), store)

    asyncio.run(
        service.index_sources([_source("https://Example.com/article/")])
    )
    asyncio.run(
        service.index_sources([_source("https://example.com/article")])
    )

    ids_first = {record.chunk_id for record in store.batches[0]}
    ids_second = {record.chunk_id for record in store.batches[1]}
    assert ids_first == ids_second  # normalized URL dedups trailing slash/case


def test_retrieve_maps_records() -> None:
    service = RAGService(FakeExtractor(), FakeEmbedder(), FakeStore())

    records = asyncio.run(service.retrieve("what is RAG?", top_k=5))

    assert len(records) == 3
    assert records[0].source_url == "https://example.com/a"
    assert records[0].distance == 0.0


def test_retrieve_infrastructure_error_maps_to_503() -> None:
    service = RAGService(
        FakeExtractor(), FakeEmbedder(error=EmbeddingServiceError("no model")), FakeStore()
    )

    with pytest.raises(RAGServiceError) as exc_info:
        asyncio.run(service.retrieve("what is RAG?", top_k=5))

    assert exc_info.value.http_status == 503

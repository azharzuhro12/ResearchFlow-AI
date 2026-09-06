"""Tests for the ChromaDB vector store wrapper.

ChromaDB is fully mocked with an in-memory fake client — no persistent
files, no real vector database.
"""

import pytest

from app.services.vector_store import (
    ChunkRecord,
    RetrievedRecord,
    VectorStore,
    VectorStoreError,
)


def _record(chunk_id: str, *, url: str = "https://example.com/a", index: int = 0) -> ChunkRecord:
    return ChunkRecord(
        chunk_id=chunk_id,
        text=f"text of {chunk_id}",
        embedding=[0.1, 0.2, 0.3],
        source_url=url,
        source_title="Example Article",
        source_domain="example.com",
        chunk_index=index,
        published_at="2026-01-01",
    )


class FakeChromaCollection:
    """In-memory Chroma collection: upsert / query / delete."""

    def __init__(self) -> None:
        self.by_id: dict[str, dict] = {}
        self.last_n_results: int | None = None

    def upsert(self, ids, documents, embeddings, metadatas) -> None:
        for chunk_id, document, embedding, metadata in zip(
            ids, documents, embeddings, metadatas
        ):
            self.by_id[chunk_id] = {
                "document": document,
                "embedding": embedding,
                "metadata": dict(metadata),
            }

    def query(self, query_embeddings, n_results, include) -> dict:
        self.last_n_results = n_results
        rows = sorted(
            self.by_id.items(),
            key=lambda item: sum(a * b for a, b in zip(item[1]["embedding"], query_embeddings[0])),
            reverse=True,
        )[:n_results]
        return {
            "documents": [[entry["document"] for _, entry in rows]],
            "metadatas": [[dict(entry["metadata"]) for _, entry in rows]],
            "distances": [[0.25 for _ in rows]],
        }

    def delete(self, where) -> None:
        for chunk_id in [
            chunk_id
            for chunk_id, entry in self.by_id.items()
            if entry["metadata"].get("source_url") == where["source_url"]
        ]:
            del self.by_id[chunk_id]


class FakeChromaClient:
    def __init__(self) -> None:
        self.collection = FakeChromaCollection()

    def get_or_create_collection(self, name, metadata=None):
        self.created_name = name
        return self.collection


@pytest.fixture
def fake_client() -> FakeChromaClient:
    return FakeChromaClient()


def test_upsert_stores_chunks_with_metadata(fake_client: FakeChromaClient) -> None:
    store = VectorStore(client=fake_client)

    written = store.upsert_chunks([_record("id-1"), _record("id-2", index=1)])

    assert written == 2
    entry = fake_client.collection.by_id["id-1"]
    assert entry["document"] == "text of id-1"
    assert entry["metadata"]["source_url"] == "https://example.com/a"
    assert entry["metadata"]["source_title"] == "Example Article"
    assert entry["metadata"]["source_domain"] == "example.com"
    assert entry["metadata"]["chunk_index"] == 0
    assert entry["metadata"]["published_at"] == "2026-01-01"


def test_upsert_empty_list_is_noop(fake_client: FakeChromaClient) -> None:
    store = VectorStore(client=fake_client)

    assert store.upsert_chunks([]) == 0
    assert fake_client.collection.by_id == {}


def test_upsert_same_id_replaces_not_duplicates(fake_client: FakeChromaClient) -> None:
    store = VectorStore(client=fake_client)

    store.upsert_chunks([_record("id-1")])
    store.upsert_chunks([_record("id-1")])  # idempotent re-index

    assert len(fake_client.collection.by_id) == 1


def test_query_returns_mapped_records_with_distance(fake_client: FakeChromaClient) -> None:
    store = VectorStore(client=fake_client)
    store.upsert_chunks([_record("id-1"), _record("id-2", index=1)])

    records = store.query_similar([0.1, 0.2, 0.3], top_k=5)

    assert len(records) == 2
    assert all(isinstance(record, RetrievedRecord) for record in records)
    assert all(record.distance == 0.25 for record in records)
    assert all(record.source_url == "https://example.com/a" for record in records)


def test_query_empty_collection_returns_empty_list(fake_client: FakeChromaClient) -> None:
    store = VectorStore(client=fake_client)

    assert store.query_similar([0.1, 0.2, 0.3], top_k=5) == []


def test_query_passes_top_k_through(fake_client: FakeChromaClient) -> None:
    store = VectorStore(client=fake_client)
    store.upsert_chunks([_record(f"id-{i}", index=i) for i in range(8)])

    store.query_similar([0.1, 0.2, 0.3], top_k=3)

    assert fake_client.collection.last_n_results == 3


def test_delete_by_source_url(fake_client: FakeChromaClient) -> None:
    store = VectorStore(client=fake_client)
    store.upsert_chunks(
        [
            _record("a-0", url="https://example.com/a"),
            _record("b-0", url="https://example.com/b"),
        ]
    )

    store.delete_by_source_url("https://example.com/a")

    assert set(fake_client.collection.by_id) == {"b-0"}


def test_chroma_failure_maps_to_503() -> None:
    class BrokenClient:
        def get_or_create_collection(self, name, metadata=None):
            raise RuntimeError("chroma down")

    store = VectorStore(client=BrokenClient())

    with pytest.raises(VectorStoreError) as exc_info:
        store.query_similar([0.1], top_k=5)

    assert exc_info.value.http_status == 503


def test_transient_bootstrap_failure_is_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    class FlakyClient(FakeChromaClient):
        def __init__(self) -> None:
            super().__init__()
            self.calls = 0

        def get_or_create_collection(self, name, metadata=None):
            self.calls += 1
            if self.calls == 1:  # transient failure on first bootstrap
                raise RuntimeError("database is locked")
            return super().get_or_create_collection(name, metadata=metadata)

    monkeypatch.setattr("time.sleep", lambda seconds: None)  # no real waiting
    client = FlakyClient()
    store = VectorStore(client=client)

    written = store.upsert_chunks([_record("id-retry")])

    assert written == 1
    assert client.calls == 2
    assert "id-retry" in client.collection.by_id


def test_persistent_bootstrap_failure_raises_after_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class AlwaysBrokenClient:
        def __init__(self) -> None:
            self.calls = 0

        def get_or_create_collection(self, name, metadata=None):
            self.calls += 1
            raise RuntimeError("chroma down")

    monkeypatch.setattr("time.sleep", lambda seconds: None)
    client = AlwaysBrokenClient()
    store = VectorStore(client=client)

    with pytest.raises(VectorStoreError) as exc_info:
        store.upsert_chunks([_record("id-doomed")])

    assert client.calls == 2  # both attempts were made before giving up
    assert exc_info.value.http_status == 503
    assert "unavailable" in exc_info.value.message
    assert isinstance(exc_info.value.__cause__, RuntimeError)  # cause chained


def test_metadata_skips_missing_optional_fields(fake_client: FakeChromaClient) -> None:
    store = VectorStore(client=fake_client)

    store.upsert_chunks(
        [
            ChunkRecord(
                chunk_id="bare",
                text="minimal",
                embedding=[0.0],
                source_url="https://example.com/bare",
            )
        ]
    )

    metadata = fake_client.collection.by_id["bare"]["metadata"]
    assert metadata == {"source_url": "https://example.com/bare", "chunk_index": 0}

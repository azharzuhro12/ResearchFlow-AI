"""Persistent ChromaDB vector store wrapper.

Stores document chunks with their source metadata and answers similarity
queries. Fully local (PersistentClient under data/chroma/) — no cloud,
no API key. ChromaDB is imported lazily so tests can inject a fake client
without paying the import cost.
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from app.core import config

logger = logging.getLogger(__name__)


class VectorStoreError(Exception):
    """Raised when the vector store fails. `message` is client-safe."""

    def __init__(self, message: str, http_status: int = 503) -> None:
        super().__init__(message)
        self.message = message
        self.http_status = http_status


@dataclass(frozen=True)
class ChunkRecord:
    """One chunk ready to be stored: text, embedding, deterministic id, metadata."""

    chunk_id: str
    text: str
    embedding: list[float]
    source_url: str
    source_title: str | None = None
    source_domain: str | None = None
    chunk_index: int = 0
    published_at: str | None = None


@dataclass(frozen=True)
class RetrievedRecord:
    """One similarity-search hit mapped from the vector store."""

    text: str
    source_url: str
    source_title: str | None = None
    source_domain: str | None = None
    chunk_index: int = 0
    distance: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


def _record_metadata(record: ChunkRecord) -> dict[str, str | int]:
    """Chroma metadata values must be scalar — skip missing fields."""
    metadata: dict[str, str | int] = {
        "source_url": record.source_url,
        "chunk_index": record.chunk_index,
    }
    if record.source_title:
        metadata["source_title"] = record.source_title
    if record.source_domain:
        metadata["source_domain"] = record.source_domain
    if record.published_at:
        metadata["published_at"] = record.published_at
    return metadata


class VectorStore:
    """Clean interface over one persistent ChromaDB collection."""

    def __init__(
        self,
        client: Any = None,
        collection_name: str | None = None,
    ) -> None:
        """`client` is injectable for tests; default is a persistent client."""
        self._client = client
        self._collection_name = collection_name or config.CHROMA_COLLECTION_NAME
        self._collection: Any = None

    def _get_collection(self) -> Any:
        """Get-or-create the collection once (reused across requests).

        The first ChromaDB bootstrap (import + persistent client +
        collection) has been observed to fail transiently (e.g. a sqlite
        lock during startup). One retry with a short pause absorbs that;
        a persistent failure raises the same client-safe error, but the
        underlying cause is logged — operators need it, clients must not
        see it.
        """
        if self._collection is None:
            last_error: Exception | None = None
            for attempt in (1, 2):
                try:
                    if self._client is None:
                        import chromadb  # lazy heavy import

                        config.CHROMA_DIR.mkdir(parents=True, exist_ok=True)
                        self._client = chromadb.PersistentClient(
                            path=str(config.CHROMA_DIR)
                        )
                    self._collection = self._client.get_or_create_collection(
                        name=self._collection_name,
                        metadata={"hnsw:space": "cosine"},
                    )
                    break
                except Exception as exc:
                    last_error = exc
                    logger.warning(
                        "ChromaDB bootstrap attempt %d/2 failed: %s: %s",
                        attempt,
                        type(exc).__name__,
                        exc,
                    )
                    if attempt == 1:
                        time.sleep(0.5)
            if self._collection is None:
                raise VectorStoreError("Vector store is unavailable.") from (
                    last_error
                )
        return self._collection

    def upsert_chunks(self, records: list[ChunkRecord]) -> int:
        """Insert or update chunks by deterministic id (idempotent re-index).

        Returns the number of chunks written.
        """
        if not records:
            return 0
        try:
            self._get_collection().upsert(
                ids=[record.chunk_id for record in records],
                documents=[record.text for record in records],
                embeddings=[record.embedding for record in records],
                metadatas=[_record_metadata(record) for record in records],
            )
        except VectorStoreError:
            raise
        except Exception:
            raise VectorStoreError("Failed to store document chunks.") from None
        return len(records)

    def query_similar(
        self, embedding: list[float], top_k: int
    ) -> list[RetrievedRecord]:
        """Return the `top_k` most similar chunks (cosine distance ascending)."""
        try:
            result = self._get_collection().query(
                query_embeddings=[embedding],
                n_results=top_k,
                include=["documents", "metadatas", "distances"],
            )
        except VectorStoreError:
            raise
        except Exception:
            raise VectorStoreError("Failed to query the vector store.") from None
        return self._map_query_result(result)

    def delete_by_source_url(self, source_url: str) -> None:
        """Remove every chunk belonging to one source URL."""
        try:
            self._get_collection().delete(where={"source_url": source_url})
        except VectorStoreError:
            raise
        except Exception:
            raise VectorStoreError("Failed to delete source chunks.") from None

    @staticmethod
    def _map_query_result(result: Any) -> list[RetrievedRecord]:
        """Map a Chroma query result to RetrievedRecord (empty-safe)."""
        documents = result.get("documents") or [[]]
        metadatas = result.get("metadatas") or [[]]
        distances = result.get("distances") or [[]]

        records: list[RetrievedRecord] = []
        for row_index, row in enumerate(documents):
            for index, text in enumerate(row):
                metadata = dict(metadatas[row_index][index]) if row_index < len(
                    metadatas
                ) and index < len(metadatas[row_index]) else {}
                distance = None
                if row_index < len(distances) and index < len(distances[row_index]):
                    distance = float(distances[row_index][index])
                records.append(
                    RetrievedRecord(
                        text=text,
                        source_url=metadata.get("source_url", ""),
                        source_title=metadata.get("source_title"),
                        source_domain=metadata.get("source_domain"),
                        chunk_index=metadata.get("chunk_index", 0),
                        distance=distance,
                        metadata=metadata,
                    )
                )
        return records

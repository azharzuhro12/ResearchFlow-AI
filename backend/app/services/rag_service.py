"""RAG orchestration: sources → extract → chunk → embed → ChromaDB.

Indexing is idempotent: chunk ids derive from the normalized source URL
plus chunk index, and the store upserts by id, so re-indexing the same
source replaces its chunks instead of duplicating them.

One failing website never fails the whole request — it is counted in
`failed_sources`. Infrastructure failures (embedding model, ChromaDB)
abort with a service error instead.
"""

import asyncio
import hashlib
import logging
from dataclasses import dataclass, field

from app.schemas.research import SourceToIndex
from app.services.chunking_service import chunk_text
from app.services.content_extractor import ContentExtractor, ContentExtractorError
from app.services.embedding_service import EmbeddingService, EmbeddingServiceError
from app.services.search_service import domain_of, normalize_url
from app.services.vector_store import ChunkRecord, VectorStore, VectorStoreError

logger = logging.getLogger(__name__)

# How many source pages may be fetched concurrently.
MAX_CONCURRENT_FETCHES = 5


class RAGServiceError(Exception):
    """Raised when RAG infrastructure fails. `message` is client-safe."""

    def __init__(self, message: str, http_status: int = 503) -> None:
        super().__init__(message)
        self.message = message
        self.http_status = http_status


@dataclass
class IndexStats:
    """Outcome of a batch indexing run (accumulated as sources finish)."""

    indexed_sources: int = 0
    failed_sources: int = 0
    total_chunks: int = 0
    failed_urls: list[str] = field(default_factory=list)


def _chunk_id(normalized_url: str, chunk_index: int) -> str:
    """Deterministic chunk id: same source + position → same id (upsert key)."""
    digest = hashlib.sha256(f"{normalized_url}::{chunk_index}".encode())
    return digest.hexdigest()


class RAGService:
    """Coordinates content extraction, chunking, embedding, and storage."""

    def __init__(
        self,
        extractor: ContentExtractor,
        embedder: EmbeddingService,
        store: VectorStore,
    ) -> None:
        self._extractor = extractor
        self._embedder = embedder
        self._store = store

    async def index_sources(self, sources: list[SourceToIndex]) -> IndexStats:
        """Index every source; extraction failures count as failed sources."""
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_FETCHES)

        async def index_one(source: SourceToIndex) -> int:
            async with semaphore:
                return await self._index_single(source)

        outcomes = await asyncio.gather(
            *(index_one(source) for source in sources),
            return_exceptions=True,
        )

        stats = IndexStats()
        for source, outcome in zip(sources, outcomes, strict=True):
            if isinstance(outcome, BaseException):
                self._handle_index_failure(source, outcome, stats)
            else:
                stats.indexed_sources += 1
                stats.total_chunks += outcome
        return stats

    async def _index_single(self, source: SourceToIndex) -> int:
        """Extract, chunk, embed, and store one source. Returns chunk count."""
        content = await self._extractor.extract(source.url)
        chunks = chunk_text(content.text)
        if not chunks:
            raise ContentExtractorError("Source page contained no readable text.")

        # Embedding is CPU-heavy local inference — keep the event loop free.
        embeddings = await asyncio.to_thread(self._embedder.embed_documents, chunks)

        key = normalize_url(source.url)
        domain = source.source or domain_of(source.url) or None
        records = [
            ChunkRecord(
                chunk_id=_chunk_id(key, index),
                text=chunk,
                embedding=embedding,
                source_url=source.url,
                source_title=source.title or None,
                source_domain=domain,
                chunk_index=index,
                published_at=source.published_at,
            )
            for index, (chunk, embedding) in enumerate(zip(chunks, embeddings, strict=True))
        ]
        await asyncio.to_thread(self._store.upsert_chunks, records)
        logger.info(
            "Indexed %s (%d chunks)", domain or source.url, len(records)
        )
        return len(records)

    def _handle_index_failure(
        self, source: SourceToIndex, error: BaseException, stats: IndexStats
    ) -> None:
        """Count one failed source; re-raise infrastructure errors instead."""
        if isinstance(error, (EmbeddingServiceError, VectorStoreError)):
            raise RAGServiceError(error.message, error.http_status) from error
        if isinstance(error, ContentExtractorError):
            logger.warning("Indexing failed for %r: %s", source.url, error.message)
        else:
            logger.exception("Unexpected indexing failure for %r", source.url)
        stats.failed_sources += 1
        stats.failed_urls.append(source.url)

    async def retrieve(self, query: str, top_k: int) -> list:
        """Embed the query and return the most similar stored chunks."""
        try:
            embedding = await asyncio.to_thread(self._embedder.embed_query, query)
            records = await asyncio.to_thread(
                self._store.query_similar, embedding, top_k
            )
        except (EmbeddingServiceError, VectorStoreError) as exc:
            raise RAGServiceError(exc.message, exc.http_status) from None
        logger.info("Retrieved %d chunks (top_k=%d)", len(records), top_k)
        return records

"""Local Sentence Transformers embeddings.

Loads the model once and reuses it across requests (never per-request).
The heavy `sentence_transformers`/torch import happens lazily inside the
default factory, so importing this module stays cheap and test-friendly.
"""

import logging
import threading
from collections.abc import Callable
from typing import Any

from app.core import config

logger = logging.getLogger(__name__)

# How many texts to push through the model per batch call.
BATCH_SIZE = 32

# Factory type: model_name -> SentenceTransformer-like object with .encode().
ModelFactory = Callable[[str], Any]


def default_model_factory(model_name: str) -> Any:
    """Load the real SentenceTransformer model (downloads on first use)."""
    from sentence_transformers import SentenceTransformer  # lazy heavy import

    return SentenceTransformer(model_name)


class EmbeddingServiceError(Exception):
    """Raised when embedding generation fails. `message` is client-safe."""

    def __init__(self, message: str, http_status: int = 503) -> None:
        super().__init__(message)
        self.message = message
        self.http_status = http_status


class EmbeddingService:
    """Embeds documents and queries with a local Sentence Transformers model."""

    def __init__(
        self,
        model_name: str | None = None,
        model_factory: ModelFactory = default_model_factory,
    ) -> None:
        self._model_name = model_name or config.EMBEDDING_MODEL_NAME
        self._model_factory = model_factory
        self._model: Any | None = None
        self._lock = threading.Lock()

    @property
    def model_name(self) -> str:
        return self._model_name

    def _get_model(self) -> Any:
        """Load the model on first use, then cache it (singleton per service)."""
        if self._model is None:
            with self._lock:
                if self._model is None:
                    logger.info("Loading embedding model %r", self._model_name)
                    try:
                        self._model = self._model_factory(self._model_name)
                    except Exception:
                        raise EmbeddingServiceError(
                            "Failed to load the local embedding model."
                        ) from None
        return self._model

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of document texts (chunk order preserved)."""
        if not texts:
            return []
        try:
            vectors = self._get_model().encode(
                texts, batch_size=BATCH_SIZE, show_progress_bar=False
            )
        except EmbeddingServiceError:
            raise
        except Exception:
            raise EmbeddingServiceError("Failed to embed document chunks.") from None
        return [[float(value) for value in vector] for vector in vectors]

    def embed_query(self, text: str) -> list[float]:
        """Embed a single retrieval query."""
        if not text.strip():
            raise EmbeddingServiceError("Cannot embed an empty query.")
        try:
            vector = self._get_model().encode(text, show_progress_bar=False)
        except EmbeddingServiceError:
            raise
        except Exception:
            raise EmbeddingServiceError("Failed to embed the query.") from None
        return [float(value) for value in vector]


# Application-level singleton: the model is loaded once per process.
_default_service: EmbeddingService | None = None
_default_service_lock = threading.Lock()


def get_default_embedding_service() -> EmbeddingService:
    """Return the shared EmbeddingService (created on first call)."""
    global _default_service
    if _default_service is None:
        with _default_service_lock:
            if _default_service is None:
                _default_service = EmbeddingService()
    return _default_service

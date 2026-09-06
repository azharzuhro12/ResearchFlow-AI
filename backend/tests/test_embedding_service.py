"""Tests for the embedding service.

The Sentence Transformers model is fully mocked — no model download,
no torch, no cost.
"""

import pytest

from app.services.embedding_service import (
    EmbeddingService,
    EmbeddingServiceError,
)


class FakeModel:
    """Deterministic stand-in for SentenceTransformer: 3-dim vectors."""

    def __init__(self) -> None:
        self.encode_calls = 0

    def encode(self, texts, batch_size=None, show_progress_bar=None):
        self.encode_calls += 1
        if isinstance(texts, str):
            return self._vector(texts)
        return [self._vector(text) for text in texts]

    @staticmethod
    def _vector(text: str) -> list[float]:
        return [float(len(text) % 7), 1.0, -0.5]


@pytest.fixture
def fake_model() -> FakeModel:
    return FakeModel()


def test_embed_documents_returns_vectors_in_order(fake_model: FakeModel) -> None:
    service = EmbeddingService(model_factory=lambda name: fake_model)

    vectors = service.embed_documents(["alpha", "longer text", "x"])

    assert len(vectors) == 3
    assert all(isinstance(value, float) for vector in vectors for value in vector)
    assert vectors[0] == fake_model._vector("alpha")  # order preserved
    assert vectors[1] == fake_model._vector("longer text")


def test_embed_documents_empty_input_returns_empty(fake_model: FakeModel) -> None:
    service = EmbeddingService(model_factory=lambda name: fake_model)

    assert service.embed_documents([]) == []
    assert fake_model.encode_calls == 0  # model not even called


def test_embed_query_returns_vector(fake_model: FakeModel) -> None:
    service = EmbeddingService(model_factory=lambda name: fake_model)

    vector = service.embed_query("what is RAG?")

    assert vector == fake_model._vector("what is RAG?")


def test_embed_empty_query_raises(fake_model: FakeModel) -> None:
    service = EmbeddingService(model_factory=lambda name: fake_model)

    with pytest.raises(EmbeddingServiceError):
        service.embed_query("   ")


def test_model_is_loaded_once_and_reused(fake_model: FakeModel) -> None:
    load_count = 0

    def factory(name: str) -> FakeModel:
        nonlocal load_count
        load_count += 1
        return fake_model

    service = EmbeddingService(model_factory=factory)
    service.embed_documents(["one"])
    service.embed_query("two")
    service.embed_documents(["three", "four"])

    assert load_count == 1  # singleton per service instance


def test_model_load_failure_returns_503() -> None:
    def broken_factory(name: str):
        raise RuntimeError("torch missing")

    service = EmbeddingService(model_factory=broken_factory)

    with pytest.raises(EmbeddingServiceError) as exc_info:
        service.embed_documents(["text"])

    assert exc_info.value.http_status == 503


def test_encode_failure_is_wrapped(fake_model: FakeModel) -> None:
    def bad_encode(texts, **kwargs):
        raise ValueError("inference failed")

    fake_model.encode = bad_encode
    service = EmbeddingService(model_factory=lambda name: fake_model)

    with pytest.raises(EmbeddingServiceError) as exc_info:
        service.embed_documents(["text"])

    assert "embed" in exc_info.value.message.lower()

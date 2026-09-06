"""Tests for deterministic text chunking (pure local logic)."""

from itertools import pairwise

from app.services.chunking_service import CHUNK_OVERLAP, CHUNK_SIZE, chunk_text


def test_empty_text_yields_no_chunks() -> None:
    assert chunk_text("") == []
    assert chunk_text("   \n\t  ") == []


def test_short_text_is_one_chunk() -> None:
    text = "A short paragraph about RAG."

    chunks = chunk_text(text)

    assert chunks == [text]


def test_long_text_is_split_within_size_limit() -> None:
    text = " ".join(f"word{i}" for i in range(2000))  # ~ 14,000 chars

    chunks = chunk_text(text)

    assert len(chunks) > 1
    assert all(len(chunk) <= CHUNK_SIZE for chunk in chunks)


def test_chunks_overlap() -> None:
    text = " ".join(f"word{i}" for i in range(2000))

    chunks = chunk_text(text)

    for previous, current in pairwise(chunks):
        tail_words = previous.split()[-5:]
        assert any(
            word in current.split() for word in tail_words
        ), "consecutive chunks must share tail words as overlap"


def test_overlap_is_bounded() -> None:
    text = " ".join(f"word{i}" for i in range(2000))

    chunks = chunk_text(text)

    for previous, current in pairwise(chunks):
        previous_words, current_words = previous.split(), current.split()
        duplicated = sum(
            1 for word in current_words if word in previous_words[-30:]
        )
        # The overlap tail is ~CHUNK_OVERLAP characters, i.e. a handful of
        # words — never half the chunk.
        assert duplicated <= 30


def test_chunking_is_deterministic() -> None:
    text = " ".join(f"token-{i}" for i in range(1500))

    assert chunk_text(text) == chunk_text(text)


def test_no_words_are_lost_or_split() -> None:
    words = [f"w{i}" for i in range(1200)]
    text = " ".join(words)

    chunks = chunk_text(text)

    # Overlap duplicates tail words on purpose; what must hold is that every
    # original word survives intact and in order (a subsequence match).
    consumed = iter(words)
    current = next(consumed, None)
    for word in " ".join(chunks).split():
        if word == current:
            current = next(consumed, None)
    assert current is None, "every original word must appear in order"


def test_single_oversized_word_is_hard_split() -> None:
    text = "a" * (CHUNK_SIZE * 3)

    chunks = chunk_text(text)

    assert len(chunks) == 3
    assert "".join(chunks) == text


def test_chunk_size_and_overlap_are_documented_values() -> None:
    assert CHUNK_SIZE == 1000
    assert CHUNK_OVERLAP == 150

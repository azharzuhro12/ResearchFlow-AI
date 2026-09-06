"""Deterministic text chunking for RAG indexing.

Splits cleaned text into overlapping chunks on word boundaries:

- CHUNK_SIZE is the target maximum in characters (words are never split;
  a single oversized word is character-split as a last resort).
- CHUNK_OVERLAP is the target number of leading characters repeated from
  the previous chunk, so sentences crossing a boundary stay retrievable.

Both parameters are documented constants — same input, same chunks, always.
"""

CHUNK_SIZE = 1000
CHUNK_OVERLAP = 150


def chunk_text(text: str) -> list[str]:
    """Split `text` into overlapping word-boundary chunks (deterministic)."""
    words = text.split()
    if not words:
        return []

    # Fast path: short texts stay as one chunk.
    if len(text) <= CHUNK_SIZE:
        return [text.strip()] if text.strip() else []

    chunks: list[str] = []
    start = 0  # index into `words`
    while start < len(words):
        chunk_words, length = [], 0
        for index in range(start, len(words)):
            addition = len(words[index]) + (1 if chunk_words else 0)
            if length + addition > CHUNK_SIZE and chunk_words:
                break
            chunk_words.append(words[index])
            length += addition
        chunk = " ".join(chunk_words)

        if len(chunk) > CHUNK_SIZE:  # single oversized word — hard split
            for position in range(0, len(chunk), CHUNK_SIZE):
                piece = chunk[position : position + CHUNK_SIZE]
                if piece.strip():
                    chunks.append(piece)
        else:
            chunks.append(chunk)

        # Next chunk starts back inside the tail so consecutive chunks overlap.
        if start + len(chunk_words) >= len(words):
            break
        start = _advance_with_overlap(chunk_words, start)

    return chunks


def _advance_with_overlap(chunk_words: list[str], start: int) -> int:
    """Return the word index to start the next chunk from.

    Steps past the whole chunk, then rewinds until ~CHUNK_OVERLAP characters
    of context are retained. Always advances by at least one word.
    """
    next_start = start + len(chunk_words)
    overlap_chars = 0
    while next_start > start + 1 and overlap_chars < CHUNK_OVERLAP:
        next_start -= 1
        # words[next_start] just became part of the overlap tail
        overlap_chars += len(chunk_words[next_start - start]) + 1
    return next_start

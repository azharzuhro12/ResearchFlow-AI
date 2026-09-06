"""Deterministic citation parsing and validation for synthesis answers.

Evidence citations look like [E1], [E1][E2], or [E1, E2]. Parsing is pure
regex on the answer text — it never depends on the LLM self-reporting its
citations, and it never uses eval().
"""

import re

# A bracketed reference group: [E1], [E1, E2], [E1; E2] (case-insensitive).
_CITATION_GROUP_RE = re.compile(r"\[\s*E\d+(?:\s*[,;]\s*E\d+)*\s*]", re.IGNORECASE)
# A single evidence ID inside (or outside) a group.
_EVIDENCE_ID_RE = re.compile(r"E\d+", re.IGNORECASE)
# Whitespace left behind after removing citations.
_SPACING_CLEANUP_RE = re.compile(r"[ \t]{2,}")


def format_evidence_id(index: int) -> str:
    """Human-readable evidence ID for a 1-based position (1 -> 'E1')."""
    return f"E{index}"


def extract_citations(text: str) -> list[str]:
    """Return every evidence ID referenced in `text`, uppercase, in order
    of first appearance, without duplicates."""
    seen: set[str] = set()
    ordered: list[str] = []
    for group in _CITATION_GROUP_RE.finditer(text):
        for match in _EVIDENCE_ID_RE.finditer(group.group(0)):
            evidence_id = match.group(0).upper()
            if evidence_id not in seen:
                seen.add(evidence_id)
                ordered.append(evidence_id)
    return ordered


def validate_citations(
    cited: list[str], valid_ids: list[str] | set[str]
) -> tuple[list[str], list[str]]:
    """Split cited IDs into (valid, invalid), preserving order, deduplicated.

    Valid IDs are the ones actually present in the current evidence context
    (E1...En). Anything else — e.g. [E999] when only E1–E5 exist — is invalid
    and must never surface as a citation.
    """
    allowed = set(valid_ids)
    seen: set[str] = set()
    valid: list[str] = []
    invalid: list[str] = []
    for evidence_id in cited:
        if evidence_id in seen:
            continue
        seen.add(evidence_id)
        (valid if evidence_id in allowed else invalid).append(evidence_id)
    return valid, invalid


def strip_citations(text: str, ids_to_remove: list[str]) -> str:
    """Remove the given evidence references from `text`, safely.

    Handles every bracket style: [E999] disappears entirely, and inside a
    group like [E1, E999] only E999 is removed (leaving [E1]). Whitespace
    around removed citations is tidied up.
    """
    removal = {evidence_id.upper() for evidence_id in ids_to_remove}
    if not removal:
        return text

    def _filter_group(match: re.Match[str]) -> str:
        inner_ids = _EVIDENCE_ID_RE.findall(match.group(0))
        kept = [i.upper() for i in inner_ids if i.upper() not in removal]
        if not kept:
            return ""
        return "[" + ", ".join(kept) + "]"

    cleaned = _CITATION_GROUP_RE.sub(_filter_group, text)
    cleaned = _SPACING_CLEANUP_RE.sub(" ", cleaned)
    # Tidy gaps left before punctuation, e.g. "claims ." -> "claims."
    cleaned = re.sub(r"\s+([.,;:!?])", r"\1", cleaned)
    return cleaned

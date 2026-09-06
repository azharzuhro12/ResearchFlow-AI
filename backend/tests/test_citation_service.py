"""Tests for the deterministic citation parser/validator (pure regex, no LLM)."""

from app.services.citation_service import (
    extract_citations,
    format_evidence_id,
    strip_citations,
    validate_citations,
)


def test_single_citation() -> None:
    assert extract_citations("Hybrid retrieval works well [E1].") == ["E1"]


def test_adjacent_citations() -> None:
    text = "Modern RAG systems increasingly use hybrid retrieval [E1][E2]."
    assert extract_citations(text) == ["E1", "E2"]


def test_comma_group_citation() -> None:
    assert extract_citations("Claims supported by both [E1, E2].") == ["E1", "E2"]


def test_semicolon_group_citation() -> None:
    assert extract_citations("Supported broadly [E3; E1].") == ["E3", "E1"]


def test_no_citations() -> None:
    assert extract_citations("No evidence markers anywhere here.") == []


def test_unknown_high_id_is_extracted_then_validated() -> None:
    # The parser is value-agnostic: E999 is extracted...
    assert extract_citations("Fabricated [E999].") == ["E999"]
    # ...and rejected by the validator when only E1-E5 exist.
    valid, invalid = validate_citations(["E999"], ["E1", "E2", "E3", "E4", "E5"])
    assert valid == []
    assert invalid == ["E999"]


def test_mixed_valid_and_invalid() -> None:
    text = "Grounded claim [E1][E999] and more [E2, E999]."
    cited = extract_citations(text)
    assert cited == ["E1", "E999", "E2"]

    valid, invalid = validate_citations(cited, ["E1", "E2"])
    assert valid == ["E1", "E2"]
    assert invalid == ["E999"]


def test_citations_are_deduplicated_in_order() -> None:
    text = "First [E2] then [E1] again [E2] and [E1, E1]."
    assert extract_citations(text) == ["E2", "E1"]


def test_lowercase_citation_is_normalized() -> None:
    assert extract_citations("supported by [e1]") == ["E1"]


def test_validate_citations_preserves_order_and_dedupes() -> None:
    valid, invalid = validate_citations(
        ["E2", "E9", "E2", "E1", "E9"], ["E1", "E2", "E3"]
    )
    assert valid == ["E2", "E1"]
    assert invalid == ["E9"]


def test_strip_removes_whole_bracket_for_invalid_only() -> None:
    text = "Some claim [E999]. Another sentence."
    assert strip_citations(text, ["E999"]) == "Some claim. Another sentence."


def test_strip_keeps_valid_inside_mixed_group() -> None:
    text = "Supported by [E1, E999] together."
    assert strip_citations(text, ["E999"]) == "Supported by [E1] together."


def test_strip_handles_adjacent_mix() -> None:
    text = "Hybrid retrieval combines lexical and semantic search [E1][E999]."
    assert (
        strip_citations(text, ["E999"])
        == "Hybrid retrieval combines lexical and semantic search [E1]."
    )


def test_strip_noop_when_nothing_to_remove() -> None:
    text = "Untouched [E1] text."
    assert strip_citations(text, ["E999"]) == text


def test_format_evidence_id() -> None:
    assert format_evidence_id(1) == "E1"
    assert format_evidence_id(12) == "E12"

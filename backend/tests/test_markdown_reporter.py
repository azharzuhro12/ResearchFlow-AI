"""Tests for the Markdown reporter (deterministic, no LLM, no GLM)."""

from app.services.markdown_reporter import (
    MarkdownReporter,
    classify_answer,
    render_markdown,
    split_answer_sections,
)
from app.services.synthesis_service import (
    CitationRecord,
    SynthesisOutcome,
)


def _citation(evidence_id: str, position: int) -> CitationRecord:
    return CitationRecord(
        evidence_id=evidence_id,
        source_title=f"Research Source {position}",
        source_url=f"https://example.com/source-{position}",
        source_domain="example.com",
        chunk_index=position,
    )


def _outcome(**overrides) -> SynthesisOutcome:
    defaults: dict = {
        "status": "success",
        "query": "What are the latest RAG techniques?",
        "answer": (
            "## Ringkasan\n"
            "Retrieval hybrid menjadi tren dominan.\n\n"
            "## Temuan Utama\n"
            "Pencarian hybrid meningkatkan recall [E1].\n\n"
            "Reranking menaikkan presisi [E2].\n\n"
            "## Kesimpulan\n"
            "Pipeline RAG terus membaik."
        ),
        "citations": [_citation("E1", 1), _citation("E2", 2)],
        "evidence_count": 2,
    }
    defaults.update(overrides)
    return SynthesisOutcome(**defaults)


# ------------------------------------------------------------------ structure


def test_report_has_required_sections() -> None:
    markdown = render_markdown(_outcome())

    for heading in (
        "# Laporan Riset",
        "**Pertanyaan Riset:**",
        "## Ringkasan Eksekutif",
        "## Temuan Utama",
        "## Analisis Rinci",
        "## Kesimpulan",
        "## Sumber",
    ):
        assert heading in markdown


def test_report_carries_the_research_question() -> None:
    assert "What are the latest RAG techniques?" in render_markdown(_outcome())


def test_detailed_analysis_contains_complete_answer_verbatim() -> None:
    answer = "First claim [E1].\n\nSecond claim [E2]."
    markdown = render_markdown(_outcome(answer=answer))

    assert answer in markdown  # complete, word for word, markers preserved


def test_answer_citation_markers_preserved() -> None:
    markdown = render_markdown(_outcome())
    assert "[E1]" in markdown
    assert "[E2]" in markdown


# ------------------------------------------------------------------- sources


def test_sources_list_validated_metadata_only() -> None:
    markdown = render_markdown(_outcome())

    assert "### [E1] Research Source 1" in markdown
    assert "https://example.com/source-1" in markdown
    assert "example.com" in markdown
    assert "Chunk: 1" in markdown


def test_sources_empty_when_no_citations() -> None:
    markdown = render_markdown(
        _outcome(answer="Ungrounded claim.", citations=[])
    )
    assert "Tidak ada sitasi tervalidasi" in markdown


# ---------------------------------------------------------- honest statuses


def test_insufficient_evidence_report_is_explicit() -> None:
    markdown = render_markdown(
        SynthesisOutcome(
            status="insufficient_evidence",
            query="What are the latest RAG techniques?",
            answer="There is not enough indexed evidence.",
            citations=[],
            evidence_count=0,
        )
    )

    assert "Bukti Belum Cukup" in markdown
    assert "## Sumber" in markdown
    # No fabricated findings or citations.
    assert "### [E" not in markdown


def test_ungrounded_report_carries_caution_banner() -> None:
    markdown = render_markdown(
        _outcome(status="ungrounded", answer="Claim without citations.", citations=[])
    )

    assert "Tanpa Grounding" in markdown
    # Still renders the analysis; it is simply flagged, not hidden.
    assert "Claim without citations." in markdown


# --------------------------------------------------- hostile / messy inputs


def test_malicious_question_cannot_inject_markdown_structure() -> None:
    malicious = "RAG research\n\n# FAKE HEADING\n\n<script>alert(1)</script>"
    markdown = render_markdown(_outcome(query=malicious))

    assert "# Laporan Riset" in markdown
    # The question is collapsed to one escaped line: the fake heading can
    # only appear mid-line (never at line start), so it renders as text.
    assert "\n# FAKE HEADING" not in markdown
    assert "# FAKE HEADING" in markdown  # still visible, but inert
    # '<' is escaped, so CommonMark renders the tag as literal text.
    assert "\\<script>alert(1)\\</script>" in markdown


def test_special_characters_in_titles_are_escaped() -> None:
    markdown = render_markdown(
        _outcome(
            citations=[
                CitationRecord(
                    evidence_id="E1",
                    source_title="Weird *Title* [with] _markup_",
                    source_url="https://example.com/a(b)",
                    source_domain="example.com",
                    chunk_index=0,
                )
            ]
        )
    )

    assert "\\*Title\\*" in markdown
    assert "\\[with\\]" in markdown
    # Parentheses in a bare URL would break Markdown links — neutralized.
    assert "%28" in markdown and "%29" in markdown


def test_unicode_answer_survives_markdown() -> None:
    answer = "Café research — naïve “quotes” and → arrows [E1]."
    assert answer in render_markdown(_outcome(answer=answer))


# --------------------------------------------------------- section parsing


def test_split_answer_sections_and_classify() -> None:
    sections, preamble = split_answer_sections(
        "Intro text.\n\n## Summary\nShort.\n\n## Conclusion\nDone."
    )
    assert preamble == "Intro text."
    assert [s.heading for s in sections] == ["Summary", "Conclusion"]

    slots = classify_answer(sections, preamble)
    assert slots["summary"] == "Short."
    assert slots["conclusion"] == "Done."
    # No findings heading -> deterministic empty slot, not invented text.
    assert slots["findings"] == ""

    # Indonesian headings (the prompt's primary output) classify as well.
    id_sections, _ = split_answer_sections(
        "Pengantar.\n\n## Ringkasan\nSingkat.\n\n## Temuan Utama\nTemuan.\n\n## Kesimpulan\nSelesai."
    )
    id_slots = classify_answer(id_sections, "Pengantar.")
    assert id_slots["summary"] == "Singkat."
    assert id_slots["findings"] == "Temuan."
    assert id_slots["conclusion"] == "Selesai."


def test_classify_falls_back_to_first_paragraph() -> None:
    sections, preamble = split_answer_sections("First paragraph.\n\nSecond paragraph.")
    slots = classify_answer(sections, preamble)
    assert slots["summary"] == "First paragraph."
    assert "tidak menghasilkan bagian kesimpulan eksplisit" in slots["conclusion"]


def test_markdown_reporter_facade_matches_function() -> None:
    assert MarkdownReporter().render(_outcome()) == render_markdown(_outcome())


def test_render_is_deterministic() -> None:
    assert render_markdown(_outcome()) == render_markdown(_outcome())

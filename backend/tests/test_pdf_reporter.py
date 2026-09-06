"""Tests for the PDF reporter — real local files, fully offline (no GLM)."""

from pathlib import Path

from app.services.pdf_reporter import PDFReporter
from app.services.synthesis_service import CitationRecord, SynthesisOutcome


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
            "## Executive Summary\n"
            "Hybrid retrieval is the dominant trend [E1].\n\n"
            "Reranking lifts precision [E2]."
        ),
        "citations": [_citation("E1", 1), _citation("E2", 2)],
        "evidence_count": 2,
    }
    defaults.update(overrides)
    return SynthesisOutcome(**defaults)


def _render(tmp_path: Path, outcome: SynthesisOutcome) -> Path:
    output = tmp_path / "report.pdf"
    return PDFReporter().render(outcome, output)


def test_pdf_file_is_created_non_empty(tmp_path: Path) -> None:
    output = _render(tmp_path, _outcome())

    assert output.is_file()
    assert output.stat().st_size > 0


def test_pdf_has_valid_signature(tmp_path: Path) -> None:
    output = _render(tmp_path, _outcome())

    assert output.read_bytes()[:5] == b"%PDF-"


def test_pdf_for_insufficient_evidence(tmp_path: Path) -> None:
    output = _render(
        tmp_path,
        SynthesisOutcome(
            status="insufficient_evidence",
            query="What are the latest RAG techniques?",
            answer="Not enough indexed evidence.",
            citations=[],
            evidence_count=0,
        ),
    )

    assert output.read_bytes()[:5] == b"%PDF-"


def test_pdf_for_ungrounded_answer(tmp_path: Path) -> None:
    output = _render(
        tmp_path,
        _outcome(status="ungrounded", answer="No citations.", citations=[]),
    )

    assert output.stat().st_size > 0


def test_pdf_survives_unicode_text(tmp_path: Path) -> None:
    # Unicode that Helvetica/Latin-1 cannot encode must not crash the build.
    output = _render(
        tmp_path,
        _outcome(
            answer="Café — “quotes”, → arrows, CJK 研究, emoji 🚀 [E1].",
        ),
    )

    assert output.read_bytes()[:5] == b"%PDF-"


def test_pdf_survives_xml_hostile_text(tmp_path: Path) -> None:
    # Angle brackets and quotes must not break ReportLab's mini-XML parser.
    output = _render(
        tmp_path,
        _outcome(
            query="Is <script>alert('x')</script> dangerous?",
            answer="A < b & c > d in XML terms [E1].",
        ),
    )

    assert output.read_bytes()[:5] == b"%PDF-"

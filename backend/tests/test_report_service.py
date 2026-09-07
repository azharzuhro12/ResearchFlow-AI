"""Tests for report ids, filename safety, and the ReportService."""

from pathlib import Path

import pytest

from app.services.markdown_reporter import MarkdownReporter
from app.services.pdf_reporter import PDFReporter
from app.services.report_service import (
    FILENAME_PREFIX,
    MAX_SLUG_LENGTH,
    REPORT_ID_PATTERN,
    ReportService,
    ReportServiceError,
    build_report_id,
    report_path,
    slugify_question,
)
from app.services.synthesis_service import CitationRecord, SynthesisOutcome


def _outcome(**overrides) -> SynthesisOutcome:
    defaults: dict = {
        "status": "success",
        "query": "What are the latest RAG techniques?",
        "answer": "Hybrid retrieval dominates [E1].",
        "citations": [
            CitationRecord(
                evidence_id="E1",
                source_title="Research Source 1",
                source_url="https://example.com/source-1",
                source_domain="example.com",
                chunk_index=0,
            )
        ],
        "evidence_count": 1,
    }
    defaults.update(overrides)
    return SynthesisOutcome(**defaults)


# ------------------------------------------------------------ slug / id safety


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("What are the latest RAG techniques?", "what-are-the-latest-rag-techniques"),
        ("  SPIKY   question!!  ", "spiky-question"),
        ("../../etc/passwd", "etc-passwd"),  # separators stripped, not joined
        ("..\\..\\windows\\system32", "windows-system32"),
        ("a/b/c/d/e", "a-b-c-d-e"),
        ("nul\x00byte\x00question", "nul-byte-question"),
        ("", "report"),
        ("!!!???***", "report"),
        ("JS 検索 テスト", "js"),
    ],
)
def test_slugify_never_produces_unsafe_path(question: str, expected: str) -> None:
    slug = slugify_question(question)
    assert slug == expected
    assert slug == slug.strip("-")
    assert "/" not in slug and "\\" not in slug and "." not in slug
    assert "\x00" not in slug


def test_slug_is_length_capped() -> None:
    slug = slugify_question("word " * 100)
    assert len(slug) <= MAX_SLUG_LENGTH
    assert slug.startswith("word-word")


def test_build_report_id_shape_and_uniqueness() -> None:
    ids = {build_report_id("What are the latest RAG techniques?") for _ in range(200)}

    # Crypto-safe suffix -> no collisions across 200 builds.
    assert len(ids) == 200
    for report_id in ids:
        assert REPORT_ID_PATTERN.match(report_id)
        assert report_id.startswith("what-are-the-latest-rag-techniques_")


@pytest.mark.parametrize(
    "bad_id",
    [
        "../../etc/passwd_12345678",
        "slug/extra_12345678",
        "slug\\extra_12345678",
        "slug_1234567",  # 7 hex chars
        "slug_123456789",  # 9 hex chars
        "SLUG_12345678",  # uppercase
        "slugUU_12345678",  # uppercase hex
        ".._12345678",
        ".hidden_12345678",
        "slug_12345678.pdf",  # smuggled extension
        "slug_12345678.md",
        "a" * 100 + "_12345678",  # over length cap
        "slug_x12345678",  # non-hex suffix
        "",
    ],
)
def test_report_path_rejects_malformed_ids(tmp_path: Path, bad_id: str) -> None:
    assert report_path(tmp_path, bad_id, ".md") is None
    assert report_path(tmp_path, bad_id, ".pdf") is None


def test_report_path_rejects_unexpected_extensions(tmp_path: Path) -> None:
    good_id = "valid-slug_12345678"
    for extension in (".exe", ".sh", ".txt", ".py", "", ".MD"):
        assert report_path(tmp_path, good_id, extension) is None


def test_report_path_builds_internal_path(tmp_path: Path) -> None:
    path = report_path(tmp_path, "valid-slug_12345678", ".md")
    assert path == tmp_path.resolve() / f"{FILENAME_PREFIX}valid-slug_12345678.md"


# ------------------------------------------------------------------- service


def test_generate_writes_markdown_and_pdf(tmp_path: Path) -> None:
    service = ReportService(reports_dir=tmp_path)

    result = service.generate(_outcome())

    assert REPORT_ID_PATTERN.match(result.report_id)
    markdown = tmp_path / result.markdown_filename
    pdf = tmp_path / result.pdf_filename
    assert markdown.is_file() and markdown.stat().st_size > 0
    assert pdf.is_file() and pdf.read_bytes()[:5] == b"%PDF-"
    assert result.markdown_filename == f"{FILENAME_PREFIX}{result.report_id}.md"
    assert result.pdf_filename == f"{FILENAME_PREFIX}{result.report_id}.pdf"
    # No other files leaked into the directory.
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        result.markdown_filename,
        result.pdf_filename,
    ]


def test_generate_insufficient_evidence_still_reports(tmp_path: Path) -> None:
    service = ReportService(reports_dir=tmp_path)

    result = service.generate(
        _outcome(
            status="insufficient_evidence",
            answer="Not enough evidence.",
            citations=[],
            evidence_count=0,
        )
    )

    markdown = (tmp_path / result.markdown_filename).read_text()
    assert "Bukti Belum Cukup" in markdown
    assert (tmp_path / result.pdf_filename).read_bytes()[:5] == b"%PDF-"


def test_generate_ungrounded_still_reports(tmp_path: Path) -> None:
    service = ReportService(reports_dir=tmp_path)

    result = service.generate(
        _outcome(status="ungrounded", citations=[])
    )

    markdown = (tmp_path / result.markdown_filename).read_text()
    assert "Tanpa Grounding" in markdown
    assert (tmp_path / result.pdf_filename).stat().st_size > 0


def test_generate_creates_missing_reports_dir(tmp_path: Path) -> None:
    reports_dir = tmp_path / "nested" / "reports"
    service = ReportService(reports_dir=reports_dir)

    result = service.generate(_outcome())

    assert (reports_dir / result.markdown_filename).is_file()


def test_generate_cleans_up_orphan_markdown_when_pdf_fails(tmp_path: Path) -> None:
    class ExplodingPDFReporter(PDFReporter):
        def render(self, outcome, output_path):
            raise RuntimeError("PDF engine exploded")

    service = ReportService(
        markdown_reporter=MarkdownReporter(),
        pdf_reporter=ExplodingPDFReporter(),
        reports_dir=tmp_path,
    )

    with pytest.raises(ReportServiceError) as exc_info:
        service.generate(_outcome())

    assert exc_info.value.http_status == 500
    assert list(tmp_path.iterdir()) == []  # the orphan .md was removed


def test_generate_markdown_write_failure_is_safe(tmp_path: Path) -> None:
    class ExplodingMarkdownReporter(MarkdownReporter):
        def render(self, outcome):
            raise RuntimeError("markdown engine exploded")

    service = ReportService(
        markdown_reporter=ExplodingMarkdownReporter(),
        reports_dir=tmp_path,
    )

    with pytest.raises(ReportServiceError) as exc_info:
        service.generate(_outcome())

    assert exc_info.value.http_status == 500
    assert list(tmp_path.iterdir()) == []

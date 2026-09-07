"""Report generation — validated synthesis → Markdown + PDF files.

The report generator never calls GLM: it consumes an already-validated
SynthesisOutcome from Step 5 and renders it with the deterministic
Markdown/PDF reporters. Files land in the local `reports/` directory under
filesystem-safe, collision-resistant names built from a sanitized slug of
the research question plus a cryptographically random suffix.
"""

import logging
import re
import secrets
from dataclasses import dataclass
from pathlib import Path

from app.core import config
from app.services.markdown_reporter import MarkdownReporter
from app.services.pdf_reporter import PDFReporter
from app.services.synthesis_service import SynthesisOutcome

logger = logging.getLogger(__name__)

# Longest slug (chars) taken from the research question.
MAX_SLUG_LENGTH = 40
# Random suffix length in hex characters (crypto-safe via `secrets`).
SUFFIX_HEX_LENGTH = 8
# A valid stored report id: slug + "_" + hex suffix, nothing else.
REPORT_ID_PATTERN = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?_[0-9a-f]{8}$"
)
FILENAME_PREFIX = "researchflow_"


class ReportServiceError(Exception):
    """Raised when report generation fails. `message` is client-safe."""

    def __init__(self, message: str, http_status: int = 500) -> None:
        super().__init__(message)
        self.message = message
        self.http_status = http_status


@dataclass(frozen=True)
class ReportResult:
    """Metadata about a generated report pair."""

    report_id: str
    markdown_filename: str
    pdf_filename: str


def slugify_question(question: str) -> str:
    """Sanitize a research question into a filesystem-safe slug.

    Keeps [a-z0-9] words joined by '-', caps the length, and never returns
    an empty string. Path separators, dots, null bytes, and any other
    unsafe characters are stripped — the slug cannot traverse.
    """
    words = re.findall(r"[a-z0-9]+", question.lower())
    slug = "-".join(words)[:MAX_SLUG_LENGTH].strip("-")
    return slug or "report"


def build_report_id(question: str) -> str:
    """Collision-resistant, filesystem-safe id: `<slug>_<hex8>`."""
    return f"{slugify_question(question)}_{secrets.token_hex(SUFFIX_HEX_LENGTH // 2)}"


def report_path(reports_dir: Path, report_id: str, extension: str) -> Path | None:
    """Resolve a validated report id to a path inside `reports_dir`.

    Returns None when the id is malformed or the resolved path escapes the
    reports directory (defense in depth — the regex already forbids every
    separator character).
    """
    if not REPORT_ID_PATTERN.match(report_id):
        return None
    if extension not in (".md", ".pdf"):
        return None
    root = reports_dir.resolve()
    candidate = (root / f"{FILENAME_PREFIX}{report_id}{extension}").resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


class ReportService:
    """Renders a validated synthesis outcome as Markdown + PDF on disk."""

    def __init__(
        self,
        markdown_reporter: MarkdownReporter | None = None,
        pdf_reporter: PDFReporter | None = None,
        reports_dir: Path | None = None,
    ) -> None:
        """Reporters and the output directory are injectable for tests."""
        self._markdown = markdown_reporter or MarkdownReporter()
        self._pdf = pdf_reporter or PDFReporter()
        self._reports_dir = reports_dir or config.REPORTS_DIR

    def generate(self, outcome: SynthesisOutcome) -> ReportResult:
        """Write `<prefix><report_id>.md` and `.pdf` into reports/.

        On partial failure the orphan file is removed so a report id never
        resolves to a half-written pair.
        """
        report_id = build_report_id(outcome.query)
        markdown_path = self._reports_dir / f"{FILENAME_PREFIX}{report_id}.md"
        pdf_path = self._reports_dir / f"{FILENAME_PREFIX}{report_id}.pdf"

        try:
            self._reports_dir.mkdir(parents=True, exist_ok=True)
            markdown_path.write_text(
                self._markdown.render(outcome), encoding="utf-8"
            )
        except Exception as exc:
            logger.exception("Failed to write the Markdown report")
            raise ReportServiceError(
                "Gagal menulis file laporan.", http_status=500
            ) from exc

        try:
            self._pdf.render(outcome, pdf_path)
        except Exception as exc:
            markdown_path.unlink(missing_ok=True)  # no half-written pairs
            logger.exception("Failed to render the PDF report")
            raise ReportServiceError(
                "Gagal membuat laporan PDF.", http_status=500
            ) from exc

        logger.info(
            "Generated report %s (synthesis status: %s)",
            report_id,
            outcome.status,
        )
        return ReportResult(
            report_id=report_id,
            markdown_filename=markdown_path.name,
            pdf_filename=pdf_path.name,
        )

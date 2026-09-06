"""Report metadata catalog — the service layer over ReportRepository (Step 9).

Every generated report (interactive POST /api/research/report runs AND
scheduled executions) gets one metadata row in SQLite: what was
generated, from which query, by which run, and under which filenames.
The Markdown/PDF files themselves stay in ``reports/`` and are never
managed (or deleted) through this catalog.

Layering: API → ReportCatalogService → ReportRepository → SQLAlchemy →
SQLite. The API layer never queries the ORM directly.
"""

import logging
from collections.abc import Sequence
from datetime import datetime

from app.database.database import PersistenceError, session_scope
from app.database.models import ReportRow
from app.database.repositories import ReportRepository

logger = logging.getLogger(__name__)


class ReportCatalogError(Exception):
    """The report catalog could not be read/written (→ HTTP 503).

    `message` is client-safe: no SQL, no paths, no stack traces.
    """

    def __init__(
        self, message: str = "Report storage is temporarily unavailable."
    ) -> None:
        super().__init__(message)
        self.message = message


class ReportCatalogService:
    """Records and lists report metadata backed by SQLite."""

    def record_report(
        self,
        *,
        report_id: str,
        schedule_id: str | None,
        execution_id: str | None,
        query: str,
        markdown_filename: str,
        pdf_filename: str,
        created_at: datetime,
        synthesis_status: str,
    ) -> None:
        """Persist metadata for one generated report pair (idempotent).

        Raises ReportCatalogError on storage failure — callers decide
        whether that is fatal (nothing is fatal for already-written files).
        """
        try:
            with session_scope() as session:
                ReportRepository(session).create(
                    report_id=report_id,
                    schedule_id=schedule_id,
                    execution_id=execution_id,
                    query=query,
                    markdown_filename=markdown_filename,
                    pdf_filename=pdf_filename,
                    created_at=created_at,
                    synthesis_status=synthesis_status,
                )
        except PersistenceError as exc:
            raise ReportCatalogError() from exc

    def list_reports(self, *, limit: int) -> Sequence[ReportRow]:
        """Most recently generated reports (newest first)."""
        try:
            with session_scope() as session:
                return ReportRepository(session).list_recent(limit=limit)
        except PersistenceError as exc:
            raise ReportCatalogError() from exc

    def find_report(self, report_id: str) -> ReportRow | None:
        """Look up one report's metadata (None when unknown).

        Never raises: download resolution treats catalog problems as
        "no metadata" and falls back to the deterministic path check.
        """
        try:
            with session_scope() as session:
                return ReportRepository(session).get(report_id)
        except PersistenceError:
            logger.warning("Report catalog lookup failed for a download")
            return None

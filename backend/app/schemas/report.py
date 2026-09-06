"""Pydantic schemas for report metadata APIs (Step 9).

These expose METADATA about generated reports only — identifiers and
filenames, never filesystem paths, never file contents.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

SynthesisStatus = Literal["success", "insufficient_evidence", "ungrounded"]


class ReportMetadata(BaseModel):
    """One persisted report record (metadata only; files stay in reports/)."""

    report_id: str
    query: str
    markdown_filename: str
    pdf_filename: str
    created_at: datetime
    synthesis_status: SynthesisStatus
    schedule_id: str | None = None
    execution_id: str | None = None


class ReportListResponse(BaseModel):
    """Response body for GET /api/research/reports."""

    status: Literal["success"] = "success"
    reports: list[ReportMetadata]
    total: int = Field(ge=0)

"""Pydantic schemas for the research APIs."""

from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator

MIN_QUESTION_LENGTH = 10
MAX_QUESTION_LENGTH = 2000


class ResearchPlanRequest(BaseModel):
    """Request body for POST /api/research/plan."""

    question: str = Field(
        min_length=MIN_QUESTION_LENGTH,
        max_length=MAX_QUESTION_LENGTH,
        description="The research question to turn into a plan.",
    )

    @field_validator("question", mode="before")
    @classmethod
    def strip_whitespace(cls, value: object) -> object:
        """Trim whitespace before length validation so blank input is rejected."""
        if isinstance(value, str):
            return value.strip()
        return value


class ResearchPlanResponse(BaseModel):
    """Response body for POST /api/research/plan."""

    status: Literal["success"] = "success"
    question: str
    plan: list[str] = Field(min_length=1)


class ResearchSearchRequest(ResearchPlanRequest):
    """Request body for POST /api/research/search (same question rules)."""


class SourceResult(BaseModel):
    """One discovered web source — metadata only, no content extraction yet."""

    title: str
    url: str
    snippet: str | None = None
    source: str | None = None
    published_at: str | None = None
    query: str | None = None


class ResearchSearchResponse(BaseModel):
    """Response body for POST /api/research/search."""

    status: Literal["success"] = "success"
    question: str
    queries: list[str] = Field(min_length=1)
    sources: list[SourceResult]
    total_sources: int = Field(ge=0)


# --- Step 4: RAG indexing & retrieval ---


MIN_QUERY_LENGTH = 3
MAX_SOURCES_PER_REQUEST = 50
MIN_TOP_K = 1
MAX_TOP_K = 20


class SourceToIndex(BaseModel):
    """One web source to fetch, chunk, embed, and index."""

    title: str = Field(min_length=1, max_length=500)
    url: str = Field(min_length=1, max_length=2000)
    snippet: str | None = None
    source: str | None = None
    published_at: str | None = None

    @field_validator("title", "url", mode="before")
    @classmethod
    def strip_fields(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("url")
    @classmethod
    def url_must_be_http(cls, value: str) -> str:
        """Only http(s) URLs are indexable (defence in depth — the extractor
        re-validates before any request is made)."""
        parts = urlsplit(value)
        if parts.scheme.lower() not in {"http", "https"} or not parts.netloc:
            raise ValueError("URL must be a valid http(s) URL")
        return value


class IndexSourcesRequest(BaseModel):
    """Request body for POST /api/research/index."""

    sources: list[SourceToIndex] = Field(
        min_length=1, max_length=MAX_SOURCES_PER_REQUEST
    )


class IndexSourcesResponse(BaseModel):
    """Response body for POST /api/research/index."""

    status: Literal["success"] = "success"
    indexed_sources: int = Field(ge=0)
    failed_sources: int = Field(ge=0)
    total_chunks: int = Field(ge=0)


class RetrieveRequest(BaseModel):
    """Request body for POST /api/research/retrieve."""

    query: str = Field(
        min_length=MIN_QUERY_LENGTH,
        max_length=MAX_QUESTION_LENGTH,
        description="The question to answer from the indexed knowledge base.",
    )
    top_k: int = Field(
        default=5, ge=MIN_TOP_K, le=MAX_TOP_K, description="How many chunks to return."
    )

    @field_validator("query", mode="before")
    @classmethod
    def strip_whitespace(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value


class RetrievedChunk(BaseModel):
    """One semantically retrieved chunk with its source provenance."""

    text: str
    source_title: str | None = None
    source_url: str
    source_domain: str | None = None
    chunk_index: int
    distance: float | None = None


class RetrieveResponse(BaseModel):
    """Response body for POST /api/research/retrieve."""

    status: Literal["success"] = "success"
    query: str
    results: list[RetrievedChunk]
    total_results: int = Field(ge=0)


# ------------------------------------------------------------- Step 5: synthesis


class SynthesisRequest(BaseModel):
    """Request body for POST /api/research/synthesize."""

    query: str = Field(
        min_length=MIN_QUERY_LENGTH,
        max_length=MAX_QUESTION_LENGTH,
        description="The research question to answer from indexed evidence.",
    )
    top_k: int = Field(
        default=5,
        ge=MIN_TOP_K,
        le=MAX_TOP_K,
        description="How many chunks to retrieve as evidence.",
    )

    @field_validator("query", mode="before")
    @classmethod
    def strip_whitespace(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value


class Citation(BaseModel):
    """A validated citation mapped to real retrieved-chunk metadata.

    Every field comes from ChromaDB metadata server-side — never from LLM
    output. The LLM only ever produces the evidence ID.
    """

    evidence_id: str
    source_title: str | None = None
    source_url: str
    source_domain: str | None = None
    chunk_index: int = 0


class SynthesisResponse(BaseModel):
    """Response body for POST /api/research/synthesize.

    status: "success" (grounded answer with citations),
            "insufficient_evidence" (nothing indexed for this query),
            "ungrounded" (answer produced but no citation could be validated).
    """

    status: Literal["success", "insufficient_evidence", "ungrounded"]
    query: str
    answer: str
    citations: list[Citation] = Field(default_factory=list)
    evidence_count: int = Field(ge=0)


# ------------------------------------------------------------- Step 6: reports


class ReportRequest(BaseModel):
    """Request body for POST /api/research/report.

    Same shape as SynthesisRequest: the endpoint runs the (single) synthesis
    first, then renders the validated outcome as Markdown + PDF.
    """

    query: str = Field(
        min_length=MIN_QUERY_LENGTH,
        max_length=MAX_QUESTION_LENGTH,
        description="The research question to build a report for.",
    )
    top_k: int = Field(
        default=5,
        ge=MIN_TOP_K,
        le=MAX_TOP_K,
        description="How many chunks to retrieve as synthesis evidence.",
    )

    @field_validator("query", mode="before")
    @classmethod
    def strip_whitespace(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value


class ReportResponse(BaseModel):
    """Response body for POST /api/research/report.

    Contains only filesystem-safe identifiers and prebuilt download URLs —
    never an absolute path, API key, or stack trace.
    """

    status: Literal["success"] = "success"
    report_id: str
    query: str
    synthesis_status: Literal["success", "insufficient_evidence", "ungrounded"]
    markdown_filename: str
    pdf_filename: str
    markdown_download_url: str
    pdf_download_url: str

"""Automated research execution — one full pipeline run for one question.

This is the orchestrator the scheduler (Step 7) calls. It does NOT
re-implement any stage: it composes the existing Step 3–6 services in the
documented pipeline order:

    question → search queries → web search → index → retrieve+synthesize
             → citation validation → Markdown + PDF report

A single `run()` results in exactly ONE synthesis operation (which itself
performs retrieval + citation validation) and ONE report generation — the
same guarantee the POST /api/research/report endpoint gives interactive
users. No stage is duplicated.
"""

import logging
from dataclasses import dataclass

from app.schemas.research import SourceResult, SourceToIndex
from app.services.markdown_reporter import classify_answer, split_answer_sections
from app.services.query_generator import QueryGenerator
from app.services.rag_service import RAGService, RAGServiceError
from app.services.report_service import ReportResult, ReportService, ReportServiceError
from app.services.search_service import SearchService, SearchServiceError
from app.services.synthesis_service import (
    SynthesisOutcome,
    SynthesisService,
    SynthesisServiceError,
)

logger = logging.getLogger(__name__)

# Evidence chunk budget for the single synthesis call (matches the API
# endpoints' default; MAX_EVIDENCE_CHUNKS still caps inside the service).
DEFAULT_TOP_K = 5


class ResearchExecutionError(Exception):
    """Raised when a pipeline stage fails. `message` is client-safe."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class CitationSummary:
    """Client-safe projection of one validated citation record.

    Built ONLY from the citation records SynthesisService already
    validated against retrieved chunk metadata — never from free-form
    LLM text. `url` therefore always points at a real indexed source.
    """

    evidence_id: str
    title: str
    url: str
    domain: str


@dataclass(frozen=True)
class ExecutionResult:
    """Client-safe summary of one completed pipeline run.

    The research-content fields (evidence_count / summary / findings /
    citations) are derived from the SAME SynthesisOutcome the report was
    generated from — no extra pipeline work, no second GLM call.
    """

    report_id: str
    synthesis_status: str
    sources_found: int
    indexed_sources: int
    failed_sources: int
    total_chunks: int
    evidence_count: int = 0
    summary: str = ""
    findings: str = ""
    citations: tuple[CitationSummary, ...] = ()


def _to_index_sources(sources: list[SourceResult]) -> list[SourceToIndex]:
    """Map search results (metadata) to indexable sources (same shape)."""
    return [
        SourceToIndex(
            title=source.title,
            url=source.url,
            snippet=source.snippet,
            source=source.source,
            published_at=source.published_at,
        )
        for source in sources
    ]


class ResearchExecutionService:
    """Runs the full research pipeline once, reusing the existing services."""

    def __init__(
        self,
        query_generator: QueryGenerator,
        search: SearchService,
        rag: RAGService,
        synthesis: SynthesisService,
        reports: ReportService,
    ) -> None:
        self._query_generator = query_generator
        self._search = search
        self._rag = rag
        self._synthesis = synthesis
        self._reports = reports

    async def run(self, question: str, top_k: int = DEFAULT_TOP_K) -> ExecutionResult:
        """Execute one full research run for `question`.

        Stage failures raise ResearchExecutionError with a client-safe
        message; the caller (scheduler) records the status and moves on.
        """
        # 1–2. Search queries (never raises — falls back internally) and
        # web search. All queries failing is a hard failure for this run.
        try:
            queries = await self._query_generator.generate_queries(question)
            sources = await self._search.collect_sources(queries)
        except SearchServiceError as exc:
            raise ResearchExecutionError(f"Pencarian gagal: {exc.message}") from None
        logger.info(
            "Research run: %d source(s) found via %d query(ies)",
            len(sources),
            len(queries),
        )

        # 3. Index usable sources (per-source failures are expected and
        # counted; infrastructure failures raise RAGServiceError).
        try:
            stats = await self._rag.index_sources(_to_index_sources(sources))
        except RAGServiceError as exc:
            raise ResearchExecutionError(f"Pengindeksan gagal: {exc.message}") from None

        # 4–5. ONE synthesis call: retrieval, GLM grounding, and citation
        # validation all happen inside SynthesisService.synthesize().
        try:
            outcome: SynthesisOutcome = await self._synthesis.synthesize(
                question, top_k
            )
        except SynthesisServiceError as exc:
            raise ResearchExecutionError(f"Sintesis gagal: {exc.message}") from None

        # 6. ONE report generation (Markdown + PDF) from the outcome above.
        try:
            result: ReportResult = self._reports.generate(outcome)
        except ReportServiceError as exc:
            raise ResearchExecutionError(f"Pembuatan laporan gagal: {exc.message}") from None

        # Research content for notifications — re-derived from the SAME
        # outcome the report used (same slot extractor as the reporters),
        # so Discord shows exactly what the report shows. No extra calls.
        sections, preamble = split_answer_sections(outcome.answer)
        slots = classify_answer(sections, preamble)
        return ExecutionResult(
            report_id=result.report_id,
            synthesis_status=outcome.status,
            sources_found=len(sources),
            indexed_sources=stats.indexed_sources,
            failed_sources=stats.failed_sources,
            total_chunks=stats.total_chunks,
            evidence_count=outcome.evidence_count,
            summary=slots["summary"],
            findings=slots["findings"],
            citations=tuple(
                CitationSummary(
                    evidence_id=citation.evidence_id,
                    title=citation.source_title or "",
                    url=citation.source_url,
                    domain=citation.source_domain or "",
                )
                for citation in outcome.citations
            ),
        )


def default_research_execution_service() -> ResearchExecutionService:
    """Wire the real (production) pipeline lazily.

    Called only when an execution actually starts, so listing schedules
    never loads the embedding model or touches ChromaDB.
    """
    # Local imports keep module import light and avoid import cycles.
    from app.services.content_extractor import ContentExtractor
    from app.services.embedding_service import get_default_embedding_service
    from app.services.glm_service import GLMService
    from app.services.search_provider import DuckDuckGoSearchProvider
    from app.services.vector_store import VectorStore

    glm = GLMService()
    rag = RAGService(
        ContentExtractor(), get_default_embedding_service(), VectorStore()
    )
    return ResearchExecutionService(
        query_generator=QueryGenerator(glm),
        search=SearchService(DuckDuckGoSearchProvider()),
        rag=rag,
        synthesis=SynthesisService(rag, glm),
        reports=ReportService(),
    )

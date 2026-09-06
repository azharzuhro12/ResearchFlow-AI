"""API routes for research operations."""

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse

from app.core import config
from app.database.database import from_db
from app.schemas.report import ReportListResponse, ReportMetadata
from app.schemas.research import (
    Citation,
    IndexSourcesRequest,
    IndexSourcesResponse,
    ReportRequest,
    ReportResponse,
    ResearchPlanRequest,
    ResearchPlanResponse,
    ResearchSearchRequest,
    ResearchSearchResponse,
    RetrievedChunk,
    RetrieveRequest,
    RetrieveResponse,
    SynthesisRequest,
    SynthesisResponse,
)
from app.services.content_extractor import ContentExtractor
from app.services.embedding_service import (
    EmbeddingService,
    get_default_embedding_service,
)
from app.services.glm_service import GLMService, GLMServiceError
from app.services.query_generator import QueryGenerator
from app.services.rag_service import RAGService, RAGServiceError
from app.services.report_catalog_service import (
    ReportCatalogError,
    ReportCatalogService,
)
from app.services.report_service import (
    FILENAME_PREFIX,
    REPORT_ID_PATTERN,
    ReportService,
    ReportServiceError,
)
from app.services.research_planner import ResearchPlanner, ResearchPlannerError
from app.services.search_provider import DuckDuckGoSearchProvider, SearchProvider
from app.services.search_service import SearchService, SearchServiceError
from app.services.synthesis_service import (
    SynthesisOutcome,
    SynthesisService,
    SynthesisServiceError,
)
from app.services.vector_store import VectorStore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/research", tags=["research"])


def get_glm_service() -> GLMService:
    """Provide the GLM client (override in tests)."""
    return GLMService()


def get_research_planner(
    glm: GLMService = Depends(get_glm_service),
) -> ResearchPlanner:
    """Provide the research planner (override in tests)."""
    return ResearchPlanner(glm)


@router.post("/plan", response_model=ResearchPlanResponse)
async def create_research_plan(
    request: ResearchPlanRequest,
    planner: ResearchPlanner = Depends(get_research_planner),
) -> ResearchPlanResponse:
    """Turn a research question into a structured research plan using GLM."""
    try:
        plan = await planner.generate_plan(request.question)
    except GLMServiceError as exc:
        # Client-safe message from the GLM service layer.
        raise HTTPException(status_code=exc.http_status, detail=exc.message) from None
    except ResearchPlannerError:
        raise HTTPException(
            status_code=502, detail="Unable to generate research plan."
        ) from None
    except Exception:
        # Log the real traceback server-side; return a generic message.
        logger.exception("Unexpected error while generating research plan")
        raise HTTPException(
            status_code=500, detail="Unable to generate research plan."
        ) from None
    return ResearchPlanResponse(question=request.question, plan=plan)


def get_query_generator(
    glm: GLMService = Depends(get_glm_service),
) -> QueryGenerator:
    """Provide the search query generator (override in tests)."""
    return QueryGenerator(glm)


def get_search_provider() -> SearchProvider:
    """Provide the web search provider (override in tests)."""
    return DuckDuckGoSearchProvider()


def get_search_service(
    provider: SearchProvider = Depends(get_search_provider),
) -> SearchService:
    """Provide the search orchestrator (override in tests)."""
    return SearchService(provider)


@router.post("/search", response_model=ResearchSearchResponse)
async def search_research_sources(
    request: ResearchSearchRequest,
    generator: QueryGenerator = Depends(get_query_generator),
    search_service: SearchService = Depends(get_search_service),
) -> ResearchSearchResponse:
    """Find web sources for a research question.

    Flow: generate search queries (GLM + fallback) → free web search →
    deduplicate by URL → return source metadata.
    """
    try:
        # Query generation never raises — it falls back to simple queries.
        queries = await generator.generate_queries(request.question)
        sources = await search_service.collect_sources(queries)
    except SearchServiceError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.message) from None
    except Exception:
        logger.exception("Unexpected error while searching research sources")
        raise HTTPException(
            status_code=500, detail="Unable to search the web. Please try again."
        ) from None
    return ResearchSearchResponse(
        question=request.question,
        queries=queries,
        sources=sources,
        total_sources=len(sources),
    )


def get_content_extractor() -> ContentExtractor:
    """Provide the page content extractor (override in tests)."""
    return ContentExtractor()


def get_rag_embedding_service() -> EmbeddingService:
    """Provide the shared local embedding service (model loaded once)."""
    return get_default_embedding_service()


def get_vector_store() -> VectorStore:
    """Provide the persistent ChromaDB-backed store (override in tests)."""
    return VectorStore()


def get_rag_service(
    extractor: ContentExtractor = Depends(get_content_extractor),
    embedder: EmbeddingService = Depends(get_rag_embedding_service),
    store: VectorStore = Depends(get_vector_store),
) -> RAGService:
    """Provide the RAG orchestrator (override in tests)."""
    return RAGService(extractor, embedder, store)


@router.post("/index", response_model=IndexSourcesResponse)
async def index_research_sources(
    request: IndexSourcesRequest,
    rag: RAGService = Depends(get_rag_service),
) -> IndexSourcesResponse:
    """Fetch, clean, chunk, embed, and store sources in the local vector DB.

    Extraction failures are per-source outcomes (counted in failed_sources);
    infrastructure failures (embedding model, ChromaDB) surface as 503.
    """
    try:
        stats = await rag.index_sources(request.sources)
    except RAGServiceError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.message) from None
    except Exception:
        logger.exception("Unexpected error while indexing sources")
        raise HTTPException(
            status_code=500, detail="Unable to index sources. Please try again."
        ) from None
    return IndexSourcesResponse(
        indexed_sources=stats.indexed_sources,
        failed_sources=stats.failed_sources,
        total_chunks=stats.total_chunks,
    )


@router.post("/retrieve", response_model=RetrieveResponse)
async def retrieve_research_chunks(
    request: RetrieveRequest,
    rag: RAGService = Depends(get_rag_service),
) -> RetrieveResponse:
    """Semantic search over the indexed knowledge base (local embeddings)."""
    try:
        records = await rag.retrieve(request.query, request.top_k)
    except RAGServiceError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.message) from None
    except Exception:
        logger.exception("Unexpected error while retrieving chunks")
        raise HTTPException(
            status_code=500, detail="Unable to retrieve sources. Please try again."
        ) from None
    results = [
        RetrievedChunk(
            text=record.text,
            source_title=record.source_title,
            source_url=record.source_url,
            source_domain=record.source_domain,
            chunk_index=record.chunk_index,
            distance=record.distance,
        )
        for record in records
    ]
    return RetrieveResponse(
        query=request.query, results=results, total_results=len(results)
    )


def get_synthesis_service(
    rag: RAGService = Depends(get_rag_service),
    glm: GLMService = Depends(get_glm_service),
) -> SynthesisService:
    """Provide the synthesis orchestrator (override in tests)."""
    return SynthesisService(rag, glm)


@router.post("/synthesize", response_model=SynthesisResponse)
async def synthesize_research_answer(
    request: SynthesisRequest,
    synthesis: SynthesisService = Depends(get_synthesis_service),
) -> SynthesisResponse:
    """Answer a research question from indexed evidence with validated citations.

    Flow: retrieve → assign evidence IDs → GLM synthesis → deterministic
    citation extraction/validation → map E-IDs to real ChromaDB metadata.
    No evidence returns 200 with status "insufficient_evidence" (GLM is
    not called).
    """
    try:
        outcome: SynthesisOutcome = await synthesis.synthesize(
            request.query, request.top_k
        )
    except SynthesisServiceError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.message) from None
    except Exception:
        logger.exception("Unexpected error while synthesizing research answer")
        raise HTTPException(
            status_code=500, detail="Unable to generate the research answer."
        ) from None
    return SynthesisResponse(
        status=outcome.status,
        query=outcome.query,
        answer=outcome.answer,
        citations=[
            Citation(
                evidence_id=citation.evidence_id,
                source_title=citation.source_title,
                source_url=citation.source_url,
                source_domain=citation.source_domain,
                chunk_index=citation.chunk_index,
            )
            for citation in outcome.citations
        ],
        evidence_count=outcome.evidence_count,
    )


# ---------------------------------------------------------- Step 6: reports


def get_report_service() -> ReportService:
    """Provide the report generator (override in tests)."""
    return ReportService()


def get_report_catalog_service() -> ReportCatalogService:
    """Provide the report metadata catalog (override in tests)."""
    return ReportCatalogService()


@router.post("/report", response_model=ReportResponse)
async def generate_research_report(
    request: ReportRequest,
    synthesis: SynthesisService = Depends(get_synthesis_service),
    reports: ReportService = Depends(get_report_service),
    catalog: ReportCatalogService = Depends(get_report_catalog_service),
) -> ReportResponse:
    """Generate a downloadable Markdown + PDF report for a research question.

    Synthesis runs exactly once here (the same Step 5 pipeline, no extra GLM
    call); the validated outcome is then rendered deterministically. An
    insufficient-evidence synthesis still produces an honest report that
    states the limitation instead of fabricating findings. Report metadata
    is persisted in SQLite (Step 9); if only that write fails, the report
    files still exist and the request still succeeds.
    """
    try:
        outcome: SynthesisOutcome = await synthesis.synthesize(
            request.query, request.top_k
        )
    except SynthesisServiceError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.message) from None
    except Exception:
        logger.exception("Unexpected error while synthesizing report content")
        raise HTTPException(
            status_code=500, detail="Unable to generate the research report."
        ) from None

    try:
        result = reports.generate(outcome)
    except ReportServiceError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.message) from None
    except Exception:
        logger.exception("Unexpected error while generating report files")
        raise HTTPException(
            status_code=500, detail="Unable to generate the research report."
        ) from None

    # Best-effort metadata persistence: the files are already written, so
    # a storage hiccup must not fail the request (scheduled executions
    # record the same metadata through the scheduler).
    try:
        catalog.record_report(
            report_id=result.report_id,
            schedule_id=None,
            execution_id=None,
            query=outcome.query,
            markdown_filename=result.markdown_filename,
            pdf_filename=result.pdf_filename,
            created_at=datetime.now(UTC),
            synthesis_status=outcome.status,
        )
    except ReportCatalogError:
        logger.warning(
            "Report files were generated but metadata could not be saved"
        )

    return ReportResponse(
        report_id=result.report_id,
        query=outcome.query,
        synthesis_status=outcome.status,
        markdown_filename=result.markdown_filename,
        pdf_filename=result.pdf_filename,
        markdown_download_url=(
            f"/api/research/reports/{result.report_id}/markdown"
        ),
        pdf_download_url=f"/api/research/reports/{result.report_id}/pdf",
    )


_DOWNLOAD_MEDIA_TYPES = {".md": "text/markdown", ".pdf": "application/pdf"}


def _report_file_response(report_id: str, extension: str) -> FileResponse:
    """Resolve a validated report id to a FileResponse, or raise 404.

    Step 9: the persisted report metadata is PREFERRED for resolving the
    expected filenames, with a deterministic fallback for reports that
    predate persistence. Security is unchanged: the id must match the
    strict report-id pattern, the stored filename is only ever ACCEPTED
    when it equals the deterministic `<prefix><id><ext>` name (so the
    catalog can confirm existence but never redirect a path), and the
    resolved path must stay inside the reports directory. There is no
    generic file-download endpoint in this API.
    """
    if not REPORT_ID_PATTERN.match(report_id) or extension not in (
        ".md",
        ".pdf",
    ):
        # Malformed or traversal-shaped id: same generic 404, no details.
        raise HTTPException(status_code=404, detail="Report not found.")

    expected_filename = f"{FILENAME_PREFIX}{report_id}{extension}"
    catalog = get_report_catalog_service()
    row = catalog.find_report(report_id)
    filename = expected_filename  # legacy/pre-catalog fallback
    if row is not None:
        stored = row.markdown_filename if extension == ".md" else row.pdf_filename
        if stored != expected_filename:
            # Stored name disagrees with the id: resolve nothing extra.
            logger.warning("Report metadata filename mismatch ignored")
            filename = expected_filename

    root = config.REPORTS_DIR.resolve()
    path = (root / filename).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        raise HTTPException(status_code=404, detail="Report not found.") from None
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Report not found.")
    return FileResponse(
        path,
        media_type=_DOWNLOAD_MEDIA_TYPES[extension],
        filename=expected_filename,
    )


@router.get("/reports", response_model=ReportListResponse)
async def list_reports(
    limit: int = Query(
        default=20, ge=1, le=100, description="How many reports to return."
    ),
    catalog: ReportCatalogService = Depends(get_report_catalog_service),
) -> ReportListResponse:
    """List generated report METADATA (newest first, Step 9).

    Identifiers and filenames only — never filesystem paths, never file
    contents, never absolute locations.
    """
    try:
        rows = catalog.list_reports(limit=limit)
    except ReportCatalogError as exc:
        raise HTTPException(status_code=503, detail=exc.message) from None
    return ReportListResponse(
        reports=[
            ReportMetadata(
                report_id=row.id,
                query=row.query,
                markdown_filename=row.markdown_filename,
                pdf_filename=row.pdf_filename,
                created_at=from_db(row.created_at),
                synthesis_status=row.synthesis_status,
                schedule_id=row.schedule_id,
                execution_id=row.execution_id,
            )
            for row in rows
        ],
        total=len(rows),
    )


@router.get("/reports/{report_id}/markdown")
async def download_report_markdown(report_id: str) -> FileResponse:
    """Download a generated report as Markdown (id validated, path internal)."""
    return _report_file_response(report_id, ".md")


@router.get("/reports/{report_id}/pdf")
async def download_report_pdf(report_id: str) -> FileResponse:
    """Download a generated report as PDF (id validated, path internal)."""
    return _report_file_response(report_id, ".pdf")

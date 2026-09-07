"""Tests for ResearchExecutionService — the automated pipeline orchestrator.

Every collaborator is faked; nothing here touches GLM, the web, the
embedding model, or ChromaDB. The tests verify orchestration ORDER, the
single-synthesis/single-report guarantee, and failure wrapping.
"""

import asyncio

import pytest

from app.schemas.research import SourceResult
from app.services.query_generator import QueryGenerator
from app.services.rag_service import IndexStats, RAGService, RAGServiceError
from app.services.report_service import ReportResult, ReportService, ReportServiceError
from app.services.research_execution_service import (
    ResearchExecutionError,
    ResearchExecutionService,
)
from app.services.search_service import SearchService, SearchServiceError
from app.services.synthesis_service import (
    CitationRecord,
    SynthesisOutcome,
    SynthesisService,
    SynthesisServiceError,
)

QUESTION = "What are the latest developments in Retrieval-Augmented Generation?"

# A synthesis answer shaped like the real Indonesian output (sectioned,
# with validated citation records) so research-content derivation has
# something realistic to chew on.
SECTIONED_ANSWER = (
    "## Ringkasan\n"
    "RAG berkembang pesat menuju kurasi sumber dan evaluasi grounding.\n\n"
    "## Temuan Utama\n"
    "- Kurasi sumber sebelum indeksasi menaikkan kualitas jawaban.\n"
    "- Evaluasi grounding kini standar de facto.\n\n"
    "## Kesimpulan\n"
    "Arah riset bergerak ke grounding yang terukur."
)
VALIDATED_CITATIONS = [
    CitationRecord(
        evidence_id="E1",
        source_title="RAG survey",
        source_url="https://example.com/rag-survey",
        source_domain="example.com",
        chunk_index=3,
    ),
    CitationRecord(
        evidence_id="E2",
        source_title=None,
        source_url="https://notes.example.org/grounding",
        source_domain="notes.example.org",
        chunk_index=1,
    ),
]


class ScriptedPipeline:
    """Builds fakes for all five stages, sharing one ordered call log."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.indexed_sources: list | None = None
        self.synthesis_question: str | None = None
        self.report_outcome: SynthesisOutcome | None = None
        self.errors: dict[str, Exception] = {}

    def install_error(self, stage: str, error: Exception) -> None:
        self.errors[stage] = error

    def build(self) -> ResearchExecutionService:
        pipeline = self

        class FakeQueryGenerator(QueryGenerator):
            def __init__(self) -> None:
                pass

            async def generate_queries(self, question: str) -> list[str]:
                pipeline.calls.append("queries")
                return ["query one", "query two"]

        class FakeSearchService(SearchService):
            def __init__(self) -> None:
                pass

            async def collect_sources(self, queries, *args, **kwargs):
                pipeline.calls.append("search")
                if "search" in pipeline.errors:
                    raise pipeline.errors["search"]
                return [
                    SourceResult(
                        title=f"Source {index}",
                        url=f"https://example.com/article-{index}",
                        snippet="A snippet.",
                        source="example.com",
                        published_at=None,
                        query="query one",
                    )
                    for index in range(1, 3)
                ]

        class FakeRagService(RAGService):
            def __init__(self) -> None:
                pass

            async def index_sources(self, sources):
                pipeline.calls.append("index")
                if "index" in pipeline.errors:
                    raise pipeline.errors["index"]
                pipeline.indexed_sources = sources
                return IndexStats(
                    indexed_sources=2, failed_sources=0, total_chunks=7
                )

        class FakeSynthesisService(SynthesisService):
            def __init__(self) -> None:
                pass

            async def synthesize(self, query: str, top_k: int) -> SynthesisOutcome:
                pipeline.calls.append("synthesize")
                if "synthesize" in pipeline.errors:
                    raise pipeline.errors["synthesize"]
                pipeline.synthesis_question = query
                return SynthesisOutcome(
                    status="success",
                    query=query,
                    answer=SECTIONED_ANSWER,
                    citations=list(VALIDATED_CITATIONS),
                    evidence_count=5,
                )

        class FakeReportService(ReportService):
            def __init__(self) -> None:
                pass

            def generate(self, outcome: SynthesisOutcome) -> ReportResult:
                pipeline.calls.append("report")
                if "report" in pipeline.errors:
                    raise pipeline.errors["report"]
                pipeline.report_outcome = outcome
                return ReportResult(
                    report_id="scheduled-report-ab12cd34",
                    markdown_filename="researchflow_scheduled-report-ab12cd34.md",
                    pdf_filename="researchflow_scheduled-report-ab12cd34.pdf",
                )

        return ResearchExecutionService(
            query_generator=FakeQueryGenerator(),
            search=FakeSearchService(),
            rag=FakeRagService(),
            synthesis=FakeSynthesisService(),
            reports=FakeReportService(),
        )


def test_full_pipeline_runs_each_stage_once_in_order() -> None:
    pipeline = ScriptedPipeline()
    service = pipeline.build()

    result = asyncio.run(service.run(QUESTION))

    # Exactly the documented order, each stage exactly once.
    assert pipeline.calls == ["queries", "search", "index", "synthesize", "report"]
    assert result.report_id == "scheduled-report-ab12cd34"
    assert result.synthesis_status == "success"
    assert result.sources_found == 2
    assert result.indexed_sources == 2
    assert result.failed_sources == 0
    assert result.total_chunks == 7


def test_search_results_are_mapped_to_indexable_sources() -> None:
    pipeline = ScriptedPipeline()
    service = pipeline.build()

    asyncio.run(service.run(QUESTION))

    assert pipeline.indexed_sources is not None
    assert [source.url for source in pipeline.indexed_sources] == [
        "https://example.com/article-1",
        "https://example.com/article-2",
    ]
    assert pipeline.indexed_sources[0].title == "Source 1"
    assert pipeline.indexed_sources[0].snippet == "A snippet."


def test_synthesis_receives_the_original_question() -> None:
    pipeline = ScriptedPipeline()
    service = pipeline.build()

    asyncio.run(service.run(QUESTION))

    assert pipeline.synthesis_question == QUESTION
    # The report renders the SAME validated outcome synthesis produced.
    assert pipeline.report_outcome is not None
    assert pipeline.report_outcome.query == QUESTION
    assert pipeline.report_outcome.status == "success"


def test_result_carries_research_content_from_the_same_outcome() -> None:
    # ExecutionResult must carry the research CONTENT (for Discord)
    # derived from the SAME SynthesisOutcome the report was built from —
    # using the same slot extractor as the reporters, with citations
    # mapped from the already-validated citation records. This must not
    # add pipeline stages or duplicate any work.
    pipeline = ScriptedPipeline()
    service = pipeline.build()

    result = asyncio.run(service.run(QUESTION))

    # No extra pipeline calls were made to gather this content.
    assert pipeline.calls == ["queries", "search", "index", "synthesize", "report"]
    assert result.evidence_count == 5
    assert result.summary == (
        "RAG berkembang pesat menuju kurasi sumber dan evaluasi grounding."
    )
    assert result.findings == (
        "- Kurasi sumber sebelum indeksasi menaikkan kualitas jawaban.\n"
        "- Evaluasi grounding kini standar de facto."
    )
    # Citations come from the VALIDATED records only — URLs and domains
    # are copied from source metadata, never re-derived from answer text.
    assert [c.evidence_id for c in result.citations] == ["E1", "E2"]
    assert result.citations[0].url == "https://example.com/rag-survey"
    assert result.citations[0].domain == "example.com"
    assert result.citations[1].title == ""  # None source_title → empty, not "None"
    assert result.citations[1].url == "https://notes.example.org/grounding"


@pytest.mark.parametrize(
    ("stage", "error", "expected_prefix"),
    [
        ("search", SearchServiceError("web search unavailable"), "Pencarian gagal:"),
        ("index", RAGServiceError("vector store unavailable"), "Pengindeksan gagal:"),
        (
            "synthesize",
            SynthesisServiceError("the AI could not answer"),
            "Sintesis gagal:",
        ),
        ("report", ReportServiceError("disk full"), "Pembuatan laporan gagal:"),
    ],
)
def test_stage_failures_raise_client_safe_errors(
    stage: str, error: Exception, expected_prefix: str
) -> None:
    pipeline = ScriptedPipeline()
    pipeline.install_error(stage, error)
    service = pipeline.build()

    with pytest.raises(ResearchExecutionError) as excinfo:
        asyncio.run(service.run(QUESTION))

    message = str(excinfo.value)
    assert message.startswith(expected_prefix)
    # The original client-safe message is preserved, no traceback leaks.
    assert error.message in message


def test_failure_stops_later_stages() -> None:
    pipeline = ScriptedPipeline()
    pipeline.install_error("search", SearchServiceError("down"))
    service = pipeline.build()

    with pytest.raises(ResearchExecutionError):
        asyncio.run(service.run(QUESTION))

    # Nothing after the failed search stage ran — in particular no GLM
    # synthesis and no report generation for a failed run.
    assert pipeline.calls == ["queries", "search"]

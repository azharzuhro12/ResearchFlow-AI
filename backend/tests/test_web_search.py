"""Tests for Step 3 — automated web research.

Every external call (GLM, web search) is mocked. No real requests, no cost.
"""

import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from app.api.research import (
    get_query_generator,
    get_search_service,
)
from app.main import app
from app.services.glm_service import GLMServiceError
from app.services.query_generator import (
    QueryGenerator,
    QueryGeneratorError,
    fallback_queries,
    normalize_queries,
    parse_queries_payload,
)
from app.services.search_provider import (
    DuckDuckGoSearchProvider,
    SearchProvider,
    SearchProviderError,
    SearchResult,
)
from app.services.search_service import (
    SearchService,
    SearchServiceError,
    normalize_url,
)
from tests.test_research_plan import FakeGLMService

client = TestClient(app)

VALID_QUESTION = "What are the latest RAG techniques in 2026?"


class FakeSearchProvider(SearchProvider):
    """In-memory search provider — never performs HTTP requests."""

    def __init__(
        self,
        results: list[SearchResult] | None = None,
        error: SearchProviderError | None = None,
        fail_queries: tuple[str, ...] = (),
    ) -> None:
        self.results = results or []
        self.error = error
        self.fail_queries = set(fail_queries)
        self.calls: list[str] = []

    async def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        self.calls.append(query)
        if query in self.fail_queries:
            raise SearchProviderError("Web search is currently unavailable.", 503)
        if self.error is not None:
            raise self.error
        return self.results[:max_results]


# ------------------------------------------------------- query generator


class TestQueryGenerator:
    def test_valid_response_returns_queries(self) -> None:
        glm = FakeGLMService(content='{"queries": ["a b", "c d", "e f"]}')
        queries = asyncio.run(QueryGenerator(glm).generate_queries(VALID_QUESTION))
        assert queries == ["a b", "c d", "e f"]

    def test_too_few_queries_are_padded_with_fallback(self) -> None:
        glm = FakeGLMService(content='{"queries": ["only one query"]}')
        queries = asyncio.run(QueryGenerator(glm).generate_queries(VALID_QUESTION))
        assert 3 <= len(queries) <= 5
        assert queries[0] == "only one query"

    def test_too_many_queries_are_truncated(self) -> None:
        payload = '{"queries": ["q1", "q2", "q3", "q4", "q5", "q6", "q7"]}'
        glm = FakeGLMService(content=payload)
        queries = asyncio.run(QueryGenerator(glm).generate_queries(VALID_QUESTION))
        assert len(queries) == 5

    def test_code_fenced_json_is_parsed(self) -> None:
        glm = FakeGLMService(
            content='```json\n{"queries": ["a", "b", "c"]}\n```'
        )
        queries = asyncio.run(QueryGenerator(glm).generate_queries(VALID_QUESTION))
        assert queries == ["a", "b", "c"]

    def test_malformed_glm_response_uses_fallback(self) -> None:
        glm = FakeGLMService(content="Sorry, I cannot help with that.")
        queries = asyncio.run(QueryGenerator(glm).generate_queries(VALID_QUESTION))
        assert 3 <= len(queries) <= 5

    def test_glm_error_uses_fallback(self) -> None:
        glm = FakeGLMService(error=GLMServiceError("GLM API is not configured.", 503))
        queries = asyncio.run(QueryGenerator(glm).generate_queries(VALID_QUESTION))
        assert 3 <= len(queries) <= 5
        assert any("RAG" in query for query in queries)

    def test_fallback_queries_shape(self) -> None:
        queries = fallback_queries(VALID_QUESTION)
        assert 3 <= len(queries) <= 5
        assert all(isinstance(q, str) and q.strip() for q in queries)

    def test_parse_invalid_json_raises(self) -> None:
        with pytest.raises(QueryGeneratorError):
            parse_queries_payload("no json here")

    def test_normalize_removes_duplicates_case_insensitive(self) -> None:
        queries = normalize_queries(["Alpha", "alpha", "beta"], VALID_QUESTION)
        alpha_like = [q for q in queries if q.casefold() == "alpha"]
        assert len(alpha_like) == 1


# ---------------------------------------------------------- search provider


class TestDuckDuckGoProvider:
    def _provider_with_raw(self, raw: object) -> DuckDuckGoSearchProvider:
        provider = DuckDuckGoSearchProvider()
        provider._search_sync = lambda query, max_results: raw  # type: ignore[assignment]
        return provider

    def test_successful_search_maps_fields(self) -> None:
        raw = [
            {"title": "RAG Paper", "href": "https://arxiv.org/abs/2401", "body": "A paper."},
            {"title": "Alt Keys", "url": "https://example.com/x", "snippet": "alt"},
        ]
        results = asyncio.run(self._provider_with_raw(raw).search("query"))
        assert results == [
            SearchResult("RAG Paper", "https://arxiv.org/abs/2401", "A paper."),
            SearchResult("Alt Keys", "https://example.com/x", "alt"),
        ]

    def test_empty_result(self) -> None:
        assert asyncio.run(self._provider_with_raw([]).search("query")) == []

    def test_malformed_items_are_dropped(self) -> None:
        raw = [
            {"title": "", "href": "https://a.com/no-title"},  # no title
            {"title": "No url"},  # no url
            "garbage",  # not a dict
            {"title": "OK", "href": "https://a.com/ok", "body": ""},
        ]
        results = asyncio.run(self._provider_with_raw(raw).search("query"))
        assert len(results) == 1
        assert results[0].url == "https://a.com/ok"
        assert results[0].snippet is None

    def test_timeout_maps_to_504(self) -> None:
        provider = DuckDuckGoSearchProvider(timeout_seconds=0.1)

        def slow_sync(query: str, max_results: int) -> list[dict]:
            time.sleep(0.5)
            return []

        provider._search_sync = slow_sync  # type: ignore[assignment]
        with pytest.raises(SearchProviderError) as excinfo:
            asyncio.run(provider.search("query"))
        assert excinfo.value.http_status == 504

    def test_provider_failure_maps_to_503(self) -> None:
        provider = DuckDuckGoSearchProvider()
        provider._search_sync = lambda q, m: (_ for _ in ()).throw(RuntimeError("boom"))  # type: ignore[assignment]
        with pytest.raises(SearchProviderError) as excinfo:
            asyncio.run(provider.search("query"))
        assert excinfo.value.http_status == 503


# ------------------------------------------------- deduplication / service


class TestNormalizeUrl:
    def test_trailing_slash_and_case_are_equivalent(self) -> None:
        assert (
            normalize_url("https://Example.com/article/")
            == normalize_url("https://example.com/article")
        )

    def test_fragment_is_dropped(self) -> None:
        assert normalize_url("https://a.com/x#section") == normalize_url("https://a.com/x")

    def test_query_strings_are_kept_distinct(self) -> None:
        assert normalize_url("https://a.com/x?p=1") != normalize_url("https://a.com/x?p=2")


class TestSearchService:
    def _source(self, url: str, title: str = "Title") -> SearchResult:
        return SearchResult(title=title, url=url, snippet="snippet")

    def test_merges_and_deduplicates_across_queries(self) -> None:
        provider = FakeSearchProvider(
            results=[
                self._source("https://a.com/1", "First"),
                self._source("https://a.com/1/", "Duplicate"),
                self._source("https://b.com/2", "Second"),
            ]
        )
        sources = asyncio.run(SearchService(provider).collect_sources(["q1", "q2"]))
        assert len(sources) == 2
        assert sources[0].title == "First"
        assert sources[0].source == "a.com"
        assert sources[0].query == "q1"

    def test_single_query_failure_is_tolerated(self) -> None:
        provider = FakeSearchProvider(
            results=[self._source("https://a.com/1")],
            fail_queries=("bad query",),
        )
        sources = asyncio.run(
            SearchService(provider).collect_sources(["bad query", "good query"])
        )
        assert len(sources) == 1

    def test_all_queries_failing_raises_503(self) -> None:
        provider = FakeSearchProvider(error=SearchProviderError("unavailable", 503))
        with pytest.raises(SearchServiceError) as excinfo:
            asyncio.run(SearchService(provider).collect_sources(["q1", "q2"]))
        assert excinfo.value.http_status == 503

    def test_all_queries_timing_out_raises_504(self) -> None:
        provider = FakeSearchProvider(error=SearchProviderError("timed out", 504))
        with pytest.raises(SearchServiceError) as excinfo:
            asyncio.run(SearchService(provider).collect_sources(["q1", "q2"]))
        assert excinfo.value.http_status == 504

    def test_zero_results_returns_empty_list(self) -> None:
        provider = FakeSearchProvider(results=[])
        sources = asyncio.run(SearchService(provider).collect_sources(["q1"]))
        assert sources == []

    def test_max_total_caps_results(self) -> None:
        provider = FakeSearchProvider(
            results=[
                self._source("https://a.com/1"),
                self._source("https://b.com/2"),
                self._source("https://c.com/3"),
            ]
        )
        sources = asyncio.run(
            SearchService(provider).collect_sources(["q1"], max_total=2)
        )
        assert len(sources) == 2

    def test_www_prefix_stripped_from_domain(self) -> None:
        provider = FakeSearchProvider(results=[self._source("https://www.example.com/x")])
        sources = asyncio.run(SearchService(provider).collect_sources(["q1"]))
        assert sources[0].source == "example.com"


# ------------------------------------------------------------------- API


@pytest.fixture
def install_search_deps():
    """Override GLM + search dependencies for the /search endpoint."""

    def _install(
        glm: FakeGLMService,
        provider: FakeSearchProvider,
    ) -> FakeSearchProvider:
        app.dependency_overrides[get_query_generator] = lambda: QueryGenerator(glm)
        app.dependency_overrides[get_search_service] = lambda: SearchService(provider)
        return provider

    yield _install
    app.dependency_overrides.pop(get_query_generator, None)
    app.dependency_overrides.pop(get_search_service, None)


class TestSearchAPI:
    def test_valid_question_returns_sources(
        self, install_search_deps
    ) -> None:
        provider = install_search_deps(
            glm=FakeGLMService(content='{"queries": ["q1 a", "q2 b", "q3 c"]}'),
            provider=FakeSearchProvider(
                results=[SearchResult("RAG Paper", "https://arxiv.org/abs/1", "About RAG")]
            ),
        )
        response = client.post("/api/research/search", json={"question": VALID_QUESTION})
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["question"] == VALID_QUESTION
        assert data["queries"] == ["q1 a", "q2 b", "q3 c"]
        assert data["total_sources"] == 1
        source = data["sources"][0]
        assert source["title"] == "RAG Paper"
        assert source["url"] == "https://arxiv.org/abs/1"
        assert source["source"] == "arxiv.org"
        assert source["published_at"] is None
        assert source["query"] == "q1 a"
        assert provider.calls == ["q1 a", "q2 b", "q3 c"]

    def test_short_question_is_rejected(self) -> None:
        response = client.post("/api/research/search", json={"question": "short"})
        assert response.status_code == 422

    def test_empty_question_is_rejected(self) -> None:
        response = client.post("/api/research/search", json={"question": "   "})
        assert response.status_code == 422

    def test_provider_error_returns_503(self, install_search_deps) -> None:
        install_search_deps(
            glm=FakeGLMService(content='{"queries": ["a", "b", "c"]}'),
            provider=FakeSearchProvider(
                error=SearchProviderError("Web search is currently unavailable.", 503)
            ),
        )
        response = client.post("/api/research/search", json={"question": VALID_QUESTION})
        assert response.status_code == 503
        assert "unavailable" in response.json()["detail"]

    def test_glm_failure_uses_fallback_queries(
        self, install_search_deps
    ) -> None:
        install_search_deps(
            glm=FakeGLMService(error=GLMServiceError("not configured", 503)),
            provider=FakeSearchProvider(
                results=[SearchResult("Source", "https://a.com/x", "snippet")]
            ),
        )
        response = client.post("/api/research/search", json={"question": VALID_QUESTION})
        assert response.status_code == 200
        data = response.json()
        assert 3 <= len(data["queries"]) <= 5
        assert data["total_sources"] == 1

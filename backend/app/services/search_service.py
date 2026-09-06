"""Search orchestration: run queries, merge results, deduplicate, cap.

Step 3 collects source METADATA only — no full-page scraping, no content
extraction (that comes in a later step).
"""

import logging
from urllib.parse import urlsplit, urlunsplit

from app.schemas.research import SourceResult
from app.services.search_provider import (
    SearchProvider,
    SearchProviderError,
    SearchResult,
)

logger = logging.getLogger(__name__)

DEFAULT_MAX_PER_QUERY = 5
DEFAULT_MAX_TOTAL = 25


class SearchServiceError(Exception):
    """Raised when the search orchestration fails. `message` is client-safe."""

    def __init__(self, message: str, http_status: int = 502) -> None:
        super().__init__(message)
        self.message = message
        self.http_status = http_status


def normalize_url(url: str) -> str:
    """Simple URL normalization used as the dedup key.

    Lowercases scheme/host, drops the fragment, and ignores trailing slashes —
    so https://Example.com/article/ and https://example.com/article match.
    Query strings are kept (they can point to different content).
    """
    parts = urlsplit(url.strip())
    return urlunsplit(
        (
            parts.scheme.lower(),
            parts.netloc.lower(),
            parts.path.rstrip("/"),
            parts.query,
            "",
        )
    )


def domain_of(url: str) -> str:
    """Extract the site domain (without a leading www.) for display."""
    host = urlsplit(url).netloc.lower()
    return host.removeprefix("www.")


class SearchService:
    """Runs multiple queries against one provider and merges the results."""

    def __init__(self, provider: SearchProvider) -> None:
        self.provider = provider

    async def collect_sources(
        self,
        queries: list[str],
        max_results_per_query: int = DEFAULT_MAX_PER_QUERY,
        max_total: int = DEFAULT_MAX_TOTAL,
    ) -> list[SourceResult]:
        """Search every query, deduplicate by URL, and cap the total.

        A single failing query never breaks the whole request. Only when
        EVERY query fails does this raise SearchServiceError.
        """
        collected: list[tuple[str, SearchResult]] = []
        failures: list[SearchProviderError] = []

        for query in queries:
            try:
                results = await self.provider.search(query, max_results_per_query)
            except SearchProviderError as exc:
                logger.warning("Search failed for query %r: %s", query, exc.message)
                failures.append(exc)
                continue
            collected.extend((query, result) for result in results)

        unique: list[SourceResult] = []
        seen: set[str] = set()
        for query, result in collected:
            key = normalize_url(result.url)
            if not key or key in seen:
                continue
            seen.add(key)
            unique.append(
                SourceResult(
                    title=result.title,
                    url=result.url,
                    snippet=result.snippet,
                    source=domain_of(result.url) or None,
                    published_at=None,  # not provided by the free provider
                    query=query,
                )
            )

        if not unique and queries and len(failures) == len(queries):
            # Every query failed — surface a clear error to the client.
            first = failures[0]
            raise SearchServiceError(first.message, first.http_status)

        return unique[:max_total]

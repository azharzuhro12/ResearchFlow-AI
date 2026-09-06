"""Web search provider abstraction + a free, no-API-key implementation.

Uses the `ddgs` library (DuckDuckGo and other free backends) — no API key,
no payment, no subscription. Swap providers by implementing SearchProvider.
"""

import asyncio
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Default wall-clock budget for a single search query.
DEFAULT_TIMEOUT_SECONDS = 15.0


class SearchProviderError(Exception):
    """Raised when a search call fails. `message` is safe to show clients."""

    def __init__(self, message: str, http_status: int = 502) -> None:
        super().__init__(message)
        self.message = message
        self.http_status = http_status


@dataclass(slots=True)
class SearchResult:
    """Minimal, consistent metadata for one web result."""

    title: str
    url: str
    snippet: str | None


class SearchProvider:
    """Abstract web search provider."""

    async def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        """Search the web for `query`, returning up to `max_results` results."""
        raise NotImplementedError


class DuckDuckGoSearchProvider(SearchProvider):
    """Free web search via ddgs (no API key required).

    The blocking ddgs call runs in a worker thread with a wall-clock timeout
    so it never blocks the event loop or hangs the request.
    """

    def __init__(self, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS) -> None:
        self.timeout_seconds = timeout_seconds

    async def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        try:
            raw_results = await asyncio.wait_for(
                asyncio.to_thread(self._search_sync, query, max_results),
                timeout=self.timeout_seconds,
            )
        except TimeoutError:
            raise SearchProviderError(
                "Web search timed out. Please try again.",
                http_status=504,
            ) from None
        except Exception:
            logger.warning("Search provider failed for query: %r", query)
            raise SearchProviderError(
                "Web search is currently unavailable.",
                http_status=503,
            ) from None

        mapped = (self._to_result(item) for item in raw_results)
        return [result for result in mapped if result is not None]

    def _search_sync(self, query: str, max_results: int) -> list[dict]:
        from ddgs import DDGS  # imported lazily; also easy to monkeypatch in tests

        with DDGS() as ddgs:
            return list(ddgs.text(query, max_results=max_results))

    @staticmethod
    def _to_result(item: object) -> SearchResult | None:
        """Map a raw provider item to SearchResult; None if unusable.

        Handles both legacy (`href`/`body`) and alternate (`url`/`snippet`)
        key layouts. Missing fields are never invented.
        """
        if not isinstance(item, dict):
            return None
        url = str(item.get("href") or item.get("url") or "").strip()
        title = str(item.get("title") or "").strip()
        if not url or not title:
            return None
        snippet = str(item.get("body") or item.get("snippet") or "").strip()
        return SearchResult(title=title[:300], url=url, snippet=snippet or None)

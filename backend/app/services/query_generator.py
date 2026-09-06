"""Search Query Generator — turns a research question into web search queries.

Uses a single GLM call and falls back to deterministic queries when GLM is
unavailable or returns garbage, so web research can always proceed.
"""

import logging
import re

from app.services.glm_service import GLMService, GLMServiceError
from app.services.json_utils import SafeJSONError, extract_json_object, string_list

logger = logging.getLogger(__name__)

MIN_QUERIES = 3
MAX_QUERIES = 5

QUERY_SYSTEM_PROMPT = """You are the Search Query Generator of ResearchFlow AI, an autonomous research automation platform.

Convert the user's research question into web search queries.

Rules:
- Produce between 3 and 5 queries.
- Each query is 3 to 8 words, plain text: no quotes, no boolean operators, no site: filters.
- Queries must cover diverse angles of the question (e.g. overview, recent advances, research papers, comparisons).
- Keep important entities and years from the original question.
- Write queries in English.

Respond with STRICT JSON only — no markdown, no code fences, no extra text — in exactly this shape:
{"queries": ["query 1", "query 2", "query 3"]}"""

_TRAILING_PUNCT_RE = re.compile(r"[?!.]+\s*$")


class QueryGeneratorError(Exception):
    """Raised when a GLM response cannot be parsed into queries."""


class QueryGenerator:
    """Generates web search queries from a research question using GLM + fallback."""

    def __init__(self, glm: GLMService) -> None:
        self.glm = glm

    async def generate_queries(self, question: str) -> list[str]:
        """Return MIN_QUERIES..MAX_QUERIES search queries. Never raises."""
        queries: list[str] = []
        try:
            user_prompt = (
                f"Research question: {question}\n\n"
                "Return the search queries as strict JSON."
            )
            raw = await self.glm.complete(QUERY_SYSTEM_PROMPT, user_prompt)
            queries = parse_queries_payload(raw)
        except (GLMServiceError, QueryGeneratorError) as exc:
            logger.warning("GLM query generation failed (%s); using fallback", exc)
        return normalize_queries(queries, question)


def parse_queries_payload(raw: str) -> list[str]:
    """Safely parse GLM output into a list of query strings."""
    try:
        return string_list(extract_json_object(raw), "queries")
    except SafeJSONError as exc:
        raise QueryGeneratorError(str(exc)) from exc


def fallback_queries(question: str) -> list[str]:
    """Deterministic queries derived from the question (no GLM needed)."""
    core = _TRAILING_PUNCT_RE.sub("", question.strip())[:120].strip() or question.strip()
    return [
        core,
        f"{core} research",
        f"{core} recent advances",
        f"{core} papers",
    ]


def normalize_queries(queries: list[str], question: str) -> list[str]:
    """Deduplicate and clamp the query list to [MIN_QUERIES, MAX_QUERIES]."""
    seen: set[str] = set()
    unique: list[str] = []
    for query in queries:
        cleaned = query.strip()
        if cleaned and cleaned.casefold() not in seen:
            seen.add(cleaned.casefold())
            unique.append(cleaned)

    if len(unique) < MIN_QUERIES:
        for fallback in fallback_queries(question):
            if len(unique) >= MAX_QUERIES:
                break
            if fallback.casefold() not in seen:
                seen.add(fallback.casefold())
                unique.append(fallback)

    if len(unique) < MIN_QUERIES:  # pathological input; guarantee the minimum
        unique = fallback_queries(question)
    return unique[:MAX_QUERIES]

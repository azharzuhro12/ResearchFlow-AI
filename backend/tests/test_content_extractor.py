"""Tests for source content extraction.

All HTTP traffic is mocked with httpx.MockTransport — no real requests,
no external websites, no cost.
"""

import asyncio

import httpx
import pytest

from app.services.content_extractor import (
    MAX_RESPONSE_BYTES,
    MAX_TEXT_CHARS,
    ContentExtractor,
    ContentExtractorError,
    validate_url,
)

GOOD_HTML = (
    "<html><head><title>t</title><style>x{}</style></head>"
    "<body><script>bad()</script><h1>Retrieval Augmented Generation</h1>"
    "<p>RAG combines retrieval with generation.</p></body></html>"
)


def _extractor(handler) -> ContentExtractor:
    return ContentExtractor(transport=httpx.MockTransport(handler))


def test_successful_extraction() -> None:
    service = _extractor(lambda request: httpx.Response(200, text=GOOD_HTML))

    content = asyncio.run(service.extract("https://example.com/article"))

    assert "Retrieval Augmented Generation" in content.text
    assert "bad()" not in content.text
    assert content.truncated is False


def test_timeout_returns_504() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow")

    service = _extractor(handler)

    with pytest.raises(ContentExtractorError) as exc_info:
        asyncio.run(service.extract("https://example.com/slow"))

    assert exc_info.value.http_status == 504


def test_connection_error_returns_502() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    service = _extractor(handler)

    with pytest.raises(ContentExtractorError) as exc_info:
        asyncio.run(service.extract("https://example.com/dead"))

    assert exc_info.value.http_status == 502


def test_http_error_status_returns_502() -> None:
    service = _extractor(lambda request: httpx.Response(404, text="gone"))

    with pytest.raises(ContentExtractorError) as exc_info:
        asyncio.run(service.extract("https://example.com/missing"))

    assert "HTTP 404" in exc_info.value.message
    assert exc_info.value.http_status == 502


def test_non_text_content_type_is_rejected() -> None:
    service = _extractor(
        lambda request: httpx.Response(
            200, headers={"Content-Type": "image/png"}, content=b"\x89PNG"
        )
    )

    with pytest.raises(ContentExtractorError) as exc_info:
        asyncio.run(service.extract("https://example.com/photo.png"))

    assert "not a text page" in exc_info.value.message


def test_empty_page_is_rejected() -> None:
    service = _extractor(
        lambda request: httpx.Response(200, text="<html><body></body></html>")
    )

    with pytest.raises(ContentExtractorError) as exc_info:
        asyncio.run(service.extract("https://example.com/blank"))

    assert "no readable text" in exc_info.value.message


def test_oversized_response_is_truncated_safely() -> None:
    huge_html = "<html><body><p>" + ("word " * (MAX_RESPONSE_BYTES // 2)) + "</p></body></html>"

    service = _extractor(lambda request: httpx.Response(200, text=huge_html))

    content = asyncio.run(service.extract("https://example.com/huge"))

    assert content.truncated is True
    assert len(content.text) <= MAX_TEXT_CHARS + 1  # capped, not exploded
    assert content.text  # still has readable text


def test_extracted_text_is_capped_at_max_chars() -> None:
    # Under the byte cap but over the character cap once cleaned.
    long_html = "<html><body>" + ("<p>sentence text here. </p>" * 6000) + "</body></html>"

    service = _extractor(lambda request: httpx.Response(200, text=long_html))

    content = asyncio.run(service.extract("https://example.com/long"))

    assert len(content.text) <= MAX_TEXT_CHARS
    assert content.truncated is True


def test_redirect_to_safe_location_is_followed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/old":
            return httpx.Response(
                301, headers={"Location": "https://example.com/new"}
            )
        return httpx.Response(200, text=GOOD_HTML)

    service = _extractor(handler)

    content = asyncio.run(service.extract("https://example.com/old"))

    assert "Retrieval Augmented Generation" in content.text


def test_redirect_to_private_target_is_rejected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            302, headers={"Location": "http://169.254.169.254/metadata"}
        )

    service = _extractor(handler)

    with pytest.raises(ContentExtractorError) as exc_info:
        asyncio.run(service.extract("https://evil.example.com/redirect"))

    assert "local/private" in exc_info.value.message


def test_redirect_loop_is_terminated() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": "https://example.com/loop"})

    service = _extractor(handler)

    with pytest.raises(ContentExtractorError) as exc_info:
        asyncio.run(service.extract("https://example.com/loop"))

    assert "too many times" in exc_info.value.message


# --------------------------------------------------------- URL validation


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/a",
        "http://example.com",
        "https://sub.domain.org/path?query=1",
    ],
)
def test_valid_urls_are_accepted(url: str) -> None:
    assert validate_url(url) == url


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com/file",
        "javascript:alert(1)",
        "not-a-url",
        "",
        "http://localhost:8010/health",
        "https://127.0.0.1/x",
        "http://0.0.0.0/",
        "https://[::1]/",
        "http://10.0.0.5/internal",
        "http://192.168.1.10/router",
        "http://172.16.0.1/panel",
        "http://169.254.169.254/metadata",
        "https://[fe80::1]/",
        "https://[fc00::123]/",
        # Embedded credentials (Step 11 hardening).
        "http://user:pass@example.com/",
        "https://admin@example.com/secret",
        # Non-standard ports (Step 11 hardening).
        "http://example.com:8080/",
        "https://example.com:8443/x",
        "http://example.com:bad/",
        # Non-dotted IP forms the system resolver accepts (decimal/hex/
        # octal loopbacks) — plain dotted-quad parsing misses these.
        "http://2130706433/",
        "http://0x7f000001/",
        "http://0177.0.0.1/",
        "http://3232235777/",  # 192.168.1.1 in decimal
        # Reserved/multicast literals caught by is_global.
        "http://240.0.0.1/",
        "http://224.0.0.1/",
    ],
)
def test_unsafe_urls_are_rejected(url: str) -> None:
    with pytest.raises(ContentExtractorError):
        validate_url(url)

"""Source content extraction: URL → HTTP GET → HTML → clean readable text.

External websites are untrusted input:
- only http/https is fetched (never file:// or other schemes)
- obvious private/local network targets are rejected (basic SSRF guard)
- every request has a timeout and a response-size cap
- nothing from the page is ever executed — text extraction only
"""

import ipaddress
import logging
import socket
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from app.services.text_cleaner import clean_html

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT_SECONDS = 15.0
# How many HTTP redirects to follow (each hop re-validated against SSRF rules).
MAX_REDIRECTS = 3
# Hard cap on downloaded bytes per page (truncates safely beyond this).
MAX_RESPONSE_BYTES = 5 * 1024 * 1024  # 5 MB
# Hard cap on extracted characters per page.
MAX_TEXT_CHARS = 100_000

# Plausible browser-ish UA; some sites reject empty/pythonic defaults.
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) ResearchFlowAI/0.1 (+research bot)"
)

_ALLOWED_SCHEMES = {"http", "https"}

# Fetch only the schemes' standard ports (None = scheme default). Anything
# else (internal admin panels, databases, etc.) is refused outright.
_ALLOWED_PORTS = {80, 443}
_BLOCKED_HOSTNAMES = {"localhost", "127.0.0.1", "0.0.0.0", "::1", "[::1]"}

# Content types we are willing to parse as text.
_TEXT_CONTENT_TYPES = ("text/", "application/xhtml", "application/xml")


class ContentExtractorError(Exception):
    """Raised when one source cannot be extracted. `message` is client-safe."""

    def __init__(self, message: str, http_status: int = 502) -> None:
        super().__init__(message)
        self.message = message
        self.http_status = http_status


@dataclass(frozen=True)
class ExtractedContent:
    """Readable text extracted from one source page."""

    text: str
    truncated: bool = False


def _is_private_ipv4(host: str) -> bool:
    """True for literal private/loopback/link-local IPv4 addresses (MVP guard)."""
    parts = host.split(".")
    if len(parts) != 4 or not all(p.isdigit() for p in parts):
        return False
    first, second = int(parts[0]), int(parts[1])
    return (
        first == 10
        or first == 127
        or (first == 172 and 16 <= second <= 31)
        or (first == 192 and second == 168)
        or (first == 169 and second == 254)
    )


def _is_non_global_ip(host: str) -> bool:
    """True when `host` is an IP literal (dotted OR inet_aton forms like
    ``2130706433`` / ``0x7f.0.0.1``) that is not a global address.

    ``socket.inet_aton`` accepts the same non-dotted forms the system
    resolver does, so this closes the "decimal/hex loopback" bypass that
    plain dotted-quad parsing misses.
    """
    try:
        addr = ipaddress.ip_address(socket.inet_ntoa(socket.inet_aton(host)))
    except (OSError, ValueError):
        return False  # not an IPv4 literal at all — hostname rules apply
    # is_global alone does not flag multicast in all Python versions.
    return not addr.is_global or addr.is_multicast


def validate_url(url: str) -> str:
    """Return the URL if acceptable for fetching, else raise (client-safe).

    MVP SSRF guard: allow http(s) on standard ports only, reject malformed
    URLs, embedded credentials, and local/private targets (including
    non-dotted IP forms). Redirects are never followed by the client, so a
    "safe" URL cannot be turned into an internal request mid-flight. Not
    exhaustive (DNS rebinding is out of scope) — good enough for a local
    research tool, kept deliberately simple per spec.
    """
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        raise ContentExtractorError("Invalid source URL.") from None

    if parts.scheme.lower() not in _ALLOWED_SCHEMES or not parts.netloc:
        raise ContentExtractorError(
            "Invalid source URL: only http(s) URLs are supported."
        )
    if "@" in parts.netloc:
        raise ContentExtractorError(
            "Invalid source URL: embedded credentials are not allowed."
        )
    try:
        port = parts.port  # raises ValueError for malformed ports
    except ValueError:
        raise ContentExtractorError("Invalid source URL: bad port.") from None
    if port is not None and port not in _ALLOWED_PORTS:
        raise ContentExtractorError(
            "Invalid source URL: only ports 80 and 443 are allowed."
        )

    host = parts.hostname or ""
    if (
        host.lower() in _BLOCKED_HOSTNAMES
        or _is_private_ipv4(host)
        or _is_non_global_ip(host)
        or (
            ":" in host  # literal IPv6 (hostnames never contain ':')
            and host.lower().startswith(("fc", "fd", "fe8", "fe9", "fea", "feb", "::"))
        )
    ):
        raise ContentExtractorError(
            "Invalid source URL: local/private targets are not allowed."
        )
    return url.strip()


class ContentExtractor:
    """Fetches a web page and returns cleaned plain text."""

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        """`transport` is injectable for tests (httpx.MockTransport)."""
        self._transport = transport

    async def extract(self, url: str) -> ExtractedContent:
        """Download `url` (bounded), extract, and clean readable text."""
        safe_url = validate_url(url)

        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(REQUEST_TIMEOUT_SECONDS),
                transport=self._transport,
                follow_redirects=False,
            ) as client:
                html, truncated = await self._fetch_bounded(client, safe_url)
        except ContentExtractorError:
            raise
        except httpx.TimeoutException:
            raise ContentExtractorError(
                "Timed out while downloading the source page.",
                http_status=504,
            ) from None
        except httpx.HTTPError:
            raise ContentExtractorError(
                "Failed to download the source page.", http_status=502
            ) from None

        text = clean_html(html)
        if not text:
            raise ContentExtractorError("Source page contained no readable text.")
        if len(text) > MAX_TEXT_CHARS:
            text = text[:MAX_TEXT_CHARS].rstrip()
            truncated = True
        return ExtractedContent(text=text, truncated=truncated)

    async def _fetch_bounded(
        self, client: httpx.AsyncClient, url: str
    ) -> tuple[bytes, bool]:
        """GET with manual redirects — every hop is SSRF-validated again.

        Redirects are followed up to MAX_REDIRECTS times; a redirect to a
        local/private target is rejected just like a direct request.
        """
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.5",
        }
        for _ in range(MAX_REDIRECTS + 1):
            async with client.stream("GET", url, headers=headers) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    location = response.headers.get("location", "")
                    if not location:
                        raise ContentExtractorError(
                            "Source page redirected without a target."
                        )
                    url = validate_url(str(httpx.URL(url).join(location)))
                    continue

                if response.status_code >= 400:
                    raise ContentExtractorError(
                        f"Source page returned HTTP {response.status_code}."
                    )
                content_type = (
                    response.headers.get("content-type", "").split(";")[0].strip().lower()
                )
                if content_type and not content_type.startswith(_TEXT_CONTENT_TYPES):
                    raise ContentExtractorError(
                        f"Source is not a text page (content-type {content_type!r})."
                    )
                return await self._read_bounded(response)

        raise ContentExtractorError("Source page redirected too many times.")

    @staticmethod
    async def _read_bounded(response: httpx.Response) -> tuple[bytes, bool]:
        """Read the body up to MAX_RESPONSE_BYTES; stop (truncate) beyond."""
        chunks: list[bytes] = []
        received = 0
        truncated = False
        async for chunk in response.aiter_bytes():
            received += len(chunk)
            if received > MAX_RESPONSE_BYTES:
                keep = len(chunk) - (received - MAX_RESPONSE_BYTES)
                chunks.append(chunk[:keep])
                truncated = True
                break
            chunks.append(chunk)
        return b"".join(chunks), truncated

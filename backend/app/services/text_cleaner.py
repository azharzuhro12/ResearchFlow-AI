"""Text cleaning for extracted web content.

Turns raw HTML into plain readable text evidence for RAG: strips
non-content tags, removes navigation noise, and normalizes whitespace.
No summarization — the original wording must survive for citations.
"""

import re

from bs4 import BeautifulSoup, Comment

# Tags that never contain readable article content.
_NOISE_TAGS = (
    "script",
    "style",
    "noscript",
    "template",
    "iframe",
    "svg",
    "canvas",
    "form",
    "nav",
    "header",
    "footer",
    "aside",
)

# Whitespace runs (spaces, tabs, non-breaking spaces) collapse to one space.
_MULTIPLE_SPACES = re.compile(r"[ \t\u00a0]+")


def clean_html(html: str) -> str:
    """Extract readable text from an HTML document.

    Removes script/style/template blocks and common navigation chrome,
    then joins the remaining text with newlines and normalizes whitespace.
    Returns "" when nothing readable remains.
    """
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup.find_all(_NOISE_TAGS):
        tag.decompose()
    for comment in soup.find_all(string=lambda text: isinstance(text, Comment)):
        comment.extract()

    text = soup.get_text(separator="\n")
    return clean_text(text)


def clean_text(text: str) -> str:
    """Normalize whitespace in already-plain text.

    Strips each line, collapses space runs, and limits blank lines to one
    (a blank line still separates paragraphs; runs of blanks collapse).
    """
    cleaned = [_MULTIPLE_SPACES.sub(" ", line).strip() for line in text.splitlines()]
    kept: list[str] = []
    for line in cleaned:
        # Skip leading blanks and any blank that follows another blank.
        if not line and (not kept or not kept[-1]):
            continue
        kept.append(line)
    return "\n".join(kept).strip()

"""Markdown report rendering — deterministic, no LLM.

Converts a validated SynthesisOutcome into a clean Markdown document.
Citation markers ([E1], [E2], ...) from the synthesis answer are preserved
verbatim; source metadata comes only from the validated citation objects,
never from the LLM. All formatting is plain string work, so the same input
always produces the same output.
"""

import re

from app.services.synthesis_service import (
    INSUFFICIENT_EVIDENCE_ANSWER,
    STATUS_INSUFFICIENT_EVIDENCE,
    STATUS_UNGROUNDED,
    SynthesisOutcome,
)

# Heading line in the synthesis answer, e.g. "## Key Findings".
_HEADING_RE = re.compile(r"^(#{1,6})\s*(.+?)\s*#*\s*$", re.MULTILINE)
# Characters that carry meaning in Markdown inline syntax.
_INLINE_SPECIALS = str.maketrans(
    {
        "*": "\\*",
        "_": "\\_",
        "`": "\\`",
        "[": "\\[",
        "]": "\\]",
        "<": "\\<",
    }
)

# Slot keys match headings in BOTH English and Indonesian — the synthesis
# prompt asks for Indonesian headers (## Ringkasan, ## Temuan Utama,
# ## Kesimpulan) but English headings from older answers still classify.
_SUMMARY_KEYS = ("summary", "executivesummary", "overview", "ringkasan", "ikhtisar")
_FINDINGS_KEYS = ("finding", "findings", "keyfindings", "temuan")
_CONCLUSION_KEYS = (
    "conclusion",
    "conclusions",
    "takeaway",
    "takeaways",
    "kesimpulan",
    "catatanpenutup",
)


class Section:
    """One extracted heading + body block from the synthesis answer."""

    def __init__(self, heading: str, body: str) -> None:
        self.heading = heading
        self.body = body.strip()


def _slug_heading(heading: str) -> str:
    """Normalize a heading for matching (lowercase, alphanumeric only)."""
    return re.sub(r"[^a-z0-9]", "", heading.lower())


def _escape_inline(text: str) -> str:
    """Escape Markdown inline syntax in untrusted one-line metadata."""
    return text.translate(_INLINE_SPECIALS)


def _one_line(text: str) -> str:
    """Collapse newlines/tabs so user input can never inject headings."""
    return re.sub(r"\s+", " ", text).strip()


def split_answer_sections(answer: str) -> tuple[list[Section], str]:
    """Split the synthesis answer into headed sections + leading prose.

    Returns (sections, preamble) where preamble is any text before the
    first heading. Purely deterministic string parsing.
    """
    matches = list(_HEADING_RE.finditer(answer))
    if not matches:
        return [], answer.strip()

    preamble = answer[: matches[0].start()].strip()
    sections: list[Section] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(answer)
        sections.append(Section(match.group(2), answer[match.end() : end]))
    return sections, preamble


def classify_answer(sections: list[Section], preamble: str) -> dict[str, str]:
    """Extract report slots from the synthesis answer deterministically.

    Slots: "summary", "findings", "conclusion". Sections whose heading does
    not match a slot are simply left in the answer — the Detailed Analysis
    section always carries the COMPLETE synthesis answer verbatim, so no
    content is ever dropped or rewritten. Shared by both reporters.
    """
    slots: dict[str, str] = {"summary": "", "findings": "", "conclusion": ""}

    for section in sections:
        key = _slug_heading(section.heading)
        if any(k in key for k in _SUMMARY_KEYS) and not slots["summary"]:
            slots["summary"] = section.body
        elif any(k in key for k in _FINDINGS_KEYS) and not slots["findings"]:
            slots["findings"] = section.body
        elif any(k in key for k in _CONCLUSION_KEYS) and not slots["conclusion"]:
            slots["conclusion"] = section.body

    # Deterministic fallbacks — extracted from the answer, never invented.
    if not slots["summary"]:
        fallback_text = preamble or (sections[0].body if sections else "")
        slots["summary"] = fallback_text.split("\n\n")[0].strip()
    if not slots["conclusion"]:
        slots["conclusion"] = (
            "Sintesis tidak menghasilkan bagian kesimpulan eksplisit; lihat "
            "analisis rinci di atas."
        )
    return slots


def render_markdown(outcome: SynthesisOutcome) -> str:
    """Render a validated synthesis outcome as a Markdown research report."""
    question = _escape_inline(_one_line(outcome.query))
    lines: list[str] = [
        "# Laporan Riset",
        "",
        f"**Pertanyaan Riset:** {question}",
        "",
    ]

    if outcome.status == STATUS_INSUFFICIENT_EVIDENCE:
        lines += [
            "> **Status: Bukti Belum Cukup**",
            ">",
            "> Bukti yang berhasil diambil belum cukup untuk menghasilkan "
            + "jawaban riset yang berbasis sumber. Indeks lebih banyak sumber "
            + "lalu coba lagi.",
            "",
            "## Analisis Rinci",
            "",
            _escape_inline(INSUFFICIENT_EVIDENCE_ANSWER),
            "",
            "## Sumber",
        ]
        if not outcome.citations:
            lines.append("")
            lines.append("_Tidak ada sumber tervalidasi — tidak ada sitasi yang dihasilkan._")
        return "\n".join(lines).rstrip() + "\n"

    if outcome.status == STATUS_UNGROUNDED:
        lines += [
            "> **Status: Tanpa Grounding**",
            ">",
            "> Jawaban yang dihasilkan tidak dapat dikaitkan cukup dengan "
            + "bukti yang diambil — perlakukan analisis di bawah dengan "
            + "hati-hati.",
            "",
        ]

    slots = classify_answer(*split_answer_sections(outcome.answer))
    lines += [
        "## Ringkasan Eksekutif",
        "",
        slots["summary"] or "Sintesis tidak menghasilkan ringkasan.",
        "",
        "## Temuan Utama",
        "",
        slots["findings"] or "Sintesis tidak melaporkan temuan utama terpisah.",
        "",
        "## Analisis Rinci",
        "",
        # The COMPLETE validated synthesis answer, verbatim — headings and
        # citation markers preserved.
        outcome.answer,
        "",
        "## Kesimpulan",
        "",
        slots["conclusion"],
        "",
        "## Sumber",
    ]

    if not outcome.citations:
        lines += ["", "_Tidak ada sitasi tervalidasi yang dilampirkan pada jawaban ini._"]
    else:
        for citation in outcome.citations:
            title = _escape_inline(_one_line(citation.source_title or citation.source_url))
            domain = _escape_inline(_one_line(citation.source_domain or "tidak diketahui"))
            url = citation.source_url.replace("(", "%28").replace(")", "%29")
            lines += [
                "",
                f"### [{citation.evidence_id}] {title}",
                "",
                f"* URL: <{url}>",
                f"* Domain: {domain}",
                f"* Chunk: {citation.chunk_index}",
            ]
    return "\n".join(lines).rstrip() + "\n"


class MarkdownReporter:
    """Renders a validated synthesis outcome as a Markdown report."""

    def render(self, outcome: SynthesisOutcome) -> str:
        return render_markdown(outcome)

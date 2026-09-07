"""PDF report rendering with ReportLab — fully offline, no external APIs.

Builds the same report structure as the Markdown reporter using platypus
flowables and the built-in Helvetica font family. Text is normalized to
Latin-1-safe characters (Helvetica has no full Unicode coverage), citation
markers are preserved verbatim, and source URLs are rendered as clickable
internal links pointing at the validated URLs only.
"""

import logging
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

from app.services.markdown_reporter import classify_answer, split_answer_sections
from app.services.synthesis_service import (
    INSUFFICIENT_EVIDENCE_ANSWER,
    STATUS_INSUFFICIENT_EVIDENCE,
    STATUS_UNGROUNDED,
    SynthesisOutcome,
)

logger = logging.getLogger(__name__)

# Common Unicode punctuation -> Latin-1-safe equivalents so Helvetica can
# render synthesis text without missing glyphs.
_UNICODE_REPLACEMENTS = {
    "\u2192": "->",
    "\u2190": "<-",
    "\u21d2": "=>",
    "\u2013": "-",
    "\u2014": "-",
    "\u2018": "'",
    "\u2019": "'",
    "\u201c": '"',
    "\u201d": '"',
    "\u2026": "...",
    "\u2022": "-",
    "\u2265": ">=",
    "\u2264": "<=",
    "\u00d7": "x",
    "\u00a0": " ",
    "\u200b": "",
}

_TITLE_STYLE = ParagraphStyle(
    "ReportTitle", fontName="Helvetica-Bold", fontSize=18, leading=22, spaceAfter=6
)
_HEADING_STYLE = ParagraphStyle(
    "ReportHeading", fontName="Helvetica-Bold", fontSize=13, leading=16, spaceBefore=12
)
_BODY_STYLE = ParagraphStyle("ReportBody", fontName="Helvetica", fontSize=10, leading=14)
_NOTE_STYLE = ParagraphStyle(
    "ReportNote", fontName="Helvetica-Oblique", fontSize=9, leading=12, textColor="#555555"
)


def _pdf_safe(text: str) -> str:
    """Normalize text to Latin-1-safe characters for built-in fonts."""
    for unicode_char, replacement in _UNICODE_REPLACEMENTS.items():
        text = text.replace(unicode_char, replacement)
    # Drop anything Helvetica/Latin-1 cannot encode (e.g. CJK, emoji).
    return text.encode("latin-1", "replace").decode("latin-1")


def _para(text: str, style: ParagraphStyle = _BODY_STYLE) -> Paragraph:
    """A Paragraph from possibly-untrusted text, XML-escaped."""
    return Paragraph(escape(_pdf_safe(text)), style)


def _link_para(url: str, label: str) -> Paragraph:
    """A clickable source URL (attribute- and text-escaped)."""
    return Paragraph(
        f'<link href={quoteattr(_pdf_safe(url))}>{escape(_pdf_safe(label))}</link>',
        _BODY_STYLE,
    )


def _answer_flowables(answer: str) -> list:
    """Render the synthesis answer verbatim: its '#' headings become bold
    paragraphs, blank lines become spacers, everything else body text."""
    flowables: list = []
    for raw_line in answer.splitlines():
        line = raw_line.rstrip()
        stripped = line.lstrip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            heading = stripped.lstrip("#").strip()
            if heading:
                flowables.append(_para(heading, _HEADING_STYLE))
        else:
            flowables.append(_para(stripped))
    return flowables


class PDFReporter:
    """Renders a validated synthesis outcome as a local PDF file."""

    def render(self, outcome: SynthesisOutcome, output_path: Path) -> Path:
        """Write the PDF to `output_path` (parent dir must exist)."""
        document = SimpleDocTemplate(
            str(output_path),
            pagesize=A4,
            title="Laporan Riset ResearchFlow AI",
            author="ResearchFlow AI",
            leftMargin=2 * cm,
            rightMargin=2 * cm,
            topMargin=2 * cm,
            bottomMargin=2 * cm,
        )
        document.build(self._story(outcome))
        return output_path

    def _story(self, outcome: SynthesisOutcome) -> list:
        story: list = [
            Paragraph("Laporan Riset", _TITLE_STYLE),
            _para(f"Pertanyaan Riset: {outcome.query}", _NOTE_STYLE),
            Spacer(1, 6),
        ]

        if outcome.status == STATUS_INSUFFICIENT_EVIDENCE:
            story += [
                _para(
                    "Status: Bukti Belum Cukup — bukti yang berhasil diambil "
                    "belum cukup untuk menghasilkan jawaban berbasis sumber.",
                    _HEADING_STYLE,
                ),
                _para(INSUFFICIENT_EVIDENCE_ANSWER),
                Paragraph("Sumber", _HEADING_STYLE),
                _para("Tidak ada sumber tervalidasi - tidak ada sitasi yang dihasilkan.", _NOTE_STYLE),
            ]
            return story

        if outcome.status == STATUS_UNGROUNDED:
            story += [
                _para(
                    "Status: Tanpa Grounding — jawaban di bawah tidak dapat "
                    "dikaitkan cukup dengan bukti yang diambil; perlakukan "
                    "dengan hati-hati.",
                    _HEADING_STYLE,
                ),
            ]

        slots = classify_answer(*split_answer_sections(outcome.answer))
        story += [
            Paragraph("Ringkasan Eksekutif", _HEADING_STYLE),
            _para(slots["summary"] or "Sintesis tidak menghasilkan ringkasan."),
            Paragraph("Temuan Utama", _HEADING_STYLE),
            _para(slots["findings"] or "Sintesis tidak melaporkan temuan utama terpisah."),
            Paragraph("Analisis Rinci", _HEADING_STYLE),
            # The COMPLETE validated answer; its markdown headings render bold.
            *_answer_flowables(outcome.answer),
            Paragraph("Kesimpulan", _HEADING_STYLE),
            _para(slots["conclusion"]),
        ]

        story.append(Paragraph("Sumber", _HEADING_STYLE))
        if not outcome.citations:
            story.append(_para("Tidak ada sitasi tervalidasi yang dilampirkan pada jawaban ini.", _NOTE_STYLE))
            return story

        for citation in outcome.citations:
            story += [
                _para(f"[{citation.evidence_id}] {citation.source_title or citation.source_url}"),
                _link_para(citation.source_url, citation.source_url),
                _para(f"Domain: {citation.source_domain or 'tidak diketahui'} | Chunk: {citation.chunk_index}", _NOTE_STYLE),
                Spacer(1, 6),
            ]
        return story

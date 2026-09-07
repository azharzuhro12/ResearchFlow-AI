"use client";

import { useState, type FormEvent } from "react";

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8010";
const MIN_QUERY_LENGTH = 3;

type SynthesisStatus =
  | "idle"
  | "loading"
  | "success"
  | "error"
  | "insufficient_evidence";

type ReportStatus = "idle" | "loading" | "success" | "error";

interface Citation {
  evidence_id: string;
  source_title: string | null;
  source_url: string;
  source_domain: string | null;
  chunk_index: number;
}

interface SynthesisResult {
  status: string;
  query: string;
  answer: string;
  citations: Citation[];
  evidenceCount: number;
  ungrounded: boolean;
}

interface ReportResult {
  reportId: string;
  markdownUrl: string;
  pdfUrl: string;
}

// Matches [E1], [E1][E2], [E1, E2] — mirrors the backend citation parser.
const CITATION_PATTERN = /\[\s*E\d+(?:\s*[,;]\s*E\d+)*\s*\]/gi;

export function SynthesisPanel() {
  const [question, setQuestion] = useState("");
  const [status, setStatus] = useState<SynthesisStatus>("idle");
  const [result, setResult] = useState<SynthesisResult | null>(null);
  const [reportStatus, setReportStatus] = useState<ReportStatus>("idle");
  const [report, setReport] = useState<ReportResult | null>(null);

  const trimmedQuestion = question.trim();
  const canSynthesize =
    trimmedQuestion.length >= MIN_QUERY_LENGTH && status !== "loading";
  const canGenerateReport = status === "success" && reportStatus !== "loading";

  async function handleSynthesize(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canSynthesize) return;

    setStatus("loading");
    setResult(null);
    setReport(null);
    setReportStatus("idle");
    try {
      const response = await fetch(`${API_BASE_URL}/api/research/synthesize`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query: trimmedQuestion, top_k: 5 }),
      });
      if (!response.ok) throw new Error(`Request failed: ${response.status}`);
      const data = await response.json();
      if (typeof data?.answer !== "string" || !Array.isArray(data?.citations)) {
        throw new Error("Malformed synthesis response");
      }
      if (data.status === "insufficient_evidence") {
        setResult(null);
        setStatus("insufficient_evidence");
        return;
      }
      setResult({
        status: data.status,
        query: trimmedQuestion,
        answer: data.answer,
        citations: data.citations,
        evidenceCount: typeof data.evidence_count === "number" ? data.evidence_count : 0,
        ungrounded: data.status === "ungrounded",
      });
      setStatus("success");
    } catch {
      setResult(null);
      setStatus("error");
    }
  }

  async function handleGenerateReport() {
    if (!result || !canGenerateReport) return;

    setReportStatus("loading");
    setReport(null);
    try {
      const response = await fetch(`${API_BASE_URL}/api/research/report`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query: result.query, top_k: 5 }),
      });
      if (!response.ok) throw new Error(`Request failed: ${response.status}`);
      const data = await response.json();
      if (
        typeof data?.report_id !== "string" ||
        typeof data?.markdown_download_url !== "string" ||
        typeof data?.pdf_download_url !== "string"
      ) {
        throw new Error("Malformed report response");
      }
      setReport({
        reportId: data.report_id,
        markdownUrl: `${API_BASE_URL}${data.markdown_download_url}`,
        pdfUrl: `${API_BASE_URL}${data.pdf_download_url}`,
      });
      setReportStatus("success");
    } catch {
      setReport(null);
      setReportStatus("error");
    }
  }

  const knownIds = new Set(result?.citations.map((c) => c.evidence_id) ?? []);

  return (
    <section className="rounded-2xl border border-zinc-800 bg-zinc-900/60 p-6">
      <h3 className="text-lg font-semibold text-zinc-100">Jawaban Riset</h3>
      <p className="mt-1 text-sm text-zinc-400">
        Susun jawaban berbasis sumber (grounded) dari basis pengetahuan yang
        telah diindeks. Setiap klaim faktual dikaitkan dengan sumber bernomor
        [E1], [E2], ... yang dipetakan di server ke halaman aslinya.
      </p>
      <form onSubmit={handleSynthesize} className="mt-4 space-y-4">
        <label
          htmlFor="synthesis-question"
          className="block text-sm font-medium text-zinc-300"
        >
          Pertanyaan riset
        </label>
        <textarea
          id="synthesis-question"
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          rows={2}
          maxLength={2000}
          placeholder="mis. Apa teknik RAG terbaru?"
          className="w-full resize-y rounded-xl border border-zinc-700 bg-zinc-950 px-4 py-3 text-zinc-100 placeholder:text-zinc-600 focus:border-sky-500 focus:outline-none focus:ring-1 focus:ring-sky-500"
        />
        <div className="flex items-center justify-between gap-3">
          <p className="min-h-4 text-xs text-zinc-500">
            {trimmedQuestion.length > 0 &&
            trimmedQuestion.length < MIN_QUERY_LENGTH
              ? `Pertanyaan minimal ${MIN_QUERY_LENGTH} karakter.`
              : ""}
          </p>
          <button
            type="submit"
            disabled={!canSynthesize}
            className="inline-flex shrink-0 items-center justify-center gap-2 rounded-xl bg-emerald-500 px-5 py-2.5 text-sm font-semibold text-zinc-950 transition hover:bg-emerald-400 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {status === "loading" && (
              <span
                className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-zinc-900/40 border-t-zinc-900"
                aria-hidden="true"
              />
            )}
            {status === "loading" ? "Menyusun jawaban riset..." : "Buat Jawaban Riset"}
          </button>
        </div>
      </form>

      {status === "error" && (
        <div
          role="alert"
          className="mt-4 rounded-xl border border-red-900/60 bg-red-950/40 px-4 py-3 text-sm text-red-300"
        >
          Gagal menyusun jawaban riset. Silakan coba lagi.
        </div>
      )}

      {status === "insufficient_evidence" && (
        <div
          role="status"
          className="mt-4 rounded-xl border border-amber-900/60 bg-amber-950/30 px-4 py-3 text-sm text-amber-300"
        >
          Bukti terindeks belum cukup untuk menjawab pertanyaan ini. Coba
          indeks lebih banyak sumber dulu.
        </div>
      )}

      {status === "success" && result && (
        <div className="mt-6 space-y-5">
          {result.ungrounded && (
            <div
              role="status"
              className="rounded-xl border border-amber-900/60 bg-amber-950/30 px-4 py-3 text-sm text-amber-300"
            >
              Jawaban yang dihasilkan tidak dapat dikaitkan dengan bukti
              terindeks spesifik, sehingga tidak ada sitasi yang ditampilkan.
              Perlakukan dengan hati-hati.
            </div>
          )}
          <div className="rounded-xl border border-zinc-800 bg-zinc-950/60 p-4">
            <div className="space-y-2 text-sm leading-relaxed text-zinc-300">
              {result.answer.split("\n").map((line, lineIndex) =>
                line.trimStart().startsWith("#") ? (
                  <p key={lineIndex} className="font-semibold text-zinc-100">
                    {line.replace(/^#+\s*/, "")}
                  </p>
                ) : (
                  <p key={lineIndex}>
                    {renderWithCitations(line, lineIndex, knownIds)}
                  </p>
                ),
              )}
            </div>
            <p className="mt-4 text-xs text-zinc-500" role="status">
              Berdasarkan {result.evidenceCount} blok bukti terindeks.
            </p>
          </div>

          <div className="rounded-xl border border-zinc-800 bg-zinc-950/60 p-4">
            <h4 className="text-sm font-semibold uppercase tracking-wide text-zinc-400">
              Laporan Riset
            </h4>
            <p className="mt-1 text-sm text-zinc-400">
              Unduh jawaban riset ini sebagai laporan terformat beserta
              sumber tervalidasi.
            </p>
            <div className="mt-3 flex flex-wrap items-center gap-3">
              <button
                type="button"
                onClick={handleGenerateReport}
                disabled={!canGenerateReport}
                className="inline-flex items-center justify-center gap-2 rounded-xl bg-sky-500 px-4 py-2 text-sm font-semibold text-zinc-950 transition hover:bg-sky-400 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {reportStatus === "loading" && (
                  <span
                    className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-zinc-900/40 border-t-zinc-900"
                    aria-hidden="true"
                  />
                )}
                {reportStatus === "loading"
                  ? "Membuat laporan..."
                  : "Buat Laporan"}
              </button>
              {report && (
                <a
                  href={report.markdownUrl}
                  className="inline-flex items-center gap-1.5 rounded-xl border border-zinc-700 px-4 py-2 text-sm font-medium text-zinc-200 transition hover:border-zinc-500 hover:text-zinc-100"
                >
                  Unduh Markdown
                </a>
              )}
              {report && (
                <a
                  href={report.pdfUrl}
                  className="inline-flex items-center gap-1.5 rounded-xl border border-zinc-700 px-4 py-2 text-sm font-medium text-zinc-200 transition hover:border-zinc-500 hover:text-zinc-100"
                >
                  Unduh PDF
                </a>
              )}
            </div>
            {report && (
              <p className="mt-3 text-xs text-zinc-500" role="status">
                ID Laporan: {report.reportId}
              </p>
            )}
            {reportStatus === "error" && (
              <p
                role="alert"
                className="mt-3 rounded-xl border border-red-900/60 bg-red-950/40 px-4 py-3 text-sm text-red-300"
              >
                Gagal membuat laporan. Silakan coba lagi.
              </p>
            )}
          </div>

          {result.citations.length > 0 && (
            <div>
              <h4 className="text-sm font-semibold uppercase tracking-wide text-zinc-400">
                Sumber
              </h4>
              <ul className="mt-3 space-y-3">
                {result.citations.map((citation) => (
                  <li
                    key={citation.evidence_id}
                    id={`citation-${citation.evidence_id}`}
                    className="rounded-xl border border-zinc-800 bg-zinc-950/60 p-4"
                  >
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="rounded bg-sky-500/15 px-1.5 py-0.5 text-xs font-semibold text-sky-400">
                        [{citation.evidence_id}]
                      </span>
                      {citation.source_domain && (
                        <span className="text-xs text-zinc-500">
                          {citation.source_domain}
                        </span>
                      )}
                      <span className="text-xs text-zinc-600">
                        Chunk #{citation.chunk_index}
                      </span>
                    </div>
                    {citation.source_title && (
                      <p className="mt-1.5 text-sm text-zinc-300">
                        {citation.source_title}
                      </p>
                    )}
                    <a
                      href={citation.source_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="mt-2 inline-block text-sm font-medium text-sky-400 transition hover:text-sky-300"
                    >
                      Buka Sumber →
                    </a>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </section>
  );
}

function renderWithCitations(
  line: string,
  lineIndex: number,
  knownIds: Set<string>,
) {
  const parts = line.split(CITATION_PATTERN);
  const citations = line.match(CITATION_PATTERN) ?? [];
  return parts.map((part, partIndex) => (
    <span key={`${lineIndex}-${partIndex}`}>
      {part}
      {partIndex < citations.length && (
        <CitationChip raw={citations[partIndex]} knownIds={knownIds} />
      )}
    </span>
  ));
}

function CitationChip({
  raw,
  knownIds,
}: {
  raw: string;
  knownIds: Set<string>;
}) {
  const ids = raw.match(/E\d+/gi) ?? [];
  const firstId = ids[0]?.toUpperCase();
  const allKnown =
    firstId !== undefined && ids.every((id) => knownIds.has(id.toUpperCase()));
  if (!allKnown) {
    // Should not happen (server strips unknown IDs) — render as plain text.
    return <span>{raw}</span>;
  }
  return (
    <a
      href={`#citation-${firstId}`}
      className="mx-0.5 inline-flex rounded bg-sky-500/15 px-1 py-0.5 text-xs font-semibold text-sky-400 no-underline transition hover:bg-sky-500/25"
    >
      {raw}
    </a>
  );
}

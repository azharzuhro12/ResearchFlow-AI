"use client";

import { useState, type FormEvent } from "react";

import type { SourceResult } from "@/components/source-results";

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8010";
const MIN_QUERY_LENGTH = 3;

type IndexStatus = "idle" | "loading" | "success" | "error";
type RetrieveStatus = "idle" | "loading" | "success" | "error";

interface IndexOutcome {
  indexedSources: number;
  failedSources: number;
  totalChunks: number;
}

interface RetrievedChunk {
  text: string;
  source_title: string | null;
  source_url: string;
  source_domain: string | null;
  chunk_index: number;
  distance: number | null;
}

interface RagPanelProps {
  sources: SourceResult[];
}

export function RagPanel({ sources }: RagPanelProps) {
  const [indexStatus, setIndexStatus] = useState<IndexStatus>("idle");
  const [indexOutcome, setIndexOutcome] = useState<IndexOutcome | null>(null);

  const [question, setQuestion] = useState("");
  const [retrieveStatus, setRetrieveStatus] = useState<RetrieveStatus>("idle");
  const [chunks, setChunks] = useState<RetrievedChunk[] | null>(null);

  const trimmedQuestion = question.trim();
  const canRetrieve =
    trimmedQuestion.length >= MIN_QUERY_LENGTH && retrieveStatus !== "loading";

  async function handleIndexSources() {
    if (indexStatus === "loading" || sources.length === 0) return;

    setIndexStatus("loading");
    setIndexOutcome(null);
    setRetrieveStatus("idle");
    setChunks(null);
    try {
      const response = await fetch(`${API_BASE_URL}/api/research/index`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          sources: sources.map((source) => ({
            title: source.title,
            url: source.url,
            snippet: source.snippet,
            source: source.source,
            published_at: source.published_at,
          })),
        }),
      });
      if (!response.ok) throw new Error(`Request failed: ${response.status}`);
      const data = await response.json();
      if (
        typeof data?.indexed_sources !== "number" ||
        typeof data?.total_chunks !== "number"
      ) {
        throw new Error("Malformed index response");
      }
      setIndexOutcome({
        indexedSources: data.indexed_sources,
        failedSources: data.failed_sources ?? 0,
        totalChunks: data.total_chunks,
      });
      setIndexStatus("success");
    } catch {
      setIndexOutcome(null);
      setIndexStatus("error");
    }
  }

  async function handleRetrieve(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canRetrieve) return;

    setRetrieveStatus("loading");
    setChunks(null);
    try {
      const response = await fetch(`${API_BASE_URL}/api/research/retrieve`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query: trimmedQuestion, top_k: 5 }),
      });
      if (!response.ok) throw new Error(`Request failed: ${response.status}`);
      const data = await response.json();
      if (!Array.isArray(data?.results)) {
        throw new Error("Malformed retrieve response");
      }
      setChunks(data.results);
      setRetrieveStatus("success");
    } catch {
      setChunks(null);
      setRetrieveStatus("error");
    }
  }

  return (
    <section className="space-y-5">
      <div className="rounded-2xl border border-zinc-800 bg-zinc-900/60 p-6">
        <h3 className="text-lg font-semibold text-zinc-100">Basis Pengetahuan</h3>
        <p className="mt-1 text-sm text-zinc-400">
          Ambil konten halaman dari sumber yang terkumpul, pecah menjadi chunk,
          lakukan embedding secara lokal, dan simpan di ChromaDB untuk
          pencarian semantik.
        </p>
        <button
          type="button"
          onClick={handleIndexSources}
          disabled={indexStatus === "loading" || sources.length === 0}
          className="mt-4 inline-flex items-center justify-center gap-2 rounded-xl border border-zinc-700 bg-zinc-900 px-5 py-2.5 text-sm font-semibold text-zinc-200 transition hover:border-sky-500 hover:text-sky-400 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {indexStatus === "loading" && (
            <span
              className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-zinc-600/40 border-t-zinc-400"
              aria-hidden="true"
            />
          )}
          {indexStatus === "loading" ? "Mengindeks sumber..." : "Indeks Sumber"}
        </button>

        {indexStatus === "error" && (
          <div
            role="alert"
            className="mt-4 rounded-xl border border-red-900/60 bg-red-950/40 px-4 py-3 text-sm text-red-300"
          >
            Gagal mengindeks sumber.
          </div>
        )}

        {indexStatus === "success" && indexOutcome && (
          <p className="mt-4 text-sm text-emerald-400" role="status">
            {indexOutcome.indexedSources} sumber terindeks ·{" "}
            {indexOutcome.totalChunks} chunk dibuat
            {indexOutcome.failedSources > 0 && (
              <span className="text-amber-400">
                {" "}
                · {indexOutcome.failedSources} sumber gagal
              </span>
            )}
          </p>
        )}
      </div>

      {indexStatus === "success" && (
        <div className="rounded-2xl border border-zinc-800 bg-zinc-900/60 p-6">
          <form onSubmit={handleRetrieve} className="space-y-4">
            <label
              htmlFor="rag-question"
              className="block text-sm font-medium text-zinc-300"
            >
              Tanyakan ke Basis Pengetahuan Riset
            </label>
            <textarea
              id="rag-question"
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
              rows={2}
              maxLength={2000}
              placeholder="Tulis pertanyaan tentang sumber yang terkumpul..."
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
                disabled={!canRetrieve}
                className="inline-flex shrink-0 items-center justify-center gap-2 rounded-xl bg-sky-500 px-5 py-2.5 text-sm font-semibold text-zinc-950 transition hover:bg-sky-400 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {retrieveStatus === "loading" && (
                  <span
                    className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-zinc-900/40 border-t-zinc-900"
                    aria-hidden="true"
                  />
                )}
                Ambil
              </button>
            </div>
          </form>

          {retrieveStatus === "error" && (
            <div
              role="alert"
              className="mt-4 rounded-xl border border-red-900/60 bg-red-950/40 px-4 py-3 text-sm text-red-300"
            >
              Gagal mengambil dari basis pengetahuan. Silakan coba lagi.
            </div>
          )}
        </div>
      )}

      {retrieveStatus === "success" && chunks !== null && (
        <section className="space-y-4 rounded-2xl border border-zinc-800 bg-zinc-900/60 p-6">
          <h3 className="text-lg font-semibold text-zinc-100">Sumber yang Diambil</h3>
          <p className="text-sm text-zinc-400">
            {chunks.length} chunk berdasarkan kemiripan semantik
          </p>
          {chunks.length === 0 && (
            <p className="text-sm text-zinc-500">
              Tidak ada konten yang cocok. Coba pertanyaan lain atau indeks
              lebih banyak sumber.
            </p>
          )}
          <ul className="space-y-3">
            {chunks.map((chunk, index) => (
              <li
                key={`${chunk.source_url}-${chunk.chunk_index}-${index}`}
                className="rounded-xl border border-zinc-800 bg-zinc-950/60 p-4"
              >
                <p className="text-sm leading-relaxed text-zinc-300">{chunk.text}</p>
                <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-zinc-500">
                  {chunk.source_domain && (
                    <span className="rounded bg-zinc-800 px-1.5 py-0.5">
                      Sumber: {chunk.source_domain}
                    </span>
                  )}
                  <span>Chunk #{chunk.chunk_index}</span>
                  {chunk.distance !== null && (
                    <span>jarak {chunk.distance.toFixed(3)}</span>
                  )}
                </div>
                {chunk.source_title && (
                  <p className="mt-1 truncate text-xs text-zinc-500">
                    {chunk.source_title}
                  </p>
                )}
                <a
                  href={chunk.source_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="mt-2 inline-block text-sm font-medium text-sky-400 transition hover:text-sky-300"
                >
                  Buka Sumber →
                </a>
              </li>
            ))}
          </ul>
        </section>
      )}
    </section>
  );
}

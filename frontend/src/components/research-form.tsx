"use client";

import { useState, type FormEvent } from "react";

import { RagPanel } from "@/components/rag-panel";
import { SynthesisPanel } from "@/components/synthesis-panel";
import { SourceResults, type SourceResult } from "@/components/source-results";

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8010";
const MIN_QUESTION_LENGTH = 10;

type FormStatus = "idle" | "loading" | "success" | "error";
type SearchStatus = "idle" | "loading" | "success" | "error";

interface PlanResult {
  question: string;
  plan: string[];
}

interface SearchOutcome {
  queries: string[];
  sources: SourceResult[];
  totalSources: number;
}

export function ResearchForm() {
  const [question, setQuestion] = useState("");
  const [status, setStatus] = useState<FormStatus>("idle");
  const [result, setResult] = useState<PlanResult | null>(null);

  const [searchStatus, setSearchStatus] = useState<SearchStatus>("idle");
  const [searchOutcome, setSearchOutcome] = useState<SearchOutcome | null>(null);

  const trimmed = question.trim();
  const canSubmit = trimmed.length >= MIN_QUESTION_LENGTH && status !== "loading";

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canSubmit) return;

    setStatus("loading");
    setResult(null);
    setSearchStatus("idle");
    setSearchOutcome(null);
    try {
      const response = await fetch(`${API_BASE_URL}/api/research/plan`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: trimmed }),
      });
      if (!response.ok) throw new Error(`Request failed: ${response.status}`);
      const data = await response.json();
      if (!data?.plan) throw new Error("Missing plan in response");
      setResult({ question: data.question ?? trimmed, plan: data.plan });
      setStatus("success");
    } catch {
      setResult(null);
      setStatus("error");
    }
  }

  async function handleSearchSources() {
    if (!result || searchStatus === "loading") return;

    setSearchStatus("loading");
    setSearchOutcome(null);
    try {
      const response = await fetch(`${API_BASE_URL}/api/research/search`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: result.question }),
      });
      if (!response.ok) throw new Error(`Request failed: ${response.status}`);
      const data = await response.json();
      if (!Array.isArray(data?.queries) || !Array.isArray(data?.sources)) {
        throw new Error("Malformed search response");
      }
      setSearchOutcome({
        queries: data.queries,
        sources: data.sources,
        totalSources: data.total_sources ?? data.sources.length,
      });
      setSearchStatus("success");
    } catch {
      setSearchOutcome(null);
      setSearchStatus("error");
    }
  }

  return (
    <div className="mx-auto w-full max-w-2xl space-y-6">
      <h2 className="text-center text-xl font-semibold text-zinc-200">
        Start a Research
      </h2>

      <form
        onSubmit={handleSubmit}
        className="space-y-4 rounded-2xl border border-zinc-800 bg-zinc-900/60 p-6"
      >
        <label htmlFor="question" className="block text-sm font-medium text-zinc-300">
          What would you like to research?
        </label>
        <textarea
          id="question"
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          rows={3}
          maxLength={2000}
          placeholder="What are the latest RAG techniques in 2026?"
          className="w-full resize-y rounded-xl border border-zinc-700 bg-zinc-950 px-4 py-3 text-zinc-100 placeholder:text-zinc-600 focus:border-sky-500 focus:outline-none focus:ring-1 focus:ring-sky-500"
        />
        <div className="flex flex-col-reverse items-center justify-between gap-3 sm:flex-row">
          <p className="min-h-4 text-xs text-zinc-500">
            {trimmed.length > 0 && trimmed.length < MIN_QUESTION_LENGTH
              ? `Question must be at least ${MIN_QUESTION_LENGTH} characters.`
              : ""}
          </p>
          <button
            type="submit"
            disabled={!canSubmit}
            className="inline-flex shrink-0 items-center justify-center gap-2 rounded-xl bg-sky-500 px-5 py-2.5 text-sm font-semibold text-zinc-950 transition hover:bg-sky-400 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {status === "loading" && (
              <span
                className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-zinc-900/40 border-t-zinc-900"
                aria-hidden="true"
              />
            )}
            {status === "loading" ? "Generating research plan..." : "Generate Research Plan"}
          </button>
        </div>
      </form>

      {status === "error" && (
        <div
          role="alert"
          className="rounded-xl border border-red-900/60 bg-red-950/40 px-4 py-3 text-sm text-red-300"
        >
          Failed to generate research plan.
        </div>
      )}

      {status === "success" && result && (
        <>
          <section className="space-y-4 rounded-2xl border border-zinc-800 bg-zinc-900/60 p-6">
            <h3 className="text-lg font-semibold text-zinc-100">Research Plan</h3>
            <div className="space-y-1">
              <p className="text-xs font-medium uppercase tracking-wider text-zinc-500">
                Research Question
              </p>
              <p className="leading-relaxed text-zinc-200">{result.question}</p>
            </div>
            <ol className="list-decimal space-y-2 pl-5 text-zinc-300 marker:font-medium marker:text-sky-400">
              {result.plan.map((step, index) => (
                <li key={index} className="leading-relaxed">
                  {step}
                </li>
              ))}
            </ol>
          </section>

          <div className="text-center">
            <button
              type="button"
              onClick={handleSearchSources}
              disabled={searchStatus === "loading"}
              className="inline-flex items-center justify-center gap-2 rounded-xl border border-zinc-700 bg-zinc-900 px-5 py-2.5 text-sm font-semibold text-zinc-200 transition hover:border-sky-500 hover:text-sky-400 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {searchStatus === "loading" && (
                <span
                  className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-zinc-600/40 border-t-zinc-400"
                  aria-hidden="true"
                />
              )}
              {searchStatus === "loading" ? "Searching the web..." : "Search Sources"}
            </button>
          </div>
        </>
      )}

      {searchStatus === "error" && (
        <div
          role="alert"
          className="rounded-xl border border-red-900/60 bg-red-950/40 px-4 py-3 text-sm text-red-300"
        >
          Unable to search the web. Please try again.
        </div>
      )}

      {searchStatus === "success" && searchOutcome && (
        <>
          <SourceResults
            queries={searchOutcome.queries}
            sources={searchOutcome.sources}
            totalSources={searchOutcome.totalSources}
          />
          <RagPanel sources={searchOutcome.sources} />
          <SynthesisPanel />
        </>
      )}
    </div>
  );
}

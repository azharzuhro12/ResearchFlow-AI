"use client";

import { useEffect, useState } from "react";

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8010";

type SynthesisStatus = "success" | "insufficient_evidence" | "ungrounded";

interface ReportMetadata {
  report_id: string;
  query: string;
  markdown_filename: string;
  pdf_filename: string;
  created_at: string;
  synthesis_status: SynthesisStatus;
  schedule_id: string | null;
  execution_id: string | null;
}

type ListStatus = "loading" | "ready" | "error";

function formatWhen(iso: string): string {
  try {
    return new Date(iso).toLocaleString(undefined, {
      dateStyle: "medium",
      timeStyle: "short",
    });
  } catch {
    return iso;
  }
}

const SYNTHESIS_STYLES: Record<SynthesisStatus, string> = {
  success: "bg-emerald-950 text-emerald-300",
  insufficient_evidence: "bg-amber-950 text-amber-300",
  ungrounded: "bg-red-950 text-red-300",
};

// Display labels for API enum values (API values stay English — identifiers).
const SYNTHESIS_LABELS: Record<SynthesisStatus, string> = {
  success: "sukses",
  insufficient_evidence: "bukti kurang",
  ungrounded: "tanpa grounding",
};

/**
 * Report catalog (Step 9): metadata of every generated report — interactive
 * runs and scheduled executions alike — straight from SQLite, newest first.
 * Files are downloaded through the backend's validated id-based endpoints;
 * no filesystem paths ever reach the browser.
 */
export function RecentReports() {
  const [reports, setReports] = useState<ReportMetadata[]>([]);
  const [status, setStatus] = useState<ListStatus>("loading");

  // Initial load — promise-callback pattern with a cancelled flag, matching
  // backend-status.tsx (setState only inside async callbacks).
  useEffect(() => {
    let cancelled = false;
    fetch(`${API_BASE_URL}/api/research/reports?limit=20`)
      .then(async (response) => {
        if (!response.ok) throw new Error(`Request failed: ${response.status}`);
        const data = await response.json();
        if (!Array.isArray(data?.reports)) {
          throw new Error("Malformed reports response");
        }
        if (!cancelled) {
          setReports(data.reports);
          setStatus("ready");
        }
      })
      .catch(() => {
        if (!cancelled) setStatus("error");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function handleRefresh() {
    try {
      const response = await fetch(`${API_BASE_URL}/api/research/reports?limit=20`);
      if (!response.ok) throw new Error(`Request failed: ${response.status}`);
      const data = await response.json();
      if (!Array.isArray(data?.reports)) {
        throw new Error("Malformed reports response");
      }
      setReports(data.reports);
      setStatus("ready");
    } catch {
      setStatus("error");
    }
  }

  return (
    <section className="mt-14 space-y-6">
      <div className="space-y-3 text-center">
        <h2 className="text-2xl font-bold tracking-tight text-zinc-100">
          Laporan yang Dihasilkan
        </h2>
        <p className="text-sm text-zinc-400">
          Semua laporan yang pernah dibuat — dari run interaktif maupun
          eksekusi terjadwal — dengan metadata tersimpan di SQLite.
        </p>
      </div>

      <div className="space-y-4 rounded-2xl border border-zinc-800 bg-zinc-900/60 p-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h3 className="text-lg font-semibold text-zinc-100">
            Katalog Laporan{" "}
            <span className="text-sm font-normal text-zinc-500">
              (terbaru dulu)
            </span>
          </h3>
          <button
            type="button"
            onClick={() => void handleRefresh()}
            className="rounded-xl border border-zinc-700 bg-zinc-900 px-4 py-2 text-sm font-semibold text-zinc-200 transition hover:border-sky-500 hover:text-sky-400"
          >
            Muat Ulang
          </button>
        </div>

        {status === "loading" && (
          <p className="text-sm text-zinc-500">Memuat laporan…</p>
        )}
        {status === "error" && (
          <p className="text-sm text-red-400" role="alert">
            Gagal memuat laporan. Apakah backend berjalan di {API_BASE_URL}?
          </p>
        )}
        {status === "ready" && reports.length === 0 && (
          <p className="text-sm text-zinc-500">
            Belum ada laporan. Buat dari formulir riset di atas, atau biarkan
            jadwal berjalan.
          </p>
        )}

        <ul className="space-y-3">
          {reports.map((report) => (
            <li
              key={report.report_id}
              className="rounded-xl border border-zinc-800 bg-zinc-950/60 p-4"
            >
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0 space-y-1">
                  <p className="truncate text-sm font-medium text-zinc-200">
                    {report.query}
                  </p>
                  <div className="flex flex-wrap items-center gap-2 text-xs text-zinc-500">
                    <span
                      className={`rounded px-1.5 py-0.5 ${SYNTHESIS_STYLES[report.synthesis_status]}`}
                    >
                      {SYNTHESIS_LABELS[report.synthesis_status]}
                    </span>
                    <span>{formatWhen(report.created_at)}</span>
                    {report.schedule_id && <span>run terjadwal</span>}
                  </div>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <a
                    href={`${API_BASE_URL}/api/research/reports/${report.report_id}/markdown`}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-1.5 text-xs font-semibold text-zinc-300 transition hover:border-sky-500 hover:text-sky-400"
                  >
                    Markdown
                  </a>
                  <a
                    href={`${API_BASE_URL}/api/research/reports/${report.report_id}/pdf`}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-1.5 text-xs font-semibold text-zinc-300 transition hover:border-sky-500 hover:text-sky-400"
                  >
                    PDF
                  </a>
                </div>
              </div>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}

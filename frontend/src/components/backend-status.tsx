"use client";

import { useEffect, useState } from "react";

type ApiStatus = "checking" | "online" | "offline";

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8010";

const STATUS_STYLES: Record<ApiStatus, { dot: string; pulse: boolean; label: string }> = {
  checking: { dot: "bg-amber-400", pulse: true, label: "Memeriksa..." },
  online: { dot: "bg-emerald-400", pulse: false, label: "Terhubung" },
  offline: { dot: "bg-red-400", pulse: false, label: "Terputus" },
};

export function BackendStatus() {
  const [status, setStatus] = useState<ApiStatus>("checking");

  useEffect(() => {
    let cancelled = false;

    fetch(`${API_BASE_URL}/health`)
      .then((response) => {
        if (!cancelled) setStatus(response.ok ? "online" : "offline");
      })
      .catch(() => {
        if (!cancelled) setStatus("offline");
      });

    return () => {
      cancelled = true;
    };
  }, []);

  const style = STATUS_STYLES[status];

  return (
    <div className="inline-flex items-center gap-2.5 rounded-full border border-zinc-800 bg-zinc-900/60 px-4 py-2 text-sm text-zinc-300">
      <span className="font-medium text-zinc-400">API Backend</span>
      <span
        className={`h-2 w-2 rounded-full ${style.dot}${
          style.pulse ? " animate-pulse" : ""
        }`}
      />
      <span>{style.label}</span>
    </div>
  );
}

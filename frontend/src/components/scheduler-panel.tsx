"use client";

import { useEffect, useState, type FormEvent } from "react";

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8010";
const MIN_QUESTION_LENGTH = 10;
const MIN_INTERVAL_MINUTES = 5;
const MAX_INTERVAL_MINUTES = 525600;
const DEFAULT_TIMEZONE = "Asia/Jakarta";

type ScheduleType = "interval" | "cron";
type ExecutionStatus = "never_run" | "running" | "success" | "failed";
type NotificationStatus = "not_configured" | "sent" | "failed";
type DiscordSetup = "configured" | "not_configured" | "unknown";

interface ScheduleInfo {
  id: string;
  question: string;
  schedule_type: ScheduleType;
  schedule_expression: string;
  interval_minutes: number | null;
  cron_expression: string | null;
  timezone: string;
  enabled: boolean;
  created_at: string;
  last_run_at: string | null;
  next_run_at: string | null;
  last_status: ExecutionStatus;
  last_error: string | null;
  last_report_id: string | null;
  notification_status?: NotificationStatus | null;
}

interface ExecutionInfo {
  id: string;
  schedule_id: string | null;
  trigger_type: "manual" | "scheduled";
  started_at: string;
  completed_at: string | null;
  status: Exclude<ExecutionStatus, "never_run">;
  error: string | null;
  report_id: string | null;
  source_count: number | null;
  notification_status?: NotificationStatus | null;
}

type ListStatus = "loading" | "ready" | "error";

function formatWhen(iso: string | null): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString(undefined, {
      dateStyle: "medium",
      timeStyle: "short",
    });
  } catch {
    return iso;
  }
}

const STATUS_STYLES: Record<ExecutionStatus, string> = {
  never_run: "bg-zinc-800 text-zinc-400",
  running: "bg-sky-950 text-sky-300",
  success: "bg-emerald-950 text-emerald-300",
  failed: "bg-red-950 text-red-300",
};

const NOTIFICATION_STYLES: Record<NotificationStatus, string> = {
  not_configured: "bg-zinc-800 text-zinc-400",
  sent: "bg-indigo-950 text-indigo-300",
  failed: "bg-red-950 text-red-300",
};

export function SchedulerPanel() {
  const [question, setQuestion] = useState("");
  const [scheduleType, setScheduleType] = useState<ScheduleType>("interval");
  const [intervalMinutes, setIntervalMinutes] = useState("30");
  const [cronExpression, setCronExpression] = useState("0 8 * * *");
  const [timezone, setTimezone] = useState(DEFAULT_TIMEZONE);

  const [schedules, setSchedules] = useState<ScheduleInfo[]>([]);
  const [listStatus, setListStatus] = useState<ListStatus>("loading");
  const [creating, setCreating] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [discordSetup, setDiscordSetup] = useState<DiscordSetup>("unknown");

  // Execution history (Step 9) — loaded on demand for ONE open schedule.
  const [historyFor, setHistoryFor] = useState<string | null>(null);
  const [historyRows, setHistoryRows] = useState<ExecutionInfo[]>([]);
  const [historyStatus, setHistoryStatus] = useState<ListStatus>("loading");

  const trimmed = question.trim();
  const parsedInterval = Number.parseInt(intervalMinutes, 10);
  const intervalValid =
    Number.isFinite(parsedInterval) &&
    parsedInterval >= MIN_INTERVAL_MINUTES &&
    parsedInterval <= MAX_INTERVAL_MINUTES;
  const cronValid = cronExpression.trim().length > 0;
  const timezoneValid = timezone.trim().length > 0;
  const canCreate =
    trimmed.length >= MIN_QUESTION_LENGTH &&
    (scheduleType === "interval" ? intervalValid : cronValid) &&
    timezoneValid &&
    !creating;

  // Initial load — same promise-callback pattern as backend-status.tsx
  // (setState only inside async callbacks, never synchronously in the
  // effect body).
  useEffect(() => {
    let cancelled = false;
    fetch(`${API_BASE_URL}/api/research/schedules`)
      .then(async (response) => {
        if (!response.ok) throw new Error(`Request failed: ${response.status}`);
        const data = await response.json();
        if (!Array.isArray(data?.schedules)) {
          throw new Error("Malformed schedules response");
        }
        if (!cancelled) {
          setSchedules(data.schedules);
          setListStatus("ready");
        }
      })
      .catch(() => {
        if (!cancelled) setListStatus("error");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Discord setup indicator (Step 8) — booleans only; the backend never
  // reveals the webhook URL. Same promise-callback pattern as above, and
  // a failed fetch just leaves the indicator hidden.
  useEffect(() => {
    let cancelled = false;
    fetch(`${API_BASE_URL}/api/research/notifications/status`)
      .then(async (response) => {
        if (!response.ok) return;
        const data = await response.json();
        if (!cancelled && typeof data?.discord?.configured === "boolean") {
          setDiscordSetup(data.discord.configured ? "configured" : "not_configured");
        }
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  async function refreshSchedules() {
    try {
      const response = await fetch(`${API_BASE_URL}/api/research/schedules`);
      if (!response.ok) throw new Error(`Request failed: ${response.status}`);
      const data = await response.json();
      if (!Array.isArray(data?.schedules)) {
        throw new Error("Malformed schedules response");
      }
      setSchedules(data.schedules);
      setListStatus("ready");
    } catch {
      setListStatus("error");
    }
  }

  async function handleCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canCreate) return;

    setCreating(true);
    setError(null);
    try {
      const body =
        scheduleType === "interval"
          ? {
              question: trimmed,
              schedule_type: "interval",
              interval_minutes: parsedInterval,
              timezone: timezone.trim(),
            }
          : {
              question: trimmed,
              schedule_type: "cron",
              cron_expression: cronExpression.trim(),
              timezone: timezone.trim(),
            };
      const response = await fetch(`${API_BASE_URL}/api/research/schedules`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!response.ok) {
        const data = await response.json().catch(() => null);
        throw new Error(data?.detail ?? `Request failed: ${response.status}`);
      }
      setQuestion("");
      await refreshSchedules();
    } catch (caught) {
      setError(
        caught instanceof Error
          ? `Failed to create schedule: ${caught.message}`
          : "Failed to create schedule."
      );
    } finally {
      setCreating(false);
    }
  }

  // Toggle the execution-history panel for one schedule. Opening always
  // fetches fresh rows (a local SQLite read — cheap, and history can grow
  // behind the open panel when a scheduled run fires).
  async function toggleHistory(scheduleId: string) {
    if (historyFor === scheduleId) {
      setHistoryFor(null);
      return;
    }
    setHistoryFor(scheduleId);
    setHistoryStatus("loading");
    setHistoryRows([]);
    try {
      const response = await fetch(
        `${API_BASE_URL}/api/research/schedules/${scheduleId}/executions?limit=20`
      );
      if (!response.ok) throw new Error(`Request failed: ${response.status}`);
      const data = await response.json();
      if (!Array.isArray(data?.executions)) {
        throw new Error("Malformed executions response");
      }
      setHistoryRows(data.executions);
      setHistoryStatus("ready");
    } catch {
      setHistoryStatus("error");
    }
  }

  async function handleAction(
    scheduleId: string,
    action: "pause" | "resume" | "run" | "delete"
  ) {
    if (busyId !== null) return;
    setBusyId(scheduleId);
    setError(null);
    try {
      const path =
        action === "delete"
          ? `${API_BASE_URL}/api/research/schedules/${scheduleId}`
          : `${API_BASE_URL}/api/research/schedules/${scheduleId}/${action}`;
      const response = await fetch(path, {
        method: action === "delete" ? "DELETE" : "POST",
      });
      if (!response.ok) {
        const data = await response.json().catch(() => null);
        throw new Error(data?.detail ?? `Request failed: ${response.status}`);
      }
      if (action === "delete" && historyFor === scheduleId) {
        setHistoryFor(null); // the schedule this panel belongs to is gone
      }
      await refreshSchedules();
    } catch (caught) {
      setError(
        caught instanceof Error
          ? `Action "${action}" failed: ${caught.message}`
          : `Action "${action}" failed.`
      );
    } finally {
      setBusyId(null);
    }
  }

  return (
    <section className="mt-14 space-y-6">
      <div className="space-y-3 text-center">
        <h2 className="text-2xl font-bold tracking-tight text-zinc-100">
          Scheduled Research
        </h2>
        <p className="text-sm text-zinc-400">
          Run the full research pipeline — search, indexing, synthesis, and
          report generation — automatically on a schedule.
        </p>
      </div>

      <div className="rounded-2xl border border-zinc-800 bg-zinc-900/60 p-6">
        <form onSubmit={handleCreate} className="space-y-4">
          <label
            htmlFor="schedule-question"
            className="block text-sm font-medium text-zinc-300"
          >
            Research Question
          </label>
          <textarea
            id="schedule-question"
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            rows={2}
            maxLength={2000}
            placeholder="e.g. What are the latest developments in Retrieval-Augmented Generation?"
            className="w-full resize-y rounded-xl border border-zinc-700 bg-zinc-950 px-4 py-3 text-zinc-100 placeholder:text-zinc-600 focus:border-sky-500 focus:outline-none focus:ring-1 focus:ring-sky-500"
          />

          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-2">
              <label
                htmlFor="schedule-type"
                className="block text-sm font-medium text-zinc-300"
              >
                Schedule Type
              </label>
              <select
                id="schedule-type"
                value={scheduleType}
                onChange={(event) =>
                  setScheduleType(event.target.value as ScheduleType)
                }
                className="w-full rounded-xl border border-zinc-700 bg-zinc-950 px-4 py-2.5 text-zinc-100 focus:border-sky-500 focus:outline-none focus:ring-1 focus:ring-sky-500"
              >
                <option value="interval">Interval (every N minutes)</option>
                <option value="cron">Cron (e.g. daily at 08:00)</option>
              </select>
            </div>

            {scheduleType === "interval" ? (
              <div className="space-y-2">
                <label
                  htmlFor="schedule-interval"
                  className="block text-sm font-medium text-zinc-300"
                >
                  Interval (minutes, min {MIN_INTERVAL_MINUTES})
                </label>
                <input
                  id="schedule-interval"
                  type="number"
                  min={MIN_INTERVAL_MINUTES}
                  max={MAX_INTERVAL_MINUTES}
                  value={intervalMinutes}
                  onChange={(event) => setIntervalMinutes(event.target.value)}
                  className="w-full rounded-xl border border-zinc-700 bg-zinc-950 px-4 py-2.5 text-zinc-100 focus:border-sky-500 focus:outline-none focus:ring-1 focus:ring-sky-500"
                />
              </div>
            ) : (
              <div className="space-y-2">
                <label
                  htmlFor="schedule-cron"
                  className="block text-sm font-medium text-zinc-300"
                >
                  Cron Expression (5 fields)
                </label>
                <input
                  id="schedule-cron"
                  type="text"
                  value={cronExpression}
                  onChange={(event) => setCronExpression(event.target.value)}
                  maxLength={100}
                  placeholder="0 8 * * *"
                  className="w-full rounded-xl border border-zinc-700 bg-zinc-950 px-4 py-2.5 font-mono text-zinc-100 placeholder:text-zinc-600 focus:border-sky-500 focus:outline-none focus:ring-1 focus:ring-sky-500"
                />
              </div>
            )}

            <div className="space-y-2">
              <label
                htmlFor="schedule-timezone"
                className="block text-sm font-medium text-zinc-300"
              >
                Timezone (IANA)
              </label>
              <input
                id="schedule-timezone"
                type="text"
                value={timezone}
                onChange={(event) => setTimezone(event.target.value)}
                maxLength={64}
                placeholder="Asia/Jakarta"
                className="w-full rounded-xl border border-zinc-700 bg-zinc-950 px-4 py-2.5 text-zinc-100 placeholder:text-zinc-600 focus:border-sky-500 focus:outline-none focus:ring-1 focus:ring-sky-500"
              />
            </div>
          </div>

          <div className="flex items-center justify-between gap-3">
            <p className="min-h-4 text-xs text-zinc-500">
              {trimmed.length > 0 && trimmed.length < MIN_QUESTION_LENGTH
                ? `Question must be at least ${MIN_QUESTION_LENGTH} characters.`
                : scheduleType === "interval" &&
                    intervalMinutes !== "" &&
                    !intervalValid
                  ? `Interval must be between ${MIN_INTERVAL_MINUTES} minutes and one year.`
                  : ""}
            </p>
            <button
              type="submit"
              disabled={!canCreate}
              className="inline-flex shrink-0 items-center justify-center gap-2 rounded-xl bg-sky-500 px-5 py-2.5 text-sm font-semibold text-zinc-950 transition hover:bg-sky-400 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {creating && (
                <span
                  className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-zinc-900/40 border-t-zinc-900"
                  aria-hidden="true"
                />
              )}
              Create Schedule
            </button>
          </div>
        </form>

        {error && (
          <div
            role="alert"
            className="mt-4 rounded-xl border border-red-900/60 bg-red-950/40 px-4 py-3 text-sm text-red-300"
          >
            {error}
          </div>
        )}
      </div>

      <div className="space-y-4 rounded-2xl border border-zinc-800 bg-zinc-900/60 p-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-wrap items-center gap-3">
            <h3 className="text-lg font-semibold text-zinc-100">Your Schedules</h3>
            {discordSetup !== "unknown" && (
              <span
                className={`inline-flex items-center gap-1.5 rounded-lg px-2 py-1 text-xs font-medium ${
                  discordSetup === "configured"
                    ? "bg-indigo-950/60 text-indigo-300"
                    : "bg-zinc-800 text-zinc-400"
                }`}
                title="Discord notifications are configured on the backend (Settings are backend-only)."
              >
                Discord:{" "}
                {discordSetup === "configured" ? "Configured" : "Not configured"}
              </span>
            )}
          </div>
          <button
            type="button"
            onClick={() => void refreshSchedules()}
            className="rounded-xl border border-zinc-700 bg-zinc-900 px-4 py-2 text-sm font-semibold text-zinc-200 transition hover:border-sky-500 hover:text-sky-400"
          >
            Refresh
          </button>
        </div>

        {listStatus === "loading" && (
          <p className="text-sm text-zinc-500">Loading schedules…</p>
        )}
        {listStatus === "error" && (
          <p className="text-sm text-red-400" role="alert">
            Unable to load schedules. Is the backend running on{" "}
            {API_BASE_URL}?
          </p>
        )}
        {listStatus === "ready" && schedules.length === 0 && (
          <p className="text-sm text-zinc-500">
            No schedules yet. Schedules are persisted in SQLite and survive
            backend restarts.
          </p>
        )}

        <ul className="space-y-3">
          {schedules.map((schedule) => (
            <li
              key={schedule.id}
              className="rounded-xl border border-zinc-800 bg-zinc-950/60 p-4"
            >
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0 space-y-1">
                  <p className="truncate text-sm font-medium text-zinc-200">
                    {schedule.question}
                  </p>
                  <div className="flex flex-wrap items-center gap-2 text-xs text-zinc-500">
                    <span className="rounded bg-zinc-800 px-1.5 py-0.5 font-mono">
                      {schedule.schedule_expression}
                    </span>
                    <span>{schedule.timezone}</span>
                    <span
                      className={
                        schedule.enabled
                          ? "text-emerald-400"
                          : "text-amber-400"
                      }
                    >
                      {schedule.enabled ? "Enabled" : "Paused"}
                    </span>
                    <span
                      className={`rounded px-1.5 py-0.5 ${STATUS_STYLES[schedule.last_status]}`}
                    >
                      {schedule.last_status}
                    </span>
                    {schedule.notification_status && (
                      <span
                        className={`rounded px-1.5 py-0.5 ${NOTIFICATION_STYLES[schedule.notification_status]}`}
                        title="Discord notification outcome for the latest run."
                      >
                        notify: {schedule.notification_status}
                      </span>
                    )}
                  </div>
                  <p className="text-xs text-zinc-500">
                    Next run: {formatWhen(schedule.next_run_at)} · Last run:{" "}
                    {formatWhen(schedule.last_run_at)}
                    {schedule.last_report_id && (
                      <>
                        {" · Report: "}
                        <a
                          href={`${API_BASE_URL}/api/research/reports/${schedule.last_report_id}/markdown`}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="font-mono text-sky-400 underline-offset-2 hover:underline"
                        >
                          {schedule.last_report_id.slice(0, 12)}… MD
                        </a>
                        {" / "}
                        <a
                          href={`${API_BASE_URL}/api/research/reports/${schedule.last_report_id}/pdf`}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-sky-400 underline-offset-2 hover:underline"
                        >
                          PDF
                        </a>
                      </>
                    )}
                  </p>
                  {schedule.last_error && (
                    <p className="text-xs text-red-400">
                      Last error: {schedule.last_error}
                    </p>
                  )}
                </div>

                <div className="flex flex-wrap items-center gap-2">
                  <button
                    type="button"
                    onClick={() => void toggleHistory(schedule.id)}
                    aria-expanded={historyFor === schedule.id}
                    className={`rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-1.5 text-xs font-semibold transition disabled:cursor-not-allowed disabled:opacity-50 ${
                      historyFor === schedule.id
                        ? "border-sky-500 text-sky-400"
                        : "text-zinc-300 hover:border-sky-500 hover:text-sky-400"
                    }`}
                  >
                    History
                  </button>
                  {schedule.enabled ? (
                    <button
                      type="button"
                      disabled={busyId === schedule.id}
                      onClick={() => void handleAction(schedule.id, "pause")}
                      className="rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-1.5 text-xs font-semibold text-zinc-300 transition hover:border-amber-500 hover:text-amber-400 disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      Pause
                    </button>
                  ) : (
                    <button
                      type="button"
                      disabled={busyId === schedule.id}
                      onClick={() => void handleAction(schedule.id, "resume")}
                      className="rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-1.5 text-xs font-semibold text-zinc-300 transition hover:border-emerald-500 hover:text-emerald-400 disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      Resume
                    </button>
                  )}
                  <button
                    type="button"
                    disabled={busyId === schedule.id}
                    onClick={() => void handleAction(schedule.id, "run")}
                    className="rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-1.5 text-xs font-semibold text-zinc-300 transition hover:border-sky-500 hover:text-sky-400 disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    Run Now
                  </button>
                  <button
                    type="button"
                    disabled={busyId === schedule.id}
                    onClick={() => void handleAction(schedule.id, "delete")}
                    className="rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-1.5 text-xs font-semibold text-zinc-300 transition hover:border-red-500 hover:text-red-400 disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    Delete
                  </button>
                </div>
              </div>

              {historyFor === schedule.id && (
                <div className="mt-4 space-y-2 rounded-xl border border-zinc-800 bg-zinc-900/40 p-4">
                  <p className="text-xs font-semibold uppercase tracking-wide text-zinc-400">
                    Execution History (newest first, persisted in SQLite)
                  </p>
                  {historyStatus === "loading" && (
                    <p className="text-sm text-zinc-500">Loading history…</p>
                  )}
                  {historyStatus === "error" && (
                    <p className="text-sm text-red-400" role="alert">
                      Unable to load execution history.
                    </p>
                  )}
                  {historyStatus === "ready" && historyRows.length === 0 && (
                    <p className="text-sm text-zinc-500">
                      This schedule has not run yet.
                    </p>
                  )}
                  {historyStatus === "ready" &&
                    historyRows.map((execution) => (
                      <div
                        key={execution.id}
                        className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-zinc-800 bg-zinc-950/60 px-3 py-2 text-xs text-zinc-400"
                      >
                        <div className="flex flex-wrap items-center gap-2">
                          <span
                            className={`rounded px-1.5 py-0.5 ${STATUS_STYLES[execution.status]}`}
                          >
                            {execution.status}
                          </span>
                          <span>{execution.trigger_type}</span>
                          <span>{formatWhen(execution.started_at)}</span>
                          {execution.source_count !== null && (
                            <span>{execution.source_count} sources</span>
                          )}
                          {execution.notification_status && (
                            <span
                              className={`rounded px-1.5 py-0.5 ${NOTIFICATION_STYLES[execution.notification_status]}`}
                            >
                              notify: {execution.notification_status}
                            </span>
                          )}
                        </div>
                        {execution.report_id && (
                          <div className="flex items-center gap-2">
                            <a
                              href={`${API_BASE_URL}/api/research/reports/${execution.report_id}/markdown`}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="text-sky-400 underline-offset-2 hover:underline"
                            >
                              Markdown
                            </a>
                            <a
                              href={`${API_BASE_URL}/api/research/reports/${execution.report_id}/pdf`}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="text-sky-400 underline-offset-2 hover:underline"
                            >
                              PDF
                            </a>
                          </div>
                        )}
                        {execution.error && (
                          <p
                            role="alert"
                            className="w-full truncate text-red-400"
                            title={execution.error}
                          >
                            {execution.error}
                          </p>
                        )}
                      </div>
                    ))}
                </div>
              )}
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}

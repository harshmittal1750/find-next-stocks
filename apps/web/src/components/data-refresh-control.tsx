"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import type { RefreshJob, RefreshProvider } from "@/lib/types";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000";
const RUNNING_STATUSES = new Set(["queued", "running"]);
const NUMBER = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });
const DATE_TIME = new Intl.DateTimeFormat("en-IN", {
  dateStyle: "medium",
  timeStyle: "short",
});

function jobLabel(
  job: RefreshJob | null,
  starting: boolean,
  providersLoaded: boolean,
  selectedCount: number,
) {
  if (starting) return "Starting refresh";
  if (job && RUNNING_STATUSES.has(job.status)) {
    return `Refreshing ${Math.round(job.progress)}%`;
  }
  if (!providersLoaded) return "Loading providers";
  if (!selectedCount) return "Select providers";
  return `Refresh selected · ${selectedCount}`;
}

function statusLabel(status: RefreshJob["status"]) {
  if (status === "completed") return "Complete";
  if (status === "completed_with_warnings") return "Finished with warnings";
  if (status === "failed") return "Failed";
  if (status === "queued") return "Queued";
  return "Running";
}

function stageStatus(status: RefreshJob["stages"][number]["status"]) {
  if (status === "completed") return "Done";
  if (status === "skipped") return "Skipped";
  if (status === "failed") return "Failed";
  if (status === "running") return "Running";
  return "Waiting";
}

function lastRefreshLabel(value: string | null) {
  if (!value) return "Never refreshed";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Refresh time unavailable";
  return `Last refresh ${DATE_TIME.format(date)}`;
}

export function DataRefreshControl() {
  const router = useRouter();
  const [job, setJob] = useState<RefreshJob | null>(null);
  const [providers, setProviders] = useState<RefreshProvider[]>([]);
  const [selectedProviders, setSelectedProviders] = useState<Set<string>>(new Set());
  const [providersLoaded, setProvidersLoaded] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const refreshedJob = useRef<string | null>(null);
  const selectionInitialized = useRef(false);

  const loadRefreshState = useCallback(
    async (initializeSelection: boolean, signal?: AbortSignal) => {
      const response = await fetch(`${API_BASE}/api/v1/refresh`, {
        cache: "no-store",
        signal,
      });
      if (!response.ok) throw new Error(`Refresh status returned ${response.status}`);
      const payload = (await response.json()) as {
        job: RefreshJob | null;
        providers: RefreshProvider[];
      };
      setProviders(payload.providers);
      setProvidersLoaded(true);
      setJob(payload.job);
      if (initializeSelection && !selectionInitialized.current) {
        const activeSelection =
          payload.job && RUNNING_STATUSES.has(payload.job.status)
            ? payload.job.selected_providers
            : payload.providers
                .filter((provider) => provider.available)
                .map((provider) => provider.provider);
        setSelectedProviders(new Set(activeSelection));
        selectionInitialized.current = true;
      }
      return payload.job;
    },
    [],
  );

  const updateJob = useCallback(
    (nextJob: RefreshJob | null) => {
      setJob(nextJob);
      if (
        nextJob &&
        !RUNNING_STATUSES.has(nextJob.status) &&
        refreshedJob.current !== nextJob.job_id
      ) {
        refreshedJob.current = nextJob.job_id;
        router.refresh();
        void loadRefreshState(false).catch(() => {
          setError("Data refreshed, but provider timestamps could not be reloaded.");
        });
      }
    },
    [loadRefreshState, router],
  );

  useEffect(() => {
    const controller = new AbortController();
    async function loadLatest() {
      try {
        const latestJob = await loadRefreshState(true, controller.signal);
        if (latestJob && RUNNING_STATUSES.has(latestJob.status)) setExpanded(true);
      } catch (requestError) {
        if (requestError instanceof Error && requestError.name !== "AbortError") {
          setError("Refresh service is unavailable. The research table remains readable.");
        }
      }
    }
    void loadLatest();
    return () => controller.abort();
  }, [loadRefreshState]);

  const activeJobId = job && RUNNING_STATUSES.has(job.status) ? job.job_id : null;

  useEffect(() => {
    if (!activeJobId) return;
    let cancelled = false;
    const poll = async () => {
      try {
        const response = await fetch(`${API_BASE}/api/v1/refresh/${activeJobId}`, {
          cache: "no-store",
        });
        if (response.status === 404) {
          if (!cancelled) {
            setJob(null);
            setError("The API restarted, so the previous in-memory progress record expired.");
          }
          return;
        }
        if (!response.ok) throw new Error(`Refresh status returned ${response.status}`);
        const payload = (await response.json()) as { job: RefreshJob };
        if (!cancelled) {
          setError(null);
          updateJob(payload.job);
        }
      } catch {
        if (!cancelled) setError("Progress polling paused. The backend job may still be running.");
      }
    };
    const interval = window.setInterval(() => void poll(), 1000);
    void poll();
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [activeJobId, updateJob]);

  const running = Boolean(job && RUNNING_STATUSES.has(job.status));
  const selectedCount = selectedProviders.size;

  function toggleProvider(provider: string) {
    setSelectedProviders((current) => {
      const next = new Set(current);
      if (next.has(provider)) next.delete(provider);
      else next.add(provider);
      return next;
    });
  }

  async function startRefresh() {
    setStarting(true);
    setError(null);
    setExpanded(true);
    try {
      const response = await fetch(`${API_BASE}/api/v1/refresh`, {
        method: "POST",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ providers: Array.from(selectedProviders) }),
      });
      const payload = (await response.json()) as {
        job?: RefreshJob;
        detail?: string;
      };
      if (!response.ok || !payload.job) {
        throw new Error(payload.detail || `Refresh request returned ${response.status}`);
      }
      updateJob(payload.job);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Unable to start refresh");
    } finally {
      setStarting(false);
    }
  }

  return (
    <div className="relative">
      <div className="flex items-center gap-2">
        <button
          aria-expanded={expanded}
          className="min-h-9 rounded-md px-2.5 font-mono text-[10px] uppercase tracking-[0.12em] text-stone-500 transition-colors hover:bg-stone-900 hover:text-stone-200 active:-translate-y-px"
          onClick={() => setExpanded((current) => !current)}
          type="button"
        >
          {expanded ? "Hide providers" : "Providers"}
        </button>
        <button
          className="min-h-9 rounded-md border border-[var(--accent)] px-3 font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--accent)] transition-[color,background-color,transform] hover:bg-[var(--accent)] hover:text-stone-950 active:-translate-y-px disabled:cursor-not-allowed disabled:border-stone-700 disabled:text-stone-600 disabled:hover:bg-transparent"
          disabled={running || starting || !providersLoaded || selectedCount === 0}
          onClick={() => void startRefresh()}
          type="button"
        >
          {jobLabel(job, starting, providersLoaded, selectedCount)}
        </button>
      </div>

      {expanded ? (
        <section
          aria-live="polite"
          className="absolute right-0 top-12 z-20 w-[min(92vw,560px)] border border-stone-800 bg-stone-950 shadow-[0_24px_60px_-28px_rgba(28,25,23,0.95)]"
        >
          <div className="flex items-start justify-between gap-6 border-b border-stone-800 px-5 py-4">
            <div>
              <p className="font-mono text-[10px] uppercase tracking-[0.16em] text-[var(--accent)]">
                Provider refresh
              </p>
              <p className="mt-2 text-sm font-medium text-stone-100">
                {job ? statusLabel(job.status) : "Ready to start"}
              </p>
              <p className="mt-1 max-w-md text-xs leading-5 text-stone-500">
                {job?.message ?? "Choose the sources to refresh in this run."}
              </p>
            </div>
            <span className="font-mono text-lg text-stone-200">{Math.round(job?.progress ?? 0)}%</span>
          </div>

          <div className="h-0.5 overflow-hidden bg-stone-900">
            <div
              className="h-full origin-left bg-[var(--accent)] transition-transform duration-500"
              style={{ transform: `scaleX(${Math.max(0, Math.min(100, job?.progress ?? 0)) / 100})` }}
            />
          </div>

          {error ? (
            <p className="border-b border-stone-800 px-5 py-3 text-xs leading-5 text-red-300">{error}</p>
          ) : null}

          <div className="max-h-[55vh] overflow-y-auto">
            {!providersLoaded ? (
              <div className="space-y-px bg-stone-900">
                {[0, 1, 2].map((item) => (
                  <div className="animate-pulse bg-stone-950 px-5 py-4" key={item}>
                    <div className="h-2.5 w-36 bg-stone-800" />
                    <div className="mt-2 h-2 w-52 bg-stone-900" />
                  </div>
                ))}
              </div>
            ) : (
              <div className="divide-y divide-stone-900">
                {providers.map((provider) => {
                  const checked = selectedProviders.has(provider.provider);
                  const disabled = running || starting || !provider.available;
                  return (
                    <label
                      className={`grid grid-cols-[auto_1fr_auto] items-start gap-3 px-5 py-3.5 transition-colors ${
                        provider.available && !running ? "cursor-pointer hover:bg-stone-900/60" : "cursor-not-allowed"
                      }`}
                      key={provider.provider}
                    >
                      <input
                        aria-label={`Refresh ${provider.label}`}
                        checked={checked}
                        className="mt-0.5 h-3.5 w-3.5 accent-[var(--accent)] disabled:opacity-30"
                        disabled={disabled}
                        onChange={() => toggleProvider(provider.provider)}
                        type="checkbox"
                      />
                      <span className="min-w-0">
                        <span className="block text-xs text-stone-300">{provider.label}</span>
                        <span className="mt-1 block font-mono text-[10px] text-stone-600">
                          {lastRefreshLabel(provider.last_refresh_at)}
                        </span>
                        {provider.reason ? (
                          <span className="mt-1 block text-[11px] leading-4 text-stone-600">
                            {provider.reason}
                          </span>
                        ) : null}
                      </span>
                      <span
                        className={`font-mono text-[9px] uppercase tracking-[0.12em] ${
                          checked ? "text-[var(--accent)]" : "text-stone-700"
                        }`}
                      >
                        {provider.available ? (checked ? "Selected" : "Available") : "Unavailable"}
                      </span>
                    </label>
                  );
                })}
              </div>
            )}

            {job ? (
              <div className="border-t border-stone-800">
                <p className="px-5 py-2.5 font-mono text-[9px] uppercase tracking-[0.14em] text-stone-700">
                  Current run
                </p>
                <div className="divide-y divide-stone-900 border-t border-stone-900">
                  {job.stages.map((stage) => (
                    <div className="grid grid-cols-[1fr_auto] gap-4 px-5 py-3.5" key={stage.stage_id}>
                      <div className="min-w-0">
                        <div className="flex items-center gap-2">
                          <p className="truncate text-xs font-medium text-stone-300">{stage.label}</p>
                          {stage.status === "running" ? (
                            <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-[var(--accent)]" />
                          ) : null}
                        </div>
                        <p className="mt-1 text-[11px] leading-4 text-stone-600">{stage.message}</p>
                        {stage.status === "running" ? (
                          <div className="mt-2 h-px overflow-hidden bg-stone-900">
                            <div
                              className="h-full origin-left bg-stone-500 transition-transform duration-500"
                              style={{ transform: `scaleX(${stage.progress / 100})` }}
                            />
                          </div>
                        ) : null}
                      </div>
                      <div className="text-right">
                        <p
                          className={`font-mono text-[10px] uppercase tracking-[0.12em] ${
                            stage.status === "failed"
                              ? "text-red-300"
                              : stage.status === "skipped"
                                ? "text-amber-300"
                                : stage.status === "completed"
                                  ? "text-stone-400"
                                  : "text-stone-600"
                          }`}
                        >
                          {stageStatus(stage.status)}
                        </p>
                        {stage.observations_written ? (
                          <p className="mt-1 font-mono text-[10px] text-stone-700">
                            {NUMBER.format(stage.observations_written)} rows
                          </p>
                        ) : null}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            ) : null}
          </div>

          {job ? (
            <div className="grid grid-cols-3 border-t border-stone-800 px-5 py-3 font-mono text-[10px] text-stone-600">
              <span>{NUMBER.format(job.total_stocks)} stocks</span>
              <span className="text-center">{NUMBER.format(job.raw_responses_archived)} responses</span>
              <span className="text-right">{NUMBER.format(job.observations_written)} observations</span>
            </div>
          ) : null}
        </section>
      ) : null}
    </div>
  );
}

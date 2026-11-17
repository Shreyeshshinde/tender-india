"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState, useTransition } from "react";
import type { RefreshRun } from "@/lib/api";
import { dateTime } from "@/lib/format";
import { startRefresh } from "./actions";

// "Refresh tenders": runs every scraper once. Only one refresh runs at a time; while it
// runs, this checks every 15 seconds and reloads the list when it has finished.
export default function RefreshButton({ run: initialRun, running: initialRunning }: {
  run: RefreshRun | null;
  running: boolean;
}) {
  const router = useRouter();
  const [run, setRun] = useState(initialRun);
  const [running, setRunning] = useState(initialRunning);
  const [error, setError] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();

  useEffect(() => {
    setRun(initialRun);
    setRunning(initialRunning);
  }, [initialRun, initialRunning]);

  useEffect(() => {
    if (!running) return;
    const timer = setInterval(async () => {
      try {
        const r = await fetch("/api/refresh", { cache: "no-store" });
        const body = await r.json();
        setRun(body.run);
        if (!body.running) {
          setRunning(false);
          router.refresh();
        }
      } catch {
        /* try again next tick */
      }
    }, 15000);
    return () => clearInterval(timer);
  }, [running, router]);

  function click() {
    setError(null);
    startTransition(async () => {
      const r = await startRefresh();
      if (!r.ok) {
        setError(r.error);
        return;
      }
      setRun(r.data.run);
      setRunning(true);
    });
  }

  let note: React.ReactNode = "Not refreshed from the website yet";
  if (running && run) {
    note = run.status === "queued"
      ? "Starting the scrapers..."
      : <>Checking every portal since <span suppressHydrationWarning>{dateTime(run.started_at)}</span>. This takes a while; you can leave this page.</>;
  } else if (run?.status === "done") {
    note = <>Last refresh <span suppressHydrationWarning>{dateTime(run.finished_at)}</span>: {run.new_count ?? 0} new</>;
  } else if (run?.status === "failed") {
    note = <>Last refresh failed: {run.message || "unknown error"}</>;
  }

  return (
    <div className="refresh">
      <button type="button" className="refresh-btn" onClick={click} disabled={running || pending}>
        {(running || pending) && <span className="spinner" aria-hidden />}
        {running ? "Refreshing..." : pending ? "Starting..." : "Refresh tenders"}
      </button>
      <span className={`refresh-note ${error || run?.status === "failed" ? "bad" : ""}`} aria-live="polite">
        {error ? `Could not start: ${error}` : note}
      </span>
    </div>
  );
}

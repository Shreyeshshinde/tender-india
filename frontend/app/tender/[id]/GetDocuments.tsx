"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import type { DocsStatus } from "@/lib/api";

const MESSAGES: Record<string, string> = {
  requested: "Fetching the files from the portal. This usually takes a few minutes.",
  captcha:
    "The government portal asks for a CAPTCHA before downloading these files. They are in the queue for manual download.",
  failed: "We could not get these files automatically. The tender may have been closed or removed from the portal.",
  partial: "Some files are ready. The rest could not be downloaded automatically.",
};

export default function GetDocuments({ tenderId, status }: { tenderId: number; status: DocsStatus }) {
  const router = useRouter();
  const [current, setCurrent] = useState<DocsStatus>(status);
  const [busy, setBusy] = useState(false);

  // While waiting, check every 10 seconds and refresh the page when files arrive.
  useEffect(() => {
    if (current !== "requested") return;
    let tries = 0;
    const timer = setInterval(async () => {
      tries += 1;
      try {
        const r = await fetch(`/api/tenders/${tenderId}`, { cache: "no-store" });
        const t = await r.json();
        if (t.docs_status !== "requested") {
          setCurrent(t.docs_status);
          router.refresh();
        }
      } catch {
        /* try again next tick */
      }
      if (tries > 60) clearInterval(timer);
    }, 10000);
    return () => clearInterval(timer);
  }, [current, tenderId, router]);

  async function request() {
    setBusy(true);
    try {
      const r = await fetch(`/api/tenders/${tenderId}/documents/request`, { method: "POST" });
      const body = await r.json();
      setCurrent(body.docs_status);
    } finally {
      setBusy(false);
    }
  }

  if (current === "done") return null;

  return (
    <div className="getdocs">
      {current && MESSAGES[current] && (
        <p className={`docs-msg ${current}`}>
          {current === "requested" && <span className="spinner" aria-hidden />}
          {MESSAGES[current]}
        </p>
      )}
      {(current === null || current === "failed" || current === "partial") && (
        <button className="btn" onClick={request} disabled={busy}>
          {busy ? "Requesting..." : current ? "Try again" : "Get documents"}
        </button>
      )}
    </div>
  );
}

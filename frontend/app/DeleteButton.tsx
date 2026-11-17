"use client";

import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";
import { deleteTender, restoreTender } from "./actions";

// Delete hides a tender for good: later refreshes will not add it again.
// In the "Deleted" list the same button restores it.
export default function DeleteButton({ id, deleted = false, goTo }: {
  id: number;
  deleted?: boolean;
  goTo?: string;          // where to go after deleting (detail page -> list)
}) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);

  function click() {
    if (!deleted && !window.confirm("Delete this tender? It will not come back when tenders are refreshed. You can restore it from the Deleted list.")) {
      return;
    }
    setError(null);
    startTransition(async () => {
      const r = deleted ? await restoreTender(id) : await deleteTender(id);
      if (!r.ok) {
        setError(r.error);
        return;
      }
      if (goTo) router.push(goTo);
      else router.refresh();
    });
  }

  return (
    <span className="del-wrap">
      <button type="button" className={`del-btn ${deleted ? "restore" : ""}`} onClick={click} disabled={pending}>
        {pending ? (deleted ? "Restoring..." : "Deleting...") : deleted ? "Restore" : "Delete"}
      </button>
      {error && <span className="del-error" role="alert">{error}</span>}
    </span>
  );
}

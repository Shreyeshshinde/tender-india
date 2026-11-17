"use client";

import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";
import { addSource, removeSource } from "../actions";

export function AddSourceForm() {
  const router = useRouter();
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [pending, startTransition] = useTransition();

  function submit(e: React.FormEvent) {
    e.preventDefault();
    setMsg(null);
    startTransition(async () => {
      const r = await addSource(name.trim(), url.trim());
      if (!r.ok) {
        setMsg({ ok: false, text: r.error });
        return;
      }
      setName("");
      setUrl("");
      setMsg({ ok: true, text: "Added. It will be read on the next refresh." });
      router.refresh();
    });
  }

  return (
    <form className="src-form" onSubmit={submit}>
      <input required minLength={2} maxLength={120} placeholder="Name, e.g. Maharashtra Police"
             value={name} onChange={(e) => setName(e.target.value)} aria-label="Website name" />
      <input required type="url" placeholder="https://..." value={url}
             onChange={(e) => setUrl(e.target.value)} aria-label="Website address" />
      <button type="submit" disabled={pending}>{pending ? "Adding..." : "Add website"}</button>
      {msg && <p className={`src-msg ${msg.ok ? "ok" : "bad"}`} role="status">{msg.text}</p>}
    </form>
  );
}

export function RemoveSourceButton({ id, name }: { id: number; name: string }) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  return (
    <button type="button" className="del-btn" disabled={pending} onClick={() => {
      if (!window.confirm(`Stop checking "${name}"? Tenders already found from it stay.`)) return;
      startTransition(async () => {
        await removeSource(id);
        router.refresh();
      });
    }}>
      {pending ? "Removing..." : "Remove"}
    </button>
  );
}

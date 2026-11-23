"use client";

import { useState } from "react";

export default function CopyTenderId({ id }: { id: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(id);
    } catch {
      const r = document.createRange();
      r.selectNodeContents(document.getElementById("tender-id-box")!);
      getSelection()?.removeAllRanges();
      getSelection()?.addRange(r);
      document.execCommand("copy");
    }
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };
  return (
    <div className="id-search">
      <div className="id-search-label">Search this Tender ID on the portal</div>
      <div className="id-search-row">
        <code id="tender-id-box" className="id-search-value">{id}</code>
        <button type="button" className="btn secondary id-copy" onClick={copy}>
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
    </div>
  );
}

// Display helpers: Indian money format, dates, "days left".

export function rupees(v: number | null | undefined): string {
  if (v == null) return "Not specified";
  if (v >= 1e7) return `₹${(v / 1e7).toFixed(2)} Cr`;
  if (v >= 1e5) return `₹${(v / 1e5).toFixed(2)} Lakh`;
  return `₹${v.toLocaleString("en-IN")}`;
}

export function dateTime(v: string | null | undefined): string {
  if (!v) return "Not specified";
  const d = new Date(v);
  return d.toLocaleString("en-IN", {
    day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit",
  });
}

export function dateOnly(v: string | null | undefined): string {
  if (!v) return "Not specified";
  return new Date(v).toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" });
}

// Dates from the API are Indian time without a zone. We compare against Indian "now".
function nowIST(): Date {
  const s = new Date().toLocaleString("en-US", { timeZone: "Asia/Kolkata" });
  return new Date(s);
}

export function timeLeft(v: string | null | undefined): { label: string; tone: "urgent" | "soon" | "ok" | "closed" } {
  if (!v) return { label: "No closing date", tone: "ok" };
  const ms = new Date(v).getTime() - nowIST().getTime();
  if (ms <= 0) return { label: "Closed", tone: "closed" };
  const hours = Math.floor(ms / 36e5);
  if (hours < 24) return { label: `${hours}h left`, tone: "urgent" };
  const days = Math.floor(hours / 24);
  if (days <= 3) return { label: `${days} day${days > 1 ? "s" : ""} left`, tone: "urgent" };
  if (days <= 7) return { label: `${days} days left`, tone: "soon" };
  return { label: `${days} days left`, tone: "ok" };
}

export const FIELD_NAMES: Record<string, string> = {
  closing_at: "Closing date",
  opening_at: "Opening date",
  doc_end_at: "Document download end",
  value: "Tender value",
  emd: "EMD",
  tender_fee: "Tender fee",
  title: "Title",
  documents: "New documents",
};

"use server";

// Admin actions (delete, restore, refresh). They run on the website's server, which adds
// the secret ADMIN_TOKEN, so the token never reaches the browser.
import { revalidatePath } from "next/cache";
import type { RefreshRun } from "@/lib/api";

const API = process.env.API_URL || "http://localhost:8000";

type Result<T> = { ok: true; data: T } | { ok: false; error: string };

async function admin<T>(path: string, method: "POST" | "DELETE"): Promise<Result<T>> {
  const headers: Record<string, string> = {};
  if (process.env.ADMIN_TOKEN) headers["X-Admin-Token"] = process.env.ADMIN_TOKEN;
  try {
    const res = await fetch(`${API}${path}`, { method, headers, cache: "no-store" });
    const body = await res.json().catch(() => null);
    if (!res.ok) return { ok: false, error: body?.detail || `Server answered ${res.status}` };
    return { ok: true, data: body as T };
  } catch {
    return { ok: false, error: "Cannot reach the server" };
  }
}

export async function deleteTender(id: number) {
  const r = await admin<{ id: number }>(`/api/tenders/${id}`, "DELETE");
  revalidatePath("/");
  return r;
}

export async function restoreTender(id: number) {
  const r = await admin<{ id: number }>(`/api/tenders/${id}/restore`, "POST");
  revalidatePath("/");
  return r;
}

export async function startRefresh() {
  return admin<{ started: boolean; run: RefreshRun; running: boolean }>("/api/refresh", "POST");
}

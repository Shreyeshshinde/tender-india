"use server";

// Admin actions (delete, restore, refresh). They run on the website's server, which adds
// the secret ADMIN_TOKEN, so the token never reaches the browser.
import { revalidatePath } from "next/cache";
import type { RefreshRun } from "@/lib/api";

const API = process.env.API_URL || "http://localhost:8000";

type Result<T> = { ok: true; data: T } | { ok: false; error: string };

async function admin<T>(path: string, method: "POST" | "DELETE", json?: unknown): Promise<Result<T>> {
  const headers: Record<string, string> = {};
  if (process.env.ADMIN_TOKEN) headers["X-Admin-Token"] = process.env.ADMIN_TOKEN;
  if (json !== undefined) headers["Content-Type"] = "application/json";
  try {
    const res = await fetch(`${API}${path}`, {
      method, headers, cache: "no-store", body: json === undefined ? undefined : JSON.stringify(json),
    });
    const body = await res.json().catch(() => null);
    if (!res.ok) {
      const d = body?.detail;
      const msg = Array.isArray(d) ? "Check the name and address" : d;
      return { ok: false, error: msg || `Server answered ${res.status}` };
    }
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

export async function addSource(name: string, url: string) {
  const r = await admin<{ id: number }>("/api/sources", "POST", { name, url });
  revalidatePath("/sources");
  return r;
}

export async function removeSource(id: number) {
  const r = await admin<{ id: number }>(`/api/sources/${id}`, "DELETE");
  revalidatePath("/sources");
  return r;
}

export async function startRefresh() {
  return admin<{ started: boolean; run: RefreshRun; running: boolean }>("/api/refresh", "POST");
}

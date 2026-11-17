// Talks to the FastAPI backend.
const API = process.env.API_URL || "http://localhost:8000";

export type TenderSummary = {
  id: number;
  source: string;
  source_name: string;
  focus: "security" | "ground_search" | null;
  focus_label: string | null;
  focus_terms: string | null;
  tender_id: string;
  ref_no: string | null;
  title: string | null;
  organisation: string | null;
  state: string | null;
  district: string | null;
  location: string | null;
  category: string | null;
  value: number | null;
  emd: number | null;
  quantity?: string | null;
  org_chain?: string | null;
  published_at: string | null;
  closing_at: string | null;
  opening_at: string | null;
  document_count: number;
  files_ready: number;
  is_active: boolean;
  first_seen_at?: string | null;
  is_new?: boolean;
  deleted_at?: string | null;
};

export type RefreshRun = {
  id: number;
  trigger: string;
  status: "queued" | "running" | "done" | "failed";
  active: boolean;
  requested_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  new_count: number | null;
  message: string | null;
};

export type TenderDoc = {
  id: number;
  section: string | null;
  name: string;
  description: string | null;
  size: string | null;
  file_status: "stored" | "captcha" | "failed" | "deleted" | null;
  file_bytes: number | null;
  available: boolean;
  download_url: string | null;
  error: string | null;
};

export type DocsStatus = "requested" | "done" | "partial" | "captcha" | "failed" | null;

export type TenderChange = {
  field: string;
  old_value: string | null;
  new_value: string | null;
  changed_at: string;
};

export type TenderFull = TenderSummary & {
  description: string | null;
  org_chain: string | null;
  tender_type: string | null;
  product_category: string | null;
  form_of_contract: string | null;
  tender_fee: number | null;
  pincode: string | null;
  period_days: string | null;
  authority_name: string | null;
  authority_address: string | null;
  doc_start_at: string | null;
  doc_end_at: string | null;
  bid_start_at: string | null;
  pre_bid_at: string | null;
  source_url: string | null;
  portal_url?: string | null;
  document_url?: string | null;
  docs_status: DocsStatus;
  docs_requested_at: string | null;
  docs_fetched_at: string | null;
  docs_enabled?: boolean;
  documents: TenderDoc[];
  changes: TenderChange[];
};

export type Facet = { value: string; count: number };
export type DistrictFacet = Facet & { state: string };

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${API}${path}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`API ${res.status} on ${path}`);
  return res.json();
}

export const api = {
  tenders: (params: URLSearchParams) =>
    get<{ total: number; page: number; page_size: number; items: TenderSummary[] }>(
      `/api/tenders?${params.toString()}`
    ),
  tender: (id: string) => get<TenderFull>(`/api/tenders/${id}`),
  filters: (state?: string, focus?: string) =>
    get<{
      views?: Record<string, number>;
      deleted?: number;
      states: Facet[];
      districts: DistrictFacet[];
      organisations: Facet[];
      categories: Facet[];
      sources: (Facet & { label: string })[];
    }>(
      `/api/filters?${new URLSearchParams({ ...(state ? { state } : {}), ...(focus ? { focus } : {}) })}`
    ),
  stats: (focus?: string) =>
    get<{
      active: number;
      closing_this_week: number;
      organisations: number;
      last_updated: string | null;
      refresh?: RefreshRun | null;
      refresh_running?: boolean;
    }>(
      `/api/stats${focus ? `?focus=${focus}` : ""}`
    ),
};

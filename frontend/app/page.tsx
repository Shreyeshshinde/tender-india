import Link from "next/link";
import AutoSubmit from "./AutoSubmit";
import DeleteButton from "./DeleteButton";
import RefreshButton from "./RefreshButton";
import { api } from "@/lib/api";
import { dateOnly, dateTime, rupees, timeLeft } from "@/lib/format";

type SP = Record<string, string | string[] | undefined>;

const MANUAL_PORTALS = [
  { name: "Chhattisgarh eProcurement", url: "https://eproc.cgstate.gov.in/CHEPS/security/getSignInAction.do",
    how: "Click Advanced Search, type the CAPTCHA, Submit" },
  { name: "Karnataka (KPPP) live tenders", url: "https://kppp.karnataka.gov.in/#/portal/searchTender/live",
    how: "Type the CAPTCHA, then Search" },
  { name: "Indian Railways (IREPS)", url: "https://www.ireps.gov.in/",
    how: "Needs mobile OTP login" },
  { name: "SAIL tenders", url: "https://sailtenders.co.in/Home/AdvancedSearch",
    how: "Search Tender (site can be slow)" },
  { name: "Cochin Airport (CIAL)", url: "https://www.cial.aero/tenders",
    how: "Bot check / CAPTCHA" },
  { name: "GMR airports (Delhi, Hyderabad) procurement", url: "https://procurement.gmrgroup.in",
    how: "Vendor login" },
  { name: "Bengaluru Airport (BIAL)", url: "https://www.bengaluruairport.com/corporate/tenders",
    how: "No public list; vendor registration" },
  { name: "Shri Mata Vaishno Devi Shrine Board", url: "https://www.maavaishnodevi.org/tenders.aspx",
    how: "Blocks automated visits" },
];

const KEYS = ["q", "focus", "source", "state", "district", "organisation", "category", "status", "closing_within_days", "sort", "page"];

export default async function Home({ searchParams }: { searchParams: Promise<SP> }) {
  const sp = await searchParams;
  const params = new URLSearchParams();
  for (const k of KEYS) {
    const v = sp[k];
    if (typeof v === "string" && v) params.set(k, v);
  }
  params.set("page_size", "20");

  const focus = typeof sp.focus === "string" && sp.focus ? sp.focus : "relevant";
  const views = [
    { value: "relevant", label: "Security & Ground Search" },
    { value: "security", label: "Security screening" },
    { value: "ground_search", label: "Ground search" },
    { value: "all", label: "All tenders" },
  ];
  const viewLink = (v: string) => {
    const n = new URLSearchParams(params);
    n.delete("page");
    n.delete("page_size");
    if (v === "relevant") n.delete("focus");
    else n.set("focus", v);
    return `/?${n.toString()}`;
  };

  let data, filters, stats;
  try {
    [data, filters, stats] = await Promise.all([
      api.tenders(params),
      api.filters(typeof sp.state === "string" ? sp.state : undefined, focus),
      api.stats(focus),
    ]);
  } catch {
    return (
      <div className="empty">
        <h2>Cannot reach the backend</h2>
        <p>Start it with <code>uvicorn app.main:app --port 8000</code> inside the backend folder.</p>
      </div>
    );
  }

  const page = Number(params.get("page") || 1);
  const pages = Math.max(1, Math.ceil(data.total / data.page_size));
  const link = (p: number) => {
    const n = new URLSearchParams(params);
    n.set("page", String(p));
    n.delete("page_size");
    return `/?${n.toString()}`;
  };
  const val = (k: string) => (typeof sp[k] === "string" ? (sp[k] as string) : "");
  const inBin = val("status") === "deleted";
  const binLink = () => {
    const n = new URLSearchParams(params);
    n.delete("page");
    n.delete("page_size");
    n.set("status", "deleted");
    return `/?${n.toString()}`;
  };

  return (
    <>
      <div className="toolbar">
        <nav className="views" aria-label="Which tenders">
          {views.map((v) => (
            <Link key={v.value} href={viewLink(v.value)} className={`view ${focus === v.value ? "on" : ""}`}>
              {v.label}
              {filters.views && <span className="view-n">{filters.views[v.value] ?? 0}</span>}
            </Link>
          ))}
        </nav>
        <RefreshButton run={stats.refresh ?? null} running={!!stats.refresh_running} />
      </div>

      <section className="stats">
        <div><b>{stats.active.toLocaleString("en-IN")}</b><span>{focus === "all" ? "Active tenders" : "Matching open tenders"}</span></div>
        <div><b>{stats.closing_this_week.toLocaleString("en-IN")}</b><span>Closing in 7 days</span></div>
        <div><b>{stats.organisations.toLocaleString("en-IN")}</b><span>Organisations</span></div>
        <div><b>{stats.last_updated ? dateTime(stats.last_updated) : "Never"}</b><span>Last updated</span></div>
      </section>

      <form className="filters" method="get">
        {focus !== "relevant" && <input type="hidden" name="focus" value={focus} />}
        <input name="q" defaultValue={val("q")} placeholder="Search title, tender ID, reference, place..." className="search" />
        <AutoSubmit name="source" defaultValue={val("source")}>
          <option value="">All portals</option>
          {(filters.sources ?? []).map((s) => <option key={s.value} value={s.value}>{s.label} ({s.count})</option>)}
        </AutoSubmit>
        <AutoSubmit name="state" defaultValue={val("state")} clears="district">
          <option value="">All states</option>
          {(filters.states ?? []).map((s) => <option key={s.value} value={s.value}>{s.value} ({s.count})</option>)}
        </AutoSubmit>
        <AutoSubmit name="district" defaultValue={val("district")}>
          <option value="">All districts</option>
          {val("state")
            ? (filters.districts ?? []).map((d) => (
                <option key={d.value} value={d.value}>{d.value} ({d.count})</option>
              ))
            : Object.entries(
                (filters.districts ?? []).reduce<Record<string, typeof filters.districts>>((acc, d) => {
                  (acc[d.state] ||= []).push(d);
                  return acc;
                }, {})
              ).map(([st, ds]) => (
                <optgroup key={st} label={st}>
                  {ds.map((d) => <option key={st + d.value} value={d.value}>{d.value} ({d.count})</option>)}
                </optgroup>
              ))}
        </AutoSubmit>
        <select name="organisation" defaultValue={val("organisation")}>
          <option value="">All organisations</option>
          {(filters.organisations ?? []).map((s) => <option key={s.value} value={s.value}>{s.value} ({s.count})</option>)}
        </select>
        <select name="category" defaultValue={val("category")}>
          <option value="">All categories</option>
          {(filters.categories ?? []).map((s) => <option key={s.value} value={s.value}>{s.value}</option>)}
        </select>
        <select name="closing_within_days" defaultValue={val("closing_within_days")}>
          <option value="">Any closing date</option>
          <option value="3">Closing in 3 days</option>
          <option value="7">Closing in 7 days</option>
          <option value="30">Closing in 30 days</option>
        </select>
        <select name="status" defaultValue={val("status") || "active"}>
          <option value="active">Open only</option>
          <option value="closed">Closed</option>
          <option value="all">All</option>
          <option value="deleted">Deleted</option>
        </select>
        <select name="sort" defaultValue={val("sort") || "closing"}>
          <option value="closing">Closing soonest</option>
          <option value="published">Newest first</option>
          <option value="value">Highest value</option>
        </select>
        <button type="submit">Search</button>
        <Link href="/" className="reset">Reset</Link>
      </form>

      <details className="manual">
        <summary>Portals that need a CAPTCHA: search these yourself</summary>
        <p>These sites show tenders only after you type a CAPTCHA, so they are not collected automatically.
          Open them, search for <b>DFMD, HHMD, metal detector, scanner</b> etc.</p>
        <ul>
          {MANUAL_PORTALS.map((m) => (
            <li key={m.url}>
              <a href={m.url} target="_blank" rel="noopener noreferrer">{m.name}</a>
              <span className="manual-how"> · {m.how}</span>
            </li>
          ))}
        </ul>
      </details>

      <div className="count-row">
        <p className="count">
          {data.total.toLocaleString("en-IN")} {inBin ? "deleted " : ""}tender{data.total === 1 ? "" : "s"}
          {inBin ? "" : " found"}
        </p>
        {inBin ? (
          <Link href="/" className="bin-link">Back to tenders</Link>
        ) : (
          (filters.deleted ?? 0) > 0 && (
            <Link href={binLink()} className="bin-link">Deleted ({filters.deleted})</Link>
          )
        )}
      </div>
      {inBin && (
        <p className="notice">
          Deleted tenders stay deleted when you refresh. Restore one to show it in the list again.
        </p>
      )}

      {data.items.length === 0 ? (
        <div className="empty">
          <h2>{inBin ? "Nothing deleted" : "No tenders match"}</h2>
          {inBin ? null : focus !== "all" ? (
            <p>
              No open security or ground search tenders in the data collected so far.{" "}
              <Link href={viewLink("all")}>See all tenders</Link> or collect more portals.
            </p>
          ) : (
            <p>Try removing a filter.</p>
          )}
        </div>
      ) : (
        <ul className="list">
          {data.items.map((t) => {
            const left = timeLeft(t.closing_at);
            return (
              <li key={t.id} className="card">
                <div className="card-main">
                  {t.focus_label && (
                    <div className={`focus-tag ${t.focus}`}>
                      {t.focus_label}
                      {t.focus_terms && <span className="focus-terms"> · {t.focus_terms}</span>}
                    </div>
                  )}
                  <Link href={`/tender/${t.id}`} className="title">{t.title || t.tender_id}</Link>
                  {(t.org_chain || t.organisation) && (
                    <div className="org-name">
                      <span className="org-label">Organisation:</span>{" "}
                      {t.org_chain ? t.org_chain.split("||").map((x) => x.trim()).filter(Boolean).join(" › ") : t.organisation}
                    </div>
                  )}
                  {(t.district || t.state) && (
                    <div className="org">{[t.district, t.state].filter(Boolean).join(", ")}</div>
                  )}
                  <div className="meta">
                    {t.is_new && !inBin && <span className="chip new">New</span>}
                    <span className="mono">{t.tender_id}</span>
                    {t.category && <span className="chip">{t.category}</span>}
                    {t.source === "demo"
                      ? <span className="chip demo">Sample</span>
                      : <span className="chip">{t.source_name}</span>}
                    <span>
                      {t.document_count} document{t.document_count === 1 ? "" : "s"}
                      {t.files_ready > 0 && (
                        <b className="ready"> · {t.files_ready} file{t.files_ready > 1 ? "s" : ""} ready</b>
                      )}
                    </span>
                    <DeleteButton id={t.id} deleted={inBin} />
                  </div>
                </div>
                <dl className="card-facts">
                  <div><dt>Closing</dt><dd>{dateTime(t.closing_at)}</dd></div>
                  <div><dt>Value</dt><dd>{rupees(t.value)}</dd></div>
                  <div><dt>EMD</dt><dd>{rupees(t.emd)}</dd></div>
                  {t.quantity && <div><dt>Quantity</dt><dd>{Number(t.quantity).toLocaleString("en-IN") === "NaN" ? t.quantity : Number(t.quantity).toLocaleString("en-IN")}</dd></div>}
                  <div><dt>Published</dt><dd>{dateOnly(t.published_at)}</dd></div>
                </dl>
                <span className={`badge ${left.tone}`}>{left.label}</span>
              </li>
            );
          })}
        </ul>
      )}

      {pages > 1 && (
        <nav className="pager">
          {page > 1 ? <Link href={link(page - 1)}>Previous</Link> : <span />}
          <span>Page {page} of {pages}</span>
          {page < pages ? <Link href={link(page + 1)}>Next</Link> : <span />}
        </nav>
      )}
    </>
  );
}

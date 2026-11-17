import Link from "next/link";
import { api, type CustomSource } from "@/lib/api";
import { dateTime } from "@/lib/format";
import { AddSourceForm, RemoveSourceButton } from "./SourceForms";

export const dynamic = "force-dynamic";

export default async function SourcesPage() {
  let sources: CustomSource[] = [];
  let failed = false;
  try {
    sources = await api.sources();
  } catch {
    failed = true;
  }

  return (
    <article className="detail">
      <Link href="/" className="back">Back to tenders</Link>
      <h1 className="page-title">Websites to scrape</h1>
      <p className="muted">
        Found a new tender website? Add it here. Every refresh (the button on the home page, or the
        automatic one) also reads these websites and saves security and ground search tenders it finds.
      </p>

      <section className="panel">
        <h2>Add a website</h2>
        <AddSourceForm />
        <p className="muted small">
          NIC eProcurement portals (address with <code>/nicgep/app</code>) are read fully, every
          organisation and tender. Any other page is opened and its table rows and links are checked for
          products like DFMD, HHMD, scanners and mine detectors. Pages behind a login or CAPTCHA cannot be read.
        </p>
      </section>

      <section className="panel">
        <h2>Added websites ({sources.length})</h2>
        {failed && <p className="muted">Cannot reach the server right now.</p>}
        {!failed && sources.length === 0 && <p className="muted">None yet.</p>}
        <ul className="src-list">
          {sources.map((s) => (
            <li key={s.id} className="src-row">
              <div className="src-info">
                <b>{s.name}</b> <span className="chip">{s.kind === "nic" ? "NIC portal" : "Web page"}</span>
                <a href={s.url} target="_blank" rel="noreferrer" className="src-url">{s.url}</a>
                <span className="muted small">
                  {s.last_run_at
                    ? <>Last checked {dateTime(s.last_run_at)}: {s.last_status}</>
                    : "Not checked yet: it will be read on the next refresh."}
                </span>
              </div>
              <RemoveSourceButton id={s.id} name={s.name} />
            </li>
          ))}
        </ul>
      </section>
    </article>
  );
}

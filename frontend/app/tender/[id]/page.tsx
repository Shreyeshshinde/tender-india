import Link from "next/link";
import { notFound } from "next/navigation";
import { api, TenderFull } from "@/lib/api";
import { dateTime, FIELD_NAMES, rupees, timeLeft } from "@/lib/format";
import GetDocuments from "./GetDocuments";
import CopyTenderId from "./CopyTenderId";
import DeleteButton from "../../DeleteButton";

export default async function TenderPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  let t: TenderFull;
  try {
    t = await api.tender(id);
  } catch {
    notFound();
  }
  const left = timeLeft(t.closing_at);
  const readyCount = t.documents.filter((d) => d.available).length;

  const sections = t.documents.reduce<Record<string, typeof t.documents>>((acc, d) => {
    const k = d.section || "Documents";
    (acc[k] ||= []).push(d);
    return acc;
  }, {});

  const row = (label: string, value: React.ReactNode) =>
    value ? <div className="kv"><dt>{label}</dt><dd>{value}</dd></div> : null;

  return (
    <article className="detail">
      <Link href="/" className="back">Back to all tenders</Link>

      <header className="detail-head">
        <div>
          <h1>{t.title || t.tender_id}</h1>
          <p className="org">{t.org_chain?.replaceAll("||", " › ") || t.organisation}</p>
        </div>
        <div className="head-side">
          <span className={`badge big ${left.tone}`}>{left.label}</span>
          {!t.deleted_at && <DeleteButton id={t.id} goTo="/" />}
        </div>
      </header>

      {t.deleted_at && (
        <p className="notice">
          You deleted this tender. It is hidden from the list and refreshes will not add it again.{" "}
          <DeleteButton id={t.id} deleted />
        </p>
      )}

      {t.source === "demo" && (
        <p className="notice">This is a sample tender for testing the website, not a real one.</p>
      )}

      <section className="dates">
        <div><span>Published</span><b>{dateTime(t.published_at)}</b></div>
        <div><span>Document download ends</span><b>{dateTime(t.doc_end_at)}</b></div>
        <div className="hl"><span>Bid closing</span><b>{dateTime(t.closing_at)}</b></div>
        <div><span>Bid opening</span><b>{dateTime(t.opening_at)}</b></div>
      </section>

      <div className="two-col">
        <section className="panel">
          <h2>Tender details</h2>
          <dl>
            {row("Tender ID", <span className="mono">{t.tender_id}</span>)}
            {row("Portal", t.source_name)}
            {row("Relevant to", t.focus_label ? `${t.focus_label}${t.focus_terms ? ` (matched: ${t.focus_terms})` : ""}` : null)}
            {row("Reference no.", t.ref_no)}
            {row("Tender type", t.tender_type)}
            {row("Category", t.category)}
            {row("Product category", t.product_category)}
            {row("Form of contract", t.form_of_contract)}
            {row("Tender value", rupees(t.value))}
            {row("EMD amount", rupees(t.emd))}
            {t.quantity && row("Quantity", t.quantity)}
            {row("Tender fee", rupees(t.tender_fee))}
            {row("Period of work", t.period_days ? `${t.period_days} days` : null)}
            {row("Location", [t.location, t.pincode].filter(Boolean).join(" - "))}
            {row("District", t.district)}
            {row("State", t.state)}
            {row("Pre-bid meeting", t.pre_bid_at ? dateTime(t.pre_bid_at) : null)}
            {row("Bid submission starts", t.bid_start_at ? dateTime(t.bid_start_at) : null)}
          </dl>
          {t.description && (<><h3>Work description</h3><p className="desc">{t.description}</p></>)}
          {(t.authority_name || t.authority_address) && (
            <>
              <h3>Tender inviting authority</h3>
              <p className="desc">{t.authority_name}<br />{t.authority_address}</p>
            </>
          )}
        </section>

        <aside>
          <section className="panel">
            <h2>
              Documents ({t.documents.length})
              {readyCount > 0 && <span className="ready-pill">{readyCount} ready</span>}
            </h2>
            {t.documents.length === 0 && <p className="muted">No documents listed.</p>}
            {Object.entries(sections).map(([section, docs]) => (
              <div key={section} className="doc-group">
                <h3>{section}</h3>
                <ul className="docs">
                  {docs.map((d, i) => (
                    <li key={i} className="doc-row">
                      <div className="doc-info">
                        <span className="file">{d.name}</span>
                        <span className="muted">{[d.description, d.size].filter(Boolean).join(" · ")}</span>
                      </div>
                      {d.available && d.download_url ? (
                        <a className="dl" href={d.download_url} target="_blank" rel="noreferrer">
                          Download
                        </a>
                      ) : (
                        <span className={`dl-state ${d.file_status || "none"}`}>
                          {d.file_status === "captcha" ? "Needs CAPTCHA"
                            : d.file_status === "failed" ? "Unavailable"
                            : d.file_status === "deleted" ? "Removed (old)"
                            : "Not fetched"}
                        </span>
                      )}
                    </li>
                  ))}
                </ul>
              </div>
            ))}
            {t.documents.length > 0 && t.docs_enabled !== false && (
              <GetDocuments tenderId={t.id} status={t.docs_status} />
            )}
            <div className="portal-btns">
              {(t.portal_url ?? t.source_url) && (
                <a href={(t.portal_url ?? t.source_url)!} target="_blank" rel="noreferrer" className="btn secondary">
                  Open official portal
                </a>
              )}
              {t.document_url && (
                <a href={t.document_url} target="_blank" rel="noreferrer" className="btn">
                  Download bid document
                </a>
              )}
            </div>
            <CopyTenderId id={t.tender_id} />
          </section>

          {t.changes.length > 0 && (
            <section className="panel">
              <h2>Changes (corrigendum)</h2>
              <ul className="changes">
                {t.changes.map((c, i) => (
                  <li key={i}>
                    <b>{FIELD_NAMES[c.field] || c.field}</b>
                    {c.old_value ? <> changed from <s>{c.old_value}</s> to <b>{c.new_value}</b></> : <>: {c.new_value}</>}
                    <span className="muted small">Noticed {dateTime(c.changed_at)}</span>
                  </li>
                ))}
              </ul>
            </section>
          )}
        </aside>
      </div>
    </article>
  );
}

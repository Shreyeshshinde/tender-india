"""
Websites you added on the website ("Websites" page). Run on every refresh.

    python -m app.scraper.custom            # every added website
    python -m app.scraper.custom --id 3     # one of them

Two kinds, chosen automatically from the address:
  nic   a GePNIC portal (address contains /nicgep/app or /eprocure/app): the full
        CPPP crawler is used, same as the built-in NIC portals.
  page  any other page: it is opened in a browser, every table row and link on it is
        read, and rows that match the security / ground search keywords (app/focus.py)
        are saved as tenders with a link back to the page. Rough but works for most
        "list of tenders" pages; dates are picked up when the row contains them.
"""
import argparse
import hashlib
import logging
import re
from urllib.parse import urljoin, urlparse

from .normalize import clean_text, now_ist, parse_date

log = logging.getLogger("custom")

NIC_MARKERS = ("/nicgep/app", "/eprocure/app")

_JS_ITEMS = r"""
() => {
  const out = [];
  document.querySelectorAll('tr').forEach(tr => {
    const a = tr.querySelector('a[href]');
    out.push({text: tr.innerText, href: a ? a.href : null});
  });
  document.querySelectorAll('a[href]').forEach(a => {
    if (!a.closest('tr')) {
      const box = a.closest('li, p, div') || a;
      out.push({text: (box.innerText || a.innerText).slice(0, 1500), href: a.href});
    }
  });
  return out;
}
"""
_DATE = re.compile(r"\b\d{1,2}[-/.](?:\d{1,2}|[A-Za-z]{3})[-/.]\d{2,4}(?:\s+\d{1,2}:\d{2}(?:\s*[AP]M)?)?", re.I)


def kind_for(url):
    return "nic" if any(m in url for m in NIC_MARKERS) else "page"


def source_key(url):
    """Short portal name stored on each tender (the site's host name)."""
    host = (urlparse(url).hostname or "site").replace("www.", "")
    return host[:40]


def item_to_record(src, item):
    """One table row / link from an added page -> tender record, or None."""
    from ..focus import classify
    text = clean_text(item.get("text"))
    if not text or len(text) < 15:
        return None
    if not classify(text)[0]:
        return None
    title = text[:500]
    dates = [d for d in (parse_date(m) for m in _DATE.findall(text)) if d]
    href = item.get("href") or src.url
    if href.startswith(("javascript:", "mailto:")):
        href = src.url
    tid = hashlib.sha1(f"{href}|{title[:200]}".encode()).hexdigest()[:16]
    return {
        "source": source_key(src.url),
        "tender_id": f"web-{tid}",
        "title": title,
        "organisation": src.name,
        "published_at": min(dates) if len(dates) > 1 else None,
        "closing_at": max(dates) if dates else None,
        "source_url": urljoin(src.url, href),
    }


def scrape_page(page, src):
    page.goto(src.url, wait_until="domcontentloaded", timeout=60000)
    try:
        page.wait_for_load_state("networkidle", timeout=20000)
    except Exception:
        pass
    return page.evaluate(_JS_ITEMS)


def run_nic(src):
    from argparse import Namespace

    from .run import run_once
    run_once(Namespace(base_url=src.url.split("?")[0], engine="auto", full=False, all_details=False,
                       with_docs=False, max_orgs=None, max_per_org=None, save_html=None))


def main():
    p = argparse.ArgumentParser(description="Scrape the websites added on the website")
    p.add_argument("--id", type=int)
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    from sqlalchemy import select

    from ..db import SessionLocal, init_db
    from ..models import CustomSource
    from .store import upsert_tender
    init_db()
    db = SessionLocal()
    q = select(CustomSource).where(CustomSource.enabled.is_(True))
    if args.id:
        q = q.where(CustomSource.id == args.id)
    sources = db.scalars(q).all()
    log.info("%d added websites to check", len(sources))
    browser = pw = page = None
    try:
        for src in sources:
            try:
                if src.kind == "nic":
                    run_nic(src)
                    src.last_status, src.last_found = "Checked (NIC portal crawler)", None
                else:
                    if page is None:
                        from playwright.sync_api import sync_playwright
                        pw = sync_playwright().start()
                        browser = pw.chromium.launch()
                        page = browser.new_page(locale="en-IN")
                    recs = {}
                    for item in scrape_page(page, src):
                        rec = item_to_record(src, item)
                        if rec:
                            recs.setdefault(rec["tender_id"], rec)
                    counts = {"new": 0, "changed": 0, "same": 0, "skipped": 0}
                    for rec in recs.values():
                        counts[upsert_tender(db, rec)] += 1
                    src.last_found = len(recs)
                    src.last_status = (f"{len(recs)} matching tenders on the page "
                                       f"({counts['new']} new)" if recs else
                                       "Page read, but no security / ground search tenders on it")
                log.info("%s: %s", src.name, src.last_status)
            except Exception as exc:
                src.last_status = f"Could not read: {str(exc).splitlines()[0][:200]}"
                log.warning("%s: %s", src.name, src.last_status)
            src.last_run_at = now_ist()
            db.commit()
    finally:
        db.commit()
        db.close()
        if browser:
            browser.close()
        if pw:
            pw.stop()


if __name__ == "__main__":
    main()

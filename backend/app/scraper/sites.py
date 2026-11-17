"""
Simple "tender table" websites (one page with a table of tenders).

    python -m app.scraper.sites                 # every site below
    python -m app.scraper.sites --site isro     # one site

Each site is read in a real browser. Where the table is a DataTable we ask it
to show all rows (same as choosing "Show all"); where there are page links we
click "next" page by page. Only security / ground search tenders are saved
(see app/scraper/store.py).
"""
import argparse
import hashlib
import logging
import re
import time
from datetime import datetime

from .normalize import clean_text

log = logging.getLogger("sites")

# cols: field -> column index in the table row
SITES = {
    "isro": {
        "name": "ISRO", "url": "https://eproc.isro.gov.in/home.html", "table": "#tenderListTable",
        "datatable": True, "org": "Indian Space Research Organisation", "state": None,
        "cols": {"tender_no": 0, "organisation": 1, "title": 2, "closing_at": 3, "opening_at": 4},
    },
    "barc": {
        "name": "BARC", "url": "https://www.barc.gov.in/tenders/index.html", "table": "#tender1",
        "datatable": True, "org": "Bhabha Atomic Research Centre", "state": "Maharashtra",
        "cols": {"tender_no": 1, "ref_no": 2, "title": 3, "published_at": 4, "closing_at": 5},
    },
    "shirdi": {
        "name": "Shri Saibaba Sansthan, Shirdi", "url": "https://sai.org.in/en/tenders", "table": "table",
        "datatable": False, "org": "Shri Saibaba Sansthan Trust, Shirdi", "state": "Maharashtra",
        "district": "Ahmadnagar",
        "cols": {"title": 1, "tender_type": 2, "ref_no": 3, "published_at": 4},
    },
    "kmrc": {
        "name": "Kolkata Metro Rail Corporation", "url": "https://www.kmrc.in/tenders.php", "table": "table",
        "datatable": False, "org": "Kolkata Metro Rail Corporation Ltd", "state": "West Bengal",
        "district": "Kolkata",
        "cols": {"tender_no": 1, "title": 2},
    },
    "sbi": {
        "name": "State Bank of India", "url": "https://sbi.bank.in/web/sbi-in-the-news/procurement-news",
        "table": "table", "datatable": False, "org": "State Bank of India", "state": None,
        "next": "a:has-text('›')", "max_pages": 30,
        "cols": {"location": 0, "title": 1, "published_at": 2, "closing_at": 3},
    },
}

_JS_ROWS = r"""
(sel) => {
  const t = document.querySelector(sel);
  if (!t) return [];
  return Array.from(t.querySelectorAll('tbody tr')).map(tr => {
    const tds = Array.from(tr.querySelectorAll('td'));
    const a = tr.querySelector('a[href]');
    return {cells: tds.map(td => td.innerText.trim()), href: a ? a.href : null};
  }).filter(r => r.cells.length >= 3);
}
"""


def _date(text):
    text = clean_text(text)
    if not text:
        return None
    text = re.sub(r"\s+", " ", text)
    for fmt in ("%d-%B-%Y %H:%M", "%d-%b-%Y %H:%M", "%d-%b-%Y", "%d-%B-%Y", "%d/%m/%Y %H:%M",
                "%d/%m/%Y", "%d-%m-%Y %H:%M", "%d-%m-%Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            pass
    return None


def row_to_record(key, row):
    cfg = SITES[key]
    cells = row["cells"]
    get = lambda f: cells[cfg["cols"][f]] if f in cfg["cols"] and cfg["cols"][f] < len(cells) else None
    title = clean_text(get("title"))
    if not title:
        return None
    tid = clean_text(get("tender_no"))
    if tid:
        tid = re.sub(r"\s+", "", tid)[:60]
    else:   # no tender number on the page: stable ID from the text
        tid = hashlib.sha1(f"{title}|{get('ref_no') or ''}|{get('location') or ''}".encode()).hexdigest()[:12]
    org = clean_text(get("organisation")) or cfg["org"]
    location = clean_text(get("location"))
    from .geo import locate
    district, state = locate(None, title, location, org)
    return {
        "source": key,
        "tender_id": f"{key.upper()}_{tid}",
        "ref_no": clean_text((get("ref_no") or "").split("\n")[0]) or None,
        "title": title,
        "organisation": f"{cfg['org']} - {location}" if location else org,
        "org_chain": f"{cfg['org']} || {location or org}",
        "tender_type": clean_text(get("tender_type")),
        "published_at": _date(get("published_at")),
        "closing_at": _date(get("closing_at")),
        "opening_at": _date(get("opening_at")),
        "location": location,
        "state": cfg.get("state") or state,
        "district": cfg.get("district") or district,
        "source_url": row.get("href") or cfg["url"],
        "documents": ([{"section": "Notice", "name": title[:120], "description": "Tender notice",
                        "url": row["href"]}] if row.get("href") and row["href"].startswith("http") else []),
    }


def scrape_site(page, key):
    cfg = SITES[key]
    page.goto(cfg["url"], wait_until="commit", timeout=90000)
    page.wait_for_selector(f"{cfg['table']} tbody tr", timeout=60000)
    page.wait_for_timeout(3000)
    if cfg.get("datatable"):
        # same as choosing "Show all entries"
        page.evaluate("(s) => jQuery(s).DataTable().page.len(-1).draw()", cfg["table"])
        page.wait_for_timeout(2000)
    rows = page.evaluate(_JS_ROWS, cfg["table"])
    if cfg.get("next"):
        seen = {tuple(r["cells"][:2]) for r in rows[:1]}
        for _ in range(cfg.get("max_pages", 20) - 1):
            nxt = page.locator(cfg["next"])
            if not nxt.count() or not nxt.first.is_visible():
                break
            time.sleep(3)
            try:
                nxt.first.click(timeout=15000)
            except Exception:
                log.info("  %s: no more pages (next link not clickable)", cfg["name"])
                break
            page.wait_for_timeout(4000)
            more = page.evaluate(_JS_ROWS, cfg["table"])
            if not more or tuple(more[0]["cells"][:2]) in seen:
                break
            seen.add(tuple(more[0]["cells"][:2]))
            rows += more
    return rows


def run_once(args):
    from playwright.sync_api import sync_playwright

    from ..db import SessionLocal, init_db
    from .store import upsert_tender
    init_db()
    keys = [args.site] if args.site else list(SITES)
    db = SessionLocal()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.show)
        page = browser.new_page(locale="en-IN")
        try:
            for key in keys:
                counts = {"new": 0, "changed": 0, "same": 0, "skipped": 0}
                try:
                    rows = scrape_site(page, key)
                except Exception as exc:
                    log.warning("%s: could not read (%s)", SITES[key]["name"], str(exc).splitlines()[0][:90])
                    continue
                uniq = {}
                for r in rows:
                    rec = row_to_record(key, r)
                    if rec:
                        uniq.setdefault(rec["tender_id"], rec)
                for rec in uniq.values():
                    counts[upsert_tender(db, rec)] += 1
                db.commit()
                log.info("%s: %d tenders read. %d new, %d changed, %d unchanged, %d not relevant",
                         SITES[key]["name"], len(uniq), counts["new"], counts["changed"], counts["same"],
                         counts["skipped"])
                time.sleep(3)
        finally:
            db.commit()
            db.close()
            browser.close()


def main():
    p = argparse.ArgumentParser(description="Simple tender-table websites")
    p.add_argument("--site", choices=list(SITES))
    p.add_argument("--show", action="store_true")
    p.add_argument("--every-hours", type=float, default=None)
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    while True:
        try:
            run_once(args)
        except KeyboardInterrupt:
            return
        except Exception:
            log.exception("sites run failed")
        if not args.every_hours:
            break
        time.sleep(args.every_hours * 3600)


if __name__ == "__main__":
    main()

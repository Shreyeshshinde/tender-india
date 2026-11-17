"""
Adani airports: public tender lists on each airport's own website.

Adani has no central public tender portal. Each airport shows a small table
("No. | Tender Description | Date of Advertisement | Submission of Applications
Date") on its Business > Tenders page, split into Commercial and Procurement.
Equipment purchases are mostly done through registered vendors (Adani SIP /
SAP Ariba), so expect few tenders here.

    python -m app.scraper.adani            # all airports below
    python -m app.scraper.adani --show     # watch the browser

Pages are opened in a real browser (they load the tables with JavaScript).
"""
import argparse
import hashlib
import logging
import re
import time
from datetime import datetime

from .normalize import clean_text

log = logging.getLogger("adani")
SOURCE = "adani"

# name, tender page, state, district
AIRPORTS = [
    ("Navi Mumbai International Airport", "https://navimumbai.adaniairports.com/en/business/tenders", "Maharashtra", "Raigad"),
    ("Chhatrapati Shivaji Maharaj International Airport, Mumbai", "https://csmia-mumbai.adaniairports.com/en/business/tenders", "Maharashtra", "Mumbai Suburban"),
    ("Jaipur International Airport", "https://jaipur.adaniairports.com/en/business/tenders", "Rajasthan", "Jaipur"),
    ("Mangaluru International Airport", "https://mangaluru.adaniairports.com/en/business/tenders", "Karnataka", "Dakshina Kannada"),
    ("Thiruvananthapuram International Airport", "https://thiruvananthapuram.adaniairports.com/en/business/tenders", "Kerala", "Thiruvananthapuram"),
    ("SVPI Airport, Ahmedabad", "https://www.adani.com/svpia-ahmedabad-airport/tenders", "Gujarat", "Ahmedabad"),
    ("CCSI Airport, Lucknow", "https://www.adani.com/ccsia-lucknow-airport/tenders", "Uttar Pradesh", "Lucknow"),
    ("LGBI Airport, Guwahati", "https://www.adani.com/lgbia-guwahati-airport/tenders", "Assam", "Kamrup Metropolitan"),
]

_JS_ROWS = r"""
() => {
  const out = [];
  document.querySelectorAll('table').forEach((tbl, ti) => {
    const head = (tbl.querySelector('thead') || tbl).innerText.toLowerCase();
    if (!head.includes('tender description')) return;
    // section name: nearest tab/heading text before the table, if any
    let section = '';
    const pane = tbl.closest('[id]');
    if (pane) section = pane.id;
    tbl.querySelectorAll('tbody tr').forEach(tr => {
      const tds = Array.from(tr.querySelectorAll('td'));
      if (tds.length < 3) return;
      const a = tr.querySelector('a[href]');
      out.push({table: ti, section, cells: tds.map(td => td.innerText.trim()),
                href: a ? a.href : null});
    });
  });
  return out;
}
"""


def _date(text):
    text = clean_text(text)
    if not text or text.upper() == "N/A":
        return None
    text = text.replace("|", " ")
    text = re.sub(r"\s+", " ", text).strip()
    for fmt in ("%d/%m/%Y %I:%M %p", "%d/%m/%Y %H:%M", "%d/%m/%Y", "%d-%m-%Y %I:%M %p", "%d-%m-%Y"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            pass
    return None


def row_to_record(airport, url, state, district, row):
    cells = row["cells"]
    if not cells or not re.fullmatch(r"\d+\.?", (cells[0] or "").strip()):
        return None                       # only numbered tender rows (skips headers / layout tables)
    desc = clean_text(cells[1]) if len(cells) > 1 else None
    if not desc or desc.lower().startswith("no data"):
        return None
    advert = _date(cells[2]) if len(cells) > 2 else None
    closing = _date(cells[3]) if len(cells) > 3 else None
    # No tender number on these pages: build a stable ID from airport + text.
    key = hashlib.sha1(f"{airport}|{desc}".encode()).hexdigest()[:12]
    section = "Procurement" if row.get("table", 0) % 2 else "Commercial"
    return {
        "source": SOURCE,
        "tender_id": f"ADANI_{key}",
        "title": desc,
        "organisation": airport,
        "org_chain": f"Adani Airport Holdings Ltd || {airport}",
        "category": section,
        "tender_type": "EOI" if "expression of interest" in desc.lower() or "eoi" in desc.lower() else "Tender",
        "published_at": advert,
        "closing_at": closing,
        "state": state,
        "district": district,
        "location": airport,
        "source_url": row.get("href") or url,
        "documents": ([{"section": "Notice", "name": desc[:120], "description": "Tender notice",
                        "url": row["href"]}] if row.get("href") else []),
    }


def run_once(args):
    from playwright.sync_api import sync_playwright

    from ..db import SessionLocal, init_db
    from .store import upsert_tender
    init_db()
    counts = {"new": 0, "changed": 0, "same": 0, "skipped": 0}
    db = SessionLocal()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.show)
        page = browser.new_page(locale="en-IN")
        try:
            for airport, url, state, district in AIRPORTS:
                db.rollback()
                try:
                    page.goto(url, wait_until="commit", timeout=60000)
                    page.wait_for_selector("table", timeout=40000)
                    page.wait_for_timeout(3000)
                    rows = page.evaluate(_JS_ROWS)
                except Exception as exc:
                    log.warning("%s: page did not load (%s)", airport, str(exc).splitlines()[0][:80])
                    continue
                recs = [r for r in (row_to_record(airport, url, state, district, x) for x in rows) if r]
                uniq = {}
                for r in recs:                    # same tender can appear in two tabs
                    uniq.setdefault(r["tender_id"], r)
                recs = list(uniq.values())
                log.info("%s: %d tenders", airport, len(recs))
                for rec in recs:
                    counts[upsert_tender(db, rec)] += 1
                db.commit()
                time.sleep(3)
        finally:
            db.commit()
            db.close()
            browser.close()
    log.info("Adani saved: %d new, %d changed, %d unchanged", counts["new"], counts["changed"], counts["same"])


def main():
    p = argparse.ArgumentParser(description="Adani airports tender pages")
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
            log.exception("Adani run failed")
        if not args.every_hours:
            break
        time.sleep(args.every_hours * 3600)


if __name__ == "__main__":
    main()

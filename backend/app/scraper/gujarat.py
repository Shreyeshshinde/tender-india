"""
Gujarat nProcure (https://tender.nprocure.com) public "Tenders In Progress" list.

The home page shows every tender in progress (~4000) in a table, 10 per page.
We pick the largest page size the page offers (150) and click "Next" through
the pages in a real browser, reading each row:

    ref no | "<Department> Tender Id :<id>  Name Of Work :...  Estimated Contract
    Value : ...  Last Date & Time For Submission : dd-mm-yyyy hh:mm:ss"

    python -m app.scraper.gujarat
    python -m app.scraper.gujarat --max-pages 1     # quick test
"""
import argparse
import logging
import re
import time
from datetime import datetime

from .normalize import clean_text, parse_money

log = logging.getLogger("gujarat")
SOURCE = "gujarat"
HOME = "https://tender.nprocure.com/"
TABLE = "#DataTables_Table_0"

_JS_PAGE = r"""
(sel) => {
  const dt = jQuery(sel).DataTable();
  const rows = Array.from(document.querySelectorAll(sel + ' tbody tr')).map(tr =>
    Array.from(tr.querySelectorAll('td')).map(td => td.innerText));
  return {info: dt.page.info(), rows};
}
"""
_JS_DRAWN = r"""
([sel, page]) => { const i = jQuery(sel).DataTable().page.info();
  return i.page === page && !jQuery(sel + '_processing').is(':visible'); }
"""

_RE_ID = re.compile(r"Tender Id\s*:\s*(\d+)", re.I)
_RE_WORK = re.compile(r"Name Of Work\s*:\s*(.+?)(?:\n\s*\n|Estimated Contract Value|Last Date|$)", re.I | re.S)
_RE_VALUE = re.compile(r"Estimated Contract Value\s*:\s*([\d,\.]+)", re.I)
_RE_LAST = re.compile(r"Last Date & Time For Submission\s*:\s*([\d\-]+\s+[\d:]+)", re.I)


def _dt(text):
    for fmt in ("%d-%m-%Y %H:%M:%S", "%d-%m-%Y %H:%M"):
        try:
            return datetime.strptime(text.strip(), fmt)
        except (AttributeError, ValueError):
            pass
    return None


def row_to_record(cells):
    if len(cells) < 2:
        return None
    blob = cells[1] or ""
    m = _RE_ID.search(blob)
    if not m:
        return None
    dept = clean_text(blob[:m.start()])
    work = _RE_WORK.search(blob)
    work = clean_text(work.group(1)) if work else None
    value = _RE_VALUE.search(blob)
    last = _RE_LAST.search(blob)
    from .geo import locate
    district, state = locate(None, work, dept)
    return {
        "source": SOURCE,
        "tender_id": f"GJ_{m.group(1)}",
        "ref_no": clean_text(cells[0]),
        "title": work,
        "organisation": dept,
        "org_chain": f"Government of Gujarat || {dept}" if dept else None,
        "value": parse_money(value.group(1)) if value else None,
        "closing_at": _dt(last.group(1)) if last else None,
        "state": "Gujarat",
        "district": district if state in (None, "Gujarat") else None,
        "source_url": HOME,
        "documents": [],
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
            page.goto(HOME, wait_until="load", timeout=90000)
            page.wait_for_selector(f"{TABLE} tbody tr a#tenderInProgress", timeout=90000)
            # "Show 150 entries" (largest option the page offers)
            page.select_option(f"{TABLE}_length select", "150")
            page.wait_for_function(_JS_DRAWN, arg=[TABLE, 0], timeout=90000)
            page.wait_for_timeout(2000)
            n = 0
            while True:
                data = page.evaluate(_JS_PAGE, TABLE)
                info = data["info"]
                if n == 0:
                    log.info("Gujarat: %d tenders in progress, %d pages",
                             max(info["recordsTotal"], info["recordsDisplay"]), info["pages"])
                for cells in data["rows"]:
                    rec = row_to_record(cells)
                    if rec:
                        counts[upsert_tender(db, rec)] += 1
                db.commit()
                n += 1
                if info["page"] + 1 >= info["pages"] or (args.max_pages and n >= args.max_pages):
                    break
                time.sleep(3)
                page.locator(f"{TABLE}_next").click()
                page.wait_for_function(_JS_DRAWN, arg=[TABLE, info["page"] + 1], timeout=90000)
                page.wait_for_timeout(1000)
                if n % 5 == 0:
                    log.info("  ... page %d of %d", n, info["pages"])
        finally:
            db.commit()
            db.close()
            browser.close()
    log.info("Gujarat saved: %d new, %d changed, %d unchanged, %d not relevant (not saved)",
             counts["new"], counts["changed"], counts["same"], counts["skipped"])


def main():
    p = argparse.ArgumentParser(description="Gujarat nProcure tenders in progress")
    p.add_argument("--show", action="store_true")
    p.add_argument("--max-pages", type=int, default=None)
    p.add_argument("--every-hours", type=float, default=None)
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    while True:
        try:
            run_once(args)
        except KeyboardInterrupt:
            return
        except Exception:
            log.exception("Gujarat run failed")
        if not args.every_hours:
            break
        time.sleep(args.every_hours * 3600)


if __name__ == "__main__":
    main()

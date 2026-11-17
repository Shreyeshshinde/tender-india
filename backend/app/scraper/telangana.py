"""
Telangana eProcurement (https://tender.telangana.gov.in) public live tender list.

Home page -> "Live Tenders" -> "More..." opens the full list (a DataTable of
~2000 tenders, 10 per page by default). We pick 100 rows per page and click
"Next" through the pages, in a real browser, reading each table page.

    python -m app.scraper.telangana
    python -m app.scraper.telangana --max-pages 2     # quick test
    python -m app.scraper.telangana --portal andhra   # Andhra Pradesh (same software)
"""
import argparse
import logging
import time
from datetime import datetime

from .normalize import clean_text, parse_money

log = logging.getLogger("telangana")
SOURCE = "telangana"
HOME = "https://tender.telangana.gov.in/login.html"
TABLE = "#pagetable13"

_JS_PAGE = r"""
(sel) => {
  const dt = jQuery(sel).DataTable();
  return {info: dt.page.info(), rows: dt.rows({page: 'current'}).data().toArray()};
}
"""
_JS_DRAWN = r"""
([sel, page]) => { const i = jQuery(sel).DataTable().page.info(); return i.page === page &&
  !jQuery(sel + '_processing').is(':visible'); }
"""


def _dt(text):
    text = clean_text(text)
    for fmt in ("%d/%m/%Y %I:%M %p", "%d/%m/%Y %H:%M", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt)
        except (TypeError, ValueError):
            pass
    return None


# Andhra Pradesh runs the same software (same table, minus the "Published" column).
PORTALS = {
    "telangana": {"home": HOME, "state": "Telangana", "prefix": "TS"},
    "andhra": {"home": "https://tender.apeprocurement.gov.in/login.html", "state": "Andhra Pradesh",
               "prefix": "AP"},
}


def row_to_record(cells, portal="telangana"):
    # TS: Department | Tender ID | Notice No | Category | Name of Work | Value | Published | Bid start | Bid close | Action
    # AP: Department | Tender ID | Notice No | Category | Name of Work | Value | Bid start | Bid close | Action
    cfg = PORTALS[portal]
    if len(cells) < 8 or not clean_text(cells[1]):
        return None
    if len(cells) < 10:                         # AP layout: no "Published" column
        cells = cells[:6] + [None] + cells[6:]
    from .geo import locate
    dept = clean_text(cells[0])
    work = clean_text(cells[4])
    district, state = locate(None, work, dept)
    return {
        "source": portal,
        "tender_id": f"{cfg['prefix']}_{clean_text(cells[1])}",
        "ref_no": clean_text(cells[2]),
        "category": clean_text(cells[3]),
        "title": work,
        "organisation": dept,
        "org_chain": f"Government of {cfg['state']} || {dept}" if dept else None,
        "value": parse_money(cells[5]),
        "published_at": _dt(cells[6]),
        "bid_start_at": _dt(cells[7]),
        "closing_at": _dt(cells[8]),
        "state": cfg["state"],
        "district": district if state in (None, cfg["state"]) else None,
        "source_url": cfg["home"],
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
            cfg = PORTALS[args.portal]
            page.goto(cfg["home"], wait_until="commit", timeout=90000)
            if args.portal == "telangana":
                page.wait_for_selector("#tab1default a:has-text('More')", state="attached", timeout=90000)
                page.wait_for_timeout(3000)
                try:
                    page.locator("#tgep-overlay button", has_text="Close").click(timeout=4000)   # notice pop-up
                except Exception:
                    pass
                page.locator("#tab1default a", has_text="More").first.click()
            else:
                # AP: "Current Tenders -> More..." submits the home page form (same as clicking it)
                page.wait_for_selector("#viewCurrentall", state="attached", timeout=90000)
                page.wait_for_timeout(3000)
                with page.expect_navigation(timeout=90000):
                    page.evaluate("() => { document.loginForm.hdnType.value = 'current';"
                                  " temp(tempName, 'loginForm'); document.loginForm.action = 'TenderDetailsHome.html';"
                                  " document.loginForm.method = 'POST'; document.loginForm.submit(); }")
            page.wait_for_selector(f"{TABLE} tbody tr", timeout=90000)
            # "Show 100 records"
            page.evaluate("(sel) => jQuery(sel).DataTable().page.len(100).draw('page')", TABLE)
            page.wait_for_timeout(4000)
            n = 0
            while True:
                data = page.evaluate(_JS_PAGE, TABLE)
                info = data["info"]
                if n == 0:
                    log.info("%s: %d live tenders, %d pages", cfg["state"], max(info["recordsTotal"], info["recordsDisplay"]), info["pages"])
                for cells in data["rows"]:
                    rec = row_to_record(cells, args.portal)
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
    log.info("%s saved: %d new, %d changed, %d unchanged, %d not relevant (not saved)",
             PORTALS[args.portal]["state"], counts["new"], counts["changed"], counts["same"], counts["skipped"])


def main():
    p = argparse.ArgumentParser(description="Telangana live tenders")
    p.add_argument("--portal", choices=list(PORTALS), default="telangana",
                   help="telangana or andhra (same software)")
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
            log.exception("Telangana run failed")
        if not args.every_hours:
            break
        time.sleep(args.every_hours * 3600)


if __name__ == "__main__":
    main()

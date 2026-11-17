"""
Bihar eProc 2.0 (https://eproc2.bihar.gov.in) public "Open area Tender List".

The page is an AngularJS app that downloads ALL active tenders at once and
shows them 20 at a time. We open it in a real browser, ask the page to show
every row, and read the table (tender ID, description, reference, department,
end date). No login, no CAPTCHA.

    python -m app.scraper.bihar
"""
import argparse
import logging
import time
from datetime import datetime

from .normalize import clean_text

log = logging.getLogger("bihar")
SOURCE = "bihar"
LIST_URL = "https://eproc2.bihar.gov.in/EPSV2Web/openarea/tenderListingPage.action"

_JS_ALL_ROWS = r"""
async () => {
  const sel = "tr[ng-repeat^='tender in tenderList']";
  const first = document.querySelector(sel);
  if (!first) return null;
  const scope = angular.element(first).scope().$parent;
  const list = scope.tenderList || [];
  scope.openAreaLoadTendetListCount = list.length + 10;   // show every row
  scope.$apply();
  await new Promise(r => setTimeout(r, 1500));
  const rows = Array.from(document.querySelectorAll(sel)).map(tr => {
    const td = tr.querySelectorAll('td');
    const desc = td[2] ? (td[2].getAttribute('title') || td[2].innerText) : null;
    return {id: td[1] && td[1].innerText.trim(), desc, ref: td[3] && td[3].innerText.trim(),
            dept: td[4] && td[4].innerText.trim(), end: td[5] && td[5].innerText.trim()};
  });
  // dates / internal ids from the data itself
  const byId = {};
  list.forEach(t => { byId[String(t.currentOrgTenderId)] = t; });
  rows.forEach(r => { const t = byId[r.id] || {};
    r.publish = t.currentTenderPublishDate || null; r.start = t.currentbidStartDate || null;
    r.open = t.currentbidOpenDate || null; r.pac = t.currentpacamt || null; });
  return {total: list.length, rows};
}
"""


def _ms(v):
    """Epoch milliseconds (as sent by the portal, Indian time) -> datetime."""
    if not v:
        return None
    from datetime import timedelta, timezone
    return datetime.fromtimestamp(v / 1000, tz=timezone(timedelta(hours=5, minutes=30))).replace(tzinfo=None)


def _end(text):
    text = clean_text(text)
    try:
        return datetime.strptime(text, "%Y-%m-%d %H:%M") if text else None
    except ValueError:
        return None


def row_to_record(r):
    if not r.get("id"):
        return None
    from .geo import locate
    desc = clean_text(r.get("desc"))
    district, state = locate(None, desc, r.get("ref"))
    return {
        "source": SOURCE,
        "tender_id": f"BR_{r['id']}",
        "ref_no": clean_text(r.get("ref")),
        "title": desc,
        "organisation": clean_text(r.get("dept")),
        "org_chain": f"Government of Bihar || {clean_text(r.get('dept'))}" if r.get("dept") else None,
        "published_at": _ms(r.get("publish")),
        "bid_start_at": _ms(r.get("start")),
        "closing_at": _end(r.get("end")),
        "opening_at": _ms(r.get("open")),
        "value": r.get("pac"),
        "state": "Bihar",
        "district": district if state in (None, "Bihar") else None,
        "source_url": LIST_URL,
        "documents": [],
    }


def run_once(args):
    from playwright.sync_api import sync_playwright

    from ..db import SessionLocal, init_db
    from .store import upsert_tender
    init_db()
    counts = {"new": 0, "changed": 0, "same": 0, "skipped": 0}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.show)
        page = browser.new_page(locale="en-IN")
        try:
            page.goto(LIST_URL, wait_until="commit", timeout=90000)
            page.wait_for_selector("tr[ng-repeat^='tender in tenderList']", timeout=90000)
            page.wait_for_timeout(2000)
            data = page.evaluate(_JS_ALL_ROWS)
        finally:
            browser.close()
    if not data:
        log.error("Bihar: tender list did not load")
        return
    log.info("Bihar: %d active tenders on the portal", data["total"])
    db = SessionLocal()
    try:
        for r in data["rows"]:
            rec = row_to_record(r)
            if rec:
                counts[upsert_tender(db, rec)] += 1
        db.commit()
    finally:
        db.close()
    log.info("Bihar saved: %d new, %d changed, %d unchanged, %d not relevant (not saved)",
             counts["new"], counts["changed"], counts["same"], counts["skipped"])


def main():
    p = argparse.ArgumentParser(description="Bihar eProc 2.0 open tenders")
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
            log.exception("Bihar run failed")
        if not args.every_hours:
            break
        time.sleep(args.every_hours * 3600)


if __name__ == "__main__":
    main()

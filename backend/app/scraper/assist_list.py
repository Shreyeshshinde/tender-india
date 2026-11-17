"""
CAPTCHA assist for tender LISTS (Chhattisgarh, Karnataka).

These portals show their tender list only after a CAPTCHA. We never solve or
bypass a CAPTCHA; YOU type it, in a real Chrome window, like a normal user.

    python -m app.scraper.assist_list --portal chhattisgarh
    python -m app.scraper.assist_list --portal karnataka

What happens:
  1. A Chrome window opens on the portal's tender search page.
  2. You type the CAPTCHA and press the search / proceed button.
  3. As soon as the tender table appears, this tool reads it, clicks "Next"
     page by page by itself, and saves relevant tenders to the database.
  4. A copy of each page is saved in debug_pages/<portal>/ (to improve the parser).

Leave the window alone while it pages through. It closes by itself when done.
"""
import argparse
import logging
import re
import time
from datetime import datetime
from pathlib import Path

from .normalize import clean_text, parse_money

log = logging.getLogger("assist_list")

PORTALS = {
    "chhattisgarh": {
        "state": "Chhattisgarh", "prefix": "CG",
        "start": "https://eproc.cgstate.gov.in/CHEPS/security/getSignInAction.do",
        "how": "Click 'Advanced Search', type the CAPTCHA, press 'Submit Captcha AND PROCEED'.",
        "open_js": None,
        # results page: not the sign-in/home page, CAPTCHA box gone
        "not_url": "getSignInAction",
    },
    "karnataka": {
        "state": "Karnataka", "prefix": "KA",
        "start": "https://kppp.karnataka.gov.in/#/portal/searchTender/live",
        "how": "Type the CAPTCHA on the 'Search Tenders' page and press Search.",
        "open_js": None,
        "not_url": None,
    },
}

# Header words -> our fields (first match wins). Works for most tender tables.
HEADER_MAP = [
    ("tender_no", ["tender id", "tender no", "rfq", "tender number", "nit no"]),
    ("ref_no", ["reference", "ref no", "notice number", "nit"]),
    ("title", ["description", "name of work", "title", "tender name", "work"]),
    ("organisation", ["department", "organisation", "organization", "dept", "office", "entity"]),
    ("value", ["estimated", "pac", "value", "amount", "ecv"]),
    ("published_at", ["publish", "published"]),
    ("bid_start_at", ["start date", "bid submission start", "submission start"]),
    ("closing_at", ["due date", "closing", "end date", "last date", "bid submission end", "submission end"]),
]

_JS_TABLES = r"""
() => Array.from(document.querySelectorAll('table')).map(t => {
  const head = Array.from(t.querySelectorAll('thead th, tr:first-child th')).map(th => th.innerText.trim());
  const rows = Array.from(t.querySelectorAll('tbody tr')).map(tr =>
     Array.from(tr.querySelectorAll('td')).map(td => td.innerText.trim())).filter(r => r.length >= 3);
  return {head, rows};
}).filter(t => t.head.join(' ').toLowerCase().includes('tender') && t.rows.length)
  // real tender tables have several columns per row (news boxes have 2-3)
  .map(t => ({head: t.head, rows: t.rows.filter(r => r.length >= 5)})).filter(t => t.rows.length)
"""

_JS_CAPTCHA_VISIBLE = r"""
() => Array.from(document.querySelectorAll('input')).some(i =>
  /captcha/i.test((i.id || '') + (i.name || '') + (i.placeholder || '')) && i.offsetParent !== null)
"""


def _ready(pg, cfg):
    """True once the human has passed the CAPTCHA and a real tender table is shown."""
    try:
        if cfg.get("not_url") and cfg["not_url"] in pg.url:
            return []
        if pg.evaluate(_JS_CAPTCHA_VISIBLE):
            return []
        tables = pg.evaluate(_JS_TABLES)
        return tables if sum(len(t["rows"]) for t in tables) >= 2 else []
    except Exception:
        return []


_NEXT = ["a:has-text('Next')", "button:has-text('Next')", "li.next a", ".paginate_button.next",
         "a[aria-label='Next']", "button[aria-label='Next page']", "a:has-text('>')"]


def _date(text):
    text = clean_text(text)
    if not text:
        return None
    text = re.sub(r"\s+", " ", text.replace("|", " ")).strip()
    for fmt in ("%d-%m-%Y %H:%M", "%d-%m-%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y %I:%M %p",
                "%d-%b-%Y %I:%M %p", "%d-%b-%Y %H:%M", "%b %d, %Y %I:%M %p", "%Y-%m-%d %H:%M",
                "%d-%m-%Y", "%d/%m/%Y", "%d-%b-%Y"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            pass
    return None


def map_columns(head):
    cols = {}
    low = [h.lower() for h in head]
    for field, words in HEADER_MAP:
        for i, h in enumerate(low):
            if i in cols.values():
                continue
            if any(w in h for w in words):
                cols[field] = i
                break
    return cols


def row_to_record(portal, head, cells):
    cfg = PORTALS[portal]
    cols = map_columns(head)
    # Body rows sometimes have one cell less than the header (e.g. no SR NO cell).
    off = len(cells) - len(head) if len(cells) < len(head) else 0

    def get(f):
        i = cols.get(f)
        if i is None:
            return None
        i += off
        return cells[i] if 0 <= i < len(cells) else None

    tid = clean_text(get("tender_no")) or clean_text(get("ref_no"))
    title = clean_text(get("title"))
    if not tid or not title:
        return None
    from .geo import locate
    org = clean_text(get("organisation"))
    district, state = locate(None, title, org)
    return {
        "source": portal,
        "tender_id": cfg["prefix"] + "_" + re.sub(r"\s+", "", tid)[:70],
        "ref_no": clean_text(get("ref_no")),
        "title": title,
        "organisation": org,
        "org_chain": f"Government of {cfg['state']} || {org}" if org else None,
        "value": parse_money(get("value")),
        "published_at": _date(get("published_at")),
        "bid_start_at": _date(get("bid_start_at")),
        "closing_at": _date(get("closing_at")),
        "state": cfg["state"],
        "district": district if state in (None, cfg["state"]) else None,
        "source_url": cfg["start"],
        "documents": [],
    }


def run(args):
    from playwright.sync_api import sync_playwright

    from ..db import SessionLocal, init_db
    from .store import upsert_tender
    init_db()
    cfg = PORTALS[args.portal]
    out = Path("debug_pages") / args.portal
    out.mkdir(parents=True, exist_ok=True)
    counts = {"new": 0, "changed": 0, "same": 0, "skipped": 0}
    db = SessionLocal()
    print(f"\n  A Chrome window is opening on {cfg['state']}.\n  -> {cfg['how']}\n"
          f"  Then leave the window alone; this tool reads the pages itself.\n")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        ctx = browser.new_context(locale="en-IN")
        page = ctx.new_page()
        page.goto(cfg["start"], wait_until="commit", timeout=120000)
        # Wait (up to 10 min) for the human to pass the CAPTCHA and the table to appear.
        deadline = time.time() + 600
        tables = []
        time.sleep(10)                         # give the page time to load first
        while time.time() < deadline:
            for pg in ctx.pages:
                tables = _ready(pg, cfg)
                if tables:
                    page = pg
                    break
            if tables:
                time.sleep(3)                  # let the results finish drawing
                break
            time.sleep(2)
        if not tables:
            log.error("No tender table appeared within 10 minutes. Nothing saved.")
            browser.close()
            return
        log.info("Tender table found. Reading pages...")
        seen_first = set()
        for n in range(1, args.max_pages + 1):
            page.wait_for_timeout(1500)
            (out / f"page_{n:03d}.html").write_text(page.content(), encoding="utf-8")
            tables = page.evaluate(_JS_TABLES)
            first = None
            for t in tables:
                for cells in t["rows"]:
                    rec = row_to_record(args.portal, t["head"], cells)
                    if rec:
                        first = first or rec["tender_id"]
                        counts[upsert_tender(db, rec)] += 1
            db.commit()
            log.info("  page %d: %s", n, f"{counts}")
            if first in seen_first:
                break                                  # "Next" did not move: last page
            seen_first.add(first)
            nxt = None
            for sel in _NEXT:
                loc = page.locator(sel)
                if loc.count() and loc.first.is_visible() and loc.first.is_enabled():
                    cls = (loc.first.get_attribute("class") or "") + (loc.first.get_attribute("aria-disabled") or "")
                    if "disabled" not in cls and "true" not in cls:
                        nxt = loc.first
                        break
            if not nxt:
                break
            time.sleep(3)
            nxt.click()
        db.close()
        browser.close()
    log.info("%s saved: %d new, %d changed, %d unchanged, %d not relevant (not saved). Pages kept in %s",
             cfg["state"], counts["new"], counts["changed"], counts["same"], counts["skipped"], out)


def import_saved(args):
    """Read tender pages the user saved from their own browser (Ctrl+S -> 'Webpage, HTML only')."""
    from playwright.sync_api import sync_playwright

    from ..db import SessionLocal, init_db
    from .store import upsert_tender
    init_db()
    files = sorted(Path(args.from_html).glob("*.htm*"))
    if not files:
        log.error("No .html files in %s", args.from_html)
        return
    counts = {"new": 0, "changed": 0, "same": 0, "skipped": 0}
    db = SessionLocal()
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.route("**/*", lambda r: r.continue_() if r.request.url.startswith("file:") else r.abort())
        for f in files:
            page.goto(f.resolve().as_uri(), wait_until="domcontentloaded")
            n = 0
            for t in page.evaluate(_JS_TABLES):
                for cells in t["rows"]:
                    rec = row_to_record(args.portal, t["head"], cells)
                    if rec:
                        n += 1
                        counts[upsert_tender(db, rec)] += 1
            db.commit()
            log.info("%s: %d tenders read", f.name, n)
        browser.close()
    db.close()
    log.info("%s saved: %d new, %d changed, %d unchanged, %d not relevant (not saved)",
             PORTALS[args.portal]["state"], counts["new"], counts["changed"], counts["same"], counts["skipped"])


def main():
    p = argparse.ArgumentParser(description="CAPTCHA-assisted tender list reader")
    p.add_argument("--portal", choices=list(PORTALS), required=True)
    p.add_argument("--max-pages", type=int, default=500)
    p.add_argument("--from-html", help="Folder of pages you saved from your own browser (Ctrl+S)")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.from_html:
        import_saved(args)
    else:
        run(args)


if __name__ == "__main__":
    main()

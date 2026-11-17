"""
GeM (Government e-Marketplace) bids: https://bidplus.gem.gov.in/all-bids

Most police / CAPF / airport / jail purchases of DFMD, HHMD, scanners etc.
happen on GeM, not on the NIC portals. This module opens the public
"All Bids" page in a real browser (Playwright), types each product keyword
into the search box, and reads the result cards, the same way a person would.

    python -m app.scraper.gem                      # all keywords below
    python -m app.scraper.gem --keyword "hhmd"     # one search
    python -m app.scraper.gem --max-pages 2        # quick test
    python -m app.scraper.gem --show               # watch the browser work
    python -m app.scraper.gem --every-hours 6      # keep updating

Good behaviour: one page action every few seconds, no login, no CAPTCHA
solving. If GeM ever shows a CAPTCHA or blocks us, the run stops.
"""
import argparse
import logging
import re
import time
from datetime import datetime

from ..focus import classify
from .normalize import clean_text

log = logging.getLogger("gem")

ALL_BIDS_URL = "https://bidplus.gem.gov.in/all-bids"
DOC_URL = "https://bidplus.gem.gov.in/showbidDocument/{}"
SOURCE = "gem"

# GeM searches the full text of bids. Keep these specific: each one is a
# separate search, and results are re-checked by app/focus.py anyway.
KEYWORDS = [
    "metal detector", "door frame metal detector", "dfmd", "hhmd",
    "walk through metal detector", "baggage scanner", "xbis",
    "explosive detector", "explosive trace detector", "bomb detection", "nljd",
    "under vehicle", "liquid scanner", "shoe scanner", "mine detector", "deep search metal detector",
    "ground penetrating radar", "ied", "uxo", "bomb disposal",
    # added 2026-11-13: wider net (slower, but results are still filtered by app/focus.py)
    "hand held metal detector", "x ray baggage", "x ray scanner", "baggage inspection",
    "explosive vapour", "explosive trace", "trace detector", "bomb blanket", "contraband",
    "metal detector pole", "multi zone", "frisking", "security screening", "parcel scanner",
]

_JS_READ_CARDS = r"""
() => Array.from(document.querySelectorAll('#bidCard .card')).map(c => {
  const links = Array.from(c.querySelectorAll('.block_header a.bid_no_hover'));
  const bid = links[0];
  const item = c.querySelector('.card-body .col-md-4 .row');
  const pop = item && item.querySelector('a[data-content]');
  const dept = c.querySelectorAll('.card-body .col-md-5 .row')[1];
  const q = c.querySelectorAll('.card-body .col-md-4 .row')[1];
  return {
    bid_no: bid ? bid.textContent.trim() : null,
    bid_href: bid ? bid.getAttribute('href') : null,
    ra_no: links[1] ? links[1].textContent.trim() : null,
    items: pop ? pop.getAttribute('data-content') : (item ? item.textContent.replace(/^\s*Items:\s*/, '') : null),
    quantity: q ? q.textContent.replace(/^\s*Quantity:\s*/, '').trim() : null,
    dept: dept ? dept.innerText : null,
    start: (c.querySelector('.start_date') || {}).textContent || null,
    end: (c.querySelector('.end_date') || {}).textContent || null,
  };
})
"""


def parse_gem_date(text):
    text = clean_text(text)
    if not text:
        return None
    for fmt in ("%d-%m-%Y %I:%M %p", "%d-%m-%Y %H:%M:%S", "%d-%m-%Y"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            pass
    return None


def card_to_record(card):
    """One GeM result card -> record for store.upsert_tender."""
    bid_no = clean_text(card.get("bid_no"))
    if not bid_no:
        return None
    lines = [clean_text(x) for x in (card.get("dept") or "").splitlines()]
    lines = [x for x in lines if x]
    ministry = lines[0] if lines else None
    department = lines[-1] if len(lines) > 1 else None
    items = clean_text(card.get("items"))
    m = re.search(r"(\d+)$", card.get("bid_href") or "")
    from .geo import locate
    district, state = locate(None, items, " ".join(lines))
    return {
        "source": SOURCE,
        "tender_id": bid_no,
        "ref_no": clean_text(card.get("ra_no")),
        "title": items,
        "organisation": department or ministry,
        "org_chain": " || ".join(lines) or None,
        "category": "Goods",
        "tender_type": "RA" if "/R/" in bid_no else "Bid",
        "product_category": None,
        "description": f"Quantity: {card.get('quantity')}" if card.get("quantity") else None,
        "quantity": clean_text(card.get("quantity")),
        "bid_start_at": parse_gem_date(card.get("start")),
        "published_at": parse_gem_date(card.get("start")),
        "closing_at": parse_gem_date(card.get("end")),
        "state": state,
        "district": district,
        "source_url": DOC_URL.format(m.group(1)) if m else ALL_BIDS_URL,
        "documents": ([{"section": "Bid document", "name": f"{bid_no}.pdf",
                        "description": "GeM bid document", "url": DOC_URL.format(m.group(1))}]
                      if m else []),
    }


class GemBrowser:
    def __init__(self, headless=True, delay=3.0):
        from playwright.sync_api import sync_playwright
        self.delay = delay
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=headless)
        self.page = self._browser.new_page(locale="en-IN")

    def close(self):
        try:
            self._browser.close()
        finally:
            self._pw.stop()

    def _blocked(self):
        html = self.page.content().lower()
        return "captcha" in html and "searchbid" not in html

    def search(self, keyword, max_pages=None):
        """Yield result cards for one keyword, page by page."""
        p = self.page
        p.goto(ALL_BIDS_URL, wait_until="networkidle", timeout=60000)
        if self._blocked():
            raise RuntimeError("GeM is asking for a CAPTCHA. Stopping (we never bypass it).")
        p.fill("#searchBid", keyword)
        p.click("#searchBidRA")
        page_no = 1
        while True:
            try:
                p.wait_for_selector("#bidCard .card, #bidCard .totalRecord", timeout=30000)
            except Exception:
                log.info("  '%s': no results", keyword)
                return
            p.wait_for_timeout(1000)
            cards = p.evaluate(_JS_READ_CARDS)
            if page_no == 1:
                total = p.inner_text("#bidCard .totalRecord") if p.query_selector("#bidCard .totalRecord") else "?"
                log.info("  '%s': %s", keyword, total.strip())
            if not cards:
                return
            yield from cards
            if max_pages and page_no >= max_pages:
                return
            nxt = p.query_selector("#light-pagination a.next")
            if not nxt:
                return
            first = cards[0].get("bid_no")
            time.sleep(self.delay)
            nxt.click()
            try:
                p.wait_for_function(
                    "f => { const a = document.querySelector('#bidCard .card a.bid_no_hover');"
                    " return a && a.textContent.trim() !== f; }", arg=first, timeout=30000)
            except Exception:
                log.warning("  next page did not load, stopping this keyword")
                return
            page_no += 1


_NUM = re.compile(r"^\s*([\d,]+(?:\.\d+)?)\s*$")


def parse_bid_pdf(data):
    """GeM bid document (PDF bytes) -> {value, emd, product_category, pincode}. Missing ones are left out."""
    import io

    import pypdf
    text = "\n".join(p.extract_text() or "" for p in pypdf.PdfReader(io.BytesIO(data)).pages)
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    out = {}

    def number_after(label, look=4):
        for i, l in enumerate(lines):
            if label.lower() in l.lower():
                for nxt in lines[i + 1:i + 1 + look]:
                    m = _NUM.match(nxt)
                    if m:
                        return float(m.group(1).replace(",", ""))
        return None

    value = number_after("Estimated Bid Value")
    if value:
        out["value"] = value
    emd = number_after("EMD Amount")
    if emd is not None:
        out["emd"] = emd
    for i, l in enumerate(lines):
        if l.endswith("/Item Category") or l == "Item Category" or l.endswith("Item Category"):
            if i + 1 < len(lines) and re.search(r"[A-Za-z]", lines[i + 1]):
                out["product_category"] = lines[i + 1][:200]
            break
    # consignee address block: first 6-digit PIN after the word "Consignee"
    pos = text.find("/Consignee")
    if pos >= 0:
        m = re.search(r"([1-8]\d{5})", text[pos:pos + 4000])
        if m:
            out["pincode"] = m.group(1)
    return out


def enrich(args):
    """Fill value / EMD / product / district of saved GeM bids from their bid PDFs."""
    from playwright.sync_api import sync_playwright

    from ..db import SessionLocal, init_db
    from ..focus import apply_focus
    from ..models import Tender
    from .geo import from_pincode
    init_db()
    db = SessionLocal()
    q = db.query(Tender).filter(Tender.source == SOURCE, Tender.deleted_at.is_(None))
    if not args.force:
        q = q.filter(Tender.emd.is_(None))
    tenders = q.all()
    log.info("Reading bid PDFs for %d GeM bids", len(tenders))
    done = 0
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(locale="en-IN")
        page = ctx.new_page()
        page.goto(ALL_BIDS_URL, wait_until="domcontentloaded", timeout=60000)   # same session as a visitor
        try:
            for t in tenders:
                if not (t.source_url or "").startswith(DOC_URL.format("")):
                    continue
                time.sleep(3)
                try:
                    r = ctx.request.get(t.source_url, timeout=90000)
                    if "pdf" not in (r.headers.get("content-type") or ""):
                        log.warning("  %s: not a PDF (%s)", t.tender_id, r.headers.get("content-type"))
                        continue
                    info = parse_bid_pdf(r.body())
                except Exception as exc:
                    log.warning("  %s: %s", t.tender_id, str(exc).splitlines()[0][:80])
                    continue
                for k in ("value", "emd", "product_category", "pincode"):
                    if info.get(k) is not None:
                        setattr(t, k, info[k])
                if info.get("pincode"):
                    district, state = from_pincode(info["pincode"])
                    if district:
                        t.district, t.state = district, state
                apply_focus(t)
                done += 1
                if done % 10 == 0:
                    db.commit()
                    log.info("  ... %d done", done)
        finally:
            db.commit()
            db.close()
            browser.close()
    log.info("GeM PDFs read: %d bids updated", done)


def run_once(args):
    from ..db import SessionLocal, init_db
    from .store import upsert_tender
    init_db()
    keywords = [args.keyword] if args.keyword else KEYWORDS
    counts = {"new": 0, "changed": 0, "same": 0, "skipped": 0}
    seen = set()
    browser = GemBrowser(headless=not args.show)
    db = SessionLocal()
    try:
        for kw in keywords:
            for card in browser.search(kw, max_pages=args.max_pages):
                rec = card_to_record(card)
                if not rec or rec["tender_id"] in seen:
                    continue
                seen.add(rec["tender_id"])
                # Full-text search also finds unrelated bids that only mention the word
                # in their documents; keep only those our focus rules accept.
                if not args.keep_all and not classify(rec["title"])[0]:
                    counts["skipped"] += 1
                    continue
                counts[upsert_tender(db, rec)] += 1
            db.commit()
            time.sleep(browser.delay)
    finally:
        try:
            db.commit()
        except Exception:
            db.rollback()
        db.close()
        browser.close()
        log.info("GeM saved: %d new, %d changed, %d unchanged, %d not relevant (skipped)",
                 counts["new"], counts["changed"], counts["same"], counts["skipped"])


def main():
    p = argparse.ArgumentParser(description="GeM bids scraper (browser)")
    p.add_argument("--keyword", help="Search one keyword instead of the built-in list")
    p.add_argument("--max-pages", type=int, default=None, help="Result pages per keyword (10 bids each)")
    p.add_argument("--keep-all", action="store_true", help="Save every result, not only relevant ones")
    p.add_argument("--show", action="store_true", help="Show the browser window")
    p.add_argument("--every-hours", type=float, default=None)
    p.add_argument("--enrich", action="store_true",
                   help="Only read bid PDFs of saved bids: value, EMD, product, district")
    p.add_argument("--force", action="store_true", help="With --enrich: re-read bids that already have EMD")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.enrich:
        enrich(args)
        return
    while True:
        try:
            run_once(args)
            enrich(argparse.Namespace(force=False))      # new bids: read their PDFs too
        except KeyboardInterrupt:
            log.info("Stopped. Progress is saved.")
            return
        except Exception:
            log.exception("GeM run failed")
        if not args.every_hours:
            break
        time.sleep(args.every_hours * 3600)


if __name__ == "__main__":
    main()

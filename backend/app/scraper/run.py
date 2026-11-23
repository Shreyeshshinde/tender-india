"""
Run the scraper.

    python -m app.scraper.run                      # full CPPP crawl (auto engine)
    python -m app.scraper.run --max-orgs 3 --max-per-org 5     # quick test
    python -m app.scraper.run --engine browser     # force Playwright
    python -m app.scraper.run --every-hours 6      # keep running, crawl every 6 hours
    python -m app.scraper.run --full               # re-open every tender (slow, e.g. once a week)

Quick mode (default): tenders already in the database whose title and dates
did not change are NOT opened again. First run is slow, later runs are fast.

Engine "auto" (default): try fast plain HTTP first. If the portal returns
no usable data (for example it needs JavaScript), switch to the browser.
"""
import argparse
import logging
import os
import sys
import time

from sqlalchemy import delete, func, select, update

from ..db import SessionLocal, init_db
from ..models import CheckedTender, Tender, TenderDocument
from .normalize import now_ist
from .cppp import GePNICClient, PortalUnreachable, crawl, parse_org_list
from .store import upsert_tender

log = logging.getLogger("scraper")


def make_client(engine, base_url):
    if engine == "browser":
        from .browser import BrowserClient
        return BrowserClient(base_url=base_url)

    http = GePNICClient(base_url=base_url)
    if engine == "http":
        return http

    # auto: check if plain HTTP gives us organisations
    try:
        orgs = parse_org_list(http.get(http.org_list_url()), base_url)
        if orgs:
            log.info("Engine: HTTP (fast). %d organisations visible.", len(orgs))
            return http
        log.warning("HTTP returned no organisations, switching to browser engine.")
    except Exception as exc:
        log.warning("HTTP failed (%s), switching to browser engine.", exc)
    from .browser import BrowserClient
    return BrowserClient(base_url=base_url)


def _download_now(db, rec, client, storage, docs_mode):
    """--with-docs: fetch files while the portal links are still fresh."""
    from ..documents.fetcher import download_documents
    db.flush()
    tender = db.scalar(select(Tender).where(Tender.source == rec["source"],
                                            Tender.tender_id == rec["tender_id"]))
    if tender is None or tender.docs_status == "done":
        return
    result = download_documents(db, tender, rec["documents"], client, storage)
    if result["captcha"]:
        docs_mode["on"] = False
        log.warning("Portal asks for a CAPTCHA to download files. Turning off --with-docs for "
                    "this run. Use: python -m app.documents.assist")


def _shard(text):
    if not text:
        return None
    i, n = (int(x) for x in text.split("/"))
    return i, n


def run_once(args):
    init_db()
    from .portals import source_for
    source = source_for(args.base_url)
    log.info("Portal: %s (%s)", source, args.base_url)
    client = make_client(args.engine, args.base_url)
    counts = {"new": 0, "changed": 0, "same": 0, "skipped": 0}
    db = SessionLocal()
    pending = 0

    # Tenders you deleted on the website: never open them again.
    deleted = set(db.scalars(select(Tender.tender_id).where(
        Tender.source == source, Tender.deleted_at.is_not(None))))

    # Tenders opened before and found not relevant: tender_id -> closing date.
    # Skipped next time unless the closing date changes (corrigendum).
    db.execute(delete(CheckedTender).where(CheckedTender.closing_at < now_ist()))
    db.commit()
    checked = {} if args.full else dict(db.execute(
        select(CheckedTender.tender_id, CheckedTender.closing_at).where(CheckedTender.source == source)).all())
    log.info("Remembered as not relevant: %d tenders", len(checked))

    # What we already have: tender_id -> (title, closing, opening, has documents)
    known = {}
    if not args.full:
        rows = db.execute(
            select(Tender.tender_id, Tender.title, Tender.closing_at, Tender.opening_at,
                   func.count(TenderDocument.id))
            .outerjoin(TenderDocument).where(Tender.source == source).group_by(Tender.id))
        known = {tid: (title, close, open_, ndocs > 0) for tid, title, close, open_, ndocs in rows}
        log.info("Quick mode: %d tenders already in database", len(known))

    focus_mode = not getattr(args, "all_details", False)
    if focus_mode:
        log.info("Focus mode: detail pages opened only for titles that could be security / "
                 "ground search. Use --all-details to open every tender.")

    def needs_detail(row):
        """Open the detail page only when it is worth it:
        focus mode -> the title could be ours; quick mode -> new or changed."""
        if row["tender_id"] in deleted:
            return False
        if focus_mode:
            from ..focus import title_could_match
            if not title_could_match(row.get("title")):
                return False
        tid = row["tender_id"]
        if tid in checked and checked[tid] == row["closing_at"]:
            return False                  # opened before, not relevant, unchanged
        old = known.get(tid)
        if old is None:
            return True
        title, close, open_, has_docs = old
        # Compare dates only: that is what a corrigendum changes. (Titles on the
        # list page and detail page can differ slightly, so they are not compared.)
        return not has_docs or close != row["closing_at"] or open_ != row["opening_at"]

    def needs_detail_full(row):
        """--full: re-open everything (still respecting focus mode)."""
        if row["tender_id"] in deleted:
            return False
        if focus_mode:
            from ..focus import title_could_match
            return title_could_match(row.get("title"))
        return True

    docs_mode = {"on": getattr(args, "with_docs", False)}
    storage = None
    if docs_mode["on"]:
        from ..documents.storage import get_storage
        storage = get_storage()

    def on_record(rec):
        nonlocal pending
        result = upsert_tender(db, rec)
        counts[result] += 1
        tid = rec["tender_id"]
        if result == "skipped" and tid not in deleted:
            from ..focus import title_could_match
            close = rec.get("closing_at")
            if title_could_match(rec.get("title")) and checked.get(tid, "?") != close:
                if tid in checked:
                    db.execute(update(CheckedTender).where(
                        CheckedTender.source == source, CheckedTender.tender_id == tid
                    ).values(closing_at=close, checked_at=now_ist()))
                else:
                    db.add(CheckedTender(source=source, tender_id=tid, closing_at=close))
                checked[tid] = close
        if not args.full:
            # remember it, so a retried organisation in this same run is quick too
            old = known.get(rec["tender_id"])
            known[rec["tender_id"]] = (rec.get("title"), rec.get("closing_at"), rec.get("opening_at"),
                                       bool(rec.get("documents")) or bool(old and old[3]))
        pending += 1
        if docs_mode["on"] and rec.get("documents") and result != "same":
            _download_now(db, rec, client, storage, docs_mode)
        if pending >= 25:          # commit in small batches
            db.commit()
            pending = 0

    try:
        total = crawl(client, max_orgs=args.max_orgs,
                      max_tenders_per_org=args.max_per_org,
                      on_record=on_record, save_html_dir=args.save_html,
                      needs_detail=needs_detail_full if args.full else needs_detail, source=source,
                      shard=_shard(getattr(args, "shard", None)))
    finally:
        # Always save what we collected, even if the crawl stopped halfway
        # (internet down, Ctrl+C, crash). The next run continues from here.
        try:
            db.commit()
        except Exception:
            db.rollback()
        db.close()
        if hasattr(client, "close"):
            client.close()
        log.info("Saved. %d new, %d changed, %d unchanged, %d not relevant (not saved)",
                 counts["new"], counts["changed"], counts["same"], counts["skipped"])
    log.info("Done. %d tenders checked.", total)


def main():
    p = argparse.ArgumentParser(description="Tender India scraper")
    p.add_argument("--engine", choices=["auto", "http", "browser"], default="auto")
    p.add_argument("--portal", default=None,
                   help="Portal name (cppp, chandigarh, maharashtra, punjab ...) or 'all'. "
                        "See app/scraper/portals.py")
    p.add_argument("--base-url", default="https://eprocure.gov.in/eprocure/app",
                   help="Any GePNIC portal, e.g. https://mahatenders.gov.in/nicgep/app")
    p.add_argument("--max-orgs", type=int, default=None)
    p.add_argument("--max-per-org", type=int, default=None)
    p.add_argument("--shard", default=None,
                   help="i/n: crawl only every n-th organisation starting at i (0-based), "
                        "so one big portal can be split across parallel jobs")
    p.add_argument("--full", action="store_true",
                   default=os.environ.get("TENDER_FULL", "").strip() in ("1", "true", "yes"),
                   help="Re-open every detail page (slow). Default skips unchanged tenders.")
    p.add_argument("--all-details", action="store_true",
                   help="Open the detail page of EVERY tender (slow). Default: only titles "
                        "that could be security / ground search")
    p.add_argument("--with-docs", action="store_true",
                   help="Also download document files for new/changed tenders (uses more disk)")
    p.add_argument("--save-html", default=None, help="Folder to save raw detail pages (debugging)")
    p.add_argument("--every-hours", type=float, default=None, help="Repeat forever with this gap")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    log.info("Tender India scraper v0.8 (focus mode: skips works tenders, progress lines)")
    from ..db import describe_db
    from .portals import PORTALS
    log.info("Database: %s", describe_db())

    if args.portal == "all":
        targets = list(PORTALS.values())
    elif args.portal:
        names = [n.strip().lower() for n in args.portal.split(",")]
        unknown = [n for n in names if n not in PORTALS]
        if unknown:
            p.error(f"Unknown portal(s): {', '.join(unknown)}. Known: {', '.join(PORTALS)}")
        targets = [PORTALS[n] for n in names]
    else:
        targets = [args.base_url]

    while True:
        for base in targets:
            one = argparse.Namespace(**{**vars(args), "base_url": base})
            try:
                run_once(one)
            except KeyboardInterrupt:
                log.info("Stopped by you. Progress is saved. Run the same command again to continue.")
                return
            except PortalUnreachable as exc:
                log.error("%s", exc)
                log.error("%s is down or not responding. Progress is saved.%s", base,
                          " Moving on to the next portal." if len(targets) > 1 else
                          " Check your connection and run again; it will skip what is already saved.")
                if len(targets) == 1 and not args.every_hours:
                    sys.exit(1)
            except Exception:
                log.exception("Crawl of %s failed", base)
        if not args.every_hours:
            break
        log.info("Sleeping %.1f hours", args.every_hours)
        time.sleep(args.every_hours * 3600)

if __name__ == "__main__":
    main()

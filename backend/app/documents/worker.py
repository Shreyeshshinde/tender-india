"""
Document worker: downloads files for tenders that users asked for.

    python -m app.documents.worker                       # process all pending requests once
    python -m app.documents.worker --loop 2              # keep checking every 2 minutes
    python -m app.documents.worker --tender 2026_ASI_929316_2   # one tender right now
    python -m app.documents.worker --cleanup-days 30     # delete files of tenders closed 30+ days ago
    python -m app.documents.worker --stats               # how much space files use

Flow: website "Get documents" button -> tender.docs_status = "requested"
      -> this worker downloads the files -> "done" (or "captcha" / "failed").
"""
import argparse
import logging
import time
from datetime import timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload

from ..db import SessionLocal, describe_db, init_db
from ..models import Tender, TenderDocument
from ..scraper.cppp import GePNICClient, PortalUnreachable
from ..scraper.normalize import now_ist
from ..scraper.portals import base_url_for
from .fetcher import fetch_tender
from .storage import get_storage

log = logging.getLogger("documents")


def _client_for(tender, cache, given=None):
    """Talk to the portal this tender came from (CPPP, Chandigarh, ...)."""
    if given is not None:
        return given                      # tests pass their own fake portal
    base = base_url_for(tender.source, tender.source_url)
    if base not in cache:
        cache[base] = GePNICClient(base_url=base)
    return cache[base]


def process_pending(limit=None, client=None, storage=None):
    storage = storage or get_storage()
    done = 0
    with SessionLocal() as db:
        q = (select(Tender).where(Tender.docs_status == "requested")
             .order_by(Tender.docs_requested_at).options(selectinload(Tender.documents)))
        if limit:
            q = q.limit(limit)
        clients = {}
        for tender in db.scalars(q).all():
            log.info("Fetching documents for %s (%s)", tender.tender_id, tender.title)
            try:
                result = fetch_tender(db, tender, _client_for(tender, clients, client), storage)
            except PortalUnreachable as exc:
                log.error("Portal unreachable, will retry later: %s", exc)
                db.commit()
                break
            db.commit()
            log.info("  -> %s: %d of %d files stored", tender.docs_status, result["stored"], result["total"])
            done += 1
    return done


def fetch_one(tender_id, client=None, storage=None):
    storage = storage or get_storage()
    with SessionLocal() as db:
        tender = db.scalar(select(Tender).where(Tender.tender_id == tender_id)
                           .options(selectinload(Tender.documents)))
        if tender is None:
            raise SystemExit(f"Tender {tender_id} is not in the database")
        result = fetch_tender(db, tender, _client_for(tender, {}, client), storage)
        db.commit()
        log.info("%s: %s (%d of %d files)", tender_id, tender.docs_status, result["stored"], result["total"])
        return result


def cleanup(days, storage=None):
    """Delete stored files of tenders that closed more than `days` days ago.
    Tender records stay in the database; only the files go. A file shared with
    a still-useful tender is kept."""
    storage = storage or get_storage()
    cutoff = now_ist() - timedelta(days=days)
    with SessionLocal() as db:
        old_docs = db.scalars(
            select(TenderDocument).join(Tender)
            .where(Tender.closing_at < cutoff, TenderDocument.file_status == "stored")).all()
        removed = freed = 0
        for doc in old_docs:
            still_needed = db.scalar(
                select(func.count(TenderDocument.id)).join(Tender)
                .where(TenderDocument.file_hash == doc.file_hash, TenderDocument.id != doc.id,
                       TenderDocument.file_status == "stored",
                       or_(Tender.closing_at.is_(None), Tender.closing_at >= cutoff)))
            if not still_needed and storage.delete(doc.file_path):
                removed += 1
                freed += doc.file_bytes or 0
            doc.file_status, doc.file_path = "deleted", None
            doc.error = f"Removed {days} days after closing to save space"
        db.commit()
    log.info("Cleanup: deleted %d files, freed %.1f MB", removed, freed / 1024 / 1024)
    return removed


def stats(storage=None):
    storage = storage or get_storage()
    with SessionLocal() as db:
        by_status = dict(db.execute(select(TenderDocument.file_status, func.count())
                                    .group_by(TenderDocument.file_status)).all())
        waiting = db.scalar(select(func.count()).where(Tender.docs_status == "requested"))
    log.info("Files by status: %s", {k or "not fetched": v for k, v in by_status.items()})
    log.info("Tenders waiting for download: %d", waiting)
    log.info("Space used on disk: %.1f MB in %s", storage.total_bytes() / 1024 / 1024, storage.root)


def main():
    p = argparse.ArgumentParser(description="Tender document downloader")
    p.add_argument("--tender", help="Download documents for this tender ID now")
    p.add_argument("--limit", type=int, help="Max tenders per round")
    p.add_argument("--loop", type=float, help="Keep running, check every N minutes")
    p.add_argument("--cleanup-days", type=int, help="Delete files of tenders closed N+ days ago")
    p.add_argument("--stats", action="store_true")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    log.info("Database: %s", describe_db())
    init_db()

    if args.stats:
        return stats()
    if args.cleanup_days:
        return cleanup(args.cleanup_days)
    if args.tender:
        return fetch_one(args.tender)
    while True:
        n = process_pending(args.limit)
        if not args.loop:
            log.info("Processed %d tenders.", n)
            break
        time.sleep(args.loop * 60)


if __name__ == "__main__":
    main()

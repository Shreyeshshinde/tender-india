"""
Human-assisted download for portals that ask for a CAPTCHA.

    python -m app.documents.assist                    # go through tenders marked "captcha"
    python -m app.documents.assist --tender 2026_ASI_929316_2

What happens:
  1. A real Chrome window opens on the tender's page (we navigate there for you).
  2. YOU click each document and type the CAPTCHA, like a normal user.
  3. Every file your browser downloads is caught, saved to our storage,
     and linked to the right document in the database automatically.
  4. Press Enter in this terminal when you are done with that tender.

We never solve or bypass the CAPTCHA. A person does, which is what it is for.

Needs:  pip install playwright   and   playwright install chromium
"""
import argparse
import logging
import re
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from ..db import SessionLocal, describe_db, init_db
from ..models import Tender
from ..scraper.normalize import now_ist
from .fetcher import TenderNotListed, find_detail_url, store_file
from .storage import get_storage

log = logging.getLogger("assist")


def _key(name):
    """Compare file names loosely: 'Tendernotice_1 (1).pdf' ~ 'Tendernotice_1.pdf'."""
    stem = Path(name or "").stem.lower()
    stem = re.sub(r"\s*\(\d+\)$", "", stem)
    return re.sub(r"[^a-z0-9]", "", stem)


def match_document(tender, filename):
    """Which document row does a downloaded file belong to? Returns the row or None."""
    k = _key(filename)
    exact = [d for d in tender.documents if _key(d.name) == k]
    if exact:
        return next((d for d in exact if d.file_status != "stored"), exact[0])
    pending = [d for d in tender.documents if d.file_status != "stored"]
    loose = [d for d in pending if k and (k in _key(d.name) or _key(d.name) in k)]
    return loose[0] if len(loose) == 1 else None


def save_download(db, tender, filename, content, storage, content_type=None):
    """Store one downloaded file and link it. Returns a short message for the terminal."""
    doc = match_document(tender, filename)
    if doc is None:
        # Some portals give one ZIP with everything, or a renamed file. Keep it anyway.
        from ..models import TenderDocument
        doc = TenderDocument(section="Downloaded", name=filename)
        tender.documents.append(doc)
    store_file(db, doc, content, filename, content_type, storage)
    stored = sum(1 for d in tender.documents if d.file_status == "stored")
    total = len(tender.documents)
    tender.docs_status = "done" if stored == total else "partial"
    tender.docs_fetched_at = now_ist()
    db.commit()
    return f"saved {filename} as '{doc.name}' ({stored}/{total} files)"


def _wait_for_enter(page, prompt):
    """
    Wait for Enter WITHOUT freezing the browser connection.
    Playwright only delivers 'download' events while we call it, so the
    keyboard is read on a side thread and the main thread keeps pumping events.
    """
    import threading
    box = {}

    def read():
        box["answer"] = input(prompt)

    th = threading.Thread(target=read, daemon=True)
    th.start()
    while th.is_alive():
        page.wait_for_timeout(300)
    return (box.get("answer") or "").strip().lower()


def assist(tender_ids=None, base_url=None):
    from ..scraper.browser import BrowserClient

    storage = get_storage()
    with SessionLocal() as db:
        q = select(Tender).options(selectinload(Tender.documents))
        q = q.where(Tender.tender_id.in_(tender_ids)) if tender_ids else \
            q.where(Tender.docs_status == "captcha").order_by(Tender.closing_at)
        tenders = db.scalars(q).all()
        if not tenders:
            log.info("Nothing to do: no tenders are waiting for a CAPTCHA download.")
            return

        from ..scraper.portals import base_url_for
        if not base_url:
            portals = {base_url_for(t.source, t.source_url) for t in tenders}
            if len(portals) > 1:
                first = sorted(portals)[0]
                tenders = [t for t in tenders if base_url_for(t.source, t.source_url) == first]
                print(f"Tenders are on several portals. Doing {first} now; run again for the rest.")
            base_url = portals.pop() if len(portals) == 1 else first
        with BrowserClient(headless=False, base_url=base_url) as browser:
            page = browser._page
            current = {"tender": None}

            def on_download(download):
                t = current["tender"]
                if t is None:
                    return
                tmp = download.path()               # waits until the download finishes
                content = Path(tmp).read_bytes()
                print("   ", save_download(db, t, download.suggested_filename, content, storage))

            page.on("download", on_download)

            for n, t in enumerate(tenders, 1):
                print(f"\n[{n}/{len(tenders)}] {t.tender_id}  {t.title}")
                try:
                    browser.get(find_detail_url(browser, t))
                except TenderNotListed as exc:
                    print("    skipped:", exc)
                    t.docs_status = "failed"
                    db.commit()
                    continue
                current["tender"] = t
                missing = [d.name for d in t.documents if d.file_status != "stored"]
                print("    In the Chrome window: click each document and type the CAPTCHA.")
                print("    Still needed:", ", ".join(missing) or "nothing")
                answer = _wait_for_enter(page, "    Press Enter when done (or type q to stop): ")
                current["tender"] = None
                db.commit()
                if answer == "q":
                    break
    log.info("Finished.")


def main():
    p = argparse.ArgumentParser(description="Download CAPTCHA-protected documents with your help")
    p.add_argument("--tender", action="append", help="Tender ID (can repeat)")
    p.add_argument("--base-url", help="Portal base URL (default CPPP)")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    log.info("Database: %s", describe_db())
    init_db()
    assist(args.tender, args.base_url)


if __name__ == "__main__":
    main()

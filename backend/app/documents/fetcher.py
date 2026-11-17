"""
Download the real files for a tender.

Why we must "find the tender again" first:
  CPPP links contain a session token that expires. A link saved yesterday
  no longer works today. So we open the portal fresh:
      organisation list -> that organisation's tenders -> this tender's page
  and use the brand-new document links from that page.

For every document we look at what the portal sends back:
  real file (PDF, ZIP, XLS ...)   -> save it            status: stored
  a page asking for a CAPTCHA     -> do NOT try to solve status: captcha
  anything else                   -> note the problem   status: failed
"""
import logging
import re
from urllib.parse import unquote

from bs4 import BeautifulSoup

from ..models import TenderDocument
from ..scraper.cppp import (PortalUnreachable, parse_org_list, parse_tender_detail,
                            parse_tender_list)
from ..scraper.normalize import clean_text, now_ist
from .storage import guess_ext

log = logging.getLogger("documents")

MAX_LIST_PAGES = 60             # safety limit when searching an organisation's tender list
MAX_FILE_BYTES = 100 * 1024 * 1024

_FILE_MAGIC = (b"%PDF", b"PK\x03\x04", b"\xd0\xcf\x11\xe0", b"Rar!", b"7z\xbc\xaf")


class TenderNotListed(Exception):
    """The tender is no longer on the portal (closed, cancelled or moved)."""


# --------------------------------------------------------------------------
# Step 1: find a fresh detail page for this tender
# --------------------------------------------------------------------------
def _norm(name):
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def find_detail_url(client, tender):
    orgs = parse_org_list(client.get(client.org_list_url()), client.base_url)
    want = _norm(tender.organisation)
    org = next((o for o in orgs if _norm(o["name"]) == want), None)
    if org is None and want:
        org = next((o for o in orgs if want in _norm(o["name"]) or _norm(o["name"]) in want), None)
    if org is None:
        raise TenderNotListed(f"Organisation '{tender.organisation}' not found on the portal")

    url, pages = org["url"], 0
    while url and pages < MAX_LIST_PAGES:
        rows, url = parse_tender_list(client.get(url), client.base_url)
        pages += 1
        for row in rows:
            if row["tender_id"] == tender.tender_id:
                return row["detail_url"]
    raise TenderNotListed(f"Tender {tender.tender_id} is no longer listed on the portal")


# --------------------------------------------------------------------------
# Step 2: look at what the portal sent back
# --------------------------------------------------------------------------
def is_captcha_page(html):
    soup = BeautifulSoup(html, "lxml")
    for tag in soup.find_all(["img", "input", "form", "div", "span", "label"]):
        attrs = " ".join(str(v) for v in tag.attrs.values()).lower()
        if "captcha" in attrs:
            return True
    text = soup.get_text(" ").lower()
    return "captcha" in text and ("enter" in text or "type" in text)


def classify(resp):
    """-> ('file', bytes, filename) | ('captcha', None, None) | ('error', None, message)"""
    status = getattr(resp, "status_code", 200)
    if status and status >= 400:
        return "error", None, f"HTTP {status}"
    body = resp.content or b""
    headers = {k.lower(): v for k, v in (resp.headers or {}).items()}
    ctype = headers.get("content-type", "").lower()
    disp = headers.get("content-disposition", "")
    filename = None
    m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', disp, re.I)
    if m:
        filename = unquote(m.group(1)).strip()

    looks_like_file = body.startswith(_FILE_MAGIC) or (
        ("attachment" in disp.lower() or "octet-stream" in ctype or "pdf" in ctype)
        and not body.lstrip()[:15].lower().startswith((b"<!doctype", b"<html")))
    if looks_like_file:
        if len(body) > MAX_FILE_BYTES:
            return "error", None, f"File too large ({len(body) // (1024 * 1024)} MB)"
        if not body:
            return "error", None, "Empty file"
        return "file", body, filename

    html = body.decode("utf-8", errors="replace")
    if is_captcha_page(html):
        return "captcha", None, None
    title = BeautifulSoup(html, "lxml").title
    return "error", None, f"Got a web page instead of a file ({clean_text(title.string) if title and title.string else 'no title'})"


# --------------------------------------------------------------------------
# Step 3: download and store
# --------------------------------------------------------------------------
def store_file(db, doc, content, filename, content_type, storage):
    ext = guess_ext(filename or doc.name, content_type, content)
    rel, digest = storage.save(content, ext)
    doc.file_path, doc.file_hash = rel, digest
    doc.file_bytes, doc.content_type = len(content), content_type
    doc.file_status, doc.error, doc.fetched_at = "stored", None, now_ist()


def download_documents(db, tender, fresh_docs, client, storage):
    """Download every document of one tender using links from a FRESH detail page."""
    rows = {(d.section, d.name): d for d in tender.documents}
    stored = captcha = failed = 0
    for i, d in enumerate(fresh_docs):
        row = rows.get((d.get("section"), d.get("name")))
        if row is None:
            row = TenderDocument(section=d.get("section"), name=d.get("name"),
                                 description=d.get("description"), size=d.get("size"))
            tender.documents.append(row)
            rows[(row.section, row.name)] = row
        row.url = d.get("url") or row.url

        if row.file_status == "stored" and storage.exists(row.file_path):
            stored += 1
            continue
        if not row.url:
            row.file_status, row.error = "failed", "No download link on the portal page"
            failed += 1
            continue

        try:
            resp = client.get_response(row.url)
        except PortalUnreachable:
            raise
        except Exception as exc:
            row.file_status, row.error = "failed", str(exc)[:300]
            failed += 1
            continue

        kind, content, info = classify(resp)
        if kind == "file":
            ctype = (resp.headers or {}).get("content-type") or (resp.headers or {}).get("Content-Type")
            store_file(db, row, content, info, ctype, storage)
            stored += 1
            log.info("  saved %s (%d KB)", row.name, len(content) // 1024)
        elif kind == "captcha":
            # The portal wants a human. Don't retry the rest; they will all ask the same.
            for rest in [row] + [rows.get((x.get("section"), x.get("name"))) for x in fresh_docs[i + 1:]]:
                if rest is not None and rest.file_status != "stored":
                    rest.file_status, rest.error = "captcha", "Portal asks for a CAPTCHA"
            captcha = sum(1 for r in tender.documents if r.file_status == "captcha")
            log.info("  CAPTCHA required for %s. Marked for manual download.", tender.tender_id)
            break
        else:
            row.file_status, row.error = "failed", info
            failed += 1
            log.warning("  could not get %s: %s", row.name, info)

    total = len(tender.documents)
    stored = sum(1 for r in tender.documents if r.file_status == "stored")
    if total and stored == total:
        tender.docs_status = "done"
    elif captcha:
        tender.docs_status = "captcha"
    elif stored:
        tender.docs_status = "partial"
    else:
        tender.docs_status = "failed"
    tender.docs_fetched_at = now_ist()
    return {"stored": stored, "captcha": captcha, "failed": failed, "total": total}


def fetch_tender(db, tender, client, storage):
    """Full on-demand flow for one tender: find it again, then download its files."""
    try:
        detail_url = find_detail_url(client, tender)
    except TenderNotListed as exc:
        tender.docs_status, tender.docs_fetched_at = "failed", now_ist()
        for d in tender.documents:
            if d.file_status != "stored":
                d.file_status, d.error = "failed", str(exc)
        log.warning("%s", exc)
        return {"stored": 0, "captcha": 0, "failed": len(tender.documents),
                "total": len(tender.documents), "error": str(exc)}
    detail = parse_tender_detail(client.get(detail_url), client.base_url)
    return download_documents(db, tender, detail.get("documents") or [], client, storage)

"""
Save a cleaned tender record into the database.

  New tender ID      -> insert it with its documents.
  Known tender ID    -> compare important fields; if anything changed
                        (corrigendum), log it in tender_changes and update.
"""
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select

import os

from .normalize import now_ist
from ..focus import apply_focus, classify_tender

# Only security / ground search tenders are saved. Set TENDER_SAVE_ALL=1 to save everything.
SAVE_ONLY_RELEVANT = os.environ.get("TENDER_SAVE_ALL", "").strip() not in ("1", "true", "yes")
from ..models import Tender, TenderChange, TenderDocument

CPPP_PUBLIC_URL = "https://eprocure.gov.in/eprocure/app"

# Fields we watch for changes (corrigendums usually touch these).
TRACKED = ["title", "closing_at", "opening_at", "value", "emd", "tender_fee", "doc_end_at"]

_COLUMNS = {c.name for c in Tender.__table__.columns} - {
    "id", "first_seen_at", "last_seen_at", "updated_at", "raw"}


def _json_safe(v):
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, list):
        return [_json_safe(x) for x in v]
    if isinstance(v, dict):
        return {k: _json_safe(x) for k, x in v.items()}
    return v


def _same(a, b):
    if isinstance(a, Decimal) or isinstance(b, Decimal):
        try:
            return Decimal(str(a)) == Decimal(str(b))
        except Exception:
            return False
    return a == b


def upsert_tender(db, record, only_relevant=None):
    """Insert or update one tender. Returns 'new', 'changed', 'same' or 'skipped'
    ('skipped' = not saved: a new tender that is not security / ground search,
    or one you deleted on the website, which must never come back)."""
    if only_relevant is None:
        only_relevant = SAVE_ONLY_RELEVANT
    docs = record.get("documents") or []
    fields = {k: v for k, v in record.items() if k in _COLUMNS}
    if not fields.get("source_url"):
        from .portals import base_url_for
        fields["source_url"] = base_url_for(record["source"], CPPP_PUBLIC_URL)
    now = now_ist()

    tender = db.scalar(select(Tender).where(
        Tender.source == record["source"], Tender.tender_id == record["tender_id"]))

    if tender is None:
        if only_relevant and not classify_tender(
                record.get("title"), record.get("description"), record.get("product_category"),
                record.get("state"), record.get("source"), record.get("organisation"),
                record.get("location"))[0]:
            return "skipped"
        tender = Tender(**fields, raw=_json_safe(record),
                        first_seen_at=now, last_seen_at=now, updated_at=now)
        tender.documents = [TenderDocument(**_doc(d)) for d in docs]
        apply_focus(tender)
        db.add(tender)
        return "new"

    if tender.deleted_at is not None:
        return "skipped"

    status = "same"
    for f in TRACKED:
        new = fields.get(f)
        old = getattr(tender, f)
        if new is not None and not _same(old, new):
            db.add(TenderChange(tender=tender, field=f, old_value=_str(old),
                                new_value=_str(new), changed_at=now))
            status = "changed"

    for k, v in fields.items():
        if v is not None:
            setattr(tender, k, v)

    new_doc_keys = {(d.get("section"), d.get("name")) for d in docs}
    old_doc_keys = {(d.section, d.name) for d in tender.documents}
    if docs and new_doc_keys != old_doc_keys:
        added = new_doc_keys - old_doc_keys
        if added:
            db.add(TenderChange(tender=tender, field="documents", old_value=None,
                                new_value=", ".join(n for _, n in sorted(added, key=str)),
                                changed_at=now))
            status = "changed"
        _sync_documents(tender, docs)
    elif docs:
        _sync_documents(tender, docs)     # same files: refresh links/sizes, keep downloads

    apply_focus(tender)
    tender.raw = _json_safe(record)
    tender.last_seen_at = now
    if status == "changed":
        tender.updated_at = now
    return status


def _sync_documents(tender, docs):
    """
    Update the document list WITHOUT losing files we already downloaded:
    existing rows are kept (link and size refreshed), new ones added,
    ones that disappeared from the portal removed.
    """
    by_key = {(d.section, d.name): d for d in tender.documents}
    keep = []
    for d in docs:
        key = (d.get("section"), d.get("name"))
        row = by_key.get(key)
        if row is None:
            row = TenderDocument(**_doc(d))
        else:
            for k in ("description", "size", "url"):
                if d.get(k) is not None:
                    setattr(row, k, d.get(k))
        keep.append(row)
    tender.documents = keep


def _doc(d):
    return {k: d.get(k) for k in ("section", "name", "description", "size", "url")}


def _str(v):
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.strftime("%d-%b-%Y %I:%M %p")
    return str(v)

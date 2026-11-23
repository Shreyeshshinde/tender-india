"""
FastAPI backend.

GET    /api/tenders               search + filters + paging (status=deleted lists deleted ones)
GET    /api/tenders/{id}          one tender with documents and change history
DELETE /api/tenders/{id}          delete a tender (admin); scrapers never add it again
POST   /api/tenders/{id}/restore  undo a delete (admin)
GET    /api/filters               values for the filter dropdowns
GET    /api/stats                 small numbers for the home page
GET    /api/refresh               latest refresh (scrape round) and whether one is running
POST   /api/refresh               start a refresh (admin); never two at once

Admin calls need the header X-Admin-Token when ADMIN_TOKEN is set (always set it
on a public server; without it, as on your own PC, anyone can use them).
"""
import os
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from .db import get_session, init_db
from .documents.storage import get_storage
from pydantic import BaseModel, Field

from .models import CustomSource, Tender, TenderDocument
from .refresh import latest_run, request_refresh, run_dict
from .scraper.portals import DISPLAY_NAMES
from .focus import LABELS as FOCUS_LABELS
from .search import search_conditions
from .scraper.normalize import now_ist

# "Get documents" downloads files to this server's disk. Turn it off where there is
# no lasting disk (Cloud Run): DOCUMENTS_ENABLED=0.
DOCUMENTS_ENABLED = os.getenv("DOCUMENTS_ENABLED", "1").strip().lower() not in ("0", "false", "no")


def require_admin(x_admin_token: str | None = Header(None)):
    expected = os.getenv("ADMIN_TOKEN")
    if expected and not secrets.compare_digest(x_admin_token or "", expected):
        raise HTTPException(401, "Admin token missing or wrong")

@asynccontextmanager
async def lifespan(_app):
    init_db()            # create tables / add new columns on startup
    yield


app = FastAPI(title="Tender India API", version="0.4", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:3000").split(","),
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
)


FOCUS_PATTERN = "^(relevant|security|ground_search|solar|all)$"


def focus_condition(focus):
    """relevant = security OR ground search (the default view)."""
    if focus == "relevant":
        return Tender.focus.in_(("security", "ground_search"))
    if focus in ("security", "ground_search", "solar"):
        return Tender.focus == focus
    return None


UNDATED_OPEN_DAYS = 5     # refresh runs at least twice a week


def is_open(now):
    """Closing date still ahead, or no closing date on the website but still listed there."""
    return or_(Tender.closing_at > now,
               (Tender.closing_at.is_(None)) & (Tender.last_seen_at > now - timedelta(days=UNDATED_OPEN_DAYS)))


def with_focus(stmt, focus):
    cond = focus_condition(focus)
    return stmt.where(cond) if cond is not None else stmt


NOT_DELETED = Tender.deleted_at.is_(None)


def new_since(db):
    """Tenders first seen after the latest refresh began are marked "New"."""
    run = latest_run(db)
    return (run.started_at or run.requested_at) if run else None


def _num(v):
    return float(v) if v is not None else None


def _dt(v):
    return v.isoformat() if v else None


def tender_summary(t: Tender, since=None):
    return {
        "id": t.id,
        "source": t.source,
        "source_name": DISPLAY_NAMES.get(t.source, t.source),
        "focus": t.focus,
        "focus_label": FOCUS_LABELS.get(t.focus),
        "focus_terms": t.focus_terms,
        "tender_id": t.tender_id,
        "ref_no": t.ref_no,
        "title": t.title,
        "organisation": t.organisation,
        "state": t.state,
        "district": t.district,
        "location": t.location,
        "category": t.category,
        "value": _num(t.value),
        "emd": _num(t.emd),
        "quantity": t.quantity,
        "org_chain": t.org_chain,
        "published_at": _dt(t.published_at),
        "closing_at": _dt(t.closing_at),
        "opening_at": _dt(t.opening_at),
        "document_count": len(t.documents),
        "files_ready": sum(1 for d in t.documents if d.file_status == "stored"),
        "is_active": bool(t.closing_at > now_ist() if t.closing_at else
                          t.last_seen_at and t.last_seen_at > now_ist() - timedelta(days=UNDATED_OPEN_DAYS)),
        "first_seen_at": _dt(t.first_seen_at),
        "is_new": bool(since and t.first_seen_at and t.first_seen_at >= since),
        "deleted_at": _dt(t.deleted_at),
    }


_FILE_EXTS = (".pdf", ".zip", ".rar", ".xls", ".xlsx", ".doc", ".docx")


def portal_links(t: Tender):
    """(website to open, direct document link or None). Some scrapers store the bid
    document itself as source_url (GeM); the "open portal" button must not download it."""
    from urllib.parse import urlparse
    from .scraper.portals import PORTALS
    url = t.source_url or ""
    is_file = "showbidDocument" in url or urlparse(url).path.lower().endswith(_FILE_EXTS)
    if t.source == "gem":
        portal = "https://bidplus.gem.gov.in/all-bids"
    elif t.source in PORTALS:
        portal = PORTALS[t.source]
    elif url and not is_file:
        portal = url
    else:
        u = urlparse(url)
        portal = f"{u.scheme}://{u.netloc}/" if u.netloc else None
    return portal, (url if is_file else None)


def tender_full(t: Tender):
    data = tender_summary(t)
    portal_url, document_url = portal_links(t)
    data.update({
        "description": t.description,
        "org_chain": t.org_chain,
        "tender_type": t.tender_type,
        "product_category": t.product_category,
        "form_of_contract": t.form_of_contract,
        "tender_fee": _num(t.tender_fee),
        "pincode": t.pincode,
        "period_days": t.period_days,
        "authority_name": t.authority_name,
        "authority_address": t.authority_address,
        "doc_start_at": _dt(t.doc_start_at),
        "doc_end_at": _dt(t.doc_end_at),
        "bid_start_at": _dt(t.bid_start_at),
        "pre_bid_at": _dt(t.pre_bid_at),
        "source_url": t.source_url,
        "portal_url": portal_url,
        "document_url": document_url,
        "first_seen_at": _dt(t.first_seen_at),
        "updated_at": _dt(t.updated_at),
        "docs_status": t.docs_status,
        "docs_requested_at": _dt(t.docs_requested_at),
        "docs_fetched_at": _dt(t.docs_fetched_at),
        "docs_enabled": DOCUMENTS_ENABLED,
        "documents": [
            {"id": d.id, "section": d.section, "name": d.name, "description": d.description,
             "size": d.size, "file_status": d.file_status, "file_bytes": d.file_bytes,
             "available": d.file_status == "stored",
             "download_url": f"/api/documents/{d.id}/file" if d.file_status == "stored" else None,
             "error": d.error} for d in t.documents
        ],
        "changes": [
            {"field": c.field, "old_value": c.old_value, "new_value": c.new_value,
             "changed_at": _dt(c.changed_at)} for c in t.changes
        ],
    })
    return data


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/tenders")
def list_tenders(
    q: str | None = None,
    state: str | None = None,
    district: str | None = None,
    source: str | None = None,
    focus: str = Query("relevant", pattern=FOCUS_PATTERN),
    organisation: str | None = None,
    category: str | None = None,
    status: str = Query("active", pattern="^(active|closed|all|deleted)$"),
    closing_within_days: int | None = Query(None, ge=1, le=365),
    min_value: float | None = None,
    max_value: float | None = None,
    sort: str = Query("closing", pattern="^(closing|published|value)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_session),
):
    now = now_ist()
    stmt = with_focus(select(Tender), focus)
    stmt = stmt.where(Tender.deleted_at.is_not(None) if status == "deleted" else NOT_DELETED)

    if q:
        # Each "concept" must match (AND); short forms like DFMD = door frame metal detector (OR).
        cols = [Tender.title, Tender.ref_no, Tender.tender_id, Tender.organisation,
                Tender.description, Tender.location, Tender.product_category, Tender.org_chain,
                Tender.district, Tender.state]
        for cond in search_conditions(q, cols):
            stmt = stmt.where(cond)
    if state:
        stmt = stmt.where(Tender.state == state)
    if district:
        stmt = stmt.where(Tender.district == district)
    if source:
        stmt = stmt.where(Tender.source == source)
    if organisation:
        stmt = stmt.where(Tender.organisation == organisation)
    if category:
        stmt = stmt.where(Tender.category == category)
    if status == "active":
        stmt = stmt.where(is_open(now))
    elif status == "closed":
        stmt = stmt.where(Tender.closing_at <= now)
    if closing_within_days:
        stmt = stmt.where(Tender.closing_at > now,
                          Tender.closing_at <= now + timedelta(days=closing_within_days))
    if min_value is not None:
        stmt = stmt.where(Tender.value >= min_value)
    if max_value is not None:
        stmt = stmt.where(Tender.value <= max_value)

    total = db.scalar(select(func.count()).select_from(stmt.subquery()))

    if status == "deleted":
        stmt = stmt.order_by(Tender.deleted_at.desc())
    elif sort == "published":
        stmt = stmt.order_by(Tender.published_at.desc())
    elif sort == "value":
        stmt = stmt.order_by(Tender.value.desc().nulls_last())
    else:
        stmt = stmt.order_by(Tender.closing_at.asc())

    stmt = stmt.options(selectinload(Tender.documents)) \
               .offset((page - 1) * page_size).limit(page_size)
    since = new_since(db)
    items = [tender_summary(t, since) for t in db.scalars(stmt)]
    return {"total": total, "page": page, "page_size": page_size, "items": items}


@app.get("/api/tenders/{tender_pk}")
def get_tender(tender_pk: int, db: Session = Depends(get_session)):
    t = db.scalar(select(Tender).where(Tender.id == tender_pk)
                  .options(selectinload(Tender.documents), selectinload(Tender.changes)))
    if not t:
        raise HTTPException(404, "Tender not found")
    return tender_full(t)


@app.delete("/api/tenders/{tender_pk}", dependencies=[Depends(require_admin)])
def delete_tender(tender_pk: int, db: Session = Depends(get_session)):
    """Hide a tender for good. The row stays (as a marker) so no refresh adds it back."""
    t = db.get(Tender, tender_pk)
    if not t:
        raise HTTPException(404, "Tender not found")
    if t.deleted_at is None:
        t.deleted_at = now_ist()
        db.commit()
    return {"id": t.id, "deleted_at": _dt(t.deleted_at)}


@app.post("/api/tenders/{tender_pk}/restore", dependencies=[Depends(require_admin)])
def restore_tender(tender_pk: int, db: Session = Depends(get_session)):
    t = db.get(Tender, tender_pk)
    if not t:
        raise HTTPException(404, "Tender not found")
    t.deleted_at = None
    db.commit()
    return {"id": t.id, "deleted_at": None}


class SourceIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    url: str = Field(min_length=8, max_length=500)


def source_dict(s: CustomSource):
    return {"id": s.id, "name": s.name, "url": s.url, "kind": s.kind, "enabled": s.enabled,
            "created_at": _dt(s.created_at), "last_run_at": _dt(s.last_run_at),
            "last_status": s.last_status, "last_found": s.last_found}


def builtin_sources():
    """Websites the built-in scrapers already read (taken from their code, so always current)."""
    from .scraper import adani, bihar, custom, gem, gujarat, sites, telangana
    from .scraper.portals import PORTALS
    out = [{"name": "GeM (Government e-Marketplace)", "url": gem.ALL_BIDS_URL, "kind": "GeM"}]
    for key, cfg in telangana.PORTALS.items():
        out.append({"name": f"{cfg['state']} eProcurement", "url": cfg["home"], "kind": "State portal"})
    out.append({"name": "Bihar eProcurement", "url": bihar.LIST_URL, "kind": "State portal"})
    out.append({"name": "Gujarat nProcure", "url": gujarat.HOME, "kind": "State portal"})
    for name, url, *_ in adani.AIRPORTS:
        out.append({"name": f"{name} (Adani)", "url": url, "kind": "Airport"})
    for cfg in sites.SITES.values():
        out.append({"name": cfg["name"], "url": cfg["url"], "kind": "Website"})
    for p in custom.SOLAR_PAGES:
        out.append({"name": p["name"], "url": p["url"], "kind": "Solar"})
    for key, url in PORTALS.items():
        out.append({"name": DISPLAY_NAMES.get(key, key), "url": url, "kind": "NIC portal"})
    return out


@app.get("/api/sources")
def list_sources(db: Session = Depends(get_session)):
    """Websites added on the website (newest first); scraped on every refresh."""
    return [source_dict(s) for s in db.scalars(select(CustomSource).order_by(CustomSource.id.desc()))]


@app.get("/api/sources/builtin")
def list_builtin_sources():
    return builtin_sources()


@app.post("/api/sources", dependencies=[Depends(require_admin)])
def add_source(body: SourceIn, db: Session = Depends(get_session)):
    from urllib.parse import urlparse
    from .scraper.custom import kind_for
    url = body.url.strip()
    u = urlparse(url)
    if u.scheme not in ("http", "https") or not u.netloc:
        raise HTTPException(422, "Enter a full address starting with http:// or https://")
    if db.scalar(select(CustomSource).where(CustomSource.url == url)):
        raise HTTPException(409, "This website is already in the list")
    s = CustomSource(name=body.name.strip(), url=url, kind=kind_for(url), enabled=True,
                     created_at=now_ist())
    db.add(s)
    db.commit()
    return source_dict(s)


@app.delete("/api/sources/{source_id}", dependencies=[Depends(require_admin)])
def delete_source(source_id: int, db: Session = Depends(get_session)):
    """Stop checking a website. Tenders already found from it stay."""
    s = db.get(CustomSource, source_id)
    if not s:
        raise HTTPException(404, "Website not found")
    db.delete(s)
    db.commit()
    return {"id": source_id, "deleted": True}


@app.get("/api/refresh")
def refresh_status(db: Session = Depends(get_session)):
    run = latest_run(db)
    return {"run": run_dict(run), "running": bool(run and run.active is not None)}


@app.post("/api/refresh", dependencies=[Depends(require_admin)])
def start_refresh(db: Session = Depends(get_session)):
    """Refresh button: start one round of every scraper, unless one is already running."""
    try:
        run, started = request_refresh(db, trigger="button")
    except Exception as exc:
        raise HTTPException(502, f"Could not start the refresh: {exc}")
    return {"started": started, "run": run_dict(run), "running": True}


@app.get("/api/filters")
def filters(state: str | None = None,
            focus: str = Query("relevant", pattern=FOCUS_PATTERN),
            db: Session = Depends(get_session)):
    """Dropdown values with counts of OPEN tenders in the chosen view.
    Pass ?state= to get only its districts."""
    now = now_ist()
    fc = focus_condition(focus)
    base = [is_open(now), NOT_DELETED] + ([fc] if fc is not None else [])

    def counts(col, *extra):
        rows = db.execute(select(col, func.count())
                          .where(col.is_not(None), *base, *extra)
                          .group_by(col).order_by(func.count().desc(), col))
        return [{"value": v, "count": c} for v, c in rows]

    district_rows = db.execute(
        select(Tender.district, Tender.state, func.count())
        .where(Tender.district.is_not(None), *base,
               *([Tender.state == state] if state else []))
        .group_by(Tender.district, Tender.state).order_by(Tender.state, Tender.district))

    # how many open tenders each view has (for the view switcher)
    view_counts = dict(db.execute(select(Tender.focus, func.count())
                                  .where(is_open(now), NOT_DELETED)
                                  .group_by(Tender.focus)).all())
    views = {
        "relevant": view_counts.get("security", 0) + view_counts.get("ground_search", 0),
        "security": view_counts.get("security", 0),
        "ground_search": view_counts.get("ground_search", 0),
        "solar": view_counts.get("solar", 0),
        "all": sum(view_counts.values()),
    }
    return {
        "views": views,
        "deleted": db.scalar(select(func.count()).where(Tender.deleted_at.is_not(None))),
        "states": counts(Tender.state),
        "districts": [{"value": d, "state": s, "count": c} for d, s, c in district_rows],
        "organisations": counts(Tender.organisation),
        "categories": counts(Tender.category),
        "sources": [{"value": f["value"], "label": DISPLAY_NAMES.get(f["value"], f["value"]),
                     "count": f["count"]} for f in counts(Tender.source)],
    }


@app.get("/api/stats")
def stats(focus: str = Query("relevant", pattern=FOCUS_PATTERN),
          db: Session = Depends(get_session)):
    now = now_ist()
    fc = focus_condition(focus)
    extra = [NOT_DELETED] + ([fc] if fc is not None else [])
    active = db.scalar(select(func.count()).where(is_open(now), *extra))
    week = db.scalar(select(func.count()).where(
        Tender.closing_at > now, Tender.closing_at <= now + timedelta(days=7), *extra))
    orgs = db.scalar(select(func.count(func.distinct(Tender.organisation)))
                     .where(is_open(now), *extra))
    last = db.scalar(select(func.max(Tender.last_seen_at)))
    run = latest_run(db)
    return {"active": active, "closing_this_week": week, "organisations": orgs,
            "last_updated": _dt(last), "refresh": run_dict(run),
            "refresh_running": bool(run and run.active is not None)}


# --------------------------------------------------------------------------
# Documents (actual files)
# --------------------------------------------------------------------------
@app.post("/api/tenders/{tender_pk}/documents/request")
def request_documents(tender_pk: int, db: Session = Depends(get_session)):
    """User clicked "Get documents". The document worker picks this up."""
    if not DOCUMENTS_ENABLED:
        raise HTTPException(503, "Document downloads are switched off on this server")
    t = db.get(Tender, tender_pk)
    if not t:
        raise HTTPException(404, "Tender not found")
    if t.docs_status in ("done", "requested"):
        return {"docs_status": t.docs_status}
    if t.docs_status == "captcha":
        # Already known to need a person; just bump it to the front of the manual queue.
        t.docs_requested_at = now_ist()
        db.commit()
        return {"docs_status": "captcha"}
    t.docs_status, t.docs_requested_at = "requested", now_ist()
    db.commit()
    return {"docs_status": "requested"}


@app.get("/api/documents/{doc_id}/file")
def download_document(doc_id: int, db: Session = Depends(get_session)):
    d = db.get(TenderDocument, doc_id)
    if not d or d.file_status != "stored" or not d.file_path:
        raise HTTPException(404, "File not available yet")
    storage = get_storage()
    if not storage.exists(d.file_path):
        raise HTTPException(404, "File missing from storage")
    ext = d.file_path.rsplit(".", 1)[-1] if "." in d.file_path else "bin"
    name = d.name if d.name.lower().endswith("." + ext) else f"{d.name}.{ext}"
    media = d.content_type if d.content_type and "html" not in d.content_type else None
    return FileResponse(storage.path(d.file_path), filename=name,
                        media_type=media or "application/octet-stream",
                        content_disposition_type="inline" if ext == "pdf" else "attachment")

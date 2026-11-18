"""
Database tables.

tenders           one row per tender (from any portal)
tender_documents  the documents listed for a tender
tender_changes    history of changes (for example a corrigendum moving the closing date)
scrape_runs       one row per "Refresh" (a round of every scraper)
"""
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base
from .scraper.normalize import now_ist


def _now():
    return now_ist()


class Tender(Base):
    __tablename__ = "tenders"
    __table_args__ = (
        UniqueConstraint("source", "tender_id", name="uq_source_tender"),
        Index("ix_tenders_closing", "closing_at"),
        Index("ix_tenders_state", "state"),
        Index("ix_tenders_district", "district"),
        Index("ix_tenders_docs_status", "docs_status"),
        Index("ix_tenders_focus", "focus"),
        Index("ix_tenders_org", "organisation"),
        Index("ix_tenders_deleted", "deleted_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(40))            # cppp, mahatenders, gem ...
    tender_id: Mapped[str] = mapped_column(String(80))          # 2026_AIIMS_123456_1
    ref_no: Mapped[str | None] = mapped_column(String(300))
    title: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    organisation: Mapped[str | None] = mapped_column(String(300))
    org_chain: Mapped[str | None] = mapped_column(Text)
    tender_type: Mapped[str | None] = mapped_column(String(100))
    category: Mapped[str | None] = mapped_column(String(100))
    product_category: Mapped[str | None] = mapped_column(String(200))
    form_of_contract: Mapped[str | None] = mapped_column(String(100))
    value: Mapped[float | None] = mapped_column(Numeric(18, 2))
    emd: Mapped[float | None] = mapped_column(Numeric(18, 2))
    quantity: Mapped[str | None] = mapped_column(String(100))          # GeM: number of units
    tender_fee: Mapped[float | None] = mapped_column(Numeric(18, 2))
    location: Mapped[str | None] = mapped_column(String(300))
    pincode: Mapped[str | None] = mapped_column(String(10))
    state: Mapped[str | None] = mapped_column(String(60))
    district: Mapped[str | None] = mapped_column(String(80))

    # Relevance to our business (see app/focus.py)
    focus: Mapped[str | None] = mapped_column(String(20))          # security | ground_search | None
    focus_score: Mapped[int | None] = mapped_column()
    focus_terms: Mapped[str | None] = mapped_column(String(300))
    period_days: Mapped[str | None] = mapped_column(String(20))
    authority_name: Mapped[str | None] = mapped_column(String(300))
    authority_address: Mapped[str | None] = mapped_column(Text)

    published_at: Mapped[datetime | None] = mapped_column(DateTime)
    doc_start_at: Mapped[datetime | None] = mapped_column(DateTime)
    doc_end_at: Mapped[datetime | None] = mapped_column(DateTime)
    bid_start_at: Mapped[datetime | None] = mapped_column(DateTime)
    closing_at: Mapped[datetime | None] = mapped_column(DateTime)
    opening_at: Mapped[datetime | None] = mapped_column(DateTime)
    pre_bid_at: Mapped[datetime | None] = mapped_column(DateTime)

    source_url: Mapped[str | None] = mapped_column(Text)

    # Document download state for the whole tender:
    #   none -> requested -> done | partial | captcha | failed
    docs_status: Mapped[str | None] = mapped_column(String(20))
    docs_requested_at: Mapped[datetime | None] = mapped_column(DateTime)
    docs_fetched_at: Mapped[datetime | None] = mapped_column(DateTime)
    raw: Mapped[dict | None] = mapped_column(JSON)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    # Set when you delete the tender on the website. The row is kept as a marker so the
    # scrapers never add it again; "Restore" clears it.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime)

    documents: Mapped[list["TenderDocument"]] = relationship(
        back_populates="tender", cascade="all, delete-orphan", order_by="TenderDocument.id")
    changes: Mapped[list["TenderChange"]] = relationship(
        back_populates="tender", cascade="all, delete-orphan", order_by="TenderChange.changed_at.desc()")


class TenderDocument(Base):
    __tablename__ = "tender_documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    tender_pk: Mapped[int] = mapped_column(ForeignKey("tenders.id", ondelete="CASCADE"), index=True)
    section: Mapped[str | None] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(400))
    description: Mapped[str | None] = mapped_column(Text)
    size: Mapped[str | None] = mapped_column(String(40))
    url: Mapped[str | None] = mapped_column(Text)

    # The actual file (stored outside the database, see app/documents/storage.py)
    file_status: Mapped[str | None] = mapped_column(String(20))   # stored | captcha | failed | deleted
    file_path: Mapped[str | None] = mapped_column(String(300))     # e.g. ab/ab12...ef.pdf
    file_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    file_bytes: Mapped[int | None] = mapped_column()
    content_type: Mapped[str | None] = mapped_column(String(100))
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime)
    error: Mapped[str | None] = mapped_column(Text)

    tender: Mapped[Tender] = relationship(back_populates="documents")


class TenderChange(Base):
    __tablename__ = "tender_changes"

    id: Mapped[int] = mapped_column(primary_key=True)
    tender_pk: Mapped[int] = mapped_column(ForeignKey("tenders.id", ondelete="CASCADE"), index=True)
    field: Mapped[str] = mapped_column(String(60))
    old_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str | None] = mapped_column(Text)
    changed_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    tender: Mapped[Tender] = relationship(back_populates="changes")


class CustomSource(Base):
    """A website you added on the website's "Websites" page. Checked on every refresh
    (app/scraper/custom.py)."""
    __tablename__ = "custom_sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    url: Mapped[str] = mapped_column(String(500), unique=True)
    kind: Mapped[str] = mapped_column(String(20))              # nic (GePNIC portal) | page
    enabled: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_status: Mapped[str | None] = mapped_column(Text)     # what happened last time
    last_found: Mapped[int | None] = mapped_column()           # relevant tenders seen last time


class ScrapeRun(Base):
    """One refresh = one round of every scraper (app/schedule.py).

    Only one round may be queued or running at a time: `active` is 1 while it is,
    NULL afterwards, and the column is UNIQUE, so the database itself refuses a second
    active round (two clicks at the same moment, or a schedule firing mid-run)."""
    __tablename__ = "scrape_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    trigger: Mapped[str] = mapped_column(String(20))           # button | schedule | cli
    status: Mapped[str] = mapped_column(String(20))            # queued | running | done | failed
    active: Mapped[int | None] = mapped_column(unique=True)
    requested_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    new_count: Mapped[int | None] = mapped_column()            # tenders first seen in this round
    message: Mapped[str | None] = mapped_column(Text)


class CheckedTender(Base):
    """Tenders whose detail page was opened and found NOT relevant (so not saved).
    Remembered so the next refresh does not open them again unless the closing
    date changes (corrigendum). Rows past their closing date are pruned."""
    __tablename__ = "checked_tenders"
    __table_args__ = (UniqueConstraint("source", "tender_id", name="uq_checked_source_tender"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(40))
    tender_id: Mapped[str] = mapped_column(String(120))
    closing_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    checked_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

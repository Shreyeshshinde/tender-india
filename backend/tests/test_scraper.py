from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy.orm import sessionmaker

from app.models import Tender, TenderChange
from app.scraper.cppp import build_record, parse_org_list, parse_tender_detail, parse_tender_list
from app.scraper.normalize import guess_state, parse_date, parse_money
from app.scraper.store import upsert_tender

from tests.conftest import make_test_engine

FIX = Path(__file__).parent / "fixtures"


def html(name):
    return (FIX / name).read_text(encoding="utf-8")


# ---------- cleaner ----------
@pytest.mark.parametrize("text,expected", [
    ("25-Nov-2026 03:00 PM", datetime(2026, 11, 25, 15, 0)),
    ("25/11/2026 15:00", datetime(2026, 11, 25, 15, 0)),
    ("25-Nov-2026", datetime(2026, 11, 25)),
    ("NA", None), ("", None), ("garbage", None),
])
def test_parse_date(text, expected):
    assert parse_date(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("45,00,000", Decimal("4500000")), ("₹ 90,000.50", Decimal("90000.50")),
    ("NA", None), ("0", None), (None, None),
])
def test_parse_money(text, expected):
    assert parse_money(text) == expected


def test_guess_state():
    assert guess_state("AIIMS Nagpur") == "Maharashtra"
    assert guess_state("Guwahati, Assam") == "Assam"
    assert guess_state("Gorakhpur") is None          # "goa" must not match inside a word
    assert guess_state(None, "Orissa PWD") == "Odisha"


# ---------- parsers ----------
def test_org_list():
    orgs = parse_org_list(html("org_list.html"))
    assert [o["name"] for o in orgs] == ["Airports Authority of India",
                                         "All India Institute of Medical Science-Nagpur"]
    assert orgs[0]["count"] == 164
    assert orgs[0]["url"].startswith("https://eprocure.gov.in/eprocure/app?")


def test_tender_list():
    rows, nxt = parse_tender_list(html("tender_list.html"))
    assert len(rows) == 2
    r = rows[0]
    assert r["tender_id"] == "2026_AIIMS_901234_1"
    assert r["ref_no"] == "AIIMS/NGP/STORE/2026/112"
    assert r["title"] == "Supply of Laboratory Equipment for Biochemistry Department"
    assert r["closing_at"] == datetime(2026, 11, 25, 15, 0)
    assert r["org_chain"].startswith("All India Institute")
    assert nxt and "linkFwd" in nxt


def test_tender_detail():
    d = parse_tender_detail(html("tender_detail.html"))
    assert d["tender_id"] == "2026_AIIMS_901234_1"
    assert d["tender_type"] == "Open Tender"
    assert d["form_of_contract"] == "Supply"
    assert d["value"] == Decimal("4500000")
    assert d["emd"] == Decimal("90000")
    assert d["tender_fee"] is None
    assert d["closing_at"] == datetime(2026, 11, 25, 15, 0)
    assert d["doc_end_at"] == datetime(2026, 11, 25, 14, 0)
    assert d["pincode"] == "441108"
    assert d["authority_name"] == "Stores Officer"
    names = {(x["section"], x["name"], x["size"]) for x in d["documents"]}
    assert names == {
        ("NIT Document", "Tendernotice_1.pdf", "412.55 KB"),
        ("Work Item Documents", "TenderDocument_Lab.pdf", "1.20 MB"),
        ("Work Item Documents", "BOQ_901234.xls", "88.00 KB"),
    }


# ---------- database save + corrigendum tracking ----------
@pytest.fixture
def db():
    engine = make_test_engine()
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _record():
    rows, _ = parse_tender_list(html("tender_list.html"))
    return build_record(rows[0], parse_tender_detail(html("tender_detail.html")))


def test_upsert_new_then_same(db):
    rec = _record()
    assert rec["state"] == "Maharashtra"
    assert rec["organisation"] == "All India Institute of Medical Science-Nagpur"
    assert upsert_tender(db, rec) == "new"
    db.commit()
    assert upsert_tender(db, _record()) == "same"
    db.commit()
    t = db.query(Tender).one()
    assert len(t.documents) == 3


def test_upsert_corrigendum(db):
    upsert_tender(db, _record()); db.commit()
    rec = _record()
    rec["closing_at"] = datetime(2026, 12, 5, 15, 0)        # date extended
    rec["documents"].append({"section": "Corrigendum", "name": "Corrigendum_1.pdf"})
    assert upsert_tender(db, rec) == "changed"
    db.commit()
    t = db.query(Tender).one()
    assert t.closing_at == datetime(2026, 12, 5, 15, 0)
    fields = {c.field for c in db.query(TenderChange)}
    assert fields == {"closing_at", "documents"}
    assert len(t.documents) == 4


# ---------- search with short forms ----------
@pytest.mark.parametrize("query,titles", [
    ("Door Frame Metal Detector", {"Supply of DFMD at Main Gate", "Door Frame Metal Detectors for Hostel"}),
    ("dfmd tenders", {"Supply of DFMD at Main Gate", "Door Frame Metal Detectors for Hostel"}),
    ("DFMD hostel", {"Door Frame Metal Detectors for Hostel"}),
    ("mri", {"MRI machine AMC"}),                       # not "Amritsar road"
    ("ups", {"Online UPS 10 KVA"}),                     # not "groups"
    ("amritsar", {"Amritsar road repair"}),
    ("tenders", None),                                  # filler only: everything
])
def test_search_short_forms(db, query, titles):
    from app.models import Tender
    from app.search import search_conditions
    from sqlalchemy import select
    all_titles = ["Supply of DFMD at Main Gate", "Door Frame Metal Detectors for Hostel",
                  "MRI machine AMC", "Amritsar road repair", "Online UPS 10 KVA",
                  "Catering for staff groups"]
    for i, t in enumerate(all_titles):
        db.add(Tender(source="t", tender_id=f"2026_T_{i}_1", title=t))
    db.commit()
    stmt = select(Tender.title)
    for c in search_conditions(query, [Tender.title]):
        stmt = stmt.where(c)
    got = set(db.scalars(stmt))
    assert got == (titles if titles is not None else set(all_titles))

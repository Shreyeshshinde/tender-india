"""Several NIC portals: correct labels, no ID clashes, documents from the right site."""
from pathlib import Path

from sqlalchemy.orm import sessionmaker

from app.models import Tender
from app.documents import worker as workermod
from app.scraper.cppp import build_record, parse_tender_detail, parse_tender_list
from app.scraper.portals import PORTALS, base_url_for, source_for
from app.scraper.store import upsert_tender
from tests.conftest import make_test_engine

FIX = Path(__file__).parent / "fixtures"


def _rec(source, base):
    rows, _ = parse_tender_list((FIX / "tender_list.html").read_text(), base)
    return build_record(rows[1], {}, source=source, base_url=base)


def test_source_names():
    assert source_for("https://eprocure.gov.in/eprocure/app") == "cppp"
    assert source_for("https://etenders.chd.nic.in/nicgep/app/") == "chandigarh"
    assert source_for("https://newportal.example.gov.in/nicgep/app") == "newportal.example.gov.in"
    assert base_url_for("chandigarh") == PORTALS["chandigarh"]


def test_state_portal_sets_state_and_url():
    rec = _rec("chandigarh", PORTALS["chandigarh"])
    assert rec["source"] == "chandigarh"
    assert rec["source_url"] == PORTALS["chandigarh"]
    # title/org text in the fixture names no place, so the portal decides the state
    rec2 = build_record({"tender_id": "2026_CHD_1_1", "org_chain": "Police Department"}, {},
                        source="chandigarh", base_url=PORTALS["chandigarh"])
    assert rec2["state"] == "Chandigarh"


def test_same_id_on_two_portals_kept_separately():
    engine = make_test_engine()
    s = sessionmaker(bind=engine)()
    assert upsert_tender(s, _rec("cppp", PORTALS["cppp"])) == "new"
    assert upsert_tender(s, _rec("chandigarh", PORTALS["chandigarh"])) == "new"
    s.commit()
    assert {t.source for t in s.query(Tender)} == {"cppp", "chandigarh"}


def test_worker_uses_each_tenders_own_portal():
    cache = {}
    t1 = Tender(source="cppp", tender_id="a", source_url=PORTALS["cppp"])
    t2 = Tender(source="chandigarh", tender_id="b", source_url=PORTALS["chandigarh"])
    c1 = workermod._client_for(t1, cache)
    c2 = workermod._client_for(t2, cache)
    assert c1.base_url == PORTALS["cppp"] and c2.base_url == PORTALS["chandigarh"]
    assert workermod._client_for(t1, cache) is c1          # reused, not recreated

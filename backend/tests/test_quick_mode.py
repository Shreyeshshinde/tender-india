"""Second crawl must skip detail pages for unchanged tenders."""
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.orm import sessionmaker

from app import db as dbmod
from app.scraper import run as runmod

from tests.conftest import make_test_engine

FIX = Path(__file__).parent / "fixtures"


class FakePortal:
    """Serves the sample pages instead of the real website and counts detail fetches."""
    base_url = "https://eprocure.gov.in/eprocure/app"

    def __init__(self, list_html):
        self.list_html = list_html
        self.detail_fetches = 0

    def org_list_url(self):
        return self.base_url + "?page=FrontEndTendersByOrganisation&service=page"

    def get(self, url, slow_retries=None):
        if "FrontEndTendersByOrganisation&service=page" in url:
            return (FIX / "org_list.html").read_text()
        if "FrontEndViewTender" in url:
            self.detail_fetches += 1
            return (FIX / "tender_detail.html").read_text()
        if "linkFwd" in url:
            return "<html></html>"            # no more pages
        return self.list_html


@pytest.fixture
def portal(monkeypatch):
    engine = make_test_engine()
    monkeypatch.setattr(runmod, "SessionLocal", sessionmaker(bind=engine, expire_on_commit=False))
    monkeypatch.setattr(runmod, "init_db", lambda: None)
    fake = FakePortal((FIX / "tender_list.html").read_text())
    monkeypatch.setattr(runmod, "make_client", lambda *a: fake)
    return fake


def args(full=False):
    return SimpleNamespace(engine="http", base_url="x", max_orgs=1, max_per_org=None,
                           save_html=None, full=full, all_details=True)


def test_second_run_skips_unchanged(portal):
    runmod.run_once(args())
    assert portal.detail_fetches == 2          # first run: open every tender

    portal.detail_fetches = 0
    runmod.run_once(args())
    assert portal.detail_fetches == 0          # second run: nothing changed, nothing opened


def test_changed_date_is_reopened(portal):
    runmod.run_once(args())
    portal.detail_fetches = 0
    portal.list_html = portal.list_html.replace("02-Dec-2026 11:00 AM", "09-Dec-2026 11:00 AM")
    runmod.run_once(args())
    assert portal.detail_fetches == 1          # only the tender with the new closing date


def test_full_reopens_everything(portal):
    runmod.run_once(args())
    portal.detail_fetches = 0
    runmod.run_once(args(full=True))
    assert portal.detail_fetches == 2


def test_focus_mode_skips_works_tenders(portal):
    a = args()
    a.all_details = False                        # default behaviour
    runmod.run_once(a)
    # fixture: "Supply of Laboratory Equipment" (opened) and
    #          "Annual Housekeeping Services" (works/services: saved from the list only)
    assert portal.detail_fetches == 1


def test_title_could_match():
    from app.focus import title_could_match
    assert title_could_match("Procurement of DFMD")
    assert title_could_match("Supply of security equipment for police")
    assert title_could_match("Purchase of stores items")
    assert not title_could_match("SPECIAL REPAIR TO BLDG NO 12 AT MES CANTT")
    assert not title_could_match("Construction of boundary wall")
    assert not title_could_match("Annual Housekeeping Services at OPD Block")

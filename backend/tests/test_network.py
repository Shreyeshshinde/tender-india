"""Internet drops: the client waits and retries, and progress is never lost."""
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests
from sqlalchemy.orm import sessionmaker

from app.models import Tender
from app.scraper import cppp
from app.scraper import run as runmod
from app.scraper.cppp import GePNICClient, PortalUnreachable

from tests.conftest import make_test_engine

FIX = Path(__file__).parent / "fixtures"


class Resp:
    status_code = 200
    text = "<html>ok</html>"

    def raise_for_status(self):
        pass


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(cppp.time, "sleep", lambda s: None)


def test_recovers_when_internet_comes_back(monkeypatch):
    c = GePNICClient()
    calls = {"n": 0}

    def flaky(url, timeout):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise requests.ConnectionError("Failed to resolve 'eprocure.gov.in' (getaddrinfo failed)")
        return Resp()

    monkeypatch.setattr(c.session, "get", flaky)
    assert c.get("/x") == "<html>ok</html>"
    assert calls["n"] == 3


def test_gives_up_after_long_outage(monkeypatch):
    c = GePNICClient()

    def down(url, timeout):
        raise requests.ConnectionError("getaddrinfo failed")

    monkeypatch.setattr(c.session, "get", down)
    with pytest.raises(PortalUnreachable):
        c.get("/x")


def test_progress_saved_when_internet_dies_midway(monkeypatch):
    engine = make_test_engine()
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(runmod, "SessionLocal", Session)
    monkeypatch.setattr(runmod, "init_db", lambda: None)

    class DiesAfterOneTender:
        base_url = cppp.CPPP_BASE
        details = 0

        def org_list_url(self):
            return self.base_url + "?page=FrontEndTendersByOrganisation&service=page"

        def get(self, url, slow_retries=None):
            if "service=page" in url:
                return (FIX / "org_list.html").read_text()
            if "FrontEndViewTender" in url:
                self.details += 1
                if self.details > 1:
                    raise PortalUnreachable("internet gone")
                return (FIX / "tender_detail.html").read_text()
            return (FIX / "tender_list.html").read_text()

    monkeypatch.setattr(runmod, "make_client", lambda *a: DiesAfterOneTender())
    args = SimpleNamespace(engine="http", base_url="x", max_orgs=1, max_per_org=None,
                           save_html=None, full=False, all_details=True)
    with pytest.raises(PortalUnreachable):
        runmod.run_once(args)

    s = Session()
    assert s.query(Tender).count() == 1      # first tender survived even though only 1 < 25 batch


def test_slow_server_retries_and_slows_down(monkeypatch):
    c = GePNICClient()
    calls = {"n": 0}

    def slow_then_ok(url, timeout):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise requests.ReadTimeout("Read timed out. (read timeout=60)")
        return Resp()

    monkeypatch.setattr(c.session, "get", slow_then_ok)
    assert c.get("/x") == "<html>ok</html>"
    assert c.delay > c.min_delay            # it slowed itself down


def test_stuck_page_is_skipped_not_fatal(monkeypatch):
    c = GePNICClient()

    def always_slow(url, timeout):
        raise requests.ReadTimeout("Read timed out.")

    monkeypatch.setattr(c.session, "get", always_slow)
    with pytest.raises(cppp.SlowPage):      # crawl catches this and moves to the next tender
        c.get("/x")


def test_many_stuck_pages_in_a_row_means_portal_down(monkeypatch):
    c = GePNICClient()
    monkeypatch.setattr(c.session, "get",
                        lambda url, timeout: (_ for _ in ()).throw(requests.ReadTimeout("x")))
    for _ in range(c.STUCK_LIMIT - 1):
        with pytest.raises(cppp.SlowPage):
            c.get("/x")
    with pytest.raises(PortalUnreachable):
        c.get("/x")


def test_404_is_not_retried(monkeypatch):
    c = GePNICClient()
    calls = {"n": 0}

    class R404(Resp):
        status_code = 404

        def raise_for_status(self):
            raise requests.HTTPError("404", response=self)

    def missing(url, timeout):
        calls["n"] += 1
        return R404()

    monkeypatch.setattr(c.session, "get", missing)
    with pytest.raises(requests.HTTPError):
        c.get("/x")
    assert calls["n"] == 1

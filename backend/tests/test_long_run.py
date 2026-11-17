"""
Long runs: CPPP sessions expire and old links stop answering.
These tests copy what happened in a real run (every organisation timing out
after about 20 minutes) and check the crawler recovers instead of wasting hours.
"""
import pytest

from app.scraper import cppp
from app.scraper.cppp import PortalUnreachable, SlowPage, crawl

BASE = cppp.CPPP_BASE
ROW = ('<tr><td>1.</td><td>03-Nov-2026 05:00 PM</td><td>25-Nov-2030 03:00 PM</td>'
       '<td>26-Nov-2030 03:30 PM</td><td><a href="/eprocure/app?page=FrontEndViewTender&sp={tid}">'
       '[Tender {n}]</a> [REF/{n}][{tid}]</td><td>Org {n}</td></tr>')


class ExpiringPortal:
    """Session-based links: a link only works while its session is alive.
    The 'server' expires the session after `life` requests (like a real timeout)."""
    base_url = BASE

    def __init__(self, n_orgs=6, life=None, broken=False, flaky_orgs=()):
        self.n_orgs, self.life, self.broken = n_orgs, life, broken
        self.flaky = {o: 1 for o in flaky_orgs}     # these fail once, then work
        self.session_id, self.requests_in_session = 1, 0
        self.expired = False
        self.visited = []
        self.resets = 0

    def reset_session(self):
        self.session_id += 1
        self.requests_in_session = 0
        self.expired = False
        self.resets += 1

    def org_list_url(self):
        return BASE + "?page=FrontEndTendersByOrganisation&service=page"

    def get(self, url, slow_retries=None):
        self.requests_in_session += 1
        if self.life and self.requests_in_session > self.life:
            self.expired = True
        if "service=page" in url:
            rows = "".join(
                f'<tr><td>{i}.</td><td>Org {i}</td><td><a href="/eprocure/app?org={i}&sess={self.session_id}">1</a></td></tr>'
                for i in range(1, self.n_orgs + 1))
            return f"<table>{rows}</table>"
        if "org=" in url:
            org = int(url.split("org=")[1].split("&")[0])
            sess = int(url.split("sess=")[1])
            if self.broken or self.expired or sess != self.session_id:
                raise SlowPage("Server too slow")        # dead link just hangs
            if self.flaky.get(org):
                self.flaky[org] -= 1
                raise SlowPage("Server too slow")
            self.visited.append(org)
            tid = f"2026_ORG{org}_{100 + org}_1"
            return f"<table>{ROW.format(tid=tid, n=org)}</table>"
        return "<html>detail</html>"


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(cppp.time, "sleep", lambda s: None)
    monkeypatch.setattr(cppp, "COOLDOWN_SECONDS", 0)


def test_session_expires_midway_and_crawler_recovers():
    portal = ExpiringPortal(n_orgs=6, life=5)       # session dies every ~5 requests
    found = []
    crawl(portal, on_record=found.append)
    assert sorted(set(portal.visited)) == [1, 2, 3, 4, 5, 6]   # nobody lost
    assert len({r["tender_id"] for r in found}) == 6
    assert portal.resets >= 1                                 # it noticed and refreshed


def test_old_links_refreshed_by_time(monkeypatch):
    portal = ExpiringPortal(n_orgs=4)
    monkeypatch.setattr(cppp, "LINK_REFRESH_SECONDS", -1)    # "15 minutes" always passed
    crawl(portal)
    assert portal.resets >= 4                                 # fresh session before each org
    assert sorted(portal.visited) == [1, 2, 3, 4]


def test_flaky_org_gets_second_chance():
    portal = ExpiringPortal(n_orgs=3, flaky_orgs=[2, 2])     # org 2 fails twice in a row
    crawl(portal)
    assert sorted(portal.visited) == [1, 2, 3]                # recovered in the second pass


def test_portal_fully_down_stops_after_two_pauses(monkeypatch):
    pauses = []
    monkeypatch.setattr(cppp.time, "sleep", lambda s: pauses.append(s))
    portal = ExpiringPortal(n_orgs=20, broken=True)
    with pytest.raises(PortalUnreachable):
        crawl(portal)
    assert len(pauses) == cppp.MAX_COOLDOWNS                  # paused twice, then gave up
    assert portal.visited == []

"""Security / ground search relevance on realistic Indian tender titles."""
import pytest

from app.focus import classify


@pytest.mark.parametrize("title,expected", [
    # security screening
    ("Procurement of Door Frame Metal Detector (DFMD) Multi Zone - 35 Nos", "security"),
    ("Supply of DFMD and HHMD for Police Department", "security"),
    ("Tender Invited For Door Frame Metal Detector", "security"),
    ("Purchase of Hand-Held Metal Detectors", "security"),
    ("Annual Maintenance Contract of DFMD and HHMD at Airport", "security"),
    ("Supply of X-Ray Baggage Inspection System (XBIS)", "security"),
    ("Procurement of Non Linear Junction Detector for BDDS", "security"),
    ("Explosive Vapour Detector for State Police", "security"),
    ("Mobile phone detector for Central Jail", "security"),
    ("Supply of Metal Detector for Court Complex Entrance", "security"),
    ("Under Vehicle Scanning System at Main Gate", "security"),
    ("Liquid Explosive Detector / Bottled liquid scanner", "security"),
    # ground search
    ("Procurement of Mine Detector for Engineer Regiment", "ground_search"),
    ("Deep Search Metal Detector (DSMD) - 10 Nos", "ground_search"),
    ("UXO clearance equipment", "ground_search"),
    ("Supply of Metal Detector for Army Engineers", "ground_search"),
    ("Ground Penetrating Radar for IED detection", "ground_search"),
    ("Underwater Metal Detector for Navy Divers", "ground_search"),
    # NOT ours
    ("Supply of Metal Detector for Coal Conveyor Belt at CHP", None),
    ("Metal Detector for Food Processing Line", None),
    ("Provision of Security Guards at Hostel", None),
    ("Security services with metal detector frisking", None),
    ("Supply of ERW Pipes", None),
    ("GPR survey of pavement for road condition", None),
    ("Road repair near Latur", None),
    ("Supply of CCTV cameras", None),
    ("Military hospital catering", None),
    ("Mining plant spares", None),
])
def test_classify_titles(title, expected):
    focus, score, terms = classify(title)
    assert focus == expected, f"{title!r} -> {focus} {terms}"
    if expected:
        assert score > 0 and terms


def test_description_counts_too():
    assert classify("Procurement of security equipment", "Items: DFMD 5 nos, HHMD 20 nos")[0] == "security"


def test_matched_terms_are_readable():
    _, _, terms = classify("Supply of DFMD and HHMD")
    assert terms == ["DFMD", "HHMD"]


def test_api_views():
    from datetime import timedelta

    from fastapi.testclient import TestClient
    from sqlalchemy.orm import sessionmaker

    from app import main as mainmod
    from app.db import get_session
    from app.scraper.normalize import now_ist
    from app.scraper.store import upsert_tender
    from tests.conftest import make_test_engine

    Session = sessionmaker(bind=make_test_engine(), expire_on_commit=False)
    soon = now_ist() + timedelta(days=5)
    with Session() as s:
        for i, title in enumerate(["Supply of DFMD and HHMD", "Deep Search Metal Detector",
                                   "Road repair", "Housekeeping services"]):
            upsert_tender(s, {"source": "cppp", "tender_id": f"2026_X_{i}_1", "title": title,
                              "closing_at": soon})
        s.commit()

    def override():
        with Session() as s:
            yield s

    mainmod.app.dependency_overrides[get_session] = override
    try:
        c = TestClient(mainmod.app)
        titles = lambda **p: sorted(i["title"] for i in c.get("/api/tenders", params=p).json()["items"])
        assert titles() == ["Deep Search Metal Detector", "Supply of DFMD and HHMD"]   # default view
        assert titles(focus="security") == ["Supply of DFMD and HHMD"]
        assert titles(focus="ground_search") == ["Deep Search Metal Detector"]
        assert len(titles(focus="all")) == 4
        item = c.get("/api/tenders").json()["items"][0]
        assert item["focus_label"] and item["focus_terms"]
        f = c.get("/api/filters").json()
        assert f["views"] == {"relevant": 2, "security": 1, "ground_search": 1, "all": 4}
        assert c.get("/api/stats").json()["active"] == 2
        assert c.get("/api/stats", params={"focus": "all"}).json()["active"] == 4
    finally:
        mainmod.app.dependency_overrides.clear()

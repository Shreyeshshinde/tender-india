"""Refresh button (one round at a time) and deleting tenders (they never come back)."""
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app import main as mainmod
from app import refresh
from app.db import get_session
from app.models import ScrapeRun, Tender
from app.scraper.normalize import now_ist
from app.scraper.store import upsert_tender
from tests.conftest import make_test_engine


TODAY = now_ist().replace(microsecond=0)


def record(tid, title="Supply of DFMD door frame metal detector", days=10):
    return {"source": "cppp", "tender_id": tid, "title": title, "organisation": "BSF",
            "closing_at": TODAY + timedelta(days=days)}


@pytest.fixture
def Session(monkeypatch):
    monkeypatch.setenv("REFRESH_RUNNER", "none")       # never start real scrapers in tests
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    engine = make_test_engine()
    S = sessionmaker(bind=engine, expire_on_commit=False)

    def session_override():
        db = S()
        try:
            yield db
        finally:
            db.close()

    mainmod.app.dependency_overrides[get_session] = session_override
    yield S
    mainmod.app.dependency_overrides.clear()


@pytest.fixture
def client(Session):
    return TestClient(mainmod.app)        # no "with": keeps the real database out of it


def add(Session, *recs):
    with Session() as db:
        for r in recs:
            upsert_tender(db, r)
        db.commit()
        return {t.tender_id: t.id for t in db.scalars(select(Tender))}


# ---------- deleting ----------
def test_delete_hides_tender_everywhere_and_restore_brings_it_back(Session, client):
    ids = add(Session, record("T1"), record("T2"))
    assert client.get("/api/tenders").json()["total"] == 2

    assert client.delete(f"/api/tenders/{ids['T1']}").status_code == 200
    listing = client.get("/api/tenders").json()
    assert [t["tender_id"] for t in listing["items"]] == ["T2"]
    assert client.get("/api/stats").json()["active"] == 1
    filters = client.get("/api/filters").json()
    assert filters["views"]["relevant"] == 1 and filters["deleted"] == 1

    deleted = client.get("/api/tenders?status=deleted").json()
    assert [t["tender_id"] for t in deleted["items"]] == ["T1"]
    assert deleted["items"][0]["deleted_at"]

    assert client.post(f"/api/tenders/{ids['T1']}/restore").status_code == 200
    assert client.get("/api/tenders").json()["total"] == 2
    assert client.get("/api/tenders?status=deleted").json()["total"] == 0


def test_deleted_tender_is_not_brought_back_or_changed_by_a_scrape(Session, client):
    ids = add(Session, record("T1"))
    client.delete(f"/api/tenders/{ids['T1']}")
    with Session() as db:
        changed = record("T1", title="Supply of DFMD (corrigendum)", days=20)
        assert upsert_tender(db, changed) == "skipped"
        db.commit()
        t = db.get(Tender, ids["T1"])
        assert t.deleted_at is not None
        assert t.title == "Supply of DFMD door frame metal detector"   # untouched
        assert t.changes == []
    assert client.get("/api/tenders").json()["total"] == 0


def test_same_tender_is_saved_only_once(Session):
    add(Session, record("T1"))
    with Session() as db:
        assert upsert_tender(db, record("T1")) == "same"
        db.commit()
        assert len(db.scalars(select(Tender)).all()) == 1


def test_admin_token_protects_delete_restore_and_refresh(Session, client, monkeypatch):
    ids = add(Session, record("T1"))
    monkeypatch.setenv("ADMIN_TOKEN", "s3cret")
    assert client.delete(f"/api/tenders/{ids['T1']}").status_code == 401
    assert client.delete(f"/api/tenders/{ids['T1']}",
                         headers={"X-Admin-Token": "wrong"}).status_code == 401
    assert client.post("/api/refresh").status_code == 401
    assert client.post(f"/api/tenders/{ids['T1']}/restore").status_code == 401
    ok = client.delete(f"/api/tenders/{ids['T1']}", headers={"X-Admin-Token": "s3cret"})
    assert ok.status_code == 200
    assert client.get("/api/tenders").status_code == 200       # reading stays public


# ---------- refresh ----------
def test_refresh_button_starts_only_one_round(Session, client):
    first = client.post("/api/refresh").json()
    assert first["started"] is True and first["run"]["status"] == "queued"
    second = client.post("/api/refresh").json()
    assert second["started"] is False and second["run"]["id"] == first["run"]["id"]
    status = client.get("/api/refresh").json()
    assert status["running"] is True and status["run"]["id"] == first["run"]["id"]


def test_round_claims_button_request_and_reports_new_tenders(Session, client):
    run_id = client.post("/api/refresh").json()["run"]["id"]
    with Session() as db:
        run = refresh.claim(db, "schedule")
        assert run.id == run_id and run.status == "running" and run.trigger == "button"
        assert refresh.claim(db, "schedule") is None          # a second round must not start
    add(Session, record("NEW1"))
    with Session() as db:
        refresh.finish(db, run_id, ok=True, message="All scrapers finished")

    status = client.get("/api/refresh").json()
    assert status["running"] is False
    assert status["run"]["status"] == "done" and status["run"]["new_count"] == 1
    items = client.get("/api/tenders").json()["items"]
    assert items[0]["tender_id"] == "NEW1" and items[0]["is_new"] is True

    again = client.post("/api/refresh").json()                 # finished: a new round may start
    assert again["started"] is True and again["run"]["id"] != run_id


def test_scheduled_round_creates_its_own_run(Session):
    with Session() as db:
        run = refresh.claim(db, "schedule")
        assert run.status == "running" and run.trigger == "schedule"
        assert refresh.request_refresh(db)[1] is False         # button while it runs: no new round


def test_database_refuses_two_active_rounds(Session):
    with Session() as db:
        assert refresh._create(db, "button", "queued") is not None
        assert refresh._create(db, "button", "queued") is None
        assert len(db.scalars(select(ScrapeRun)).all()) == 1


def test_stale_round_does_not_block_forever(Session):
    with Session() as db:
        run = refresh._create(db, "button", "running")
        run.requested_at = now_ist() - timedelta(hours=refresh.STALE_HOURS + 1)
        db.commit()
        new, started = refresh.request_refresh(db)
        assert started is True and new.id != run.id
        assert db.get(ScrapeRun, run.id).status == "failed"


def test_failed_start_releases_the_lock(Session, client, monkeypatch):
    def boom():
        raise RuntimeError("no permission")
    monkeypatch.setattr(refresh, "dispatch", boom)
    r = client.post("/api/refresh")
    assert r.status_code == 502 and "no permission" in r.json()["detail"]
    status = client.get("/api/refresh").json()
    assert status["running"] is False and status["run"]["status"] == "failed"


def test_documents_can_be_switched_off(Session, client, monkeypatch):
    ids = add(Session, record("T1"))
    monkeypatch.setattr(mainmod, "DOCUMENTS_ENABLED", False)
    assert client.post(f"/api/tenders/{ids['T1']}/documents/request").status_code == 503
    assert client.get(f"/api/tenders/{ids['T1']}").json()["docs_enabled"] is False


def test_parallel_groups_cover_every_portal_once():
    from app.refresh_cli import groups
    from app.scraper.portals import PORTALS
    g = groups()
    nic = [p for name, cmds in g.items() if name.startswith("nic-") for p in cmds[0][-1].split(",")]
    assert sorted(nic) == sorted(PORTALS) and len(nic) == len(set(nic))
    assert {"gem", "states", "websites"} <= set(g) and len(g) <= 20


def test_parallel_start_and_finish(Session, monkeypatch, capsys):
    from app import refresh_cli
    import app.db as dbmod
    monkeypatch.setattr(dbmod, "SessionLocal", Session)
    monkeypatch.setattr(dbmod, "init_db", lambda *a: None)
    refresh_cli.cmd_start(None)
    out = capsys.readouterr().out
    assert "run_id=1" in out and '"gem"' in out
    refresh_cli.cmd_start(None)                       # second one must not start
    assert "run_id=\n" in capsys.readouterr().out
    refresh_cli.cmd_finish(type("A", (), {"run_id": 1, "ok": True, "message": "done"})())
    with Session() as db:
        assert db.get(ScrapeRun, 1).status == "done"

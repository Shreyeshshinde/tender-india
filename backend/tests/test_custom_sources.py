"""Websites added on the website: API and turning page rows into tenders."""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app import main as mainmod
from app.db import get_session
from app.scraper.custom import item_to_record, kind_for, source_key
from tests.conftest import make_test_engine


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    S = sessionmaker(bind=make_test_engine(), expire_on_commit=False)

    def override():
        db = S()
        try:
            yield db
        finally:
            db.close()
    mainmod.app.dependency_overrides[get_session] = override
    yield TestClient(mainmod.app)
    mainmod.app.dependency_overrides.clear()


def test_add_list_delete_source(client):
    r = client.post("/api/sources", json={"name": "Maha Police", "url": "https://mahapolice.gov.in/tenders"})
    assert r.status_code == 200 and r.json()["kind"] == "page"
    nic = client.post("/api/sources", json={"name": "Goa", "url": "https://eprocure.goa.gov.in/nicgep/app"})
    assert nic.json()["kind"] == "nic"
    assert client.post("/api/sources", json={"name": "Again", "url": "https://mahapolice.gov.in/tenders"}).status_code == 409
    assert client.post("/api/sources", json={"name": "Bad", "url": "mahapolice.gov.in"}).status_code == 422
    assert len(client.get("/api/sources").json()) == 2
    assert client.delete(f"/api/sources/{r.json()['id']}").status_code == 200
    assert [s["name"] for s in client.get("/api/sources").json()] == ["Goa"]


def test_admin_token_needed_to_add(client, monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "x")
    assert client.post("/api/sources", json={"name": "A site", "url": "https://a.gov.in/"}).status_code == 401


def test_page_rows_become_relevant_tenders_only():
    src = SimpleNamespace(url="https://www.example.gov.in/tenders", name="Example Police")
    rec = item_to_record(src, {"text": "Supply of Door Frame Metal Detector (DFMD) 10 Nos. Last date 25-11-2026",
                               "href": "/docs/t1.pdf"})
    assert rec["source"] == "example.gov.in" and rec["organisation"] == "Example Police"
    assert rec["source_url"] == "https://www.example.gov.in/docs/t1.pdf"
    assert rec["tender_id"].startswith("web-")
    assert item_to_record(src, {"text": "Construction of boundary wall at police lines", "href": None}) is None
    assert kind_for("https://x.nic.in/nicgep/app?page=Home") == "nic"
    assert source_key("https://www.isro.gov.in/x") == "isro.gov.in"

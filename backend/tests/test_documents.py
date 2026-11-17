"""Document downloads: real files, CAPTCHA detection, dedupe, cleanup, API, assist matching."""
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import selectinload, sessionmaker

from app import main as mainmod
from app.db import get_session
from app.documents import storage as storagemod
from app.documents import worker as workermod
from app.documents.assist import match_document, save_download
from app.documents.fetcher import classify, fetch_tender, is_captcha_page
from app.documents.storage import LocalStorage
from app.models import Tender, TenderDocument
from app.scraper import run as runmod
from app.scraper.cppp import build_record, parse_tender_detail, parse_tender_list
from app.scraper.normalize import now_ist
from app.scraper.store import upsert_tender
from tests.conftest import make_test_engine

FIX = Path(__file__).parent / "fixtures"
PDF_A = b"%PDF-1.4\n% notice\n" + b"A" * 2000
PDF_B = b"%PDF-1.4\n% specs\n" + b"B" * 3000
CAPTCHA_HTML = (b"<html><body><form id='captchaForm'><img src='/eprocure/captcha.jpg'>"
                b"<input name='captchaText'> Enter the characters shown</form></body></html>")


class R:
    def __init__(self, content, ctype="application/pdf", status=200, disp=None):
        self.content, self.status_code = content, status
        self.headers = {"Content-Type": ctype}
        if disp:
            self.headers["Content-Disposition"] = disp

    @property
    def text(self):
        return self.content.decode("utf-8", "replace")


class Portal:
    """Fake CPPP. files: {'D1': response, ...} for the three document links in the fixture."""
    base_url = "https://eprocure.gov.in/eprocure/app"

    def __init__(self, files, list_html=None):
        self.files = files
        self.list_html = list_html or (FIX / "tender_list.html").read_text()
        self.file_requests = 0

    def org_list_url(self):
        return self.base_url + "?page=FrontEndTendersByOrganisation&service=page"

    def get(self, url, slow_retries=None):
        if "service=page" in url:
            return (FIX / "org_list.html").read_text()
        if "FrontEndViewTender" in url and "sp=X" in url:
            return (FIX / "tender_detail.html").read_text()
        if "linkFwd" in url:
            return "<html></html>"
        return self.list_html

    def get_response(self, url):
        self.file_requests += 1
        key = url.rsplit("sp=", 1)[-1]
        return self.files.get(key, R(b"<html><title>Error</title></html>", "text/html"))


@pytest.fixture
def env(tmp_path, monkeypatch):
    engine = make_test_engine()
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    store = LocalStorage(tmp_path / "files")
    storagemod.set_storage(store)
    for mod in (workermod, runmod):
        monkeypatch.setattr(mod, "SessionLocal", Session)
    monkeypatch.setattr(runmod, "init_db", lambda: None)
    yield SimpleNamespace(Session=Session, store=store, engine=engine)
    storagemod.set_storage(None)


def _save_tender(Session):
    rows, _ = parse_tender_list((FIX / "tender_list.html").read_text())
    rec = build_record(rows[0], parse_tender_detail((FIX / "tender_detail.html").read_text()))
    with Session() as s:
        upsert_tender(s, rec)
        s.commit()
        return s.scalar(select(Tender.id))


def _load(s, pk):
    return s.scalar(select(Tender).where(Tender.id == pk).options(selectinload(Tender.documents)))


# ---------- recognising what the portal sent ----------
def test_classify():
    assert classify(R(PDF_A))[0] == "file"
    assert classify(R(PDF_A, "application/octet-stream", disp='attachment; filename="a.pdf"'))[2] == "a.pdf"
    assert classify(R(CAPTCHA_HTML, "text/html"))[0] == "captcha"
    kind, _, msg = classify(R(b"<html><title>Session Timed Out</title></html>", "text/html"))
    assert kind == "error" and "Session Timed Out" in msg
    assert classify(R(b"", status=404))[0] == "error"
    assert is_captcha_page(CAPTCHA_HTML.decode())
    assert not is_captcha_page((FIX / "tender_detail.html").read_text())


# ---------- downloading ----------
def test_downloads_and_dedupes(env):
    pk = _save_tender(env.Session)
    portal = Portal({"D1": R(PDF_A), "D2": R(PDF_B), "D3": R(PDF_A)})   # D3 is the same file as D1
    with env.Session() as s:
        t = _load(s, pk)
        result = fetch_tender(s, t, portal, env.store)
        s.commit()
        assert result["stored"] == 3 and t.docs_status == "done"
        hashes = {d.file_hash for d in t.documents}
        assert len(hashes) == 2                                  # stored once, linked twice
    files = [f for f in (env.store.root).rglob("*") if f.is_file()]
    assert len(files) == 2


def test_captcha_stops_and_marks(env):
    pk = _save_tender(env.Session)
    portal = Portal({"D1": R(CAPTCHA_HTML, "text/html")})
    with env.Session() as s:
        t = _load(s, pk)
        fetch_tender(s, t, portal, env.store)
        s.commit()
        assert t.docs_status == "captcha"
        assert {d.file_status for d in t.documents} == {"captcha"}
    assert portal.file_requests == 1                             # did not hammer the portal


def test_tender_no_longer_listed(env):
    pk = _save_tender(env.Session)
    portal = Portal({}, list_html="<html><table></table></html>")
    with env.Session() as s:
        t = _load(s, pk)
        r = fetch_tender(s, t, portal, env.store)
        assert t.docs_status == "failed" and "no longer listed" in r["error"]


def test_corrigendum_keeps_downloaded_files(env):
    pk = _save_tender(env.Session)
    with env.Session() as s:
        fetch_tender(s, _load(s, pk), Portal({"D1": R(PDF_A), "D2": R(PDF_B), "D3": R(PDF_A)}), env.store)
        s.commit()
    rows, _ = parse_tender_list((FIX / "tender_list.html").read_text())
    rec = build_record(rows[0], parse_tender_detail((FIX / "tender_detail.html").read_text()))
    rec["documents"].append({"section": "Corrigendum", "name": "Corrigendum_1.pdf"})
    with env.Session() as s:
        assert upsert_tender(s, rec) == "changed"
        s.commit()
        t = _load(s, pk)
        status = {d.name: d.file_status for d in t.documents}
        assert status["Tendernotice_1.pdf"] == "stored"           # still there
        assert status["Corrigendum_1.pdf"] is None                # new, not fetched yet


# ---------- worker queue + cleanup ----------
def test_worker_processes_requests(env):
    pk = _save_tender(env.Session)
    with env.Session() as s:
        t = s.get(Tender, pk)
        t.docs_status, t.docs_requested_at = "requested", now_ist()
        s.commit()
    n = workermod.process_pending(client=Portal({"D1": R(PDF_A), "D2": R(PDF_B), "D3": R(PDF_A)}),
                                  storage=env.store)
    assert n == 1
    with env.Session() as s:
        assert s.get(Tender, pk).docs_status == "done"


def test_cleanup_old_files(env):
    pk = _save_tender(env.Session)
    with env.Session() as s:
        fetch_tender(s, _load(s, pk), Portal({"D1": R(PDF_A), "D2": R(PDF_B), "D3": R(PDF_A)}), env.store)
        s.get(Tender, pk).closing_at = now_ist() - timedelta(days=45)
        s.commit()
    assert workermod.cleanup(30, storage=env.store) == 2
    assert not [f for f in env.store.root.rglob("*") if f.is_file()]
    with env.Session() as s:
        assert {d.file_status for d in _load(s, pk).documents} == {"deleted"}


# ---------- --with-docs during a crawl ----------
def test_crawl_with_docs(env, monkeypatch):
    portal = Portal({"D1": R(PDF_A), "D2": R(PDF_B), "D3": R(PDF_A)})
    monkeypatch.setattr(runmod, "make_client", lambda *a: portal)
    args = SimpleNamespace(engine="http", base_url="x", max_orgs=1, max_per_org=1,
                           save_html=None, full=False, with_docs=True, all_details=True)
    runmod.run_once(args)
    with env.Session() as s:
        t = s.scalar(select(Tender).options(selectinload(Tender.documents)))
        assert t.docs_status == "done"


# ---------- API ----------
def test_api_request_and_download(env):
    pk = _save_tender(env.Session)

    def session_override():
        with env.Session() as s:
            yield s

    mainmod.app.dependency_overrides[get_session] = session_override
    try:
        c = TestClient(mainmod.app)
        assert c.post(f"/api/tenders/{pk}/documents/request").json()["docs_status"] == "requested"
        doc_id = c.get(f"/api/tenders/{pk}").json()["documents"][0]["id"]
        assert c.get(f"/api/documents/{doc_id}/file").status_code == 404     # not downloaded yet

        with env.Session() as s:
            fetch_tender(s, _load(s, pk), Portal({"D1": R(PDF_A), "D2": R(PDF_B), "D3": R(PDF_A)}), env.store)
            s.commit()
        detail = c.get(f"/api/tenders/{pk}").json()
        assert detail["docs_status"] == "done" and all(d["available"] for d in detail["documents"])
        r = c.get(detail["documents"][0]["download_url"])
        assert r.status_code == 200 and r.content == PDF_A
        assert "Tendernotice_1.pdf" in r.headers["content-disposition"]
    finally:
        mainmod.app.dependency_overrides.clear()


# ---------- human-assisted download ----------
def test_assist_matches_browser_downloads(env):
    pk = _save_tender(env.Session)
    with env.Session() as s:
        t = _load(s, pk)
        assert match_document(t, "Tendernotice_1 (1).pdf").name == "Tendernotice_1.pdf"
        assert match_document(t, "boq_901234.XLS").name == "BOQ_901234.xls"
        msg = save_download(s, t, "TenderDocument_Lab.pdf", PDF_B, env.store)
        assert "1/3" in msg and t.docs_status == "partial"
        save_download(s, t, "unexpected_bundle.zip", b"PK\x03\x04zipdata", env.store)
        assert any(d.section == "Downloaded" for d in t.documents)   # kept, not lost

"""Only security / ground search tenders are saved (store.SAVE_ONLY_RELEVANT)."""
from sqlalchemy.orm import sessionmaker

from app.models import Tender
from app.scraper.store import upsert_tender
from tests.conftest import make_test_engine


def _rec(tid, title):
    return {"source": "cppp", "tender_id": tid, "title": title}


def test_only_relevant_tenders_are_saved():
    db = sessionmaker(bind=make_test_engine())()
    assert upsert_tender(db, _rec("T1", "Construction of boundary wall"), only_relevant=True) == "skipped"
    assert upsert_tender(db, _rec("T2", "Supply of DFMD for BSF"), only_relevant=True) == "new"
    db.commit()
    assert [t.tender_id for t in db.query(Tender).all()] == ["T2"]
    # an existing tender is still updated even if its new title is not relevant
    assert upsert_tender(db, _rec("T2", "Corrigendum"), only_relevant=True) in ("changed", "same")

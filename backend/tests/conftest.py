"""
Tests use an in-memory SQLite database by default.
To run them against Postgres instead (empty test database!):
    TEST_DATABASE_URL=postgresql+psycopg://tender:tender@localhost:5432/tenders_test python -m pytest -q
"""
import os

import pytest

from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

from app.db import Base, init_db


def make_test_engine():
    url = os.getenv("TEST_DATABASE_URL")
    if url:
        engine = create_engine(url)
        # A previous test may have left a session open; close those connections
        # first, otherwise dropping the tables waits forever on their locks.
        with engine.connect() as c:
            c.execute(text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                           "WHERE datname = current_database() AND pid <> pg_backend_pid()"))
            c.commit()
        Base.metadata.drop_all(engine)
    else:
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                               poolclass=StaticPool)
    init_db(engine)
    return engine


@pytest.fixture(autouse=True)
def _save_all_tenders(monkeypatch):
    """Tests use ordinary sample tenders: save them all (the app saves only relevant ones)."""
    from app.scraper import store
    monkeypatch.setattr(store, "SAVE_ONLY_RELEVANT", False)

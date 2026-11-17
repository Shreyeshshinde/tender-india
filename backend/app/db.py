import logging
import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

log = logging.getLogger(__name__)

BACKEND_DIR = Path(__file__).resolve().parent.parent


def _load_env_file():
    """Read backend/.env (KEY=VALUE lines) so you don't have to set variables every time."""
    env = BACKEND_DIR / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_env_file()

# Postgres (recommended):  postgresql+psycopg://tender:tender@localhost:5432/tenders
# SQLite (fallback):       sqlite:///./tenders.db
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{BACKEND_DIR / 'tenders.db'}")
if DATABASE_URL.startswith("postgres://"):            # some hosts give this older form
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)

IS_SQLITE = DATABASE_URL.startswith("sqlite")


def make_engine(url):
    if url.startswith("sqlite"):
        return create_engine(url, connect_args={"check_same_thread": False}, pool_pre_ping=True)
    args = {}
    # Supabase "transaction pooler" (port 6543) cannot keep prepared statements between
    # queries. Use the "session pooler" (port 5432) when you can; this makes 6543 work too.
    if ":6543/" in url or os.getenv("DB_NO_PREPARE"):
        args["prepare_threshold"] = None
    # Hosted Postgres (Supabase free plan) allows few connections: keep the pool small,
    # and replace connections the pooler may have closed while idle.
    return create_engine(url, connect_args=args, pool_pre_ping=True, pool_recycle=300,
                         pool_size=int(os.getenv("DB_POOL_SIZE", "5")),
                         max_overflow=int(os.getenv("DB_MAX_OVERFLOW", "5")))


engine = make_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def init_db(bind=None):
    """Create missing tables and add missing columns (safe to run every time)."""
    from . import models  # noqa: F401  (registers tables)
    from .migrate import add_missing_columns
    bind = bind or engine
    Base.metadata.create_all(bind)
    add_missing_columns(bind)


def describe_db():
    if IS_SQLITE:
        return f"SQLite file ({DATABASE_URL.split('///', 1)[-1]})"
    host = DATABASE_URL.split("@", 1)[-1]
    return f"PostgreSQL ({host})"


def get_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

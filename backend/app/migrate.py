"""
Database maintenance commands.

    python -m app.migrate --copy-from sqlite:///tenders.db   # move SQLite data into Postgres
    python -m app.migrate --backfill                          # fill district/state for old tenders
    python -m app.migrate --status                            # show which DB and how many rows

New columns added to models.py are added to an existing database
automatically (init_db calls add_missing_columns), so old data is kept.
"""
import argparse
import logging

from sqlalchemy import inspect, select, text, update

log = logging.getLogger("migrate")


def add_missing_columns(bind):
    """ALTER TABLE ... ADD COLUMN for any model column the database does not have yet."""
    from .db import Base
    insp = inspect(bind)
    existing_tables = set(insp.get_table_names())
    with bind.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue
            have = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in have:
                    continue
                col_type = col.type.compile(dialect=bind.dialect)
                conn.execute(text(f'ALTER TABLE {table.name} ADD COLUMN {col.name} {col_type}'))
                log.info("Added column %s.%s", table.name, col.name)
    # indexes on existing tables (create_all skips them)
    for table in Base.metadata.sorted_tables:
        for idx in table.indexes:
            idx.create(bind, checkfirst=True)


def backfill_locations(session, only_missing=True, batch=500):
    """Work out district (and state) for tenders saved before district support existed."""
    from .models import Tender
    from .scraper.geo import locate
    q = select(Tender.id, Tender.pincode, Tender.location, Tender.authority_address,
               Tender.org_chain, Tender.district, Tender.state)
    if only_missing:
        q = q.where(Tender.district.is_(None))
    changes = []
    for tid, pin, loc, addr, chain, old_d, old_s in session.execute(q).all():
        district, state = locate(pin, loc, addr, chain)
        new = {}
        if district and district != old_d:
            new["district"] = district
        if state and not old_s:
            new["state"] = state
        if new:
            changes.append((tid, new))
    for i in range(0, len(changes), batch):
        for tid, new in changes[i:i + batch]:
            session.execute(update(Tender).where(Tender.id == tid).values(**new))
        session.commit()
    return sum(1 for _, n in changes if "district" in n)


def copy_database(src_url, dst_engine):
    """Copy every row from another database (e.g. your old SQLite file) into this one."""
    from .db import Base, init_db, make_engine
    from .models import Tender, TenderChange, TenderDocument

    src = make_engine(src_url)
    init_db(src)              # make sure the old DB has the same columns (adds district)
    init_db(dst_engine)
    with dst_engine.connect() as c:
        if c.execute(select(Tender.id).limit(1)).first():
            raise SystemExit("Target database already has tenders. Copy into an empty database.")

    total = {}
    with src.connect() as s, dst_engine.begin() as d:
        for model in (Tender, TenderDocument, TenderChange):
            table = model.__table__
            rows = [dict(r._mapping) for r in s.execute(select(table))]
            for i in range(0, len(rows), 1000):
                d.execute(table.insert(), rows[i:i + 1000])
            total[table.name] = len(rows)
        if dst_engine.dialect.name == "postgresql":     # keep IDs counting from the right number
            for model in (Tender, TenderDocument, TenderChange):
                t = model.__table__.name
                d.execute(text(
                    f"SELECT setval(pg_get_serial_sequence('{t}', 'id'), "
                    f"COALESCE((SELECT MAX(id) FROM {t}), 0) + 1, false)"))
    return total


def main():
    from .db import SessionLocal, describe_db, engine, init_db
    from .models import Tender, TenderDocument

    p = argparse.ArgumentParser(description="Tender India database tools")
    p.add_argument("--copy-from", help="Old database URL, e.g. sqlite:///tenders.db")
    p.add_argument("--backfill", action="store_true", help="Fill district/state for existing tenders")
    p.add_argument("--status", action="store_true")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    log.info("Database: %s", describe_db())
    init_db()
    if args.copy_from:
        counts = copy_database(args.copy_from, engine)
        log.info("Copied: %s", counts)
        args.backfill = True
    if args.backfill:
        with SessionLocal() as s:
            n = backfill_locations(s)
        log.info("District filled for %d tenders", n)
    with SessionLocal() as s:
        from sqlalchemy import func
        tenders = s.scalar(select(func.count(Tender.id)))
        docs = s.scalar(select(func.count(TenderDocument.id)))
        with_district = s.scalar(select(func.count(Tender.id)).where(Tender.district.is_not(None)))
    log.info("Tenders: %d (with district: %d), documents: %d", tenders, with_district, docs)


if __name__ == "__main__":
    main()

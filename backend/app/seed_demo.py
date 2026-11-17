"""
Load SAMPLE tenders so you can see the website before running a real crawl.

    python -m app.seed_demo

All rows are marked source="demo" and are NOT real tenders.
Remove them later with:  python -m app.seed_demo --clear
"""
import random
import sys
from datetime import timedelta
from decimal import Decimal


from .db import SessionLocal, init_db
from .models import Tender
from .scraper.normalize import now_ist
from .scraper.store import upsert_tender

SAMPLES = [
    ("Supply of Laboratory Equipment for Biochemistry Dept", "All India Institute of Medical Science-Nagpur", "Nagpur, Maharashtra", "Goods"),
    ("Annual Housekeeping Services at OPD Block", "All India Institute of Medical Sciences-Bhopal", "Bhopal, Madhya Pradesh", "Services"),
    ("Resurfacing of Runway 09/27", "Airports Authority of India", "Chennai, Tamil Nadu", "Works"),
    ("Construction of Boundary Wall at Regional Office", "Archaeological Survey of India", "Jaipur, Rajasthan", "Works"),
    ("Hiring of Vehicles on Monthly Basis", "Central Public Works Department", "New Delhi, Delhi", "Services"),
    ("Supply of Desktop Computers and UPS", "Indian Institute of Technology Guwahati", "Guwahati, Assam", "Goods"),
    ("Repair of Approach Road near Latur Station", "Central Public Works Department", "Latur, Maharashtra", "Works"),
    ("CCTV Surveillance System Installation", "Airports Authority of India", "Kolkata, West Bengal", "Goods"),
    ("Desilting of Canal Section KM 12 to 18", "Farakka Barrage Project", "Murshidabad, West Bengal", "Works"),
    ("AMC of Lifts and Escalators", "All India Institute of Medical Sciences-Bhubaneswar", "Bhubaneswar, Odisha", "Services"),
    ("Supply of Medical Gases", "All India Institute of Medical Sciences-Rishikesh", "Rishikesh, Uttarakhand", "Goods"),
    ("Painting Works of Staff Quarters", "Andaman Lakshadweep Harbour Works", "Port Blair, Andaman and Nicobar", "Works"),
]


def seed():
    from .scraper.geo import locate
    init_db()
    rnd = random.Random(42)
    now = now_ist().replace(minute=0, second=0, microsecond=0)
    db = SessionLocal()
    for i, (title, org, loc, cat) in enumerate(SAMPLES, 1):
        published = now - timedelta(days=rnd.randint(1, 15))
        closing = now + timedelta(days=rnd.choice([-3, 1, 2, 4, 6, 9, 14, 21, 30]), hours=15 - now.hour)
        value = Decimal(rnd.choice([8, 15, 45, 90, 250, 1200])) * 100000
        tid = f"2026_DEMO_{900000 + i}_1"
        upsert_tender(db, {
            "source": "demo", "tender_id": tid, "ref_no": f"DEMO/{2026}/{i:03d}",
            "title": title, "description": f"{title}. Sample record for testing the website.",
            "organisation": org, "org_chain": f"{org}||Procurement Cell",
            "tender_type": "Open Tender", "category": cat, "location": loc,
            "district": locate(None, loc)[0], "state": locate(None, loc)[1], "value": value, "emd": (value * Decimal("0.02")).quantize(Decimal("1")),
            "tender_fee": Decimal(rnd.choice([0, 500, 1180, 2360])) or None,
            "published_at": published, "doc_start_at": published + timedelta(hours=1),
            "bid_start_at": published + timedelta(days=1),
            "doc_end_at": closing - timedelta(hours=1), "closing_at": closing,
            "opening_at": closing + timedelta(days=1),
            "authority_name": "Executive Engineer", "authority_address": loc,
            "documents": [
                {"section": "NIT Document", "name": "Tendernotice_1.pdf", "description": "NIT", "size": "412.55 KB"},
                {"section": "Work Item Documents", "name": f"TenderDocument_{i}.pdf", "description": "Technical specifications", "size": "1.20 MB"},
                {"section": "Work Item Documents", "name": f"BOQ_{900000 + i}.xls", "description": "Price bid", "size": "88.00 KB"},
            ],
        })
    db.commit()
    # one sample corrigendum so the "changes" section has something to show
    rec_t = db.query(Tender).filter_by(source="demo").first()
    upsert_tender(db, {"source": "demo", "tender_id": rec_t.tender_id,
                       "closing_at": rec_t.closing_at + timedelta(days=7),
                       "doc_end_at": rec_t.doc_end_at + timedelta(days=7),
                       "opening_at": rec_t.opening_at + timedelta(days=7)})
    db.commit()
    db.close()
    print(f"Loaded {len(SAMPLES)} demo tenders.")


def clear():
    init_db()
    db = SessionLocal()
    rows = db.query(Tender).filter_by(source="demo").all()
    for t in rows:              # ORM delete also removes documents and changes
        db.delete(t)
    n = len(rows)
    db.commit()
    db.close()
    print(f"Removed {n} demo tenders.")


if __name__ == "__main__":
    clear() if "--clear" in sys.argv else seed()

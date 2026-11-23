"""
Which tenders matter to us: SECURITY screening and GROUND SEARCH equipment
(the CEIA-type product range). Everything else is hidden by default on the website.

Each tender is checked once when it is saved:
    focus        "security" | "ground_search" | None
    focus_score  how strong the match is (higher = more certain)
    focus_terms  which words matched, shown on the website ("DFMD, HHMD")

To change what counts as relevant, edit the lists below, then re-check
every tender already in the database:

    python -m app.focus --backfill
    python -m app.focus --test "Supply of DFMD for BSF"      # see how one title is judged
"""
import argparse
import re

# ---------------------------------------------------------------------------
# 1. Words that clearly mean "this is our kind of equipment".
#    Written in lower case; spaces/hyphens/brackets in tenders are handled.
# ---------------------------------------------------------------------------
SECURITY_TERMS = [
    # walk-through metal detectors
    "door frame metal detector", "door frame metal detectors", "dfmd", "dfmds",
    "walk through metal detector", "walk through metal detectors", "walkthrough metal detector",
    "wtmd", "archway metal detector", "multi zone metal detector", "multizone metal detector",
    "multi zone dfmd", "door frame detector", "doorframe metal detector",
    # hand-held metal detectors
    "hand held metal detector", "hand held metal detectors", "handheld metal detector",
    "handheld metal detectors", "hhmd", "hhmds", "hand held detector",
    # other people / baggage / mail screening
    "shoe scanner", "shoe metal detector", "shoe analyzer", "samdex",
    "liquid scanner", "bottle scanner", "bottled liquid scanner", "liquid explosive detector",
    "liquid explosive detection", "mail screening", "mail scanner", "letter bomb",
    "parcel bomb detector", "x ray baggage", "xbis", "baggage scanner", "baggage inspection system",
    "parcel scanner", "body scanner", "full body scanner", "security scanner",
    "explosive trace detector", "etd machine", "explosive vapour detector",
    "explosive vapor detector", "explosive detector", "bomb detector", "bomb detection",
    "non linear junction detector", "nljd", "under vehicle scanning", "under vehicle inspection",
    "uvss", "uvis", "weapon detection", "concealed weapon", "cell phone detector",
    "mobile phone detector", "ferrous detector", "contraband detector",
    "bdds", "bomb disposal", "anti sabotage", "opengate", "pole metal detector",
    "cargo screening", "x ray scanner", "x ray screening", "xray scanner", "xray baggage",
    "x ray machine for baggage", "bomb blanket", "bomb suppression blanket", "explosive vapour",
    "trace detector", "hand held explosive detector", "security screening equipment",
    # brands people name in specs
    "ceia", "garrett",
]

GROUND_TERMS = [
    "mine detector", "mine detectors", "landmine detector", "land mine detector",
    "mine detection", "mine detecting", "demining", "de mining", "mine clearance",
    "mine prodder", "deep search metal detector", "deep search metal detectors",
    "deep search detector", "dsmd", "uxo", "unexploded ordnance", "unexploded ordinance",
    "explosive remnants of war", "ied detector", "ied detection", "ied detecting",
    "counter ied", "improvised explosive device detector", "ground penetrating radar",
    "gpr", "command wire detector", "command wire", "carbon rod detector",
    "explosive ordnance disposal", "eod equipment", "eod kit",
    "underwater metal detector", "under water metal detector", "underwater detector",
    "minelab", "vallon", "schiebel",
]

# Plain "metal detector" is ours too, unless the tender is clearly about
# industrial lines (food, pharma, coal conveyors ...).
GENERIC_TERMS = ["metal detector", "metal detectors", "metal detecting", "metal detection"]

INDUSTRIAL_CONTEXT = [
    "conveyor", "conveyer", "belt", "coal handling", "chp", "crusher", "tramp", "food",
    "pharma", "pharmaceutical", "tablet", "capsule", "textile", "sugar", "packaging",
    "pipeline", "mill", "cement", "mining plant", "process line", "veterinary", "foreign body",
    "dental", "medical x ray",
]
GROUND_CONTEXT = [
    "mine", "mines", "army", "engineer", "engineers", "military", "ordnance", "uxo", "ied",
    "deep search", "demining", "battlefield",
]

# A strong word can still be wrong in some contexts.
TERM_EXCEPTIONS = {
    "gpr": ["survey", "pavement", "road", "bridge", "utility", "utilities", "geotechnical",
            "rebar", "concrete", "archaeolog"],
    "ground penetrating radar": ["pavement", "road", "bridge", "utility", "utilities",
                                 "geotechnical", "rebar", "concrete", "archaeolog"],
    "command wire": ["telephone", "cable laying", "signal"],
    "garrett": ["turbo", "turbocharger"],
}

# Never relevant, even if a word above appears (services, not equipment).
EXCLUDE_IF_ONLY = [
    "security guard", "security guards", "security services", "security personnel",
    "manpower", "housekeeping",
]

# Solar PV projects: saved only when the tender is in Maharashtra.
SOLAR_TERMS = [
    "solar pv", "solar photovoltaic", "solar power plant", "solar power project", "solar plant",
    "solar project", "solar panel", "solar panels", "solar module", "solar modules",
    "rooftop solar", "roof top solar", "ground mounted solar", "grid connected solar",
    "solar energy", "solar power", "kwp", "mwp", "solar rooftop", "solar system",
]
MAHARASHTRA_WORDS = [
    "maharashtra", "mumbai", "pune", "nagpur", "nashik", "aurangabad", "chhatrapati sambhajinagar",
    "thane", "solapur", "kolhapur", "shirdi", "ahmednagar", "ahilyanagar", "latur", "amravati",
    "akola", "jalgaon", "nanded", "satara", "sangli", "ratnagiri", "navi mumbai", "mahatenders",
    "mahadiscom", "msedcl", "mahagenco", "mahatransco", "mahaurja", "meda", "cidco", "mmrda", "midc",
]

LABELS = {"security": "Security screening", "ground_search": "Ground search", "solar": "Solar (Maharashtra)"}

# Focus mode while crawling: open a tender's detail page only if its TITLE
# could be ours. Works tenders ("construction of ...", "repair of ...") are
# saved from the list page alone, which makes big portals many times faster.
# A title is worth opening if it matches a product word above, or one of these
# broader hints (equipment purchases whose details may list our products).
TITLE_HINTS = [
    "security", "detector", "detectors", "detection", "detecting", "scanner", "scanners",
    "screening", "explosive", "explosives", "bomb", "metal", "mine", "mines", "ied", "uxo",
    "eod", "frisking", "equipment", "equipments", "procurement", "purchase", "supply",
    "supplying", "stores", "store items", "items", "gadgets", "kit", "kits", "accessories",
    "search", "surveillance", "anti", "police", "jail", "prison", "airport", "court",
    "solar", "photovoltaic",
]


def title_could_match(title):
    """True if the detail page is worth opening in focus mode."""
    if not title:
        return True                       # unknown: better look
    if classify(title)[0]:
        return True
    text = _norm(title)
    return any(_has(text, w) for w in TITLE_HINTS)

# ---------------------------------------------------------------------------


def _norm(text):
    """lower case, every non letter/digit becomes a space, padded: ' supply of dfmd '"""
    return " " + re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip() + " "


def _has(norm_text, phrase):
    return f" {_norm(phrase).strip()} " in norm_text


def _found(norm_text, terms):
    hits = []
    for term in terms:
        if not _has(norm_text, term):
            continue
        bad = TERM_EXCEPTIONS.get(term, [])
        if any(b in norm_text for b in bad):
            continue
        hits.append(term)
    return hits


def classify(*texts):
    """-> (focus, score, matched_terms). focus is None when not relevant."""
    text = _norm(" ".join(t for t in texts if t))
    if text.strip() == "":
        return None, 0, []

    sec = _found(text, SECURITY_TERMS)
    gnd = _found(text, GROUND_TERMS)
    score_sec, score_gnd = 10 * len(sec), 10 * len(gnd)

    generic = [] if (sec or gnd) else _found(text, GENERIC_TERMS)
    if generic and any(_has(text, w) for w in EXCLUDE_IF_ONLY):
        generic = []                                      # e.g. guard services with "metal detector frisking"
    if generic:
        if any(_has(text, w) for w in INDUSTRIAL_CONTEXT):
            generic = []                                  # food / conveyor metal detector: not ours
        elif any(_has(text, w) for w in GROUND_CONTEXT):
            score_gnd += 6
            gnd = generic
        else:
            score_sec += 6
            sec = generic

    if not sec and not gnd:
        solar = _found(text, SOLAR_TERMS)
        if solar:
            return "solar", 10 * len(solar), _pretty(solar)
        return None, 0, []
    if score_gnd > score_sec:
        return "ground_search", score_gnd, _pretty(gnd + sec)
    return "security", score_sec, _pretty(sec + gnd)


def _pretty(terms):
    out, seen = [], set()
    for t in terms:
        label = t.upper() if len(t) <= 5 else t
        if label.lower() not in seen:
            seen.add(label.lower())
            out.append(label)
    return out[:6]


def in_maharashtra(state=None, source=None, *texts):
    if (state or "").strip().lower() == "maharashtra" or (source or "").lower() in ("maharashtra", "mahatenders"):
        return True
    text = _norm(" ".join(t for t in texts if t))
    return any(_has(text, w) for w in MAHARASHTRA_WORDS)


def classify_tender(title=None, description=None, product_category=None, state=None,
                    source=None, organisation=None, location=None):
    """classify() plus the rule that solar tenders count only in Maharashtra."""
    focus, score, terms = classify(title, description, product_category)
    if focus == "solar" and not in_maharashtra(state, source, title, description,
                                               organisation, location):
        return None, 0, []
    return focus, score, terms


def apply_focus(tender):
    """Set focus fields on a Tender object (called whenever a tender is saved)."""
    focus, score, terms = classify_tender(tender.title, tender.description, tender.product_category,
                                          tender.state, tender.source, tender.organisation,
                                          tender.location)
    tender.focus = focus
    tender.focus_score = score or None
    tender.focus_terms = ", ".join(terms) or None
    return focus


def backfill():
    from sqlalchemy import func, select

    from .db import SessionLocal, init_db
    from .models import Tender
    init_db()
    with SessionLocal() as s:
        tenders = s.scalars(select(Tender)).all()
        for t in tenders:
            apply_focus(t)
        s.commit()
        counts = dict(s.execute(select(Tender.focus, func.count()).group_by(Tender.focus)).all())
        print(f"Checked {len(tenders)} tenders.")
        print(f"  Security screening: {counts.get('security', 0)}")
        print(f"  Ground search:      {counts.get('ground_search', 0)}")
        print(f"  Solar (Maharashtra): {counts.get('solar', 0)}")
        print(f"  Not relevant:       {counts.get(None, 0)}")
        examples = s.execute(select(Tender.focus, Tender.title, Tender.focus_terms)
                             .where(Tender.focus.is_not(None))
                             .order_by(Tender.focus_score.desc()).limit(15)).all()
        if examples:
            print("\nExamples:")
            for f, title, terms in examples:
                print(f"  [{LABELS[f]}] {title[:80]}  <- {terms}")


def main():
    p = argparse.ArgumentParser(description="Security / ground search relevance")
    p.add_argument("--backfill", action="store_true", help="Re-check every tender in the database")
    p.add_argument("--test", help="Show how one title would be judged")
    a = p.parse_args()
    if a.test:
        f, score, terms = classify(a.test)
        print(f"{LABELS.get(f, 'Not relevant')}  (score {score})  matched: {', '.join(terms) or '-'}")
    if a.backfill:
        backfill()
    if not (a.test or a.backfill):
        p.print_help()


if __name__ == "__main__":
    main()

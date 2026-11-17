"""
Search query -> database conditions.

Government tenders use a lot of short forms. Someone typing
"door frame metal detector" must also find "Supply of DFMD", and the other
way round. So the query is split into "concepts":

  "door frame metal detector delhi"
     -> concept 1: any of  [door frame metal detector, dfmd]
     -> concept 2: delhi

Every concept must match somewhere in the tender (AND), and any spelling
inside one concept is enough (OR).

Add more short forms to SYNONYMS whenever you notice a miss.
"""
import re

STOP_WORDS = {"tender", "tenders", "bid", "bids", "for", "of", "the", "and", "in", "at", "to",
              "a", "an", "on", "with", "supply", "purchase", "procurement"}

# Each group: every spelling means the same thing. Keep short forms 3+ letters
# (2-letter forms like "ac" would match inside almost every word).
SYNONYMS = [
    ["door frame metal detector", "dfmd", "door frame metal detectors", "walk through metal detector", "dfmds"],
    ["hand held metal detector", "hhmd", "handheld metal detector", "hand-held metal detector"],
    ["cctv", "closed circuit television", "closed circuit camera", "surveillance camera"],
    ["x-ray baggage", "xbis", "baggage scanner", "x ray baggage", "xray baggage"],
    ["annual maintenance contract", "amc", "annual maintenance"],
    ["comprehensive maintenance contract", "cmc"],
    ["air conditioner", "air conditioning", "split ac", "window ac"],
    ["uninterruptible power supply", "ups"],
    ["diesel generator", "dg set", "genset", "d.g. set"],
    ["led light", "led lights", "led lighting", "led fitting"],
    ["personal computer", "desktop computer", "desktop"],
    ["housekeeping", "house keeping", "cleaning services"],
    ["manpower", "man power", "outsourcing of staff", "outsourced staff"],
    ["security services", "security guard", "security guards", "security personnel"],
    ["fire fighting", "firefighting", "fire alarm", "fire extinguisher"],
    ["road", "roads", "highway"],
    ["civil work", "civil works", "construction work"],
    ["electrical work", "electrical works"],
    ["medical gas", "medical gases", "oxygen supply"],
    ["mri", "magnetic resonance imaging"],
    ["ct scan", "ct scanner", "computed tomography"],
    ["solar", "solar power", "solar pv", "photovoltaic"],
    ["vehicle hiring", "hiring of vehicle", "hiring of vehicles", "taxi", "cab services"],
    ["furniture", "furnitures"],
    ["printer", "printers", "multifunction printer", "mfp"],
]

_PHRASE_INDEX = sorted(
    ((v, i) for i, group in enumerate(SYNONYMS) for v in group),
    key=lambda x: len(x[0]), reverse=True)


def _clean(q):
    q = q.lower().replace("&", " and ")
    q = re.sub(r"[^\w\s./-]", " ", q)
    return re.sub(r"\s+", " ", q).strip()


def concepts(q):
    """Return a list of concepts; each concept is a list of spellings (OR)."""
    text = f" {_clean(q)} "
    found = []
    for phrase, gid in _PHRASE_INDEX:                 # longest phrases first
        pat = f" {phrase} "
        if pat in text:
            found.append(list(SYNONYMS[gid]))
            text = text.replace(pat, " ")
    for word in text.split():
        if word not in STOP_WORDS and len(word) >= 2:
            found.append([word])
    # drop duplicates (same group twice)
    unique, seen = [], set()
    for c in found:
        key = tuple(sorted(c))
        if key not in seen:
            seen.add(key)
            unique.append(c)
    return unique


_EDGES_BEFORE = [" ", "(", "/", "-", ",", "[", "."]
_EDGES_AFTER = [" ", ")", "/", "-", ",", "]", ".", ":", ";"]


def _patterns(spelling):
    """LIKE patterns for one spelling.
    Short forms (4 letters or less) must be a whole word, so "mri" does not
    match "Amritsar" and "ups" does not match "groups". Works on SQLite and Postgres."""
    if len(spelling) > 4 or " " in spelling:
        return [f"%{spelling}%"]
    pats = [spelling]
    pats += [f"{spelling}{a}%" for a in _EDGES_AFTER]           # at the start
    pats += [f"%{b}{spelling}" for b in _EDGES_BEFORE]          # at the end
    pats += [f"%{b}{spelling}{a}%" for b in _EDGES_BEFORE for a in _EDGES_AFTER]
    return pats


def search_conditions(q, columns):
    """SQLAlchemy conditions: one per concept, OR over spellings x columns."""
    from sqlalchemy import or_
    conds = []
    for spellings in concepts(q):
        conds.append(or_(*[col.ilike(p) for s in spellings for p in _patterns(s) for col in columns]))
    return conds

"""
Find the DISTRICT and STATE of a tender.

Order of trust:
  1. The tender's PIN code (most reliable). India Post data maps every
     PIN code to a district and state.
  2. A PIN code written somewhere in the location / address text.
  3. A district name written in the text ("Latur", "Nagpur" ...).
  4. Only the state name (from normalize.guess_state).

Data file: app/data/pincodes.json.gz, built from the India Post
All India Pincode Directory (via the MIT-licensed indiapins package).
"""
import gzip
import json
import re
from functools import lru_cache
from pathlib import Path

from .normalize import guess_state

_DATA = Path(__file__).resolve().parent.parent / "data" / "pincodes.json.gz"
_PIN_RE = re.compile(r"(?<!\d)([1-9]\d{2})\s?(\d{3})(?!\d)")

# District names that are also common English words or too short to trust in free text.
_SKIP_IN_TEXT = {"Bid", "Mon", "Una", "Mau", "Sas Nagar", "North", "South", "East", "West",
                 "Central", "Rural", "Urban"}


# Well-known city names that differ from the official India Post district name.
_CITY_ALIASES = {
    "guwahati": "Kamrup Metro", "bengaluru": "Bengaluru Urban", "bangalore": "Bengaluru Urban",
    "secunderabad": "Hyderabad", "visakhapatnam": "Visakhapatanam", "vizag": "Visakhapatanam",
    "kochi": "Ernakulam", "cochin": "Ernakulam", "gurgaon": "Gurugram", "noida": "Gautam Buddha Nagar",
    "greater noida": "Gautam Buddha Nagar", "port blair": "South Andamans", "bhilai": "Durg",
    "mangaluru": "Dakshina Kannada", "mangalore": "Dakshina Kannada", "rishikesh": "Dehradun",
    "allahabad": "Prayagraj", "mysore": "Mysuru", "bombay": "Mumbai", "calcutta": "Kolkata",
    "madras": "Chennai", "trivandrum": "Thiruvananthapuram", "baroda": "Vadodara",
    "navi mumbai": "Thane", "pimpri": "Pune", "chinchwad": "Pune",
}


@lru_cache(maxsize=1)
def _data():
    with gzip.open(_DATA, "rt", encoding="utf-8") as f:
        d = json.load(f)
    districts, states = d["districts"], d["states"]
    pins = {p: (districts[di], states[si]) for p, (di, si) in d["pins"].items()}
    # district name -> set of states it belongs to
    owners = {}
    for st, idxs in d["state_districts"].items():
        for i in idxs:
            owners.setdefault(districts[i], set()).add(st)
    usable = [(n.lower(), n) for n in districts if n not in _SKIP_IN_TEXT and len(n) >= 4]
    usable += [(alias, official) for alias, official in _CITY_ALIASES.items() if official in owners]
    usable.sort(key=lambda x: len(x[0]), reverse=True)   # "Mumbai Suburban" before "Mumbai"
    patterns = [(official, re.compile(rf"\b{re.escape(word)}\b")) for word, official in usable]
    return pins, owners, patterns


def from_pincode(pincode):
    """'413512' -> ('Latur', 'Maharashtra') or (None, None)."""
    if not pincode:
        return None, None
    digits = re.sub(r"\D", "", str(pincode))
    return _data()[0].get(digits, (None, None)) if len(digits) == 6 else (None, None)


def locate(pincode=None, *texts):
    """Best guess of (district, state) from a PIN code and any location / address text."""
    district, state = from_pincode(pincode)
    if district:
        return district, state

    blob = " ".join(t for t in texts if t)
    for m in _PIN_RE.finditer(blob):
        district, state = from_pincode(m.group(1) + m.group(2))
        if district:
            return district, state

    state = guess_state(*texts)
    low = blob.lower()
    _, owners, patterns = _data()
    for name, pat in patterns:
        if pat.search(low):
            name_states = owners.get(name, set())
            if state is None and len(name_states) == 1:
                return name, next(iter(name_states))
            if state in name_states:
                return name, state
    return None, state

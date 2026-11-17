"""
The "cleaner".

Every portal writes dates, money and places a little differently.
These helpers turn all of those into one standard shape so the
rest of the system never has to care where a tender came from.
"""
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def now_ist():
    """Current Indian time (portals publish all dates in IST, without a timezone)."""
    return datetime.now(IST).replace(tzinfo=None)

# Date formats seen on NIC GePNIC portals (CPPP and most state portals).
_DATE_FORMATS = [
    "%d-%b-%Y %I:%M %p",   # 30-Nov-2026 03:00 PM
    "%d-%b-%Y %H:%M",      # 30-Nov-2026 15:00
    "%d-%b-%Y",            # 30-Nov-2026
    "%d/%m/%Y %H:%M",      # 30/11/2026 15:00
    "%d/%m/%Y %I:%M %p",   # 30/11/2026 03:00 PM
    "%d/%m/%Y",            # 30/11/2026
    "%d-%m-%Y %H:%M",      # 30-11-2026 15:00
    "%d-%m-%Y",            # 30-11-2026
]

_EMPTY = {"", "na", "n/a", "nil", "none", "-", "--", "not applicable"}


def clean_text(value):
    """Collapse whitespace and return None for empty values."""
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    return None if text.lower() in _EMPTY else text


def parse_date(value):
    """Turn any portal date string into a Python datetime (or None)."""
    text = clean_text(value)
    if not text:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def parse_money(value):
    """'₹ 12,50,000.00' or 'Rs. 50000' -> Decimal('1250000.00'). 'NA' -> None."""
    text = clean_text(value)
    if not text:
        return None
    digits = re.sub(r"[^0-9.]", "", text)
    if not digits or digits == ".":
        return None
    try:
        amount = Decimal(digits)
    except InvalidOperation:
        return None
    return amount if amount > 0 else None


INDIAN_STATES = [
    "Andaman and Nicobar", "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar",
    "Chandigarh", "Chhattisgarh", "Dadra and Nagar Haveli and Daman and Diu", "Delhi",
    "Goa", "Gujarat", "Haryana", "Himachal Pradesh", "Jammu and Kashmir", "Jharkhand",
    "Karnataka", "Kerala", "Ladakh", "Lakshadweep", "Madhya Pradesh", "Maharashtra",
    "Manipur", "Meghalaya", "Mizoram", "Nagaland", "Odisha", "Puducherry", "Punjab",
    "Rajasthan", "Sikkim", "Tamil Nadu", "Telangana", "Tripura", "Uttar Pradesh",
    "Uttarakhand", "West Bengal",
]

# Big cities that often appear in "Location" without the state name.
_CITY_TO_STATE = {
    "mumbai": "Maharashtra", "pune": "Maharashtra", "nagpur": "Maharashtra",
    "latur": "Maharashtra", "nashik": "Maharashtra", "new delhi": "Delhi",
    "bengaluru": "Karnataka", "bangalore": "Karnataka", "chennai": "Tamil Nadu",
    "madurai": "Tamil Nadu", "hyderabad": "Telangana", "kolkata": "West Bengal",
    "ahmedabad": "Gujarat", "jaipur": "Rajasthan", "jodhpur": "Rajasthan",
    "lucknow": "Uttar Pradesh", "bhopal": "Madhya Pradesh", "patna": "Bihar",
    "bhubaneswar": "Odisha", "guwahati": "Assam", "raipur": "Chhattisgarh",
    "ranchi": "Jharkhand", "thiruvananthapuram": "Kerala", "kochi": "Kerala",
    "visakhapatnam": "Andhra Pradesh", "dehradun": "Uttarakhand", "rishikesh": "Uttarakhand",
    "shimla": "Himachal Pradesh", "srinagar": "Jammu and Kashmir", "jammu": "Jammu and Kashmir",
}

# Old or alternate spellings.
_ALIASES = {"orissa": "Odisha", "pondicherry": "Puducherry", "nct of delhi": "Delhi",
            "dadra and nagar haveli": "Dadra and Nagar Haveli and Daman and Diu",
            "daman and diu": "Dadra and Nagar Haveli and Daman and Diu",
            "andaman": "Andaman and Nicobar"}


def guess_state(*texts):
    """Find an Indian state name in location / org text. Returns None if unsure."""
    blob = " ".join(t for t in texts if t).lower()
    if not blob:
        return None
    for alias, state in _ALIASES.items():
        if alias in blob:
            return state
    for state in INDIAN_STATES:
        if re.search(rf"\b{re.escape(state.lower())}\b", blob):
            return state
    for city, state in _CITY_TO_STATE.items():
        if re.search(rf"\b{re.escape(city)}\b", blob):
            return state
    return None

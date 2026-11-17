"""
Known NIC (GePNIC) portals. They all run the same software as CPPP,
so the same scraper works; only the address changes.

    python -m app.scraper.run --portal chandigarh
    python -m app.scraper.run --portal all            # every portal below, one after another
    python -m app.scraper.run --base-url https://.../nicgep/app   # any other NIC portal

Each tender is labelled with its portal name ("source"), so the same
tender ID on two portals never collides, and documents are downloaded
from the right site.
"""
from urllib.parse import urlparse

PORTALS = {
    "cppp": "https://eprocure.gov.in/eprocure/app",
    "psu": "https://etenders.gov.in/eprocure/app",              # central PSUs: AAI, metros, mining, ports
    "coalindia": "https://coalindiatenders.nic.in/nicgep/app",  # Coal India and subsidiaries
    "defpsu": "https://eprocurebel.co.in/nicgep/app",         # BEL, BEML, MDL, GRSE, GSL, HSL
    "ntpc": "https://eprocurentpc.nic.in/nicgep/app",           # NTPC group
    "bhel": "https://eprocurebhel.co.in/nicgep/app",            # BHEL
    "defence": "https://defproc.gov.in/nicgep/app",
    "chandigarh": "https://etenders.chd.nic.in/nicgep/app",
    "maharashtra": "https://mahatenders.gov.in/nicgep/app",
    "punjab": "https://eproc.punjab.gov.in/nicgep/app",
    "haryana": "https://etenders.hry.nic.in/nicgep/app",
    "delhi": "https://govtprocurement.delhi.gov.in/nicgep/app",
    "tamilnadu": "https://tntenders.gov.in/nicgep/app",
    "kerala": "https://etenders.kerala.gov.in/nicgep/app",
    "westbengal": "https://wbtenders.gov.in/nicgep/app",
    "odisha": "https://tendersodisha.gov.in/nicgep/app",
    "himachal": "https://hptenders.gov.in/nicgep/app",
    "uttarakhand": "https://uktenders.gov.in/nicgep/app",
    "jharkhand": "https://jharkhandtenders.gov.in/nicgep/app",
    "assam": "https://assamtenders.gov.in/nicgep/app",
    "jammukashmir": "https://jktenders.gov.in/nicgep/app",
    "rajasthan": "https://eproc.rajasthan.gov.in/nicgep/app",
    "uttarpradesh": "https://etender.up.nic.in/nicgep/app",
    "madhyapradesh": "https://mptenders.gov.in/nicgep/app",
    "goa": "https://eprocure.goa.gov.in/nicgep/app",
    "arunachal": "https://arunachaltenders.gov.in/nicgep/app",
    "manipur": "https://manipurtenders.gov.in/nicgep/app",
    "meghalaya": "https://meghalayatenders.gov.in/nicgep/app",
    "mizoram": "https://mizoramtenders.gov.in/nicgep/app",
    "nagaland": "https://nagalandtenders.gov.in/nicgep/app",
    "sikkim": "https://sikkimtender.gov.in/nicgep/app",
    "tripura": "https://tripuratenders.gov.in/nicgep/app",
    "puducherry": "https://pudutenders.gov.in/nicgep/app",
    "andaman": "https://eprocure.andaman.gov.in/nicgep/app",
    "ladakh": "https://tenders.ladakh.gov.in/nicgep/app",
    "lakshadweep": "https://tendersutl.gov.in/nicgep/app",
    "daman": "https://ddtenders.gov.in/nicgep/app",
    "dnh": "https://dnhtenders.gov.in/nicgep/app",
}

DISPLAY_NAMES = {
    "cppp": "CPPP (Central)", "psu": "Central PSUs (etenders)", "coalindia": "Coal India", "defpsu": "Defence PSUs (BEL, MDL, GRSE...)", "ntpc": "NTPC", "bhel": "BHEL", "isro": "ISRO", "barc": "BARC", "shirdi": "Shirdi Sai Sansthan", "sbi": "State Bank of India", "kmrc": "Kolkata Metro", "defence": "Defence (defproc)", "chandigarh": "Chandigarh", "maharashtra": "Maharashtra",
    "punjab": "Punjab", "haryana": "Haryana", "delhi": "Delhi", "tamilnadu": "Tamil Nadu",
    "kerala": "Kerala", "westbengal": "West Bengal", "odisha": "Odisha",
    "himachal": "Himachal Pradesh", "uttarakhand": "Uttarakhand", "jharkhand": "Jharkhand",
    "assam": "Assam", "jammukashmir": "Jammu and Kashmir", "rajasthan": "Rajasthan", "uttarpradesh": "Uttar Pradesh", "madhyapradesh": "Madhya Pradesh", "goa": "Goa", "arunachal": "Arunachal Pradesh", "manipur": "Manipur", "meghalaya": "Meghalaya", "mizoram": "Mizoram", "nagaland": "Nagaland", "sikkim": "Sikkim", "tripura": "Tripura", "puducherry": "Puducherry", "andaman": "Andaman and Nicobar Islands", "ladakh": "Ladakh", "lakshadweep": "Lakshadweep", "daman": "Daman and Diu", "dnh": "Dadra and Nagar Haveli", "gem": "GeM (bidplus)", "bihar": "Bihar", "telangana": "Telangana", "andhra": "Andhra Pradesh", "gujarat": "Gujarat", "chhattisgarh": "Chhattisgarh", "karnataka": "Karnataka", "adani": "Adani Airports", "demo": "Sample data",
}

# Tenders from a state portal belong to that state even if the text does not say so.
PORTAL_STATE = {
    "chandigarh": "Chandigarh", "maharashtra": "Maharashtra", "punjab": "Punjab",
    "haryana": "Haryana", "delhi": "Delhi", "tamilnadu": "Tamil Nadu", "kerala": "Kerala",
    "westbengal": "West Bengal", "odisha": "Odisha", "himachal": "Himachal Pradesh",
    "uttarakhand": "Uttarakhand", "jharkhand": "Jharkhand", "assam": "Assam",
    "jammukashmir": "Jammu and Kashmir",
    "bihar": "Bihar",
    "telangana": "Telangana",
    "andhra": "Andhra Pradesh",
    "gujarat": "Gujarat",
    "chhattisgarh": "Chhattisgarh",
    "karnataka": "Karnataka",
    "rajasthan": "Rajasthan",
    "uttarpradesh": "Uttar Pradesh",
    "madhyapradesh": "Madhya Pradesh",
    "goa": "Goa",
    "arunachal": "Arunachal Pradesh",
    "manipur": "Manipur",
    "meghalaya": "Meghalaya",
    "mizoram": "Mizoram",
    "nagaland": "Nagaland",
    "sikkim": "Sikkim",
    "tripura": "Tripura",
    "puducherry": "Puducherry",
    "andaman": "Andaman and Nicobar Islands",
    "ladakh": "Ladakh",
    "lakshadweep": "Lakshadweep",
    "daman": "Dadra and Nagar Haveli and Daman and Diu",
    "dnh": "Dadra and Nagar Haveli and Daman and Diu",
}


def source_for(base_url):
    """Portal name for an address. Unknown NIC portals get their host name."""
    base = base_url.rstrip("/")
    for name, url in PORTALS.items():
        if url.rstrip("/") == base:
            return name
    host = urlparse(base_url).hostname or "unknown"
    return host.replace("www.", "")


def base_url_for(source, fallback=None):
    """Address to use when going back to a tender's portal (documents, etc.)."""
    if source in PORTALS:
        return PORTALS[source]
    return fallback or PORTALS["cppp"]

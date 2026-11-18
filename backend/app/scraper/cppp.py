"""
CPPP scraper (eprocure.gov.in).

How it works, in plain words:
  1. Open the "Tenders by Organisation" page. This page has NO captcha.
     It lists every central organisation and how many active tenders each has.
  2. For each organisation, open its tender list (and follow "Next" pages).
  3. For each tender row, open the detail page and read every field
     plus the list of documents.

The same NIC software (GePNIC) runs most STATE portals too
(mahatenders.gov.in, tntenders.gov.in, ...). So later, adding a state is
mostly: create GePNICClient(base_url="https://mahatenders.gov.in/nicgep/app").

Note: CPPP links contain a session token, so all pages for one crawl must be
fetched with the SAME requests.Session (cookies). That is why the client
keeps one session open.
"""
import logging
import re
import time
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from .geo import locate
from .normalize import clean_text, parse_date, parse_money

log = logging.getLogger(__name__)

CPPP_BASE = "https://eprocure.gov.in/eprocure/app"
ORG_LIST_PATH = "?page=FrontEndTendersByOrganisation&service=page"

_DOC_EXT = re.compile(r"\.(pdf|xls|xlsx|doc|docx|zip|rar|7z|csv|jpg|jpeg|png)$", re.I)
_SIZE = re.compile(r"^\s*[\d.,]+\s*(KB|MB|GB|bytes?)\s*$", re.I)
_TENDER_ID = re.compile(r"\d{4}_[A-Za-z0-9]+_\d+_\d+")


# --------------------------------------------------------------------------
# HTTP client (polite: delay between requests, retries on failure)
# --------------------------------------------------------------------------
class PortalUnreachable(Exception):
    """The internet or the portal has been down for several minutes. Stop this crawl."""


def _short(exc):
    text = str(exc)
    if "NameResolution" in text or "getaddrinfo" in text:
        return "cannot find the website address, internet may be off"
    if "timed out" in text.lower():
        return "timed out"
    return text[:120]


class SlowPage(Exception):
    """One page kept timing out. Skip it; the next run will try it again."""


class GePNICClient:
    """
    Three kinds of trouble, three different reactions:

      Internet down (DNS error, no connection)   -> wait up to ~9 min, then stop the crawl
      Server slow   (read timeout, 5xx error)    -> wait, SLOW DOWN, retry, then skip that page
      Page missing  (404 and other 4xx)          -> skip that page straight away
    """

    NETWORK_WAITS = [10, 30, 60, 120, 300]     # internet down: 10s, 30s, 1m, 2m, 5m
    SLOW_WAITS = [15, 45]                      # slow server: pause before retries 2 and 3
    MAX_DELAY = 10.0                           # never slower than 1 request per 10 s
    STUCK_LIMIT = 8                            # this many skipped pages in a row = portal is down

    def __init__(self, base_url=CPPP_BASE, delay_seconds=1.5, connect_timeout=15, read_timeout=60):
        self.base_url = base_url
        self.min_delay = delay_seconds
        self.delay = delay_seconds
        self.timeout = (connect_timeout, read_timeout)
        self.session = None
        self.reset_session()
        self._last = 0.0
        self._ok_streak = 0
        self._skipped_in_a_row = 0

    def reset_session(self):
        """Throw away cookies and start a brand-new portal session.
        Old CPPP links stop working when their session expires."""
        if self.session is not None:
            self.session.close()
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (compatible; TenderIndiaBot/0.1; +research)",
            "Accept-Language": "en-IN,en;q=0.9",
        })

    def _slow_down(self):
        new = min(self.delay * 2, self.MAX_DELAY)
        if new > self.delay:
            log.info("Server looks busy. Slowing down to 1 request every %.1fs.", new)
        self.delay = new
        self._ok_streak = 0

    def _maybe_speed_up(self):
        self._ok_streak += 1
        if self._ok_streak >= 25 and self.delay > self.min_delay:
            self.delay = max(self.min_delay, self.delay * 0.7)
            self._ok_streak = 0
            log.info("Server is responding well. Speeding up to 1 request every %.1fs.", self.delay)

    def get(self, url, slow_retries=None):
        """Fetch a page politely. Returns HTML text.
        slow_retries=0 means: if it times out once, give up quickly (caller has a better plan)."""
        return self.get_response(url, slow_retries).text

    def get_response(self, url, slow_retries=None):
        """Fetch politely and return the whole response (bytes + headers), e.g. for PDFs."""
        url = urljoin(self.base_url, url)
        slow_waits = self.SLOW_WAITS if slow_retries is None else self.SLOW_WAITS[:slow_retries]
        network_tries = 0
        slow_tries = 0
        while True:
            wait = self.delay - (time.time() - self._last)
            if wait > 0:
                time.sleep(wait)
            try:
                resp = self.session.get(url, timeout=self.timeout)
                self._last = time.time()
                if resp.status_code >= 500:
                    raise requests.HTTPError(f"server error {resp.status_code}", response=resp)
                resp.raise_for_status()          # 4xx: page really missing, no retry
                if network_tries:
                    log.info("Connection is back, continuing.")
                self._skipped_in_a_row = 0
                self._maybe_speed_up()
                return resp

            except (requests.ReadTimeout, requests.HTTPError) as exc:
                # The server answered the call but is too slow or overloaded.
                self._last = time.time()
                status = getattr(getattr(exc, "response", None), "status_code", None)
                if status is not None and status < 500:
                    raise                        # 404 etc: retrying will not help
                self._slow_down()
                if slow_tries >= len(slow_waits):
                    self._skipped_in_a_row += 1
                    if self._skipped_in_a_row >= self.STUCK_LIMIT:
                        raise PortalUnreachable(
                            f"{self.STUCK_LIMIT} pages in a row did not load. The portal seems down.")
                    raise SlowPage(f"Server too slow, skipped for now: {url}") from exc
                pause = slow_waits[slow_tries]
                slow_tries += 1
                log.warning("Server slow (%s). Waiting %ss, then try %d/%d.",
                            "timed out" if status is None else f"error {status}",
                            pause, slow_tries + 1, len(slow_waits) + 1)
                time.sleep(pause)

            except (requests.ConnectionError, requests.Timeout) as exc:
                # No internet, DNS failure, laptop slept, Wi-Fi dropped, could not connect ...
                if network_tries >= len(self.NETWORK_WAITS):
                    raise PortalUnreachable(f"Cannot reach {self.base_url}: {_short(exc)}") from exc
                pause = self.NETWORK_WAITS[network_tries]
                network_tries += 1
                log.warning("Network problem (%s). Waiting %ss before retry %d/%d.",
                            _short(exc), pause, network_tries, len(self.NETWORK_WAITS))
                time.sleep(pause)

    def org_list_url(self):
        return urljoin(self.base_url, ORG_LIST_PATH)


# --------------------------------------------------------------------------
# Parsers (pure functions: HTML in, Python dicts out, easy to unit test)
# --------------------------------------------------------------------------
def _soup(html):
    return BeautifulSoup(html, "lxml")


def _cells(tr):
    return tr.find_all("td", recursive=False) or tr.find_all("td")


def parse_org_list(html, base_url=CPPP_BASE):
    """Organisation page -> [{name, count, url}]."""
    soup = _soup(html)
    orgs = []
    for tr in soup.select("table tr"):
        tds = _cells(tr)
        if len(tds) < 3:
            continue
        link = tds[-1].find("a") or tr.find("a")
        if not link or not link.get("href"):
            continue
        count_text = clean_text(link.get_text())
        if not count_text or not count_text.isdigit():
            continue
        name = clean_text(tds[1].get_text())
        if not name:
            continue
        orgs.append({
            "name": name,
            "count": int(count_text),
            "url": urljoin(base_url, link["href"]),
        })
    return orgs


def parse_tender_list(html, base_url=CPPP_BASE):
    """
    Tender list page -> (rows, next_page_url).

    Each row looks like:
      S.No | e-Published Date | Closing Date | Opening Date |
      [Title] [Ref No][Tender ID] | Organisation Chain
    """
    soup = _soup(html)
    rows = []
    for tr in soup.select("table tr"):
        tds = _cells(tr)
        if len(tds) < 6:
            continue
        title_cell = tds[4]
        link = title_cell.find("a")
        text = clean_text(title_cell.get_text(" "))
        if not link or not text:
            continue
        brackets = [clean_text(b) for b in re.findall(r"\[([^\]]*)\]", text)]
        brackets = [b for b in brackets if b]
        tender_id = next((b for b in reversed(brackets) if _TENDER_ID.fullmatch(b)), None)
        if not tender_id:
            m = _TENDER_ID.search(text)
            tender_id = m.group(0) if m else None
        if not tender_id:
            continue
        others = [b for b in brackets if b != tender_id]
        title = clean_text(link.get_text()).strip("[] ") if link.get_text() else None
        title = title or (others[0] if others else None)
        ref_no = next((b for b in others if b != title), None)
        rows.append({
            "tender_id": tender_id,
            "title": title,
            "ref_no": ref_no,
            "published_at": parse_date(tds[1].get_text()),
            "closing_at": parse_date(tds[2].get_text()),
            "opening_at": parse_date(tds[3].get_text()),
            "org_chain": clean_text(tds[5].get_text()),
            "detail_url": urljoin(base_url, link["href"]),
        })

    next_url = None
    nxt = soup.find("a", id="linkFwd") or soup.find("a", string=re.compile(r"^\s*Next", re.I))
    if nxt and nxt.get("href"):
        next_url = urljoin(base_url, nxt["href"])
    return rows, next_url


# Detail page label -> our field name. Labels are matched loosely (lowercase, no symbols).
_LABELS = {
    "organisation chain": "org_chain",
    "tender reference number": "ref_no",
    "tender id": "tender_id",
    "tender type": "tender_type",
    "tender category": "category",
    "product category": "product_category",
    "form of contract": "form_of_contract",
    "title": "title",
    "work description": "description",
    "tender value in": "value",
    "tender value": "value",
    "emd amount in": "emd",
    "emd amount": "emd",
    "tender fee in": "tender_fee",
    "tender fee": "tender_fee",
    "location": "location",
    "pincode": "pincode",
    "period of work days": "period_days",
    "published date": "published_at",
    "document download sale start date": "doc_start_at",
    "document download sale end date": "doc_end_at",
    "bid submission start date": "bid_start_at",
    "bid submission end date": "closing_at",
    "bid opening date": "opening_at",
    "pre bid meeting date": "pre_bid_at",
    "inviting authority name": "authority_name",
    "name": "authority_name",
    "address": "authority_address",
}

_DATE_FIELDS = {"published_at", "doc_start_at", "doc_end_at", "bid_start_at",
                "closing_at", "opening_at", "pre_bid_at"}
_MONEY_FIELDS = {"value", "emd", "tender_fee"}


def _label_key(text):
    t = (text or "").lower()
    t = re.sub(r"[^a-z ]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def parse_tender_detail(html, base_url=CPPP_BASE):
    """Tender detail page -> {fields..., documents: [...]}."""
    soup = _soup(html)
    data = {}

    # GePNIC shows fields as <td class="td_caption">Label</td><td class="td_field">Value</td>
    captions = soup.select("td.td_caption") or soup.find_all("td")
    for cap in captions:
        key = _LABELS.get(_label_key(cap.get_text()))
        if not key or key in data:
            continue
        val_td = cap.find_next_sibling("td")
        if val_td is None:
            continue
        data[key] = clean_text(val_td.get_text(" "))

    for f in _DATE_FIELDS & data.keys():
        data[f] = parse_date(data[f])
    for f in _MONEY_FIELDS & data.keys():
        data[f] = parse_money(data[f])

    data["documents"] = _parse_documents(soup, base_url)
    return data


def _parse_documents(soup, base_url):
    """Find every document link (NIT, work items, BOQ...) with its section and size."""
    docs, seen = [], set()
    for a in soup.find_all("a"):
        name = clean_text(a.get_text())
        if not name or not _DOC_EXT.search(name):
            continue
        tr = a.find_parent("tr")
        cells = [clean_text(td.get_text(" ")) for td in tr.find_all("td")] if tr else []
        size = next((c for c in cells if c and _SIZE.match(c)), None)
        desc = next((c for c in cells if c and c != name and c != size
                     and not c.isdigit() and len(c) > 2), None)
        section = _doc_section(a)
        key = (section, name)
        if key in seen:
            continue
        seen.add(key)
        href = a.get("href")
        docs.append({
            "section": section,
            "name": name,
            "description": desc,
            "size": size,
            "url": urljoin(base_url, href) if href and not href.startswith("javascript") else None,
        })
    return docs


def _doc_section(a):
    """Walk up and back to find the heading this document sits under."""
    for el in a.find_all_previous("td", class_="td_caption", limit=40):
        t = _label_key(el.get_text())
        if t in {"nit document", "work item documents"}:
            return el.get_text(strip=True)
        if "corrigendum" in t and len(t) < 40:
            return "Corrigendum"
    return "Documents"


# --------------------------------------------------------------------------
# Glue: combine list row + detail page into one standard tender record
# --------------------------------------------------------------------------
def build_record(row, detail, source="cppp", base_url=None):
    rec = {**row, **{k: v for k, v in detail.items() if v not in (None, [], "")}}
    # The list page is the source of truth for the ID and key dates, because
    # quick mode compares the next run's list page against what we stored.
    for k in ("tender_id", "published_at", "closing_at", "opening_at"):
        if row.get(k) is not None:
            rec[k] = row[k]
    rec["source"] = source
    chain = rec.get("org_chain") or ""
    rec["organisation"] = clean_text(chain.split("||")[0]) if chain else None
    rec["district"], rec["state"] = locate(
        rec.get("pincode"), rec.get("location"), rec.get("authority_address"), chain)
    if not rec["state"]:
        from .portals import PORTAL_STATE
        rec["state"] = PORTAL_STATE.get(source)       # a state portal's tenders are in that state
    if base_url:
        rec["source_url"] = base_url
    rec.pop("detail_url", None)
    return rec


# Links on CPPP carry a session token that expires. Re-open the organisation list
# this often to get fresh links, and right away whenever a link stops responding.
LINK_REFRESH_SECONDS = 15 * 60
FAILS_BEFORE_COOLDOWN = 3          # organisations failing in a row = portal is struggling
COOLDOWN_SECONDS = 10 * 60
MAX_COOLDOWNS = 2                  # after this many pauses in a row, stop the crawl


def crawl(client, max_orgs=None, max_tenders_per_org=None, on_record=None, save_html_dir=None,
          needs_detail=None, source="cppp", shard=None):
    """
    Walk the whole portal. Calls on_record(record) for each tender found.

    needs_detail(row) -> bool lets the caller skip detail pages for tenders
    it already has unchanged. This is what makes repeat runs fast.

    Staying healthy on long runs:
      * organisation links are refreshed every 15 minutes (sessions expire)
      * a link that does not answer -> new session + fresh link, one quick retry
      * 3 organisations failing in a row -> pause 10 minutes, then continue
      * organisations that still failed get a second chance at the end
    """
    links = {"by_name": {}, "at": 0.0}
    stats = {"total": 0, "skipped": 0}

    def refresh_links(new_session=False, reason=None):
        if reason:
            log.info("Getting fresh links (%s).", reason)
        if new_session and hasattr(client, "reset_session"):
            client.reset_session()
        orgs = parse_org_list(client.get(client.org_list_url()), client.base_url)
        if orgs:
            links["by_name"] = {o["name"]: o["url"] for o in orgs}
            links["at"] = time.time()
        return orgs

    def crawl_org(org, quick_fail):
        url, done = links["by_name"].get(org["name"], org["url"]), 0
        seen_pages = set()
        while url:
            # list pages: give up fast on the first try, because a fresh link usually fixes it
            html = client.get(url, slow_retries=0) if quick_fail else client.get(url)
            rows, url = parse_tender_list(html, client.base_url)
            page_key = tuple(r["tender_id"] for r in rows)
            if page_key in seen_pages:              # portal sent the same page again: stop paging
                log.warning("  %s: list page repeated, stopping here.", org["name"])
                return
            seen_pages.add(page_key)
            for row in rows:
                if max_tenders_per_org and done >= max_tenders_per_org:
                    return
                if needs_detail and not needs_detail(row):
                    detail = {}          # already have it, nothing changed: no page fetch
                    stats["skipped"] += 1
                else:
                    try:
                        detail_html = client.get(row["detail_url"])
                        if save_html_dir:
                            _save(save_html_dir, row["tender_id"], detail_html)
                        detail = parse_tender_detail(detail_html, client.base_url)
                    except PortalUnreachable:
                        raise            # internet is down: stop, don't burn through tenders
                    except Exception as exc:  # one bad page: save list data, keep going
                        log.warning("Detail failed for %s: %s", row["tender_id"], exc)
                        detail = {}
                record = build_record(row, detail, source=source, base_url=client.base_url)
                if not record.get("organisation"):
                    record["organisation"] = org["name"]
                if on_record:
                    on_record(record)
                done += 1
                stats["total"] += 1
                if done % 100 == 0:
                    log.info("    ... %d of %d tenders in %s", done, org["count"], org["name"])

    def attempt(org):
        """Try an organisation; if its link is dead, get a fresh session and try once more."""
        try:
            crawl_org(org, quick_fail=True)
            return True
        except PortalUnreachable:
            raise
        except Exception as exc:
            log.warning("  %s not responding (%s). Retrying with a fresh session.",
                        org["name"], _short(exc))
        try:
            refresh_links(new_session=True)
            crawl_org(org, quick_fail=False)
            return True
        except PortalUnreachable:
            raise
        except Exception as exc:
            log.warning("  Skipping %s for now: %s", org["name"], _short(exc))
            return False

    orgs = refresh_links()
    log.info("Found %d organisations", len(orgs))
    selected = orgs[:max_orgs] if max_orgs else orgs
    if shard:
        i, n = shard
        selected = [o for k, o in enumerate(selected) if k % n == i]
        log.info("Shard %d/%d: %d organisations", i, n, len(selected))
    retry_later, fails_in_row, cooldowns = [], 0, 0

    for i, org in enumerate(selected, 1):
        if time.time() - links["at"] > LINK_REFRESH_SECONDS:
            try:
                refresh_links(new_session=True, reason="15 minutes passed, session may expire")
            except PortalUnreachable:
                raise
            except Exception as exc:
                log.warning("Could not refresh links (%s); using the current ones.", _short(exc))
        log.info("[%d/%d] %s (%d tenders)", i, len(selected), org["name"], org["count"])
        if attempt(org):
            fails_in_row, cooldowns = 0, 0
            continue
        retry_later.append(org)
        fails_in_row += 1
        if fails_in_row >= FAILS_BEFORE_COOLDOWN:
            if cooldowns >= MAX_COOLDOWNS:
                raise PortalUnreachable(
                    f"Portal still not responding after {MAX_COOLDOWNS} pauses. Stopping for now.")
            cooldowns += 1
            log.warning("%d organisations in a row failed. The portal is struggling, so pausing "
                        "%d minutes before continuing (pause %d/%d).", fails_in_row,
                        COOLDOWN_SECONDS // 60, cooldowns, MAX_COOLDOWNS)
            time.sleep(COOLDOWN_SECONDS)
            fails_in_row = 0
            if hasattr(client, "_skipped_in_a_row"):
                client._skipped_in_a_row = 0
            try:
                refresh_links(new_session=True, reason="after pause")
            except PortalUnreachable:
                raise
            except Exception:
                pass

    still_failed = []
    if retry_later:
        log.info("Second chance for %d organisations that were skipped.", len(retry_later))
        try:
            refresh_links(new_session=True)
        except PortalUnreachable:
            raise
        except Exception:
            pass
        for org in retry_later:
            log.info("[retry] %s", org["name"])
            if not attempt(org):
                still_failed.append(org["name"])

    log.info("Opened %d detail pages, skipped %d unchanged tenders",
             stats["total"] - stats["skipped"], stats["skipped"])
    if still_failed:
        log.warning("%d organisations still failed (next run will try again): %s",
                    len(still_failed), ", ".join(still_failed[:10]))
    return stats["total"]


def _save(folder, name, html):
    import pathlib
    p = pathlib.Path(folder)
    p.mkdir(parents=True, exist_ok=True)
    (p / f"{name}.html").write_text(html, encoding="utf-8")

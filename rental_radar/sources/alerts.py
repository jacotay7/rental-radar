"""Listings from saved-search alert emails (Zillow, HotPads, Trulia, Apartments.com, Zumper, ...), read over IMAP.

Several big rental sites block automated access, but they will happily email you new matches for a
saved search. We read those emails over IMAP (read-only: BODY.PEEK never marks them read), pull out
each listing's address, price, beds/baths/sqft, link and photo, and geocode the address with the free
US Census geocoder. See docs/saved-search-alerts.md.
"""
import email
import email.policy
import email.utils
import hashlib
import html
import imaplib
import logging
import re
from datetime import date, timedelta

from ..addresses import address_re, card_address_re
from ..config import imap_credentials
from ..geo import geocode

log = logging.getLogger("radar")
MARK_GONE = False  # alert listings don't reappear in later runs; don't treat absence as "rented"

SENDERS = {
    "zillow.com": "zillow", "hotpads.com": "hotpads", "trulia.com": "trulia", "apartments.com": "apartments.com",
    "zumper.com": "zumper", "padmapper.com": "padmapper", "apartmentlist.com": "apartmentlist",
    "rent.com": "rent.com", "homes.com": "homes.com", "realtor.com": "realtor-alert", "redfin.com": "redfin-alert",
    "furnishedfinder.com": "furnishedfinder",
}
LINK_PATTERNS = [
    r"zillow\.com/(homedetails|b|apartments)/", r"hotpads\.com/[^\"]+/(pad|building)", r"trulia\.com/(p|home|rental)/",
    r"apartments\.com/[^\"/]+/[a-z0-9]{6,}/", r"zumper\.com/(apartment-buildings|address)/",
    r"padmapper\.com/(buildings|apartments)/", r"apartmentlist\.com/[a-z]{2}/[^\"]+/", r"homes\.com/property/",
]
PRICE_RE = re.compile(r"\$\s?(\d{1,2},\d{3}|\d{4})(?:\+|\s*-\s*\$?[\d,]+)?(?:\s*/\s*mo)?")
BEDS_RE = re.compile(r"(\d)\s*(?:bd|bds|beds?|bedrooms?|br)\b", re.I)
BATHS_RE = re.compile(r"(\d(?:\.\d)?)\s*(?:ba|baths?|bathrooms?)\b", re.I)
SQFT_RE = re.compile(r"([\d,]{3,6})\s*(?:sq\.?\s*ft|sqft|square feet)", re.I)
STUDIO_RE = re.compile(r"\bstudio\b", re.I)
CARD_PRICE_RE = re.compile(r"^\$\s?(\d{1,2},\d{3}|\d{3,4})\+?(?:\s*/\s*mo)?$")
MIN_RENT_PER_BED = 600  # below this the "rent" is per room (co-living / student houses), not the whole place


def _html_to_text(s: str) -> str:
    s = re.sub(r"(?is)<(script|style).*?</\1>", " ", s)
    s = re.sub(r"(?i)<br\s*/?>|</(p|div|tr|td|li|h\d|table)>", "\n", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    return re.sub(r"[ \t\xa0]+", " ", re.sub(r"\n\s*\n+", "\n", s))


def _links(raw_html: str) -> list[str]:
    hrefs = [html.unescape(h) for h in re.findall(r'href="([^"]+)"', raw_html)]
    return [h for h in hrefs if any(re.search(p, h) for p in LINK_PATTERNS)]


def _card_media(raw_html: str, site: str, street: str, start: int) -> tuple[str, str | None, int]:
    """Listing link and photo for the card whose address appears at/after `start` in the raw HTML.

    Emails repeat the link/photo per card just before the address text, so take the nearest ones before it.
    """
    pos = raw_html.find(html.escape(street, quote=False), start)
    if pos < 0:
        pos = raw_html.find(street, start)
    if pos < 0:
        return "", None, start
    head = raw_html[start:pos]
    link = photo = None
    if site == "zillow":
        z = re.findall(r"(\d+)_zpid", head)
        link = f"https://www.zillow.com/homedetails/{z[-1]}_zpid/" if z else None
        ph = re.findall(r"https://photos\.zillowstatic\.com/fp/[\w-]+\.(?:jpg|webp)", head)
        photo = ph[-1] if ph else None
    else:
        # <a href=LINK><img src=PHOTO alt="ADDRESS"> (Zumper and most newsletter-style templates)
        m = list(re.finditer(r'<a href="([^"]+)"[^>]*>\s*<img src="([^"]+)"', head))
        if m:
            link, photo = html.unescape(m[-1].group(1)), m[-1].group(2)
        if not link:
            hrefs = _links(head)
            link = hrefs[-1] if hrefs else None
    return link or "", photo, pos + len(street)


def _record(site, address, street, city, received, **kw) -> dict:
    rec = {
        "uid": f"{site}:{hashlib.sha1(address.lower().encode()).hexdigest()[:12]}",
        "source": site, "source_id": address, "title": f"{street}, {city}",
        "address": address, "city": city, "posted_at": received, "photos": [],
    }
    rec.update(kw)
    return rec


def parse_cards(raw_html: str, site: str, received: str, state: str, filtered: bool = True,
                site_housing: dict | None = None) -> list[dict]:
    """Card-style alerts (Zillow, Zumper, ...): price/beds/baths lines followed by an address line.

    `filtered` means the email lists matches for a saved search set up with your pet / home-type filters;
    recommendation / "similar homes" emails are not.
    """
    text = re.sub(r"[͏​‌﻿\xad]", "", _html_to_text(raw_html))
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    addr_line = card_address_re(state)
    out, seen, block, cursor = [], set(), [], 0
    for ln in lines:
        if re.match(r"(?i)^(view listing|see (latest|more)\b)", ln):
            block = []  # end of a card; don't let its price leak into the next one
            continue
        m = addr_line.match(ln)
        pi = max((i for i, b in enumerate(block) if CARD_PRICE_RE.match(b)), default=None)
        if not (m and pi is not None):
            block.append(ln)
            continue
        facts = " | ".join(block[max(0, pi - 2):])
        rent = int(CARD_PRICE_RE.match(block[pi]).group(1).replace(",", ""))
        block = []
        street, city, zipc = m.group(1).strip(), m.group(2).strip(), m.group(3) or ""
        link, photo, cursor = _card_media(raw_html, site, street, cursor)
        address = f"{street}, {city}, {state} {zipc}".strip()
        if address.lower() in seen:
            continue
        seen.add(address.lower())
        beds = BEDS_RE.search(facts)
        nbeds = int(beds.group(1)) if beds else (0 if STUDIO_RE.search(facts) else None)
        if nbeds and rent / nbeds < MIN_RENT_PER_BED:
            log.info("alerts: skipping %s ($%d for %d beds looks like per-room pricing)", address, rent, nbeds)
            continue
        baths, sqft = BATHS_RE.search(facts), SQFT_RE.search(facts)
        out.append(_record(
            site, address, street, city, received, url=link, price=rent, beds=nbeds,
            baths=float(baths.group(1)) if baths else None,
            sqft=int(sqft.group(1).replace(",", "")) if sqft else None,
            housing_type=(site_housing or {}).get(site) if filtered else None,
            body=facts, dogs_ok=filtered, dog_policy_unknown=not filtered,
            photos=[photo] if photo else [],
            contact={"note": f"Open the {site} listing and use its Contact / Request tour button."},
        ))
    return out


def parse_generic(raw_html: str, site: str, received: str, state: str) -> list[dict]:
    """Fallback parser for unfamiliar layouts: one listing per street address found in the email."""
    text = _html_to_text(raw_html)
    links = _links(raw_html)
    matches = list(address_re(state).finditer(text))
    out, seen = [], set()
    for i, m in enumerate(matches):
        street, city, zipc = m.group(1).strip(), m.group(2).strip(), m.group(3) or ""
        address = f"{street}, {city}, {state} {zipc}".strip()
        if address.lower() in seen:
            continue
        seen.add(address.lower())
        # Listing facts usually sit just before the address within the same card; else just after it
        start = matches[i - 1].end() if i > 0 else max(0, m.start() - 400)
        end = matches[i + 1].start() if i + 1 < len(matches) else m.end() + 400
        window = text[max(start, m.start() - 400):m.end() + 40]
        if not PRICE_RE.search(window):
            window = text[max(start, m.start() - 400):min(end, m.end() + 400)]
        price, beds = PRICE_RE.search(window), BEDS_RE.search(window)
        baths, sqft = BATHS_RE.search(window), SQFT_RE.search(window)
        out.append(_record(
            site, address, street, city, received,
            url=links[min(i, len(links) - 1)] if links else "",
            price=int(price.group(1).replace(",", "")) if price else None,
            beds=0 if (not beds and STUDIO_RE.search(window)) else (int(beds.group(1)) if beds else None),
            baths=float(baths.group(1)) if baths else None,
            sqft=int(sqft.group(1).replace(",", "")) if sqft else None,
            housing_type="house" if re.search(r"\b(house|single[- ]family)\b", window, re.I) else None,
            body=window.strip(),
            contact={"note": f"Open the {site} listing (the link may route through the alert's click tracker) "
                             "and use its Contact / Request tour button."},
        ))
    return out


def is_filtered(site: str, sender: str, subject: str, filtered_sites) -> bool:
    """Whether an alert email holds saved-search matches rather than recommendations."""
    if site == "zillow":
        return "instant-updates" in sender or "saved search" in subject.lower()
    return site in filtered_sites


def parse_alert(raw_html: str, site: str, received: str, state: str, filtered: bool = True,
                site_housing: dict | None = None) -> list[dict]:
    """Card parser first; fall back to the generic one-listing-per-address parser."""
    recs = parse_cards(raw_html, site, received, state, filtered, site_housing)
    if not recs:
        recs = parse_generic(raw_html, site, received, state)
        for r in recs:
            r["dogs_ok"], r["dog_policy_unknown"] = filtered, not filtered
    return recs


def sender_site(from_header: str, senders: dict) -> str | None:
    """The alert site for a From header, matching the address's real domain (or a subdomain of it).
    A substring test would accept `zillow.com.evil.example` or a display name containing the domain."""
    domain = email.utils.parseaddr(from_header or "")[1].rpartition("@")[2].lower()
    for known, site in senders.items():
        if domain == known or domain.endswith("." + known):
            return site
    return None


def authenticated(msg) -> bool:
    """False when the receiving mail server checked this message and it failed SPF, DKIM and DMARC,
    i.e. the From address is probably forged. Servers that don't record results get the benefit of
    the doubt (True)."""
    results = " ".join(str(h) for h in msg.get_all("Authentication-Results") or []).lower()
    if not results:
        return True
    return any(f"{check}=pass" in results for check in ("dkim", "spf", "dmarc"))


def safe_url(url: str) -> str:
    """Only http(s) links are kept; anything else (javascript:, data:, mailto:) is dropped."""
    return url if re.match(r"https?://[^\s\"'<>]+$", url or "", re.I) else ""


def _all_mail_folder(m) -> str:
    """Gmail's "All Mail" folder, whatever it is called in the account's language (it has the \\All flag)."""
    typ, boxes = m.list()
    for raw in (boxes or []) if typ == "OK" else []:
        line = raw.decode(errors="ignore") if isinstance(raw, bytes) else str(raw)
        if "\\All" in line:
            name = re.search(r'"([^"]+)"\s*$', line)
            if name:
                return f'"{name.group(1)}"'
    return '"[Gmail]/All Mail"'


def _message_ids(m, host: str, senders: dict, days: int) -> list[bytes]:
    """IDs of recent emails from alert senders. Gmail gets a server-side search; other servers a date
    search filtered by From header."""
    if "gmail" in host:
        m.select(_all_mail_folder(m), readonly=True)
        typ, data = m.search(None, "X-GM-RAW", f'"from:({" OR ".join(senders)}) newer_than:{days}d"')
        return data[0].split() if typ == "OK" else []
    m.select("INBOX", readonly=True)
    since = (date.today() - timedelta(days=days)).strftime("%d-%b-%Y")
    typ, data = m.search(None, "SINCE", since)
    ids = data[0].split() if typ == "OK" else []
    keep = []
    for mid in ids:
        typ, hdr = m.fetch(mid, "(BODY.PEEK[HEADER.FIELDS (FROM)])")
        if typ == "OK" and hdr and isinstance(hdr[0], tuple):
            frm = hdr[0][1].decode(errors="ignore").partition(":")[2]
            if sender_site(frm, senders):
                keep.append(mid)
    return keep


def search(cfg, conn=None) -> list[dict]:
    user, pw = imap_credentials()
    if not user or not pw or conn is None:
        log.info("alerts: no IMAP credentials; skipping")
        return []
    acfg = cfg["sources"]["alerts"]
    state = cfg["search"]["state"]
    senders = {**SENDERS, **(acfg.get("extra_senders") or {})}
    host = acfg.get("imap_host", "imap.gmail.com")
    m = imaplib.IMAP4_SSL(host, timeout=60)
    m.login(user, pw.replace(" ", ""))
    ids = _message_ids(m, host, senders, acfg.get("lookback_days", 14))
    out: dict[str, dict] = {}
    skipped = 0
    for mid in ids[-300:]:
        typ, msgdata = m.fetch(mid, "(BODY.PEEK[])")
        if typ != "OK" or not msgdata or not isinstance(msgdata[0], tuple):
            continue
        msg = email.message_from_bytes(msgdata[0][1], policy=email.policy.default)
        frm = (msg.get("From") or "").lower()
        site = sender_site(frm, senders)
        if not site:
            continue
        if not authenticated(msg):
            log.warning("alerts: skipping an email claiming to be from %s that failed SPF/DKIM/DMARC", site)
            skipped += 1
            continue
        part = msg.get_body(("html", "plain"))
        if part is None:
            continue
        subject = str(msg.get("Subject", ""))
        filtered = is_filtered(site, frm, subject, acfg.get("filtered_sites") or [])
        for rec in parse_alert(part.get_content(), site, msg.get("Date", ""), state, filtered,
                               acfg.get("site_housing")):
            lat, lon = geocode(conn, rec["address"])
            if lat is None:
                continue
            rec["lat"], rec["lon"] = lat, lon
            rec["url"] = safe_url(rec.get("url"))
            rec["photos"] = [p for p in rec.get("photos") or [] if safe_url(p)]
            rec["alert_subject"] = subject
            out[rec["uid"]] = rec
    m.logout()
    conn.commit()
    log.info("alerts: %d emails scanned, %d listings parsed%s", len(ids), len(out),
             f", {skipped} unauthenticated emails skipped" if skipped else "")
    return list(out.values())


def fetch_detail(listing: dict, cfg) -> dict:
    return listing

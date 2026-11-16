"""Craigslist apartments/housing for rent (apa) via the site's own JSON search API + posting pages.

Configure `sources.craigslist.site` (the subdomain, e.g. "sfbay", "chicago", "seattle"), a `postal`
code near the center of your search, and `distance_mi`.
"""
import html
import re
from datetime import UTC, datetime

from .. import http

SEARCH = "https://sapi.craigslist.org/web/v8/postings/search/full"
AREAS = "https://reference.craigslist.org/Areas"   # every Craigslist site, with center coordinates
PAGE_CAP = 360  # the API returns at most this many per query; we split price bands to stay under it


def nearest_site(lat: float, lon: float) -> dict | None:
    """The US Craigslist site whose center is closest to (lat, lon), for the setup wizard.

    Returns {site, name, timezone, km} or None if the area list can't be fetched."""
    from ..geo import km
    try:
        areas = http.get(AREAS, timeout=20, retries=1, min_interval=0).json()
    except Exception:
        return None
    best = min((a for a in areas if a.get("Country") == "US" and a.get("Latitude") is not None),
               key=lambda a: km(lat, lon, a["Latitude"], a["Longitude"]), default=None)
    if not best:
        return None
    return {"site": best["Hostname"], "name": best.get("Description") or best["Hostname"],
            "timezone": best.get("Timezone"), "km": km(lat, lon, best["Latitude"], best["Longitude"])}


def _query(cfg, min_price, max_price):
    src = cfg["sources"]["craigslist"]
    params = {
        "batch": "1-0-360-0-0", "cc": "US", "lang": "en", "searchPath": "apa",
        "postal": str(src["postal"]), "search_distance": src.get("distance_mi", 20),
        "min_bedrooms": cfg["size"]["min_beds"], "min_price": min_price, "max_price": max_price,
        "sort": "date",
    }
    if cfg["pets"].get("dog"):
        params["pets_dog"] = 1
    if cfg["pets"].get("cat"):
        params["pets_cat"] = 1
    resp = http.get(SEARCH, params=params, min_interval=2.0)
    resp.raise_for_status()
    return resp.json()["data"]


def _parse_item(item, decode, cfg):
    site = cfg["sources"]["craigslist"]["site"]
    pid = decode["minPostingId"] + item[0]
    posted = decode["minPostedDate"] + item[1]
    price = item[3] if isinstance(item[3], int) else None
    loc = item[4] if isinstance(item[4], str) and "~" in item[4] else None
    lat = lon = None
    subarea = None
    if loc:
        parts = loc.split("~")
        idx = parts[0].split(":")
        try:
            lat, lon = float(parts[1]), float(parts[2])
            subarea = decode["locations"][int(idx[0])][2] or None
        except (ValueError, IndexError, TypeError):
            pass
    slug, beds, sqft, images, title = None, None, None, [], None
    for el in item[5:]:
        if isinstance(el, list) and el:
            tag = el[0]
            if tag == 6:
                slug = el[1]
            elif tag == 5:
                beds = el[1] if len(el) > 1 else None
                sqft = el[2] if len(el) > 2 else None
            elif tag == 4:
                images = [f"https://images.craigslist.org/{i.split(':', 1)[1]}_600x450.jpg" for i in el[1:]]
        elif isinstance(el, str) and len(el) > 8 and " " in el:
            title = el
    path = f"{subarea}/apa" if subarea else "apa"
    return {
        "uid": f"craigslist:{pid}",
        "source": "craigslist",
        "source_id": str(pid),
        "url": f"https://{site}.craigslist.org/{path}/d/{slug or 'x'}/{pid}.html",
        "title": title,
        "price": price,
        "beds": beds,
        "baths": None,
        "sqft": sqft,
        "lat": lat,
        "lon": lon,
        "posted_at": datetime.fromtimestamp(posted, UTC).isoformat(),
        "photos": images[:6],
        "dogs_ok": bool(cfg["pets"].get("dog")),  # the search filtered on pets_dog=1
        "contact": {},
    }


def search(cfg, conn=None) -> list[dict]:
    """All matching listings up to budget hard_max, splitting price bands under the API cap."""
    hard_max = cfg["budget"]["hard_max"]
    out: dict[str, dict] = {}
    bands = [(100, hard_max)]
    while bands:
        lo, hi = bands.pop()
        data = _query(cfg, lo, hi)
        items = data.get("items", [])
        if len(items) >= PAGE_CAP and hi - lo > 50:
            mid = (lo + hi) // 2
            bands += [(lo, mid), (mid + 1, hi)]
            continue
        for it in items:
            try:
                rec = _parse_item(it, data["decode"], cfg)
            except (KeyError, IndexError, TypeError):
                continue
            out[rec["uid"]] = rec
    return list(out.values())


def _text(fragment: str) -> str:
    s = re.sub(r"<br\s*/?>", "\n", fragment)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n", s)).strip()


HOUSING_TYPES = {"apartment", "condo", "cottage/cabin", "duplex", "flat", "house", "in-law", "loft",
                 "townhouse", "manufactured", "assisted living", "land"}


def fetch_detail(listing: dict, cfg=None) -> dict | None:
    """Fetch the posting page; returns the updated listing, or None if the post is gone."""
    resp = http.get(listing["url"], min_interval=1.2)
    if resp.status_code in (404, 410) or "This posting has been deleted" in resp.text \
            or "This posting has expired" in resp.text:
        return None
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code} for a posting page (blocked or rate-limited?)")
    s = resp.text
    out = dict(listing)
    m = re.search(r'<section id="postingbody">(.*?)</section>', s, re.S)
    if m:
        out["body"] = _text(m.group(1)).replace("QR Code Link to This Post", "").strip()
    attrs = [_text(a) for a in re.findall(r'<(?:span|div) class="attr[^"]*">(.*?)</(?:span|div)>', s, re.S)]
    out["attrs"] = [a for a in attrs if a]
    m = re.search(r'<span id="titletextonly">(.*?)</span>', s, re.S)
    if m:
        out["title"] = _text(m.group(1))
    m = re.search(r'<div class="mapaddress">(.*?)</div>', s, re.S)
    if m:
        out["address"] = _text(m.group(1))
    m = re.search(r'data-latitude="([-\d.]+)"\s+data-longitude="([-\d.]+)"', s)
    if m:
        out["lat"], out["lon"] = float(m.group(1)), float(m.group(2))
    m = re.search(r'(\d+(?:\.\d+)?)Ba\b', " ".join(out["attrs"]))
    if m:
        out["baths"] = float(m.group(1))
    avail = [a for a in out["attrs"] if a.lower().startswith("available")]
    out["available_text"] = avail[0] if avail else None
    out["housing_type"] = next((a for a in out["attrs"] if a.lower() in HOUSING_TYPES), None)
    low = [a.lower() for a in out["attrs"]]
    out["dogs_ok"] = any("dogs are ok" in a for a in low)
    out["cats_ok"] = any("cats are ok" in a for a in low)
    body = out.get("body", "")
    contact = dict(out.get("contact") or {})
    phones = re.findall(r"\(?\b\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}\b", body)
    if phones:
        contact["phone"] = phones[0]
    emails = re.findall(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", body)
    if emails:
        contact["email"] = emails[0]
    links = re.findall(r"https?://[^\s<>\"]+", body)
    if links:
        contact["links"] = links[:2]
    contact["note"] = ("Use the 'reply' button on the post (email/phone/text options); "
                       "phone may be behind 'show contact info'.")
    out["contact"] = contact
    return out

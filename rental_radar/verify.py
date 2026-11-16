"""Address and property-record checks.

A common scam copies a real (often just-sold) home's listing and posts it with an invented or
altered street number. If the posted address doesn't exist, or sits far from the map pin, flag it.
We also look the address up on Realtor.com (is it for sale? recently sold? what is it worth?) and
match it against our own sale-history index (sales.py). See docs/scam-detection.md.
"""
import json
import logging
import re
from datetime import date

from . import db, geo, http, sales
from .addresses import address_re

log = logging.getLogger("radar")
CHECK_SOURCES = {"craigslist"}           # sources whose addresses are typed in by the poster
GEOCODED_SOURCES = {"realtor", "zillow", "hotpads", "trulia", "apartments.com"}  # addresses from a geocoded feed
DIY_HOSTS = r"turbotenant|avail\.co|rentredi|zumper|apartments\.com|hotpads"


def _city_from_coords(lat, lon) -> str | None:
    try:
        r = http.get("https://geocoding.geo.census.gov/geocoder/geographies/coordinates",
                     params={"x": lon, "y": lat, "benchmark": "Public_AR_Current", "vintage": "Current_Current",
                             "format": "json", "layers": "Incorporated Places,Census Designated Places"},
                     min_interval=0.5)
        geos = r.json()["result"]["geographies"]
        for layer in ("Incorporated Places", "Census Designated Places"):
            if geos.get(layer):
                return re.sub(r"\s+(city|CDP|town|village|borough)$", "", geos[layer][0]["NAME"])
    except Exception as e:
        log.debug("reverse geocode failed: %s", e)
    return None


def _osm(addr: str):
    """OpenStreetMap Nominatim lookup requiring an exact house-number match (max 1 request/second)."""
    try:
        r = http.get("https://nominatim.openstreetmap.org/search",
                     params={"q": addr, "format": "json", "limit": 1, "addressdetails": 1, "countrycodes": "us"},
                     headers={"User-Agent": http.TOOL_UA}, min_interval=1.1)
        res = r.json()
        num = addr.split()[0]
        if res and res[0].get("address", {}).get("house_number", "").split("-")[0] == num:
            return float(res[0]["lat"]), float(res[0]["lon"])
    except Exception as e:
        log.debug("osm lookup failed: %s", e)
    return None, None


def full_address(d, state: str) -> str | None:
    """Best full 'street, city, ST zip' for a listing, or None if there is no street number."""
    m = address_re(state).search(d.get("body") or "")
    if m:
        return f"{m.group(1).strip()}, {m.group(2).strip()}, {state} {m.group(3) or ''}".strip()
    street = (d.get("address") or "").strip().rstrip(",")
    if not re.match(r"^\d+\s+\w", street):
        return None
    if "," in street:
        return street
    city = _city_from_coords(d["lat"], d["lon"])
    return f"{street}, {city}, {state}" if city else None


def check(conn, d, state: str) -> dict:
    addr = full_address(d, state)
    if not addr:
        return {"status": "no_address"}
    lat, lon = geo.geocode(conn, addr)
    if lat is None:
        lat, lon = _osm(addr)  # second opinion; Census misses some new/unusual addresses
    if lat is None:
        return {"status": "not_found", "address": addr}
    dist = geo.km(lat, lon, d["lat"], d["lon"])
    if dist > 1.5:
        return {"status": "far", "address": addr, "km": round(dist, 1)}
    return {"status": "ok", "address": addr}


def property_record(addr: str) -> dict | None:
    """Current Realtor.com record for an address: for sale? recently sold? also listed for rent? value?"""
    from homeharvest import scrape_property
    try:
        df = scrape_property(location=addr, listing_type="for_rent", past_days=365)
    except Exception as e:
        log.debug("property lookup failed %s: %s", addr, e)
        return None
    if df is None or not len(df):
        return {"status": "unknown"}
    row = df.iloc[0]

    def val(k):
        v = row.get(k)
        if v is None or str(v) in ("NaT", "nan", "<NA>", "None"):
            return None
        return v.item() if hasattr(v, "item") else v  # numpy scalar -> plain Python

    return {"status": str(val("status") or "unknown"), "list_price": val("list_price"),
            "last_sold_date": str(val("last_sold_date") or "")[:10] or None, "sold_price": val("sold_price"),
            "estimated_value": val("estimated_value"), "feed": val("mls"), "text": (val("text") or "")[:3000]}


def verify_all(conn, cfg, min_score: float) -> int:
    """Check addresses of plausible candidates not yet checked, and match every candidate (plus anything
    already emailed) against the local sale-listing history. Returns the number of listings updated."""
    state = cfg["search"]["state"]
    rows = conn.execute(
        "SELECT uid, data FROM listings WHERE status!='gone' AND detail_fetched=1 AND (score>=? OR emailed_at IS NOT NULL)",
        (min_score,)).fetchall()
    text_index = sales.TextIndex(conn, [json.loads(r["data"]).get("body") or "" for r in rows])
    n = 0
    for r in rows:
        d = json.loads(r["data"])
        before = json.dumps(d, sort_keys=True)
        if "address_check" not in d:
            if d["source"] in CHECK_SOURCES:
                d["address_check"] = check(conn, d, state)
            elif d["source"] in GEOCODED_SOURCES:
                d["address_check"] = {"status": "ok", "address": d.get("address")}
        ac = d.get("address_check") or {}
        pc = d.get("property_check")
        if d["source"] != "realtor" and ac.get("status") == "ok" and ac.get("address") \
                and (pc is None or "estimated_value" not in pc):
            rec = property_record(ac["address"])
            if rec:
                d["property_check"] = rec
        sm = sales.match(conn, dict(d, address=ac.get("address") or d.get("address")), text_index)
        if sm:
            d["sale_match"] = sm
        else:
            d.pop("sale_match", None)
        if json.dumps(d, sort_keys=True) != before:
            conn.execute("UPDATE listings SET data=? WHERE uid=?", (json.dumps(d), r["uid"]))
            n += 1
    conn.commit()
    return n


def check_links(conn, cfg, limit=40) -> int:
    """Re-open the original posting on DIY-landlord sites for emailed and near-email listings. A posting taken
    down within days of going up is a fraud signal: platforms pull reported scams quickly."""
    rows = conn.execute(
        "SELECT uid, data FROM listings WHERE status IN ('active','interested') AND dup_of IS NULL AND detail_fetched=1 "
        "AND (emailed_at IS NOT NULL OR score>=?) ORDER BY score DESC",
        (cfg["scoring"]["email_threshold"] - 10,)).fetchall()
    n = 0
    for r in rows:
        d = json.loads(r["data"])
        url = (d.get("contact") or {}).get("external_url") or ""
        if not url.startswith("http") or d.get("link_removed") or n >= limit:
            continue
        host = url.split("/")[2]
        if not re.search(DIY_HOSTS, host):
            continue  # only DIY-landlord sites (Zillow-family sites block automated access; don't try)
        try:
            resp = http.get(url, min_interval=2.0, retries=0)
        except Exception:
            continue
        n += 1
        if resp.status_code in (404, 410):
            posted = (d.get("posted_at") or "")[:10]
            try:
                days = (date.today() - date.fromisoformat(posted)).days
            except ValueError:
                days = None
            d["link_removed"] = {"at": db.now(), "site": host.split(".")[-2].title(), "status": resp.status_code,
                                 "days_after_post": days}
            conn.execute("UPDATE listings SET data=? WHERE uid=?", (json.dumps(d), r["uid"]))
    conn.commit()
    return n

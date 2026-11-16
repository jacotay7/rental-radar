"""Redfin rentals (which also aggregates several other rental feeds) via Redfin's stingray JSON API.

The search area is the config bounding box, tiled into quarters whenever a tile hits the result cap.
"""
from .. import http

SEARCH = "https://www.redfin.com/stingray/api/v1/search/rentals"
PAGE_CAP = 350
PROPERTY_TYPES = {6: "house", 13: "townhouse", 3: "condo", 4: "multi-family (2-4 units)", 5: "apartment building"}


def _query(cfg, south, north, west, east):
    poly = f"{west} {south},{east} {south},{east} {north},{west} {north},{west} {south}"
    params = {
        "al": 1, "isRentals": "true", "num_homes": PAGE_CAP,
        "poly": poly, "uipt": "1,2,3,4", "min_num_beds": cfg["size"]["min_beds"],
        "max_price": cfg["budget"]["hard_max"],
    }
    market = cfg["sources"]["redfin"].get("market")
    if market:
        params["market"] = market
    if cfg["pets"].get("dog"):
        params["dogs_allowed"] = "true"
    resp = http.get(SEARCH, params=params, min_interval=2.0)
    resp.raise_for_status()
    return resp.json().get("homes", [])


def _parse(home, cfg) -> dict:
    hd, rx = home["homeData"], home["rentalExtension"]
    addr = hd.get("addressInfo", {})
    c = addr.get("centroid", {}).get("centroid", {})
    price = rx.get("rentPriceRange", {})
    beds = rx.get("bedRange", {})
    sqft = rx.get("sqftRange", {})
    baths = rx.get("bathRange", {})
    street = addr.get("formattedStreetLine", "")
    city = addr.get("city", "")
    state = addr.get("state") or cfg["search"]["state"]
    return {
        "uid": f"redfin:{rx['rentalId']}",
        "source": "redfin",
        "source_id": rx["rentalId"],
        "url": "https://www.redfin.com" + hd.get("url", ""),
        "title": rx.get("propertyName") or f"{street}, {city}",
        "price": price.get("min"),
        "price_max": price.get("max"),
        "beds": beds.get("max"),
        "beds_min": beds.get("min"),
        "baths": baths.get("max"),
        "sqft": sqft.get("max") or None,
        "lat": c.get("latitude"),
        "lon": c.get("longitude"),
        "address": f"{street}, {city}, {state} {addr.get('zip', '')}".strip(),
        "city": city,
        "housing_type": PROPERTY_TYPES.get(hd.get("propertyType"), "unknown"),
        "units_available": rx.get("numAvailableUnits"),
        "body": rx.get("description", ""),
        "posted_at": rx.get("freshnessTimestamp") or rx.get("lastUpdated"),
        "dogs_ok": bool(cfg["pets"].get("dog")),  # the search filtered on dogs_allowed
        "feed": rx.get("feedOriginalSource"),
        "photos": [],
        "contact": {
            "phone": rx.get("desktopPhone") or rx.get("paidPhone"),
            "name": rx.get("propertyName"),
            "external_url": rx.get("rentalPropertyExternalUrl"),
            "note": "Call/text the number, or use 'Request a tour' / 'Contact' on the Redfin page.",
        },
    }


def search(cfg, conn=None) -> list[dict]:
    b = cfg["search"]["bbox"]
    tiles = [(b["south"], b["north"], b["west"], b["east"])]
    out: dict[str, dict] = {}
    while tiles:
        s, n, w, e = tiles.pop()
        homes = _query(cfg, s, n, w, e)
        if len(homes) >= PAGE_CAP and (n - s) > 0.01:
            ms, mw = (s + n) / 2, (w + e) / 2
            tiles += [(s, ms, w, mw), (s, ms, mw, e), (ms, n, w, mw), (ms, n, mw, e)]
            continue
        for h in homes:
            try:
                rec = _parse(h, cfg)
            except KeyError:
                continue
            out[rec["uid"]] = rec
    return list(out.values())


def fetch_detail(listing: dict, cfg) -> dict:
    """Unit-level pricing for multi-unit buildings + amenity lists."""
    out = dict(listing)
    rid = listing["source_id"]
    r = http.get(f"https://www.redfin.com/stingray/api/v1/rentals/{rid}/amenities", min_interval=1.0)
    if r.status_code in (403, 429) or r.status_code >= 500:
        raise RuntimeError(f"HTTP {r.status_code} from Redfin (blocked or rate-limited?)")
    try:
        if r.ok:
            a = r.json()
            out["amenities"] = sorted(set(a.get("unitAmenities", []) + a.get("communityAmenities", [])
                                          + a.get("standardizedAmenities", []) + a.get("otherAmenities", [])))
    except (ValueError, TypeError, AttributeError):
        pass                      # no amenity data for this rental: not an error
    if (listing.get("units_available") or 1) > 1 or (listing.get("beds_min") != listing.get("beds")):
        r = http.get(f"https://www.redfin.com/stingray/api/v1/rentals/{rid}/floorPlans", min_interval=1.0)
        if r.status_code in (403, 429) or r.status_code >= 500:
            raise RuntimeError(f"HTTP {r.status_code} from Redfin (blocked or rate-limited?)")
        try:
            units = [u for grp in r.json().get("unitTypesByBedroom", [])
                     for ut in grp.get("availableUnitTypes", []) for u in ut.get("units", [])]
            ok = [u for u in units if (u.get("bedrooms") or 0) >= cfg["size"]["min_beds"]
                  and u.get("rentPrice") and u["rentPrice"] <= cfg["budget"]["hard_max"]]
            if ok:
                # Best-fit unit: most bedrooms, then most sqft, then cheapest
                ok.sort(key=lambda u: (-(u.get("bedrooms") or 0), -(u.get("sqft") or 0), u["rentPrice"]))
                u = ok[0]
                out.update(price=u["rentPrice"], beds=u.get("bedrooms"), sqft=u.get("sqft"),
                           baths=(u.get("fullBaths") or 0) + 0.5 * (u.get("halfBaths") or 0),
                           unit_name=u.get("name"), matching_units=len(ok))
            else:
                out["no_matching_unit"] = True
        except (ValueError, TypeError, AttributeError, KeyError):
            pass                  # unexpected floor-plan shape: keep the search-level data
    return out

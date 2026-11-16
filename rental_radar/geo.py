"""Location enrichment: geocoding, neighborhood lookup, commute (drive / transit / bike), nearby dog parks.

All reference data (neighborhoods, transit stations, parks) comes from the areas file
(see docs/regions.md), so nothing here is specific to one city.
"""
import logging
import math

from . import db, http

log = logging.getLogger("radar")
OSRM = "https://router.project-osrm.org/table/v1/driving/"
CENSUS_GEOCODER = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"


def km(lat1, lon1, lat2, lon2) -> float:
    """Great-circle distance in km."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def census_match(address: str) -> dict | None:
    """US Census one-line geocoder (free, no key, US addresses only). Uncached.
    Returns {lat, lon, matched, city, state, zip} or None."""
    try:
        r = http.get(CENSUS_GEOCODER, params={"address": address, "benchmark": "Public_AR_Current", "format": "json"},
                     min_interval=0.5)
        m = r.json()["result"]["addressMatches"]
        if m:
            comp = m[0].get("addressComponents") or {}
            return {"lat": m[0]["coordinates"]["y"], "lon": m[0]["coordinates"]["x"],
                    "matched": m[0].get("matchedAddress"), "city": (comp.get("city") or "").title() or None,
                    "state": comp.get("state"), "zip": comp.get("zip")}
    except Exception as e:
        log.debug("geocode failed %s: %s", address, e)
    return None


def census_geocode(address: str) -> tuple[float | None, float | None]:
    m = census_match(address)
    return (m["lat"], m["lon"]) if m else (None, None)


def geocode(conn, address: str) -> tuple[float | None, float | None]:
    """Cached Census geocode. Misses are cached too, so a bad address is only looked up once."""
    key = "geo:" + address.lower()
    val = db.cache_get(conn, key)
    if val is not None:
        if "|" not in val:
            return None, None
        lat, lon = val.split("|")
        return float(lat), float(lon)
    lat, lon = census_geocode(address)
    db.cache_set(conn, key, f"{lat}|{lon}" if lat is not None else "none")
    return lat, lon


def neighborhood(lat, lon, city: str | None, areas: dict) -> dict:
    """Closest curated neighborhood (relative to its radius), else the city default, else the global default."""
    best = None
    for n in areas["neighborhoods"]:
        d = km(lat, lon, n["lat"], n["lon"])
        rel = d / n.get("radius_km", 1.0)
        if best is None or rel < best[0]:
            best = (rel, n)
    if best:
        rel, n = best
        if rel <= 1.0:
            return {"name": n["name"], "score": n["score"], "pros": n.get("pros", ""), "cons": n.get("cons", ""),
                    "confidence": "in"}
        if rel <= 1.8:
            return {"name": f"near {n['name']}", "score": n["score"] - 0.5, "pros": n.get("pros", ""),
                    "cons": n.get("cons", ""), "confidence": "near"}
    c = (city or "").strip().lower()
    return {
        "name": (city or "Unknown area").title(),
        "score": areas["city_defaults"].get(c, areas.get("default_score", 5.5)),
        "pros": "",
        "cons": "Neighborhood not in your areas file; check it carefully" if areas["neighborhoods"] else "",
        "confidence": "city",
    }


def nearest_dog_park(lat, lon, areas: dict) -> tuple[str, float] | None:
    parks = areas.get("dog_parks") or []
    if not parks:
        return None
    return min(((p["name"], km(lat, lon, p["lat"], p["lon"])) for p in parks), key=lambda x: x[1])


def transit_commute(lat, lon, cfg) -> dict | None:
    """Best station-access + wait + ride + last-mile trip to the destination, in minutes.

    Access is a walk if the station is within `walk_km`, otherwise a short drive / drop-off
    (skipped when longer than `max_access_minutes`: at that point you'd just drive).
    """
    t = cfg["commute"]["transit"]
    if not t.get("enabled"):
        return None
    exits = t.get("exits") or {}
    best = None
    for st in cfg["_areas"]["transit_stations"]:
        d = km(lat, lon, st["lat"], st["lon"])
        if d > 10:
            continue
        if d <= t.get("walk_km", 1.0):
            access, how = d * 1.3 * 12, "walk"            # ~12 min/km with street detours
        else:
            access, how = d * 1.4 / 35 * 60 + 2, "drop-off"  # ~35 km/h local driving + 2 min drop-off
            if access > t.get("max_access_minutes", 12):
                continue
        for dest, ride in (st.get("rides") or {}).items():
            ex = exits.get(dest)
            # Being dropped at the exit station itself is really just driving to work
            if ex is None or st["code"] == dest:
                continue
            total = access + t.get("avg_wait", 6) + ride + ex.get("last_mile_minutes", 0)
            if best is None or total < best["minutes"]:
                best = {"minutes": round(total), "station": st["name"], "access": how,
                        "exit": ex.get("name", dest), "last_mile": ex.get("last_mile_mode", "walk")}
    return best


def drive_minutes(conn, points: list[tuple[float, float]], cfg) -> list[float | None]:
    """Free-flow drive minutes from each point to the destination via the public OSRM table API (cached)."""
    dest = (cfg["destination"]["lat"], cfg["destination"]["lon"])
    dkey = f"{dest[0]:.4f},{dest[1]:.4f}"
    keys = [f"{lat:.4f},{lon:.4f}" for lat, lon in points]
    cached = {}
    for k in set(keys):
        v = db.cache_get(conn, f"drive:{dkey}:{k}")
        if v is not None:
            cached[k] = float(v)
    todo = sorted({k for k in keys if k not in cached})
    for i in range(0, len(todo), 80):
        chunk = todo[i:i + 80]
        coords = ";".join(f"{k.split(',')[1]},{k.split(',')[0]}" for k in chunk) + f";{dest[1]},{dest[0]}"
        n = len(chunk)
        try:
            resp = http.get(OSRM + coords, params={"sources": ";".join(map(str, range(n))), "destinations": str(n)},
                            min_interval=1.5)
            durs = resp.json()["durations"]
        except Exception as e:
            log.debug("OSRM failed: %s", e)
            continue
        for k, row in zip(chunk, durs):
            if row and row[0] is not None:
                cached[k] = row[0] / 60
                db.cache_set(conn, f"drive:{dkey}:{k}", cached[k])
    conn.commit()
    return [cached.get(k) for k in keys]


def commute(lat, lon, free_flow_drive, cfg) -> dict:
    c = cfg["commute"]
    dest = cfg["destination"]
    f = c["rush_hour_factor"]
    if free_flow_drive is not None:
        drive = round(free_flow_drive * f + c.get("parking_minutes", 3))
    else:
        # Fallback if OSRM is unavailable: straight line x 1.3 detour at ~60 km/h, plus 5 min
        drive = round(km(lat, lon, dest["lat"], dest["lon"]) * 1.3 * f + 5)
    transit = transit_commute(lat, lon, cfg)
    best = min(m for m in [drive, transit and transit["minutes"]] if m is not None)
    d = km(lat, lon, dest["lat"], dest["lon"])
    bike = round(d * 1.3 / 15 * 60) if d <= c.get("bike_max_km", 6) else None  # ~15 km/h with detours
    return {"drive": drive, "transit": transit, "bike": bike, "best": best}

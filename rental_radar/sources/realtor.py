"""Realtor.com rentals via the HomeHarvest library (many agent-listed houses, plus structured pet policies).

Searched city by city from `search.cities`; results outside the bounding box are dropped later.
"""
import datetime
import logging
import re
import time

from . import Incomplete

log = logging.getLogger("radar")

STYLES = {"SINGLE_FAMILY": "house", "TOWNHOMES": "townhouse", "TOWNHOUSE": "townhouse", "CONDOS": "condo",
          "CONDO": "condo", "CONDO_TOWNHOME": "condo", "DUPLEX_TRIPLEX": "duplex",
          "MULTI_FAMILY": "multi-family (2-4 units)", "APARTMENT": "apartment", "MOBILE": "manufactured"}


def _phones(obj) -> list[str]:
    out = []
    for ph in (getattr(obj, "phones", None) or []):
        num = ph.get("number") if isinstance(ph, dict) else getattr(ph, "number", None)
        if num:
            out.append(num)
    return out


def _parse(p, cfg) -> dict | None:
    pp = p.pet_policy
    desc = p.description
    addr = p.address
    price, beds, sqft, baths, unit_avail = p.list_price, desc.beds if desc else None, \
        desc.sqft if desc else None, None, None
    if desc and desc.baths_full is not None:
        baths = desc.baths_full + 0.5 * (desc.baths_half or 0)
    if p.units:
        ok = []
        for u in p.units:
            ud = u.description
            if ud and (ud.beds or 0) >= cfg["size"]["min_beds"] and u.list_price \
                    and u.list_price <= cfg["budget"]["hard_max"]:
                ok.append(u)
        if ok:
            ok.sort(key=lambda u: (-(u.description.beds or 0), -(u.description.sqft or 0), u.list_price))
            u = ok[0]
            price, beds, sqft = u.list_price, u.description.beds, u.description.sqft
            baths = u.description.baths
            if u.availability and u.availability.date:
                unit_avail = u.availability.date
        elif not price:
            return None
    price = price or p.list_price_min
    if not price or price > cfg["budget"]["hard_max"] or (beds or 0) < cfg["size"]["min_beds"]:
        return None
    attrs = []
    for d in (p.details or []):
        cat = d.get("category") if isinstance(d, dict) else getattr(d, "category", "")
        texts = d.get("text") if isinstance(d, dict) else getattr(d, "text", [])
        attrs += [f"{cat}: {t}" for t in (texts or [])][:15]
    attrs += [t.replace("_", " ") for t in (p.tags or [])]
    adv = p.advertisers
    agent = adv.agent if adv else None
    office = adv.office if adv else None
    phones = _phones(agent) + _phones(office)
    photos = [str(ph.href) for ph in (p.photos or [])[:6] if getattr(ph, "href", None)]
    if not photos and desc and desc.primary_photo:
        photos = [str(desc.primary_photo)]
    body = (desc.text if desc else "") or ""
    if pp and pp.dogs_large:
        body += "\nLarge dogs allowed."
    if pp and pp.cats:
        body += "\nCats allowed."
    available = None
    if unit_avail:
        available = f"available {unit_avail:%b %d}".lower()
    for a in attrs:
        m = re.search(r"Availability Date: (\d{4}-\d{2}-\d{2})", a)
        if m:
            available = f"available {datetime.date.fromisoformat(m.group(1)):%b %d}".lower()
    if pp is not None and pp.dogs is False:
        body += "\nRealtor.com pet policy: no dogs."

    return {
        "uid": f"realtor:{p.property_id}",
        "source": "realtor",
        "source_id": str(p.property_id),
        "url": str(p.property_url),
        "title": (desc.name if desc and desc.name else None) or (addr.full_line if addr else ""),
        "price": int(price),
        "beds": beds,
        "baths": baths,
        "sqft": sqft,
        "lat": p.latitude,
        "lon": p.longitude,
        "address": f"{addr.full_line}, {addr.city}, {cfg['search']['state']} {addr.zip}" if addr else None,
        "city": addr.city if addr else None,
        "housing_type": STYLES.get(str(desc.style.value if hasattr(desc.style, "value") else desc.style), "unknown")
        if desc and desc.style else "unknown",
        "body": body,
        "attrs": attrs,
        "available_text": available,
        "posted_at": p.list_date.isoformat() if p.list_date else None,
        "photos": photos,
        "dogs_ok": bool(pp and pp.dogs),
        "cats_ok": bool(pp and pp.cats),
        "dog_policy_unknown": pp is None or pp.dogs is None,
        "feed": p.mls,
        "estimated_value": p.estimated_value,
        "contact": {
            "name": " / ".join(x for x in [agent.name if agent else None, office.name if office else None] if x),
            "phone": phones[0] if phones else None,
            "email": (agent.email if agent else None) or (office.email if office else None),
            "note": "Use 'Contact property' / 'Request a tour' on the Realtor.com page"
                    + (" (call the agent)." if phones else "."),
        },
    }


def search(cfg, conn=None) -> list[dict]:
    from homeharvest import scrape_property  # imported here: it pulls in pandas, slow for every command
    out: dict[str, dict] = {}
    failed = []
    for city in cfg["search"]["cities"]:
        try:
            props = scrape_property(location=f"{city}, {cfg['search']['state']}", listing_type="for_rent", return_type="pydantic",
                                    beds_min=cfg["size"]["min_beds"], price_max=cfg["budget"]["hard_max"])
        except Exception as e:
            log.warning("realtor %s failed: %s", city, e)
            failed.append(city)
            continue
        for p in props:
            try:
                rec = _parse(p, cfg)
            except Exception as e:
                log.debug("realtor parse error: %s", e)
                continue
            if rec and rec["lat"] is not None:
                out[rec["uid"]] = rec
        time.sleep(3)
    if failed and len(failed) == len(cfg["search"]["cities"]):
        raise RuntimeError(f"every city search failed ({', '.join(failed[:3])}...)")
    if failed:
        return Incomplete(out.values(), None, f"city searches failed: {', '.join(failed)}")
    return list(out.values())


def fetch_detail(listing: dict, cfg) -> dict:
    return listing  # search results already carry full details

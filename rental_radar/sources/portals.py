"""Property-manager vacancy portals (AppFolio, Buildium, Rentvine) listed in portals.yaml.

Many single-family homes are rented only through local property managers, and most of them publish
vacancies on a hosted portal. Those public pages are plain HTML/JSON meant to be browsed, so we poll
them politely. See docs/sources.md for how to find your region's managers.
"""
import html
import json
import logging
import re

import yaml

from .. import http
from ..config import resolve
from ..geo import geocode

log = logging.getLogger("radar")

BUILDIUM_TYPES = {"SingleFamily": "house", "CondoTownhome": "townhouse", "MultiFamily": "multi-family (2-4 units)"}


def _text(fragment: str) -> str:
    s = re.sub(r"(?i)<br\s*/?>|</(p|div|li)>", "\n", fragment)
    s = html.unescape(re.sub(r"<[^>]+>", " ", s))
    return re.sub(r"[ \t\xa0]+", " ", re.sub(r"\n\s*\n+", "\n", s)).strip()


def _money(s) -> int | None:
    m = re.search(r"\$?\s?([\d,]{3,7})(?:\.\d\d)?", str(s or ""))
    return int(m.group(1).replace(",", "")) if m else None


def _num(pattern, s, cast=float):
    m = re.search(pattern, s or "", re.I)
    return cast(m.group(1).replace(",", "")) if m else None


def _base(portal, uid, url, title, address, city, price, beds, baths, sqft, lat, lon, **kw) -> dict:
    rec = {
        "uid": f"pm:{portal['sub']}:{uid}", "source": "pm-portal", "source_id": f"{portal['sub']}:{uid}",
        "url": url, "title": title, "price": price, "beds": beds, "baths": baths, "sqft": sqft,
        "lat": lat, "lon": lon, "address": address, "city": city, "photos": [], "attrs": [],
        "housing_type": None, "body": "", "dogs_ok": False, "dog_policy_unknown": True,
        "contact": {"name": portal["name"], "note": f"Contact {portal['name']} via the listing page "
                                                    "(Contact Us / Schedule showing / Apply)."},
    }
    rec.update(kw)
    return rec


# ------------------------------------------------------------------ AppFolio

def _appfolio(portal, cfg, conn) -> list[dict]:
    base = f"https://{portal['sub']}.appfolio.com"
    r = http.get(base + "/listings", min_interval=1.5)
    if r.status_code != 200:
        log.warning("appfolio %s: HTTP %s", portal["sub"], r.status_code)
        return []
    s = r.text
    m = re.search(r"markers:\s*(\[.*?\])\s*,\s*\n", s, re.S)
    markers = json.loads(m.group(1)) if m else []
    cards = {}
    for card in re.split(r'<div class="listing-item result js-listing-item"', s)[1:]:
        link = re.search(r'href="(/listings/detail/[^"]+)"', card)
        if not link:
            continue
        pet = re.search(r'class="js-listing-pet-policy">(.*?)</span>\s*</p>', card, re.S)
        avail = re.search(r'js-listing-available">([^<]+)<', card)
        title = re.search(r'js-listing-title">\s*<a[^>]*>(.*?)</a>', card, re.S)
        cards[link.group(1)] = {
            "pet": _text(pet.group(1)) if pet else "",
            "available": avail.group(1).strip() if avail else "",
            "title": _text(title.group(1)) if title else "",
        }
    out = []
    for mk in markers:
        detail = mk.get("detail_page_url") or ""
        card = cards.get(detail, {})
        specs = mk.get("unit_specs") or ""
        addr = mk.get("address") or ""
        city = addr.split(",")[1].strip() if addr.count(",") >= 2 else None
        pet = card.get("pet", "")
        dogs_no = bool(re.search(r"dogs not allowed", pet, re.I))
        dogs_yes = bool(re.search(r"dogs allowed", pet, re.I)) and not dogs_no
        avail = card.get("available", "")
        body = pet + ("\npet policy: no dogs." if dogs_no else "")
        out.append(_base(
            portal, detail.rsplit("/", 1)[-1], base + detail, card.get("title") or addr, addr, city,
            _money(mk.get("rent_range")), _num(r"(\d+)\s*bd", specs, int), _num(r"([\d.]+)\s*ba", specs),
            _num(r"([\d,]+)\s*Sq", specs, int), mk.get("latitude"), mk.get("longitude"),
            photos=[mk["default_photo_url"]] if mk.get("default_photo_url") else [],
            available_text=("available now" if avail.upper() == "NOW" else f"available {avail}".lower()) if avail else None,
            body=body,
            dogs_ok=dogs_yes, dog_policy_unknown=not (dogs_yes or dogs_no), pm_no_dogs=dogs_no,
            platform="appfolio",
        ))
    return out


def _appfolio_detail(listing) -> dict:
    r = http.get(listing["url"], min_interval=1.5)
    if r.status_code != 200:
        return listing
    s = r.text
    out = dict(listing)
    m = re.search(r'class="listing-detail__description[^"]*">(.*?)</p>', s, re.S)
    if m:
        out["body"] = (_text(m.group(1)) + "\n" + listing.get("body", "")).strip()
    share = s.find("Share this listing")
    tail = _text(s[share:share + 1500]) if share > 0 else ""
    ph = re.search(r"\(?\b\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}\b", tail)
    if ph:
        out["contact"] = dict(out["contact"], phone=ph.group(0))
    pets = re.findall(r'pet-policy-item">([^<]+)<', s)
    if pets:
        out["attrs"] = out.get("attrs", []) + [f"Pet policy: {p.strip()}" for p in pets]
    return out


# ------------------------------------------------------------------ Buildium

def _buildium(portal, cfg, conn) -> list[dict]:
    base = f"https://{portal['sub']}.managebuilding.com"
    r = http.get(base + "/Resident/public/rentals", min_interval=1.5)
    if r.status_code != 200 or "/Resident/apps/portal/login" in r.url:
        log.warning("buildium %s: unavailable (%s)", portal["sub"], r.status_code)
        return []
    out = []
    for m in re.finditer(r'<a class="featured-listing[^"]*" href="([^"]+)"(.*?)</a>', r.text, re.S):
        href, inner = m.group(1), m.group(2)
        attr = dict(re.findall(r'data-([a-z-]+)="([^"]*)"', inner))
        title = re.search(r'featured-listing__title">(.*?)</h3>', inner, re.S)
        addr2 = re.search(r'featured-listing__address">(.*?)</p>', inner, re.S)
        avail = re.search(r'featured-listing__availability[^"]*">\s*(.*?)\s*</p>', inner, re.S)
        desc = re.search(r'featured-listing__description[^"]*">(.*?)<div', inner, re.S)
        img = re.search(r'<img src="([^"]+)"', inner)
        street = _text(title.group(1)) if title else ""
        cityline = _text(addr2.group(1)) if addr2 else ""
        address = f"{street}, {cityline}"
        lat, lon = geocode(conn, address)
        if lat is None:
            continue
        out.append(_base(
            portal, href.rsplit("/", 1)[-1], base + href, street, address, cityline.split(",")[0],
            _money(attr.get("rent")), int(float(attr.get("bedrooms") or 0)), float(attr.get("bathrooms") or 0) or None,
            int(float(attr.get("square-feet") or 0)) or None, lat, lon,
            housing_type=BUILDIUM_TYPES.get(attr.get("type")),
            available_text=_text(avail.group(1)).lower() if avail else None,
            body=_text(desc.group(1)) if desc else "",
            photos=[base + html.unescape(img.group(1))] if img else [],
            platform="buildium",
        ))
    return out


def _buildium_detail(listing) -> dict:
    r = http.get(listing["url"], min_interval=1.5)
    if r.status_code != 200:
        return listing
    out = dict(listing)
    descs = re.findall(r'class="unit-detail__description">(.*?)</div>', r.text, re.S)
    feats = re.search(r'class="unit-detail__features-list">(.*?)</ul>', r.text, re.S)
    text = "\n".join(_text(d) for d in descs)
    if feats:
        out["attrs"] = [x for x in _text(feats.group(1)).split("\n") if x.strip()]
    if text:
        out["body"] = text
    if re.search(r"\bdogs? (allowed|ok|welcome|considered)|pets? (allowed|ok|welcome|considered|negotiable)", text, re.I):
        out["dogs_ok"], out["dog_policy_unknown"] = True, False
    if re.search(r"\bno (pets|dogs)\b", text, re.I):
        out["dog_policy_unknown"] = False
        out["body"] += "\npet policy: no dogs."
    ph = re.search(r"\(?\b\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}\b", text)
    if ph:
        out["contact"] = dict(out["contact"], phone=ph.group(0))
    return out


# ------------------------------------------------------------------ Rentvine

def _rentvine(portal, cfg, conn) -> list[dict]:
    r = http.get(f"https://{portal['sub']}.rentvine.com/api/public/listings", min_interval=1.5)
    if r.status_code != 200:
        return []
    out = []
    for x in r.json():
        u, p, li = x.get("unit", {}), x.get("property", {}), x.get("listing", {})
        if not u.get("latitude"):
            continue
        dogs = li.get("acceptDogs")
        body = "\n".join(filter(None, [li.get("headline"), li.get("description"), li.get("petDescription")]))
        if dogs == "0":
            body += "\npet policy: no dogs."
        out.append(_base(
            portal, u.get("unitID"), f"https://{portal['sub']}.rentvine.com/public/listings",
            li.get("headline") or u.get("address"),
            f"{u.get('address')}{' ' + u['address2'] if u.get('address2') else ''}, {u.get('city')}, "
            f"{cfg['search']['state']} {u.get('postalCode')}",
            u.get("city"), _money(u.get("rent") or u.get("marketRent")), int(float(u.get("beds") or 0)),
            float(u.get("baths") or 0) or None, int(float(u.get("size") or 0)) or None,
            float(u["latitude"]), float(u["longitude"]),
            housing_type="house" if p.get("propertyTypeID") == "1" else None,
            body=body, dogs_ok=dogs == "1", dog_policy_unknown=dogs is None,
            available_text=f"available {li['availabilityDate']}" if li.get("availabilityDate") else None,
            photos=[x["image"]["large"]] if (x.get("image") or {}).get("large") else [],
            contact={"name": li.get("name") or portal["name"],
                     "phone": li.get("phone"), "email": li.get("email"),
                     "links": [li["applicationUrl"]] if li.get("applicationUrl") else [],
                     "note": "Call/email the manager or apply via the link."},
            platform="rentvine",
        ))
    return out


PLATFORMS = {"appfolio": _appfolio, "buildium": _buildium, "rentvine": _rentvine}


def search(cfg, conn=None) -> list[dict]:
    path = resolve(cfg, cfg["sources"]["pm-portal"].get("file", "portals.yaml"))
    if not path.exists():
        log.warning("pm-portal: %s not found", path)
        return []
    portals = (yaml.safe_load(path.read_text()) or {}).get("portals") or []
    out = []
    for portal in portals:
        try:
            found = PLATFORMS[portal["platform"]](portal, cfg, conn)
            out += [d for d in found if (d.get("beds") or 0) >= cfg["size"]["min_beds"]
                    and d.get("price") and d["price"] <= cfg["budget"]["hard_max"]]
        except Exception as e:
            log.warning("portal %s failed: %s", portal["sub"], e)
    if conn is not None:
        conn.commit()
    return out


def fetch_detail(listing: dict, cfg) -> dict:
    if listing.get("platform") == "appfolio":
        return _appfolio_detail(listing)
    if listing.get("platform") == "buildium":
        return _buildium_detail(listing)
    return listing

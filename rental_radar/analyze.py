"""Turn a normalized listing + location data into a 0-100 score, component scores, and pros/cons.

See docs/scoring.md for the full model and docs/scam-detection.md for the fraud checks.
Every component returns a value in 0..1 and appends human-readable pros/cons as a side effect.
"""
import re
from datetime import date

from . import geo
from .config import move_in as cfg_move_in
from .scam import address_fake, hijack_signals, scam_check, verify_flags


def _clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def _lerp(x, x0, x1, y0, y1):
    """Linear interpolation from (x0, y0) to (x1, y1), flat outside the range."""
    if x <= x0:
        return y0
    if x >= x1:
        return y1
    return y0 + (y1 - y0) * (x - x0) / (x1 - x0)


def _md(d: date) -> str:
    return f"{d:%b} {d.day}"


def listing_text(listing) -> str:
    parts = [listing.get("title") or "", listing.get("body") or "",
             " ".join(listing.get("attrs") or []), " ".join(listing.get("amenities") or [])]
    return "\n".join(parts).lower()


# ---------------------------------------------------------------- components

def price_score(listing, cfg, pros, cons):
    p = listing.get("price")
    b = cfg["budget"]
    if not p:
        cons.append("No price listed")
        return 0.4
    great = b.get("great") or round(b["target"] * 0.875)
    if p <= great:
        s = 1.0
    elif p <= b["target"]:
        s = _lerp(p, great, b["target"], 1.0, 0.8)
    elif p <= b["max"]:
        s = _lerp(p, b["target"], b["max"], 0.8, 0.5)
    else:
        s = _lerp(p, b["max"], b["hard_max"], 0.45, 0.1)
    if p <= b["target"]:
        pros.append(f"${p:,}/mo is within your ${b['target']:,} target")
    elif p <= b["max"]:
        cons.append(f"${p:,}/mo is at the top of your budget")
    else:
        cons.append(f"${p:,}/mo is OVER your ${b['max']:,} ceiling (only shown if otherwise exceptional)")
    if listing.get("price_max") and listing["price_max"] != p and not listing.get("unit_name"):
        cons.append(f"Price shown is the low end of ${p:,}-${listing['price_max']:,}; larger units may cost more")
    return s


def size_score(listing, cfg, pros, cons):
    beds = listing.get("beds") or 0
    sqft = listing.get("sqft")
    sz = cfg["size"]
    bed_s = 1.0 if beds >= sz["ideal_beds"] else (0.3 if beds >= sz["min_beds"] else 0.0)
    if sqft:
        sq_s = _lerp(sqft, sz["tiny_sqft"], sz["great_sqft"], 0.1, 1.0)
        if sqft >= sz["good_sqft"]:
            pros.append(f"Roomy: {sqft:,} sq ft")
        elif sqft < sz["min_sqft"]:
            cons.append(f"Small: only {sqft:,} sq ft")
    else:
        sq_s = 0.5
        cons.append("Square footage not listed")
    if beds >= sz["ideal_beds"]:
        pros.append(f"{beds} bedrooms")
    elif beds >= sz["min_beds"]:
        cons.append(f"Only {beds} bedroom{'s' if beds != 1 else ''} (you prefer {sz['ideal_beds']})")
    return 0.6 * bed_s + 0.4 * sq_s


def commute_score(c, cfg, pros, cons):
    drive, transit = c["drive"], c["transit"]
    best = c["best"]
    cc = cfg["commute"]
    mx = cc["max_minutes"]
    tname = cc["transit"].get("name", "Transit")
    dest = cfg["destination"].get("label") or "your destination"
    s = _lerp(best, cc["great_minutes"], mx, 1.0, 0.4) if best <= mx else _lerp(best, mx, mx + 10, 0.3, 0.0)
    transit_ok = transit and transit["minutes"] <= mx + 5
    if drive <= mx and transit_ok:
        s = _clamp(s + 0.1)
        pros.append(f"Two good commute options: ~{drive} min drive or ~{transit['minutes']} min by {tname} "
                    f"({transit['access']} at {transit['station']}, {transit['last_mile']} from {transit['exit']})")
    elif best <= 15:
        pros.append(f"Very short commute to {dest} (~{best} min)")
    if c.get("bike") and c["bike"] <= 20:
        pros.append(f"Bikeable: ~{c['bike']} min ride to {dest}")
    if best > mx:
        cons.append(f"Commute ~{best} min is over your {mx}-min limit")
    elif cc["transit"].get("enabled"):
        if not transit:
            cons.append(f"No practical {tname} option; you'd need to drive")
        elif not transit_ok:
            cons.append(f"{tname} works but is slow: ~{transit['minutes']} min ({transit['access']} at "
                        f"{transit['station']}). Mostly a driving commute")
    return s


SMALL_DOG_LB = 25   # "small dogs only" is not a dealbreaker at or below this weight


def pet_score(listing, text, lat, lon, cfg, pros, cons):
    """Dog and/or cat fit. Returns (score or None if you have no pets, flags)."""
    pets = cfg["pets"]
    if not pets.get("dog") and not pets.get("cat"):
        return None, []
    flags = []
    dealbreaker = False
    if pets.get("dog"):
        weight = pets.get("dog_weight_lb")
        s = 0.5 if listing.get("dogs_ok") else 0.25
        if listing.get("dog_policy_unknown"):
            s = 0.35
            cons.append("Pet policy not stated: ask about your dog before anything else")
        if weight:
            limits = [int(m) for m in re.findall(
                r"(?:under|up to|max(?:imum)?|less than|no more than|not to exceed|weight limit(?: of)?|<)\s*"
                r"(\d{2,3})\s*(?:lbs?|pounds)", text)]
            limits += [int(m) for m in re.findall(r"(\d{2,3})\s*(?:lbs?|pounds?)\s*(?:max|limit|or less|or under|and under)",
                                                  text)]
            limits = [x for x in limits if 10 <= x <= 200]
            if limits:
                lim = min(limits)
                if lim < weight:
                    s, dealbreaker = 0.05, True
                    cons.append(f"Dog weight limit {lim} lb: your {weight} lb dog is over it")
                elif lim < weight + 5:
                    s -= 0.2
                    cons.append(f"Dog weight limit {lim} lb: just above your dog's {weight} lb; bring a recent vet weight")
                else:
                    pros.append(f"Dog weight limit is {lim} lb (your {weight} lb dog fits)")
        if "pet policy: no dogs" in text:
            s, dealbreaker = 0.1, True
            cons.append("Listed pet policy is 'no dogs' (feeds are sometimes wrong; worth asking if otherwise perfect)")
        elif re.search(r"cats only|no dogs|\bno pets\b", text):
            s, dealbreaker = min(s, 0.1), True
            cons.append("Listing suggests no dogs")
        elif re.search(r"small (dogs?|pets?) (only|ok|welcome|allowed)|no (large|big) dogs", text):
            # "Small" usually means under about 25 lb; without a weight on file, flag it but don't rule it out
            if weight and weight <= SMALL_DOG_LB:
                pros.append(f"Small dogs welcome (yours is {weight} lb; confirm their limit)")
            elif weight:
                s, dealbreaker = min(s, 0.1), True
                cons.append(f"Listing suggests small dogs only (yours is {weight} lb)")
            else:
                s = min(s, 0.3)
                cons.append("Listing suggests small dogs only: check their size limit")
        if not dealbreaker and weight and weight >= 40 and re.search(
                r"large dogs? (are )?(welcome|ok|allowed|friendly)|(dogs?|pets?) of all sizes|all (dog|pet) sizes|"
                r"no (weight|size) (limit|restriction)|big dogs? (welcome|ok)", text):
            s += 0.3
            pros.append("Explicitly welcomes large dogs")
        elif not dealbreaker and re.search(r"dogs? (are )?(welcome|ok|allowed)|dog[- ]friendly", text):
            s += 0.1
        if re.search(r"breed restrictions?", text):
            flags.append("breed restrictions (check your dog's breed)")
        if re.search(r"dog (run|park|wash)|pet (spa|wash|park)|bark park", text):
            s += 0.1
            pros.append("On-site dog amenities (dog run/wash/park)")
        ht = (listing.get("housing_type") or "").lower()
        if ht in ("house", "duplex", "townhouse", "in-law", "cottage/cabin", "flat", "house/townhouse") \
                or "multi-family" in ht:
            s += 0.15
        park = geo.nearest_dog_park(lat, lon, cfg["_areas"])
        if park and park[1] <= 1.5:
            s += 0.1
            pros.append(f"{park[1]:.1f} km to {park[0]}")
    else:
        s = 0.6
    if pets.get("cat"):
        if re.search(r"no cats|dogs only|\bno pets\b", text):
            s, dealbreaker = min(s, 0.1), True
            cons.append("Listing suggests no cats")
        elif re.search(r"cats? (are )?(ok|welcome|allowed|considered)|cat[- ]friendly", text) or listing.get("cats_ok"):
            s += 0.2 if not pets.get("dog") else 0.05
            pros.append("Cats allowed")
    m = re.search(r"pet rent[^$\d]{0,15}\$?\s?(\d{2,3})", text)
    if m:
        flags.append(f"pet rent ~${m.group(1)}/mo")
    m = re.search(r"pet deposit[^$\d]{0,15}\$?\s?([\d,]{3,5})", text)
    if m:
        flags.append(f"pet deposit ~${m.group(1)}")
    if re.search(r"(one|1) pet (only|max)|pets? negotiable|case[- ]by[- ]case|with approval", text):
        flags.append("pets case-by-case: send a pet photo, vet records and references up front")
    if dealbreaker:
        flags.append("DEALBREAKER")
    return _clamp(s), flags


HOME_TYPES = {"house": 1.0, "cottage/cabin": 0.8, "duplex": 0.75, "townhouse": 0.75, "house/townhouse": 0.85,
              "flat": 0.65, "in-law": 0.6, "multi-family (2-4 units)": 0.65, "condo": 0.45, "loft": 0.3,
              "apartment": 0.25, "apartment building": 0.2}


def home_type_score(listing, text, cfg, pros, cons):
    table = {**HOME_TYPES, **{k.lower(): v for k, v in (cfg["scoring"].get("home_types") or {}).items()}}
    ht = (listing.get("housing_type") or "").lower()
    s = table.get(ht, 0.4)
    if ht == "house" and re.search(r"(#|\b(apt|unit)\b)\s*\w+", listing.get("address") or "", re.I):
        s = min(s, 0.6)  # a unit within a house (in-law / split house), not the whole house
    # Posters often tag houses/duplexes as "apartment"; trust the text when it is explicit
    if s < 0.75 and re.search(r"single[- ]family|\b(entire|whole|detached|stand-?alone) (house|home)\b", text):
        s = max(s, table.get("house", 1.0) * 0.9)
    elif s < 0.75 and re.search(r"\bduplex\b|\btownho(use|me)\b", text):
        s = max(s, table.get("duplex", 0.75))
    if s >= 0.9:
        pros.append("Whole house")
    elif s <= 0.3 and cfg["scoring"]["weights"].get("home_type"):
        cons.append(f"{(ht or 'apartment').capitalize()} (low on your home-type preferences)")
    return s


def yard_score(listing, text, pros, cons):
    if re.search(r"no (back)?yard|no outdoor space", text):
        cons.append("No yard")
        return 0.0
    shared_re = r"(shared|common|communal)( [\w-]+){0,2}? (back ?yard|yard|garden|outdoor)"
    private_text = re.sub(shared_re, " ", text)  # "shared fenced yard" is shared, not private
    if re.search(r"(private|fenced|own|large|big|spacious|huge|enclosed)[- ](back)?yard|fenced[- ]in|"
                 r"yard is (fully )?fenced", private_text):
        pros.append("Private/fenced yard")
        return 1.0
    if re.search(shared_re, text):
        pros.append("Shared yard")
        return 0.55
    if re.search(r"\b(back ?yard|yard|garden)\b", text):
        pros.append("Mentions a yard/garden (confirm private vs shared)")
        return 0.7
    if re.search(r"patio|deck|balcony|terrace", text):
        return 0.3
    if (listing.get("housing_type") or "").lower() == "house":
        return 0.5  # houses usually have some yard even if unmentioned
    return 0.0


MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def _nearest_year(month: int, day: int, ref: date) -> date | None:
    """The date with this month/day closest to `ref` (listings rarely state the year)."""
    best = None
    for y in (ref.year - 1, ref.year, ref.year + 1):
        try:
            d = date(y, month, day)
        except ValueError:
            continue
        if best is None or abs((d - ref).days) < abs((best - ref).days):
            best = d
    return best


def available_date(listing, text, ref: date) -> date | None:
    avail = (listing.get("available_text") or "").lower()
    m = re.search(r"available (?:on )?([a-z]{3})[a-z]* (\d{1,2})", avail) or \
        re.search(r"available (?:on |starting |from )?([a-z]{3})[a-z]*\.? (\d{1,2})", text)
    if m and m.group(1) in MONTHS:
        return _nearest_year(MONTHS[m.group(1)], int(m.group(2)), ref)
    m = re.search(r"available (?:on |starting |from )?(\d{4})-(\d{2})-(\d{2})", avail + " " + text)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    m = re.search(r"available (?:on |starting |from )?(\d{1,2})/(\d{1,2})", text)
    if m and 1 <= int(m.group(1)) <= 12 and 1 <= int(m.group(2)) <= 31:
        return _nearest_year(int(m.group(1)), int(m.group(2)), ref)
    return None


def availability(listing, text, cfg, pros, cons) -> float:
    """Point adjustment for fit with the move-in date (0 when no move-in date is configured)."""
    mi = cfg_move_in(cfg)
    if not mi:
        return 0
    a = cfg["scoring"]["availability"]
    found = available_date(listing, text, mi)
    avail = (listing.get("available_text") or "").lower()
    if found:
        delta = (found - mi).days
        if -a["early_days"] <= delta <= a["late_days"]:
            pros.append(f"Available {_md(found)}: lines up with your {_md(mi)} move")
            return a["match_bonus"]
        if delta < -a["early_days"]:
            cons.append(f"Available {_md(found)}: you may pay rent before you move, or negotiate a {_md(mi)} start")
            return -a["early_penalty"]
        cons.append(f"Not available until {_md(found)}")
        return -a["late_penalty"]
    if "available now" in avail or "available now" in text or "available immediately" in text:
        if (mi - date.today()).days > a["early_days"]:
            cons.append(f"Available now: ask if they'll hold it for a {_md(mi)} start (some will for a deposit)")
            return -a["available_now_penalty"]
    return 0


# ---------------------------------------------------------------- preferences

def stairs_penalty(text, listing, cfg, cons) -> float:
    """Walk-ups: a real cost with strollers, mobility needs, big dogs, or lots of groceries."""
    full = cfg["scoring"].get("walkup_penalty", 8)
    if not full:
        return 0.0
    ht = (listing.get("housing_type") or "").lower()
    if ht in ("house", "house/townhouse"):
        return 0.0
    if re.search(r"elevator", re.sub(r"no elevator", "", text)):
        return 0.0
    # Townhouses have interior stairs but a ground-level entry: not a walk-up
    if "townho" in ht or re.search(r"ground[- ](floor |level )?entr", text):
        return 0.0
    if re.search(r"walk-?up|no elevator|\b(3rd|third|4th|fourth|5th|fifth|top)[- ]floor|flights? of stairs", text):
        cons.append("Stairs/walk-up (no elevator)")
        return full
    if re.search(r"\b(2nd|second)[- ]floor (unit|flat|apartment|apt)|upstairs (unit|flat|apartment)|upper (unit|flat)",
                 text):
        cons.append("Upstairs unit (at least one flight of stairs)")
        return full / 2
    return 0.0


def area_adjustment(nb, cfg, pros, cons) -> float:
    """Negative = penalty for avoided areas, positive = bonus for favorites."""
    name = nb["name"].lower()
    sc = cfg["scoring"]
    if any(a.lower() in name for a in sc.get("avoid_areas") or []):
        cons.insert(0, f"{nb['name']} is on your avoid list")
        return -sc.get("avoid_area_penalty", 10)
    if any(a.lower() in name for a in sc.get("prefer_areas") or []):
        pros.insert(0, f"{nb['name']} is one of your favorite areas")
        return sc.get("prefer_area_bonus", 4)
    return 0.0


def amenity_notes(text, listing, pros, cons):
    if re.search(r"w/d in unit|washer.{0,5}dryer in unit|in-unit (laundry|washer)|washer and dryer included", text):
        pros.append("In-unit washer/dryer")
    elif re.search(r"no laundry", text):
        cons.append("No laundry on site")
    if re.search(r"garage|off-street parking|driveway|carport", text):
        pros.append("Off-street parking/garage")
    elif re.search(r"street parking", text):
        cons.append("Street parking only")
    if re.search(r"central (air|ac|a/c)|air condition", text):
        pros.append("Air conditioning")


# ---------------------------------------------------------------- main entry

def areas_rated(areas) -> bool:
    """True if the areas file rates anything (neighborhoods or city defaults)."""
    return bool(areas.get("neighborhoods") or areas.get("city_defaults"))



def analyze(listing, free_flow_drive, cfg) -> tuple[float, dict]:
    """Score one listing. Returns (score 0-100, analysis dict). See docs/scoring.md."""
    lat, lon = listing["lat"], listing["lon"]
    text = listing_text(listing)
    pros, cons = [], []
    nb = geo.neighborhood(lat, lon, listing.get("city"), cfg["_areas"])
    c = geo.commute(lat, lon, free_flow_drive, cfg)
    sc = cfg["scoring"]
    thr, top_thr = sc["email_threshold"], sc["top_pick_threshold"]

    comps = {}
    comps["price"] = price_score(listing, cfg, pros, cons)
    comps["commute"] = commute_score(c, cfg, pros, cons)
    pet, pet_flags = pet_score(listing, text, lat, lon, cfg, pros, cons)
    if pet is not None:
        comps["pets"] = pet
    comps["yard"] = yard_score(listing, text, pros, cons)
    comps["size"] = size_score(listing, cfg, pros, cons)
    comps["home_type"] = home_type_score(listing, text, cfg, pros, cons)
    # Without any neighborhood ratings every listing would get the same default score, which only squashes
    # the ranking toward the middle; drop the component instead (like `pets` without pets).
    rated = areas_rated(cfg["_areas"])
    if rated:
        comps["neighborhood"] = _clamp((nb["score"] - 3) / 6)  # 4.5 -> 0.25, 6 -> 0.5, 9+ -> 1.0
    if nb["pros"]:
        pros.append(f"{nb['name']}: {nb['pros']}")
    if nb["cons"]:
        cons.append(f"{nb['name']}: {nb['cons']}")
    if rated and nb["score"] < sc["low_area_score"]:
        cons.insert(0, f"{nb['name']} is rated {nb['score']:g}/10 in your areas file")
    amenity_notes(text, listing, pros, cons)
    layout_pen = stairs_penalty(text, listing, cfg, cons)
    area_adj = area_adjustment(nb, cfg, pros, cons)
    avail_adj = availability(listing, text, cfg, pros, cons)
    scam_pen, scam_flags = scam_check(listing, text, nb["score"], cfg, cons)
    verify_pen = verify_flags(listing, text, cfg, pros, cons)
    strong, soft = hijack_signals(listing, text, nb["score"], cfg, pros)
    caution = sum(w for w, _ in soft)
    if soft:
        label = "⚠ SEVERAL SCAM WARNING SIGNS" if caution >= 2 else "Scam check"
        cons.insert(0, f"{label}: " + "; ".join(s for _, s in soft))
    if strong:
        cons.insert(0, "⚠ LIKELY SCAM: " + "; ".join(strong) + ". Do not pay or send documents; verify the owner "
                       "through the county assessor and the listing agent first")
        scam_flags = scam_flags + strong
        scam_pen = 25
    scam_level = "likely" if scam_flags else "suspicious" if caution >= 3 else "caution" if caution >= 2 else None
    if listing.get("no_matching_unit"):
        cons.insert(0, f"No currently listed {cfg['size']['min_beds']}BR+ unit in budget at this building")

    w = {k: v for k, v in sc["weights"].items() if k in comps and v}
    total_w = sum(w.values()) or 1
    base = sum(comps[k] * w[k] for k in w) / total_w * 100
    score = base + avail_adj + area_adj - scam_pen - verify_pen - layout_pen
    beds = listing.get("beds") or 0
    if cfg["size"]["min_beds"] <= beds < cfg["size"]["ideal_beds"]:
        score -= cfg["size"].get("below_ideal_penalty", 0)

    # Hard caps: a listing that fails one of these can't be rescued by great components.
    if beds < cfg["size"]["min_beds"]:
        score = min(score, 30)
    if c["best"] > cfg["commute"]["max_minutes"] + 5:
        score = min(score, 40)
    if listing.get("no_matching_unit"):
        score = min(score, 35)
    if scam_flags:
        score = min(score, 40)
    if verify_pen >= 10 or scam_level == "suspicious":
        score = min(score, thr - 1)          # keep out of email
    if scam_level == "caution":
        score = min(score, top_thr - 1)      # digest only, never an instant top pick
    fake_address = address_fake(listing)
    if fake_address:
        score = min(score, thr - 10)         # never emailed
    if "DEALBREAKER" in pet_flags:
        pet_flags.remove("DEALBREAKER")
        score = min(score, thr - 10)         # pet not allowed: never emailed; still visible via `radar top`
    if listing.get("price") and listing["price"] > cfg["budget"]["max"] and base < cfg["budget"]["over_max_min_base"]:
        score = min(score, thr - 1)          # over budget only if exceptional

    analysis = {
        "components": {k: round(v, 2) for k, v in comps.items()},
        "base": round(base, 1),
        "adjustments": {"availability": avail_adj, "area": area_adj, "scam": 0 - scam_pen, "verify": 0 - verify_pen,
                        "layout": 0 - layout_pen},
        "neighborhood": nb,
        "commute": c,
        "pet_flags": pet_flags,
        "scam_flags": scam_flags,
        "scam_level": scam_level,
        "address_fake": fake_address,
        "caution_flags": [s for _, s in soft],
        "pros": pros,
        "cons": cons,
    }
    return round(_clamp(score, 0, 100), 1), analysis

"""Turn a normalized listing + location data into a 0-100 score, component scores, and pros/cons.

See docs/scoring.md for the full model and docs/scam-detection.md for the fraud checks.
Every component returns a value in 0..1 and appends human-readable pros/cons as a side effect.
"""
import re
from datetime import date

from . import geo
from .config import move_in as cfg_move_in


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
        elif re.search(r"small (dogs?|pets?) (only|ok|welcome|allowed)|no (large|big) dogs|cats only|no dogs|\bno pets\b",
                       text):
            s, dealbreaker = min(s, 0.1), True
            cons.append("Listing suggests small dogs only / no dogs")
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


# ---------------------------------------------------------------- fraud screening

SCAM_PATTERNS = [
    (r"western union|moneygram|wire (the )?(money|transfer|deposit)|gift card", "asks for wire/gift-card payment"),
    (r"out of (the )?(country|state) (for|on)|i am (currently )?(abroad|overseas)|missionary|deployed",
     "owner says they're away/abroad"),
    (r"keys? (will be )?(mailed|shipped|sent)", "offers to mail the keys"),
    (r"cannot show|can't show|unable to show|no showings", "can't show the property"),
    (r"god bless", "language common in scam posts"),
    (r"(send|pay) .{0,30}(deposit|payment) .{0,20}(before|to hold|to reserve)", "wants payment before a viewing/lease"),
]


def scam_check(listing, text, nb_score, cfg, cons) -> tuple[float, list[str]]:
    """Classic scam phrases and too-good-to-be-true prices. Returns (penalty, flags)."""
    sc = cfg["scam"]
    flags = [label for pat, label in SCAM_PATTERNS if re.search(pat, text)]
    p, beds = listing.get("price") or 0, listing.get("beds") or 0
    ht = (listing.get("housing_type") or "").lower()
    if p and beds >= 3 and ht == "house" and p < sc["too_cheap_house_rent"] and nb_score >= 7:
        flags.append(f"price (${p:,} for a {beds}BR house here) looks too good to be true")
    elif p and beds >= 2 and p < sc["too_cheap_rent"]:
        flags.append(f"unusually low price (${p:,})")
    if flags:
        cons.insert(0, "⚠ POSSIBLE SCAM: " + "; ".join(flags))
    return (25 if flags else 0), flags


# Do-it-yourself listing platforms: anyone can post, with no owner verification.
SELF_SERVE_FEEDS = {"TRBO": "TurboTenant", "AVAL": "Avail"}
SELF_SERVE_RE = r"turbotenant|avail\.co\b|rentredi|contact the landlord for more details"
SALE_LANGUAGE = [r"\bprimary suite\b", r"\bchef'?s kitchen\b",
                 r"\b(lacanche|miele|sub-?zero|wolf range|thermador|la cornue)\b",
                 r"\boffered at\b", r"\b(buyers?|sellers?|escrow|disclosures)\b", r"\bseismic retrofit",
                 r"\burban (sanctuary|oasis|retreat)\b", r"\bentertainer'?s (dream|delight)\b",
                 r"\bmove-in ready\b.*\bnew owners?\b"]
# Formatted numbers anywhere; bare 10-digit runs only after a phone label (post IDs are 10 digits too)
PHONE_RE = r"(?<![\d-])(?:\(([2-9]\d{2})\)\s?|([2-9]\d{2})[\s.-])(\d{3})[\s.-](\d{4})(?![\d-])" \
           r"|(?:phone|call|text|tel)\W{0,3}([2-9]\d{2})(\d{3})(\d{4})(?!\d)"
HIGH_VALUE_TYPES = ("house", "townhouse", "condo", "duplex", "house/townhouse")


def self_serve(listing, text) -> str | None:
    """Name of the do-it-yourself platform this was posted on, if any."""
    pc = listing.get("property_check") or {}
    for feed in (listing.get("feed"), pc.get("feed")):
        if feed in SELF_SERVE_FEEDS:
            return SELF_SERVE_FEEDS[feed]
    m = re.search(SELF_SERVE_RE, text + " " + ((listing.get("contact") or {}).get("external_url") or "").lower())
    if m:
        return "TurboTenant" if m.group(0) in ("turbotenant", "contact the landlord for more details") else m.group(0)
    if listing.get("source") == "craigslist":
        return "Craigslist"
    return None


def _days_since(iso) -> int | None:
    try:
        return (date.today() - date.fromisoformat(str(iso)[:10])).days
    except (TypeError, ValueError):
        return None


def _same_brokerage(listing, office) -> bool:
    """Is the rental posted by the brokerage that has the home for sale (a genuine rent-or-buy)?"""
    name = ((listing.get("contact") or {}).get("name") or "").lower()
    words = re.findall(r"[a-z]+", (office or "").lower())[:2]
    return bool(words) and " ".join(words) in name


def hijack_signals(listing, text, nb_score, cfg, pros) -> tuple[list[str], list[tuple[float, str]]]:
    """(strong flags, [(weight, warning)]) for copied / hijacked-listing scams. See docs/scam-detection.md."""
    sc = cfg["scam"]
    strong, soft = [], []
    p = listing.get("price") or 0
    ht = (listing.get("housing_type") or "").lower()
    pc = listing.get("property_check") or {}
    sm = listing.get("sale_match") or {}
    raw = (listing.get("body") or "") + "\n" + (pc.get("text") or "")
    full = text + "\n" + raw.lower()  # include the other portal's copy of the same rental

    # Rent vs. what the home is worth. Genuine single-family rentals rarely go below ~0.25%/month.
    value = listing.get("estimated_value") or pc.get("estimated_value")
    if p and value and 300_000 <= value <= 4_000_000 and ht in HIGH_VALUE_TYPES and \
            not re.search(r"\b(room for rent|private room|shared)\b", text):
        ratio = p / value * 100
        if ratio < sc["rent_to_value_likely_pct"]:
            strong.append(f"rent is only {ratio:.2f}% of the home's ~${value / 1e6:.2f}M value per month "
                          f"(genuine rentals rarely go below ~{sc['rent_to_value_caution_pct'] + 0.01:.2f}%)")
        elif ratio < sc["rent_to_value_caution_pct"]:
            soft.append((1, f"rent is low for a ~${value / 1e6:.2f}M home ({ratio:.2f}% of value per month)"))
    sqft = listing.get("sqft") or 0
    if p and sqft >= 1200 and ht in HIGH_VALUE_TYPES and nb_score >= 8 and p / sqft < sc["low_rent_per_sqft"] \
            and not value:
        soft.append((1.5 if p / sqft < sc["very_low_rent_per_sqft"] else 1,
                     f"${p / sqft:.2f}/sq ft is far below this neighborhood's market"))
    platform = self_serve(listing, full)

    # Sale history from our own index (sales.py), which remembers withdrawn listings.
    if sm.get("office") and _same_brokerage(listing, sm["office"]):
        pros.append(f"Rented by {sm['office']}, the brokerage that also has it for sale (rent-or-buy; a good sign)")
        sm = {}
    seen_days = _days_since(sm.get("last_seen"))
    if sm.get("status") in ("FOR_SALE", "PENDING", "CONTINGENT") and seen_days is not None:
        if seen_days <= 3:
            msg = (f"this address is listed FOR SALE right now (${sm.get('list_price') or 0:,.0f}"
                   f"{', ' + sm['office'] if sm.get('office') else ''})")
            lp = sm.get("list_price") or 0
            if (lp and p / lp < 0.003) or (platform and platform != "Craigslist"):
                strong.append(msg)  # cheap rent or a DIY posting on a home that's for sale: the hijack pattern
            else:
                soft.append((2, msg + ". Some owners offer rent-or-buy, but confirm with that listing agent"))
        elif seen_days <= 180:
            soft.append((1, f"this home was listed for sale until {sm['last_seen']} (${sm.get('list_price') or 0:,.0f}"
                            f"{', ' + sm['office'] if sm.get('office') else ''}). Owners do switch to renting, but "
                            "withdrawn sale listings are what scammers copy; confirm with that listing agent"))
    cf = sm.get("copied_from")
    if cf:
        if not cf["same_address"]:
            strong.append(f"the description is copied from the sale listing for {cf['address']} "
                          f"({cf['shared_phrases']} matching phrases), so the posted address is likely fake")
        elif cf.get("status") == "SOLD" and (_days_since(cf.get("sold_date")) or 9999) <= 365:
            strong.append(f"the description is copied from this home's sale listing, and it sold {cf['sold_date']}")
        else:
            soft.append((1, "the description is copied from this home's for-sale listing"))

    # Posting-pattern signals: none is damning alone; together they are the classic pattern.
    if platform:
        soft.append((0.5, f"posted on {platform} (anyone can list there, no owner verification)"))
    if ht == "house" and re.search(r"rent includes:[^\n]*\b(gas|electric)[^\n]*\bwater\b|all utilities (are )?included",
                                   full):
        soft.append((1, "all utilities included for a whole house (unusual)"))
    if re.search(r"[a-z,] (To apply|Contact the landlord)", raw):
        soft.append((1, "description is cut off mid-sentence with application text spliced in (pasted from elsewhere)"))
    hits = {m.group(0) for pat in SALE_LANGUAGE for m in [re.search(pat, full)] if m}
    if len(hits) >= 2:
        soft.append((1, f"reads like a for-sale listing ({', '.join(sorted(hits)[:3])})"))
    phones = {"".join(m) for m in re.findall(PHONE_RE, full)}  # unmatched groups are ""
    cp = "".join(ch for ch in ((listing.get("contact") or {}).get("phone") or "") if ch.isdigit())[-10:]
    if cp:
        phones.add(cp)
    if len(phones) >= 2 and listing.get("source") != "pm-portal":
        soft.append((1, f"lists {len(phones)} different phone numbers"))
    if re.search(r"available (now|immediately)", full):
        soft.append((0.5, "available immediately"))
    lr = listing.get("link_removed") or {}
    if lr and lr.get("days_after_post") is not None and lr["days_after_post"] <= 7:
        soft.append((1, f"the {lr.get('site') or 'listing'} posting was taken down {lr['days_after_post']} day(s) "
                        "after it went up (platforms remove reported fraud)"))
    return strong, soft


def verify_flags(listing, text, cfg, pros, cons) -> float:
    """'Double-check this' signals (data errors, address problems, for-sale homes). Returns a point penalty."""
    pen = 0
    p, sqft = listing.get("price") or 0, listing.get("sqft") or 0
    if p and sqft >= 1500 and p / sqft < cfg["scam"]["very_low_rent_per_sqft"]:
        cons.insert(0, f"Verify: ${p:,} for {sqft:,} sq ft is well below market. Could be rooms-for-rent, "
                       "a data error, or a hijacked listing")
        pen += 6
    if "please inquire on this site to schedule a showing" in text:
        cons.insert(0, "Verify: auto-generated aggregator text and no direct contact. Confirm the owner/manager "
                       "via county records before paying anything")
        pen += 4
    ac = listing.get("address_check") or {}
    if ac.get("status") == "not_found":
        cons.insert(0, f"⚠ ADDRESS NOT FOUND: '{ac['address']}' isn't in US Census or OpenStreetMap address data. "
                       "Fake addresses are a hallmark of copied-listing scams; verify the owner before going further")
        pen += 20
        listing["_address_fake"] = True
    elif ac.get("status") == "far":
        cons.insert(0, f"⚠ The posted address is {ac['km']} km from the map pin. Verify the real location")
        pen += 10
    pc = listing.get("property_check") or {}
    if pc.get("status") in ("FOR_SALE", "PENDING", "CONTINGENT"):
        cons.insert(0, f"⚠ This address is currently listed FOR SALE on Realtor.com "
                       f"(${pc.get('list_price') or 0:,.0f}). Copied for-sale listings are a common rental scam")
        pen += 15
    if pc.get("last_sold_date"):
        days = _days_since(pc["last_sold_date"])
        if days is not None and days <= 150:
            cons.insert(0, f"Verify: this home sold {pc['last_sold_date']} for ${pc.get('sold_price') or 0:,.0f}. "
                           "Just-sold homes are the most common source of copied rental scams; confirm the "
                           "landlord is the new owner (county records)")
            pen += 8
    if pc.get("status") == "FOR_RENT" and pc.get("list_price") and listing.get("source") != "realtor" \
            and not self_serve(listing, text + " " + (pc.get("text") or "").lower()):
        # Only an independent copy counts: two portals fed by the same DIY post prove nothing
        if abs(float(pc["list_price"]) - (listing.get("price") or 0)) <= 100:
            pros.append("Same rental also listed on Realtor.com at the same price (a good sign it's genuine)")
    if re.search(r"studio and one[- ]bedroom|studios? (and|&) 1[- ]?(bed|br)", text) and \
            not re.search(r"(two|three|2|3)[- ]?(bed|br)", text):
        cons.insert(0, "Listing text describes studios/1-bedrooms only; the bedroom count in the feed looks wrong")
        pen += 30
    # Whole units often say "great for roommates"; only seeking-a-roommate posts count
    if re.search(r"\b(room for rent|private room|shared (house|home|apartment)|room in|"
                 r"(looking for|seeking|need) (a |one |two |\d )?(new )?roommates?|roommates? wanted|"
                 r"(current|existing|other) roommates?|live with \w+ roommates?)\b", text):
        cons.insert(0, "Looks like a room in a shared home, not a whole unit")
        pen += 30
    return pen


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
    if listing.get("_address_fake"):
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
        "caution_flags": [s for _, s in soft],
        "pros": pros,
        "cons": cons,
    }
    return round(_clamp(score, 0, 100), 1), analysis

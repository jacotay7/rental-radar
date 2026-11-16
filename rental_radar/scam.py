"""Fraud screening: scam phrases, too-cheap prices, hijacked for-sale listings, and "verify this" flags.

Each check returns penalties/flags and adds plain-English warnings to the listing's cons.
`analyze.analyze` combines them into the score and scam level. See docs/scam-detection.md.
"""
import re
from datetime import date


def address_fake(listing) -> bool:
    """The posted address doesn't exist in Census or OpenStreetMap data (set by verify.check)."""
    return (listing.get("address_check") or {}).get("status") == "not_found"


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

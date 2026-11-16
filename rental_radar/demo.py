"""`radar demo`: score a handful of made-up listings with the East Bay example and render the email.

No config, credentials or network needed. Everything here is fictional: the listings, prices, contacts
and links (example.com). Locations are real neighborhoods so the neighborhood, commute and dog-park
logic has something to work with.
"""
import base64
from pathlib import Path

from . import analyze, emailer, geo, notify
from .config import EXAMPLES_DIR, load_config

PALETTE = ["#8fb8a8", "#c9a66b", "#9aa9c9", "#c98f8f", "#a3b98b", "#b39ac9", "#c9b28f", "#8fc0c9"]


def _photo(i: int, label: str) -> str:
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="560" height="130">'
           f'<rect width="560" height="130" fill="{PALETTE[i % len(PALETTE)]}"/>'
           '<path d="M245 85 L280 55 L315 85 Z M255 85 h50 v30 h-50 Z" fill="#fff" opacity=".85"/>'
           f'<text x="280" y="34" font-family="Arial" font-size="18" text-anchor="middle" fill="#fff">'
           f'Demo photo · {label}</text></svg>')
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode()).decode()


def _l(i, name, lat, lon, price, beds, baths, sqft, kind, title, body, **kw):
    d = {"uid": f"demo:{i}", "source": kw.pop("source", "craigslist"), "url": f"https://example.com/demo/{i}",
         "title": title, "price": price, "beds": beds, "baths": baths, "sqft": sqft, "lat": lat, "lon": lon,
         "address": f"(demo listing) {name}, CA", "city": kw.pop("city", "Oakland"), "housing_type": kind,
         "body": body, "dogs_ok": kw.pop("dogs_ok", True), "photos": [_photo(i, name)],
         "contact": {"name": "Demo Landlord", "phone": "5550100" + str(100 + i)[-3:]}}
    d.update(kw)
    return d


LISTINGS = [
    _l(1, "Rockridge", 37.8445, -122.2505, 3950, 3, 1, 1180, "house",
       "Craftsman 3BR near College Ave, fenced yard",
       "Charming craftsman. Dogs OK with deposit. Private fenced backyard. In-unit washer and dryer, "
       "street parking. Available March 20. Owner-managed; tours this weekend."),
    _l(2, "Albany", 37.889, -122.297, 3750, 3, 1.5, 1400, "house",
       "Albany bungalow, 3BR, garden, walk to Solano Ave",
       "Sunny bungalow with a private yard and garden. Dogs OK (no weight limit). Washer/dryer in unit, "
       "driveway parking. Available Feb 1.", city="Albany", source="redfin"),
    _l(3, "Temescal", 37.8352, -122.2628, 3400, 2, 1, 1050, "townhouse",
       "Temescal 2BR townhouse with shared yard",
       "Bright townhouse, walk to shops and BART. Shared fenced yard. Dogs under 80 lbs considered. "
       "Street parking. Available March 1."),
    _l(4, "El Cerrito hills", 37.917, -122.300, 3600, 3, 2, 1500, "house",
       "El Cerrito view home, 3BR/2BA, dog friendly",
       "Mid-century home with bay views. Pets welcome, large yard. Garage. Available March 15.",
       city="El Cerrito", source="realtor"),
    _l(5, "Alameda", 37.7645, -122.2490, 4100, 3, 2, 1700, "house",
       "Alameda Victorian, 3BR + office",
       "Restored Victorian near Park Street. Dogs considered. Backyard with patio. Laundry in basement. "
       "Available April 1.", city="Alameda", source="pm-portal"),
    _l(6, "Grand Lake", 37.8122, -122.2468, 3300, 2, 1, 950, "apartment",
       "Grand Lake 2BR, 3rd floor walk-up",
       "Top-floor (3rd floor walk-up) apartment near the lake. Cats and small dogs OK. Shared laundry. "
       "Available March 1."),
    _l(7, "Elmwood", 37.8585, -122.2522, 1850, 4, 3, 2100, "house",
       "4BR Elmwood house, all utilities included!!",
       "I am currently overseas on a mission and cannot show the house. Large dogs welcome. "
       "Send the deposit by wire transfer and I will mail you the keys.", city="Berkeley"),
    _l(8, "Piedmont Ave", 37.8262, -122.2502, 3500, 2, 2, 1200, "condo",
       "Piedmont Ave condo, 2BR/2BA",
       "Updated condo with balcony. Dogs under 25 lbs only. In-unit laundry, parking space. Available Feb 25."),
]


def build(n_top: int = 10):
    cfg = load_config(EXAMPLES_DIR / "east-bay" / "config.yaml")
    dest = cfg["destination"]
    scored = []
    for d in LISTINGS:
        # Offline stand-in for the OSRM drive time: straight-line distance at ~40 km/h with a detour factor.
        ff = geo.km(d["lat"], d["lon"], dest["lat"], dest["lon"]) * 1.3 / 40 * 60
        s, a = analyze.analyze(d, ff, cfg)
        scored.append(({"uid": d["uid"], "score": s, "emailed_at": None}, d, a))
    scored.sort(key=lambda x: -x[0]["score"])
    thr = cfg["scoring"]["email_threshold"]
    items = [x for x in scored if x[0]["score"] >= thr]
    hidden = [x for x in scored if x[0]["score"] < thr]
    return cfg, scored, items, hidden


def why_hidden(row, d, a) -> str:
    if a.get("scam_level") == "likely":
        return "likely scam: " + "; ".join(a.get("scam_flags") or [])
    nb = a["neighborhood"]["name"] + ":"
    reasons = [c for c in a["cons"] if not c.startswith(("Scam check", "⚠", nb))]
    return f"score {row['score']:.0f}: " + "; ".join(reasons[:3]).lower()


def run(out: Path, open_browser: bool = True, chat: bool = False) -> Path:
    cfg, scored, items, hidden = build()
    intro = (f"Demo: {len(LISTINGS)} made-up listings scored with the East Bay example config "
             f"(2027 move-in, 3BR, 65 lb dog, commuting to downtown Oakland). {len(items)} made the cut. "
             f"Screened out: " + "; ".join(f"“{d['title']}” ({why_hidden(r, d, a)})" for r, d, a in hidden)
             + ". Run `radar init` to set up your own search.")
    msg = emailer.build_email(items, cfg, intro=intro, kind="digest", top=items)
    out.write_text(msg.get_body(("html",)).get_content())
    print(f"Subject: {msg['Subject']}\n")
    for r, d, a in scored:
        mark = "  ✉" if (r, d, a) in items else "   "
        print(f"{mark} {r['score']:5.1f}  {emailer.headline(d, a)[:60]:60}  {d['title'][:40]}")
    print("\n✉ = would be sent. Score breakdowns are at the bottom of each card.")
    if chat:
        print("\n--- chat / push version ---\n" + notify.chat_preview(items, cfg))
    print(f"\nWrote {out}")
    if open_browser:
        import webbrowser
        webbrowser.open(out.resolve().as_uri())
    return out

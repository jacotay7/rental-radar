"""Configuration: config.yaml merged over built-in defaults, .env secrets, and the areas file.

Every path in config.yaml (data_dir, areas_file, sources.pm-portal.file) is relative to the
directory that holds config.yaml, so one checkout can run several independent searches.
"""
import copy
import os
from datetime import date
from pathlib import Path

import yaml

PACKAGE_DIR = Path(__file__).resolve().parent
REPO_DIR = PACKAGE_DIR.parent
EXAMPLES_DIR = REPO_DIR / "examples"

# Everything here can be overridden in config.yaml. Keep in sync with docs/configuration.md.
DEFAULTS: dict = {
    "search": {
        "label": "Rental search",      # shown in email subjects and headings
        "state": "CA",                 # two-letter US state; used to parse and geocode addresses
        "bbox": None,                  # {south, north, west, east}; scraped listings outside are dropped
        "cities": [],                  # "City" names searched on Realtor.com + the sale-history index
        "alert_radius_km": 50,         # saved-search alert listings are kept within this distance of the destination
        "move_in": None,               # "YYYY-MM-DD" or null to ignore availability dates
    },
    "destination": {                   # where you commute to (work, school, ...)
        "label": "work",
        "address": None,
        "lat": None,
        "lon": None,
    },
    "budget": {
        "great": None,                 # full price marks at or below this (default: 87.5% of target)
        "target": None,
        "max": None,
        "hard_max": None,              # listings above this are never collected
        "over_max_min_base": 80,       # between max and hard_max, only listings this good overall get emailed
    },
    "size": {
        "min_beds": 1,
        "ideal_beds": 2,
        "below_ideal_penalty": 6,      # points off for min_beds <= beds < ideal_beds
        "tiny_sqft": 600,              # size curve: 0.1 at tiny_sqft ... 1.0 at great_sqft
        "min_sqft": 750,               # below this is listed as a con
        "good_sqft": 1000,             # at or above this is listed as a pro
        "great_sqft": 1300,
    },
    "pets": {
        "dog": False,                  # search only dog-friendly listings and score the dog policy
        "dog_weight_lb": None,         # listings with a lower weight limit are dealbreakers
        "cat": False,
    },
    "commute": {
        "max_minutes": 35,             # best of drive / transit; well over this caps the score
        "great_minutes": 12,           # full commute marks at or below this
        "rush_hour_factor": 1.35,      # free-flow drive time (OSRM) x this = rush-hour estimate
        "parking_minutes": 3,          # added to every drive
        "bike_max_km": 6,              # show a bike estimate when the destination is this close
        "transit": {
            "enabled": False,
            "name": "Transit",         # e.g. BART, Metro, the T
            "avg_wait": 6,
            "walk_km": 1.0,            # walk to a station this close; otherwise get dropped off / drive
            "max_access_minutes": 12,  # longer than this to reach a station and you'd just drive
            "exits": {},               # station code -> {name, last_mile_minutes, last_mile_mode}
        },
    },
    "scoring": {
        "weights": {"price": 15, "commute": 15, "pets": 10, "neighborhood": 20, "size": 15,
                    "home_type": 12, "yard": 8},
        "email_threshold": 72,         # new listings at/above this go into the daily digest
        "top_pick_threshold": 80,      # new listings at/above this are emailed right away
        "walkup_penalty": 8,           # walk-up / 3rd floor+; half for a 2nd-floor unit; 0 disables
        "avoid_areas": [],             # substrings of neighborhood names you don't want
        "avoid_area_penalty": 10,
        "prefer_areas": [],            # substrings of neighborhood names you love
        "prefer_area_bonus": 4,
        "low_area_score": 5.5,         # areas rated below this (1-10) get a warning con
        "home_types": {},              # override the home-type preference table (see analyze.HOME_TYPES)
        "availability": {
            "early_days": 21,          # available up to this many days before move-in: still a match
            "late_days": 10,           # ... or this many days after
            "match_bonus": 5,
            "early_penalty": 3,
            "late_penalty": 8,
            "available_now_penalty": 2,
        },
    },
    "scam": {
        "rent_to_value_likely_pct": 0.20,   # monthly rent below this % of home value = likely scam
        "rent_to_value_caution_pct": 0.24,  # ... below this = warning sign
        "too_cheap_rent": None,             # any 2BR+ below this is flagged (default: 45% of budget.target)
        "too_cheap_house_rent": None,       # a 3BR+ house in a well-rated area below this (default: 65% of target)
        "low_rent_per_sqft": 2.0,           # $/sqft warning for 1,200+ sqft homes in top-rated areas
        "very_low_rent_per_sqft": 1.6,      # $/sqft "verify this" for 1,500+ sqft homes anywhere
    },
    "email": {
        "to": [],
        "from": None,                  # default: SMTP_USER
        "smtp_host": "smtp.gmail.com",
        "smtp_port": 465,              # 465 = implicit TLS; 587 = STARTTLS
        "max_per_email": 15,           # top-pick alert emails
        "digest_max": 30,              # daily digest: new listings
        "digest_top": 20,              # daily digest: "top N still available" running list
        "safety_tips": [],             # extra region-specific tips appended to the built-in list
    },
    "sources": {
        "craigslist": {"enabled": False, "site": None, "postal": None, "distance_mi": 20},
        "redfin": {"enabled": True, "market": None},
        "realtor": {"enabled": True},
        "pm-portal": {"enabled": False, "file": "portals.yaml"},
        "alerts": {
            "enabled": False,
            "imap_host": "imap.gmail.com",
            "lookback_days": 14,
            # Sites whose saved searches you set up with your pet/home-type filters, so their
            # alert emails are trusted to match them (others are treated as "policy unknown").
            "filtered_sites": ["zillow", "hotpads", "trulia", "apartments.com", "redfin-alert", "realtor-alert"],
            "site_housing": {},        # e.g. {zillow: house/townhouse} if that saved search excludes apartments
            "extra_senders": {},       # extra sender domains to read, e.g. {examplenewsletter.org: newsletter}
        },
    },
    "areas_file": "areas.yaml",
    "data_dir": "data",
    "schedule": {
        "unit_prefix": "rental-radar",
        "collect": "*-*-* 06..22:05:00",   # systemd OnCalendar: hourly, 6:05am-10:05pm
        "digest": "*-*-* 07:30:00",        # daily digest
        "timezone": None,                  # e.g. America/Los_Angeles; default: the machine's zone
    },
}


def deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def config_path(arg: str | None = None) -> Path:
    return Path(arg or os.environ.get("RADAR_CONFIG") or "config.yaml").expanduser().resolve()


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


def secret(*names: str) -> str | None:
    """First non-empty environment variable among `names` (new name first, legacy fallbacks after)."""
    for n in names:
        if os.environ.get(n):
            return os.environ[n]
    return None


def smtp_credentials() -> tuple[str | None, str | None]:
    return secret("SMTP_USER", "GMAIL_USER"), secret("SMTP_PASSWORD", "GMAIL_APP_PASSWORD")


def imap_credentials() -> tuple[str | None, str | None]:
    user = secret("IMAP_USER", "SMTP_USER", "GMAIL_USER")
    pw = secret("IMAP_PASSWORD", "SMTP_PASSWORD", "GMAIL_APP_PASSWORD")
    return user, pw


def _normalize_sources(raw: dict) -> dict:
    """Accept `craigslist: true` shorthand as well as the full mapping."""
    out = {}
    for k, v in (raw or {}).items():
        out[k] = {"enabled": bool(v)} if isinstance(v, bool) else v
    return out


def load_areas(path: Path) -> dict:
    empty = {"neighborhoods": [], "city_defaults": {}, "default_score": 5.5, "transit_stations": [], "dog_parks": []}
    if not path.exists():
        return empty
    data = yaml.safe_load(path.read_text()) or {}
    out = deep_merge(empty, data)
    out["city_defaults"] = {str(k).lower(): float(v) for k, v in (out.get("city_defaults") or {}).items()}
    return out


def load_config(path: Path | str | None = None) -> dict:
    path = config_path(str(path) if path else None)
    if not path.exists():
        raise FileNotFoundError(f"{path} not found. Run `radar init` to create one.")
    base = path.parent
    load_env(base / ".env")
    user = yaml.safe_load(path.read_text()) or {}
    if "sources" in user:
        user["sources"] = _normalize_sources(user["sources"])
    cfg = deep_merge(DEFAULTS, user)
    cfg["_path"] = str(path)
    cfg["_dir"] = str(base)
    cfg["_data_dir"] = str((base / cfg["data_dir"]).resolve())
    cfg["_areas"] = load_areas(base / cfg["areas_file"])
    return finalize(cfg)


def finalize(cfg: dict) -> dict:
    """Fill in defaults that are derived from other settings."""
    b, sc = cfg["budget"], cfg["scam"]
    if b.get("target"):
        b["great"] = b.get("great") or round(b["target"] * 0.875)
        sc["too_cheap_rent"] = sc.get("too_cheap_rent") or round(b["target"] * 0.45, -1)
        sc["too_cheap_house_rent"] = sc.get("too_cheap_house_rent") or round(b["target"] * 0.65, -1)
    return cfg


def resolve(cfg: dict, rel: str) -> Path:
    return Path(cfg["_dir"]) / rel


def move_in(cfg) -> date | None:
    v = cfg["search"].get("move_in")
    if not v:
        return None
    return v if isinstance(v, date) else date.fromisoformat(str(v))


def validate(cfg: dict) -> list[str]:
    """Problems that would stop a run, as human-readable strings (empty list = OK)."""
    errs = []
    d = cfg["destination"]
    if d.get("lat") is None or d.get("lon") is None:
        errs.append("destination.lat / destination.lon are required (run `radar geocode \"<address>\"`).")
    b = cfg["budget"]
    for k in ("target", "max", "hard_max"):
        if not b.get(k):
            errs.append(f"budget.{k} is required.")
    if all(b.get(k) for k in ("target", "max", "hard_max")) and not (b["target"] <= b["max"] <= b["hard_max"]):
        errs.append("budget must satisfy target <= max <= hard_max.")
    bb = cfg["search"].get("bbox")
    if not bb or any(bb.get(k) is None for k in ("south", "north", "west", "east")):
        errs.append("search.bbox needs south, north, west and east.")
    elif not (bb["south"] < bb["north"] and bb["west"] < bb["east"]):
        errs.append("search.bbox: south must be < north and west < east.")
    s = cfg["sources"]
    if s.get("craigslist", {}).get("enabled") and not (s["craigslist"].get("site") and s["craigslist"].get("postal")):
        errs.append("sources.craigslist needs `site` (e.g. sfbay) and `postal` (a ZIP near the center of your search).")
    if s.get("realtor", {}).get("enabled") and not cfg["search"].get("cities"):
        errs.append("search.cities is empty but the realtor source is enabled.")
    w = cfg["scoring"]["weights"]
    if not w or sum(w.values()) <= 0:
        errs.append("scoring.weights must have a positive total.")
    unknown = set(w) - {"price", "commute", "pets", "neighborhood", "size", "home_type", "yard"}
    if unknown:
        errs.append(f"scoring.weights has unknown components: {', '.join(sorted(unknown))}")
    if cfg["scoring"]["top_pick_threshold"] < cfg["scoring"]["email_threshold"]:
        errs.append("scoring.top_pick_threshold should be >= email_threshold.")
    if cfg["commute"]["transit"].get("enabled") and not cfg["_areas"]["transit_stations"]:
        errs.append("commute.transit is enabled but the areas file has no transit_stations.")
    if cfg["search"].get("move_in"):
        try:
            move_in(cfg)
        except ValueError:
            errs.append("search.move_in must be YYYY-MM-DD.")
    return errs

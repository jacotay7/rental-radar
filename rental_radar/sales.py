"""Local history of for-sale / pending / recently sold homes (Realtor.com via HomeHarvest).

The most common rental scam reposts a home that is, or recently was, for sale: same photos, same
description, a cheap rent, and a scammer's phone number. The sale listing is often withdrawn by the
time the fake rental appears, so live "is it for sale?" lookups miss it. We keep our own record of
every sale listing seen, refreshed daily, and match rentals against it by address and by copied
description text. See docs/scam-detection.md.
"""
import logging
import re
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from . import db, geo

log = logging.getLogger("radar")

REFRESH_HOURS = 20
SHINGLE = 8           # words per shingle for copied-text detection
SUFFIXES = {"road": "rd", "street": "st", "avenue": "ave", "drive": "dr", "boulevard": "blvd", "court": "ct",
            "place": "pl", "lane": "ln", "terrace": "ter", "circle": "cir", "parkway": "pkwy",
            "highway": "hwy", "square": "sq", "north": "n", "south": "s", "east": "e", "west": "w"}


def street_key(address: str | None) -> str | None:
    """'123 Main Street, Springfield, IL 62701' -> '123 main st'; '2 Oak Way Unit 106' -> '2 oak way #106'
    (None without a street number). Units are kept so one unit's sale doesn't flag its neighbors' rentals."""
    if not address:
        return None
    street = address.split(",")[0].lower()
    m = re.search(r"\s*(?:#|\bapt\.?|\bunit|\bste\.?)\s*([\w-]*\d[\w-]*|[a-z])$", street)
    unit = m.group(1) if m else None
    street = street[:m.start()] if m else street
    words = [SUFFIXES.get(w, w) for w in re.findall(r"[a-z0-9]+", street)]
    if not words or not words[0].isdigit():
        return None
    return " ".join(words) + (f" #{unit}" if unit else "")


def _shingles(text: str) -> set[int]:
    words = re.findall(r"[a-z0-9']+", (text or "").lower())
    return {hash(" ".join(words[i:i + SHINGLE])) for i in range(len(words) - SHINGLE + 1)}


def _val(v):
    if v is None or str(v) in ("NaT", "nan", "<NA>", "None", ""):
        return None
    return v.item() if hasattr(v, "item") else v


def refresh(conn, cities, state, force=False) -> int:
    """Pull current for-sale/pending and recently sold listings into sale_listings (about once a day)."""
    last = conn.execute("SELECT value FROM meta WHERE key='sales_refreshed'").fetchone()
    now = datetime.now(UTC)
    if last and not force and now - datetime.fromisoformat(last["value"]) < timedelta(hours=REFRESH_HOURS):
        return 0
    from homeharvest import scrape_property
    sold_days = 14 if last else 365  # first run backfills a year of sales
    ts, n = db.now(), 0
    for city in cities:
        for kind, kw in (("listed", {"listing_type": "for_sale"}),  # includes pending/contingent
                         ("sold", {"listing_type": "sold", "past_days": sold_days})):
            try:
                df = scrape_property(location=f"{city}, {state}", extra_property_data=False, **kw)
            except Exception as e:
                log.warning("sale index %s %s failed: %s", city, kind, e)
                continue
            for _, r in df.iterrows():
                street, city, zipc = (_val(r.get(k)) or "" for k in ("full_street_line", "city", "zip_code"))
                addr = f"{street}, {city}, {state} {zipc}"
                key = street_key(addr)
                if not key or _val(r.get("property_id")) is None:
                    continue
                conn.execute(
                    """INSERT INTO sale_listings VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(property_id) DO UPDATE SET status=excluded.status,
                         list_price=COALESCE(excluded.list_price, list_price),
                         sold_price=COALESCE(excluded.sold_price, sold_price),
                         list_date=COALESCE(excluded.list_date, list_date),
                         sold_date=COALESCE(excluded.sold_date, sold_date),
                         office=COALESCE(excluded.office, office),
                         text=COALESCE(excluded.text, text), last_seen=excluded.last_seen""",
                    (str(_val(r["property_id"])), key, addr, _val(r.get("latitude")), _val(r.get("longitude")),
                     str(_val(r.get("status")) or ""), _val(r.get("list_price")), _val(r.get("sold_price")),
                     str(_val(r.get("list_date")) or "")[:10] or None,
                     str(_val(r.get("last_sold_date")) or "")[:10] if kind == "sold" else None,
                     _val(r.get("mls_id")), _val(r.get("office_name")), _val(r.get("text")), ts, ts))
                n += 1
        conn.commit()
    conn.execute("INSERT OR REPLACE INTO meta VALUES ('sales_refreshed', ?)", (ts,))
    conn.commit()
    log.info("sale index: %d listings refreshed", n)
    return n


class TextIndex:
    """Shingle index over sale-listing descriptions, for spotting rental posts copied from them.
    Only shingles that occur in `rental_texts` are kept, which keeps memory small."""

    def __init__(self, conn, rental_texts, days=400):
        since = (datetime.now(UTC) - timedelta(days=days)).isoformat()
        wanted = set().union(*(_shingles(t) for t in rental_texts)) if rental_texts else set()
        self.rows = {}
        self.index: dict[int, set] = defaultdict(set)
        for r in conn.execute("SELECT * FROM sale_listings WHERE text IS NOT NULL AND last_seen>=?", (since,)):
            hits = _shingles(r["text"]) & wanted
            if hits:
                self.rows[r["property_id"]] = dict(r)
                for h in hits:
                    self.index[h].add(r["property_id"])

    def best_match(self, text: str) -> tuple[dict, int, float] | None:
        """(sale row, shared shingles, share of the rental's text) for the most-overlapping sale listing."""
        sh = _shingles(text)
        if len(sh) < 4:
            return None
        counts: dict[str, int] = defaultdict(int)
        for h in sh:
            pids = self.index.get(h, ())
            if len(pids) <= 3:  # skip boilerplate shared by many listings
                for pid in pids:
                    counts[pid] += 1
        if not counts:
            return None
        pid, n = max(counts.items(), key=lambda kv: kv[1])
        return self.rows[pid], n, n / len(sh)


def match(conn, listing, text_index: TextIndex | None = None) -> dict | None:
    """Sale-history facts about this rental's address and description, or None if nothing matches."""
    out = {}
    key = street_key(listing.get("address"))
    if key:
        rows = [r for r in conn.execute("SELECT * FROM sale_listings WHERE street_key=?", (key,))
                if r["lat"] is None or listing.get("lat") is None
                or geo.km(r["lat"], r["lon"], listing["lat"], listing["lon"]) < 0.5]
        if rows:
            r = max(rows, key=lambda r: r["last_seen"])
            out.update(status=r["status"], list_price=r["list_price"], sold_price=r["sold_price"],
                       sold_date=r["sold_date"], list_date=r["list_date"], office=r["office"],
                       first_seen=r["first_seen"][:10], last_seen=r["last_seen"][:10], address=r["address"])
    if text_index:
        best = text_index.best_match(listing.get("body") or "")
        if best and best[1] >= 4 and best[2] >= 0.15:
            r, n, frac = best
            out["copied_from"] = {"address": r["address"], "status": r["status"], "same_address": r["street_key"] == key,
                                  "shared_phrases": n, "share": round(frac, 2), "list_price": r["list_price"],
                                  "sold_date": r["sold_date"], "last_seen": r["last_seen"][:10]}
    return out or None

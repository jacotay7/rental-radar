"""SQLite persistence: listings, analysis, caches, run log, and the sale-history index."""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    uid TEXT PRIMARY KEY,          -- source:source_id
    source TEXT NOT NULL,
    data TEXT NOT NULL,            -- normalized listing JSON (see docs/architecture.md)
    analysis TEXT,                 -- enrichment + scoring JSON
    score REAL,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    detail_fetched INTEGER DEFAULT 0,
    emailed_at TEXT,
    dup_of TEXT,                   -- uid of the canonical listing if a cross-post duplicate
    status TEXT DEFAULT 'active'   -- active | gone | rejected | interested
);
CREATE TABLE IF NOT EXISTS cache (
    key TEXT PRIMARY KEY,          -- "lat,lon" -> drive minutes; "geo:<address>" -> "lat|lon"
    value TEXT
);
CREATE TABLE IF NOT EXISTS runs (
    started TEXT, finished TEXT, summary TEXT
);
CREATE TABLE IF NOT EXISTS sale_listings (  -- every for-sale/pending/sold listing seen (scam cross-check)
    property_id TEXT PRIMARY KEY,
    street_key TEXT,               -- normalized "123 main st #4"
    address TEXT, lat REAL, lon REAL,
    status TEXT,                   -- FOR_SALE | PENDING | SOLD (latest seen)
    list_price REAL, sold_price REAL, list_date TEXT, sold_date TEXT,
    mls_id TEXT, office TEXT,
    text TEXT,                     -- listing description
    first_seen TEXT, last_seen TEXT
);
CREATE INDEX IF NOT EXISTS sale_street ON sale_listings(street_key);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(data_dir: str | Path) -> sqlite3.Connection:
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(data_dir / "rentals.db")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def cache_get(conn, key: str) -> str | None:
    row = conn.execute("SELECT value FROM cache WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None


def cache_set(conn, key: str, value) -> None:
    conn.execute("INSERT OR REPLACE INTO cache VALUES (?,?)", (key, str(value)))


def get_listing(conn, uid):
    return conn.execute("SELECT * FROM listings WHERE uid=?", (uid,)).fetchone()


def upsert_listing(conn, listing: dict, detail_fetched: bool) -> bool:
    """Insert or refresh a listing. Returns True if it is new."""
    ts = now()
    row = get_listing(conn, listing["uid"])
    if row is None:
        conn.execute(
            "INSERT INTO listings (uid, source, data, first_seen, last_seen, detail_fetched) VALUES (?,?,?,?,?,?)",
            (listing["uid"], listing["source"], json.dumps(listing), ts, ts, int(detail_fetched)),
        )
        return True
    old = json.loads(row["data"])
    if not detail_fetched and row["detail_fetched"]:
        # Keep detail fields (and our own check results) from the earlier fetch; refresh search-level fields.
        old.update({k: v for k, v in listing.items() if v not in (None, "", [], {})})
        listing = old
    else:
        # Keep fields added by verification (address_check, property_check, sale_match, link_removed, ...)
        for k, v in old.items():
            if k not in listing and (k.endswith("_check") or k in ("sale_match", "link_removed", "retracted_at")):
                listing[k] = v
    conn.execute(
        "UPDATE listings SET data=?, last_seen=?, detail_fetched=MAX(detail_fetched, ?), "
        "status=CASE WHEN status='gone' THEN 'active' ELSE status END WHERE uid=?",
        (json.dumps(listing), ts, int(detail_fetched), listing["uid"]),
    )
    return False


def save_analysis(conn, uid, analysis: dict, score: float, dup_of=None):
    conn.execute("UPDATE listings SET analysis=?, score=?, dup_of=? WHERE uid=?",
                 (json.dumps(analysis), score, dup_of, uid))


def active_listings(conn):
    return conn.execute("SELECT * FROM listings WHERE status != 'gone'").fetchall()

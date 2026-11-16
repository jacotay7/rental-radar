"""Regression tests for the pre-release audit: data safety, secrets, alert-email trust, config typos."""
import json
import sqlite3
import threading
import time
from email.message import EmailMessage

import pytest
import requests

from rental_radar import analyze, db, geo, notify, pipeline, verify
from rental_radar.config import unknown_keys
from rental_radar.sources import Incomplete, alerts
from tests.conftest import listing, make_cfg


def _score(d, cfg, ff=10):
    return analyze.analyze(d, ff, cfg)


# ---------------------------------------------------------------- pets

def test_small_dogs_only_depends_on_your_dog():
    body = "Small dogs ok."
    s, a = _score(listing(body=body), make_cfg(pets={"dog": True, "dog_weight_lb": 12}))
    assert any("Small dogs welcome" in p for p in a["pros"]) and s > 50
    s, a = _score(listing(body=body), make_cfg(pets={"dog": True, "dog_weight_lb": 60}))
    assert any("small dogs only (yours is 60 lb)" in c for c in a["cons"]) and s <= 62
    _, a = _score(listing(body=body), make_cfg(pets={"dog": True, "dog_weight_lb": None}))
    assert any("check their size limit" in c for c in a["cons"])


# ---------------------------------------------------------------- database

def test_old_database_is_migrated_and_marks_survive(tmp_path):
    old = sqlite3.connect(tmp_path / "rentals.db")
    old.executescript("""CREATE TABLE listings (uid TEXT PRIMARY KEY, source TEXT NOT NULL, data TEXT NOT NULL,
        analysis TEXT, score REAL, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, detail_fetched INTEGER DEFAULT 0,
        emailed_at TEXT, dup_of TEXT, status TEXT DEFAULT 'active');
        INSERT INTO listings (uid, source, data, first_seen, last_seen, status) VALUES
          ('a:1','a','{}','t','t','rejected'), ('a:2','a','{}','t','t','interested'), ('a:3','a','{}','t','t','gone');""")
    old.commit()
    old.close()
    conn = db.connect(tmp_path)
    rows = {r["uid"]: (r["status"], r["mark"]) for r in conn.execute("SELECT * FROM listings")}
    assert rows == {"a:1": ("active", "rejected"), "a:2": ("active", "interested"), "a:3": ("gone", None)}
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    db.connect(tmp_path)                                        # opening again is a no-op


def test_newer_database_is_refused(tmp_path):
    conn = db.connect(tmp_path)
    conn.execute(f"PRAGMA user_version = {db.SCHEMA_VERSION + 1}")
    conn.commit()
    with pytest.raises(RuntimeError, match="newer Rental Radar"):
        db.connect(tmp_path)


# ---------------------------------------------------------------- collection

class Source:
    MARK_GONE = True

    def __init__(self, batches, detail=None):
        self.batches, self.detail, self.calls = list(batches), detail, 0

    def search(self, cfg, conn):
        self.calls += 1
        batch = self.batches.pop(0)
        if isinstance(batch, Exception):
            raise batch
        return batch

    def fetch_detail(self, d, cfg):
        return self.detail(d) if self.detail else d


def _setup(tmp_path, monkeypatch, src, **source_cfg):
    cfg = make_cfg(sources={"fake": {"enabled": True, **source_cfg}})
    cfg["_data_dir"] = str(tmp_path)
    monkeypatch.setattr(pipeline, "SOURCES", {"fake": src})
    monkeypatch.setattr(geo, "drive_minutes", lambda conn, pts, cfg: [8.0] * len(pts))
    return cfg, db.connect(tmp_path)


def _statuses(conn):
    return {r["uid"]: r["status"] for r in conn.execute("SELECT uid, status FROM listings")}


def test_rejected_mark_survives_a_listing_dropping_out_and_returning(tmp_path, monkeypatch):
    one = [listing(uid="fake:1", source="fake")]
    cfg, conn = _setup(tmp_path, monkeypatch, Source([one, [], one]))
    pipeline.collect(conn, cfg, 10)
    conn.execute("UPDATE listings SET mark='rejected'")
    pipeline.collect(conn, cfg, 10)
    assert _statuses(conn) == {"fake:1": "gone"}
    pipeline.collect(conn, cfg, 10)
    row = conn.execute("SELECT status, mark FROM listings").fetchone()
    assert (row["status"], row["mark"]) == ("active", "rejected")
    pipeline.score_all(conn, cfg)
    assert pipeline.pending(conn, cfg, min_score=0) == []


def test_partial_results_do_not_mark_listings_gone(tmp_path, monkeypatch):
    many = [listing(uid=f"pm:{sub}:{i}", source="fake", lat=41.9 + i / 1000) for sub in ("aaa", "bbb") for i in range(3)]
    partial = Incomplete([d for d in many if ":aaa:" in d["uid"]][:2], ("pm:bbb:",), "portals failed: bbb")
    cfg, conn = _setup(tmp_path, monkeypatch, Source([many, partial, Incomplete(many[:1], None, "a city failed")]))
    pipeline.collect(conn, cfg, 0)
    stats = pipeline.collect(conn, cfg, 0)
    st = _statuses(conn)
    assert st["pm:aaa:2"] == "gone" and all(st[f"pm:bbb:{i}"] == "active" for i in range(3))
    assert stats["fake"]["incomplete"] == "portals failed: bbb"
    pipeline.collect(conn, cfg, 0)                               # nothing can be trusted: nothing marked
    assert _statuses(conn)["pm:aaa:1"] == "active"


def test_a_collapsed_search_does_not_mark_everything_gone(tmp_path, monkeypatch):
    many = [listing(uid=f"fake:{i}", source="fake", lat=41.9 + i / 1000) for i in range(40)]
    cfg, conn = _setup(tmp_path, monkeypatch, Source([many, many[:2], many[:30]]))
    pipeline.collect(conn, cfg, 0)
    stats = pipeline.collect(conn, cfg, 0)
    assert set(_statuses(conn).values()) == {"active"} and "not marking" in stats["fake"]["incomplete"]
    pipeline.collect(conn, cfg, 0)                               # an ordinary drop still marks the missing ones
    assert list(_statuses(conn).values()).count("gone") == 10


def test_interval_hours_skips_a_recently_searched_source(tmp_path, monkeypatch):
    src = Source([[listing(uid="fake:1", source="fake")]] * 3)
    cfg, conn = _setup(tmp_path, monkeypatch, src, interval_hours=6)
    for _ in range(2):
        stats = pipeline.collect(conn, cfg, 0)
        conn.execute("INSERT INTO runs VALUES (?,?,?)", (db.now(), db.now(), json.dumps(stats)))
    assert src.calls == 1 and stats == {} and _statuses(conn) == {"fake:1": "active"}


def test_detail_fetching_stops_after_repeated_failures(tmp_path, monkeypatch):
    tried = []

    def detail(d):
        tried.append(d["uid"])
        raise RuntimeError("HTTP 403")

    good = dict(housing_type="house", beds=3, sqft=1500, body="Large dogs welcome. Private fenced yard.")
    many = [listing(uid=f"fake:{i}", source="fake", lat=41.9 + i / 1000, **good) for i in range(12)]
    cfg, conn = _setup(tmp_path, monkeypatch, Source([many], detail))
    stats = pipeline.collect(conn, cfg, 50)
    assert len(tried) == pipeline.MAX_DETAIL_ERRORS and stats["fake"]["detail_errors"] == pipeline.MAX_DETAIL_ERRORS
    assert conn.execute("SELECT SUM(detail_fetched) FROM listings").fetchone()[0] == 0     # retried next run
    conn.execute("INSERT INTO runs VALUES (?,?,?)", (db.now(), db.now(), json.dumps(stats)))
    assert any("listing pages failed" in w for w in pipeline.source_health(conn, cfg))


def test_health_warns_when_commutes_are_estimates(tmp_path, monkeypatch):
    cfg, conn = _setup(tmp_path, monkeypatch, Source([]))
    conn.execute("INSERT INTO runs VALUES (?,?,?)", (db.now(), db.now(), json.dumps({"commute_estimated": 30, "scored": 40})))
    assert any(w.startswith("routing:") for w in pipeline.source_health(conn, cfg))
    conn.execute("DELETE FROM runs")
    conn.execute("INSERT INTO runs VALUES (?,?,?)", (db.now(), db.now(), json.dumps({"commute_estimated": 1, "scored": 40})))
    assert pipeline.source_health(conn, cfg) == []


def test_only_one_run_at_a_time(tmp_path):
    cfg = make_cfg()
    cfg["_data_dir"] = str(tmp_path)
    order = []

    def second():
        with pipeline.run_lock(cfg):
            order.append("second")

    with pipeline.run_lock(cfg):
        t = threading.Thread(target=second)
        t.start()
        time.sleep(0.3)
        order.append("first")
    t.join(timeout=15)
    assert order == ["first", "second"]
    with pipeline.run_lock(cfg), pytest.raises(RuntimeError, match="another radar run"):
        with pipeline.run_lock(cfg, wait_seconds=0):
            pass


# ---------------------------------------------------------------- verification

def test_address_check_outcomes(monkeypatch):
    d = listing(address="100 Main St, Chicago, IL 60601", lat=41.9, lon=-87.65)
    monkeypatch.setattr(geo, "geocode", lambda conn, addr: (41.9, -87.65))
    assert verify.check(None, d, "IL")["status"] == "ok"
    monkeypatch.setattr(geo, "geocode", lambda conn, addr: (41.95, -87.65))
    assert verify.check(None, d, "IL") == {"status": "far", "address": d["address"], "km": 5.6}
    monkeypatch.setattr(geo, "geocode", lambda conn, addr: (None, None))
    monkeypatch.setattr(verify, "_osm", lambda addr, base: (None, None))
    assert verify.check(None, d, "IL")["status"] == "not_found"
    assert verify.check(None, listing(address="Main St near the park"), "IL") == {"status": "no_address"}


def test_fake_address_is_recorded_and_never_sent(cfg):
    d = listing(housing_type="house", beds=3, sqft=1500, body="Large dogs welcome. Private fenced yard.",
                address_check={"status": "not_found", "address": "4127 Elm St, Chicago, IL"})
    s, a = _score(d, cfg)
    assert a["address_fake"] and s <= cfg["scoring"]["email_threshold"] - 10 and "_address_fake" not in d


def test_property_is_looked_up_once_even_when_unknown(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(verify, "property_record", lambda addr: calls.append(addr) or {"status": "unknown"})
    conn = db.connect(tmp_path)
    db.upsert_listing(conn, listing(uid="zillow:1", source="zillow"), True)
    conn.execute("UPDATE listings SET score=90")
    for _ in range(3):
        verify.verify_all(conn, make_cfg(), 50)
    assert len(calls) == 1


def test_removed_diy_posting_is_recorded(tmp_path, monkeypatch):
    conn = db.connect(tmp_path)
    d = listing(uid="redfin:1", source="redfin", posted_at="2030-01-01T00:00:00",
                contact={"external_url": "https://rental.turbotenant.com/p/demo"})
    db.upsert_listing(conn, d, True)
    conn.execute("UPDATE listings SET score=90")
    monkeypatch.setattr(verify.http, "get", lambda url, **k: type("R", (), {"status_code": 410})())
    assert verify.check_links(conn, make_cfg()) == 1
    lr = json.loads(conn.execute("SELECT data FROM listings").fetchone()["data"])["link_removed"]
    assert lr["status"] == 410 and lr["site"] == "Turbotenant"


# ---------------------------------------------------------------- notifications

def test_webhook_secret_never_appears_in_errors(monkeypatch, caplog):
    for k in ("SLACK_WEBHOOK_URL", "NTFY_URL", "TELEGRAM_BOT_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.example/api/webhooks/123/SECRET_TOKEN")

    def post(url, **kw):
        raise requests.ConnectionError(f"Max retries exceeded with url: {url}")

    monkeypatch.setattr(notify.requests, "post", post)
    with pytest.raises(RuntimeError) as err:
        notify.send_test(make_cfg(email={"to": []}))
    assert "SECRET_TOKEN" not in str(err.value) and "SECRET_TOKEN" not in caplog.text
    assert "discord.example" in str(err.value)


# ---------------------------------------------------------------- alert emails

def test_alert_sender_must_really_be_the_site():
    s = alerts.SENDERS
    assert alerts.sender_site("Zillow <rental-instant-updates@mail.zillow.com>", s) == "zillow"
    assert alerts.sender_site("alerts@zumper.com", s) == "zumper"
    assert alerts.sender_site('"zillow.com" <someone@evil.example>', s) is None
    assert alerts.sender_site("x@zillow.com.evil.example", s) is None
    assert alerts.sender_site("x@notzillow.com", s) is None


def test_alert_authentication_and_links():
    msg = EmailMessage()
    assert alerts.authenticated(msg)                                           # server records nothing
    msg["Authentication-Results"] = "mx.example; dkim=pass header.d=zillow.com; spf=pass"
    assert alerts.authenticated(msg)
    forged = EmailMessage()
    forged["Authentication-Results"] = "mx.example; dkim=none; spf=fail smtp.mailfrom=evil.example; dmarc=fail"
    assert not alerts.authenticated(forged)
    assert alerts.safe_url("https://www.zillow.com/homedetails/1_zpid/")
    assert alerts.safe_url("javascript:alert(1)") == "" and alerts.safe_url("data:text/html,x") == ""


# ---------------------------------------------------------------- config

def test_unknown_settings_are_reported():
    user = {"scoring": {"wieghts": {}, "weights": {"price": 1}, "home_types": {"condo": 0.5}}, "budgte": {},
            "commute": {"transit": {"exits": {"ANY": {"name": "x"}}}},
            "sources": {"zillow": {"enabled": True}, "redfin": {"interval_hours": 3, "markt": "x"}}}
    assert unknown_keys(user) == ["scoring.wieghts", "budgte", "sources.zillow (no such source)", "sources.redfin.markt"]

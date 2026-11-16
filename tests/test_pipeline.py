"""End-to-end run with a fake source and no network."""
import json
from datetime import UTC

from rental_radar import db, geo, pipeline
from tests.conftest import listing, make_cfg


class FakeSource:
    MARK_GONE = True

    @staticmethod
    def search(cfg, conn):
        return [
            listing(uid="fake:1", source="fake", housing_type="house", beds=3, sqft=1500, price=1900,
                    body="Large dogs welcome. Private fenced backyard. Available mar 1."),
            listing(uid="fake:2", source="fake", lat=41.9001, lon=-87.6501, beds=3, sqft=1500, price=1900,
                    title="Nice place", body="Same post, reposted."),          # cross-post duplicate
            listing(uid="fake:3", source="fake", lat=50.0, lon=-80.0),        # outside the search box
        ]

    @staticmethod
    def fetch_detail(d, cfg):
        return d


def test_full_run_writes_preview(tmp_path, monkeypatch):
    cfg = make_cfg(sources={"fake": {"enabled": True}})
    cfg["_data_dir"] = str(tmp_path)
    monkeypatch.setattr(pipeline, "SOURCES", {"fake": FakeSource})
    monkeypatch.setattr(geo, "drive_minutes", lambda conn, pts, cfg: [8.0] * len(pts))
    conn = db.connect(tmp_path)
    stats = pipeline.run(conn, cfg, mode="digest", send=False)
    assert stats["fake"]["found"] == 2
    rows = {r["uid"]: r for r in conn.execute("SELECT * FROM listings")}
    assert rows["fake:1"]["score"] >= cfg["scoring"]["top_pick_threshold"]
    assert rows["fake:2"]["dup_of"] == "fake:1"
    html = (tmp_path / "preview.html").read_text()
    assert "100 Main St" in html and "Top 1 still available" in html
    a = json.loads(rows["fake:1"]["analysis"])
    assert "Private/fenced yard" in a["pros"]


def _log_runs(conn, summaries):
    from datetime import datetime, timedelta
    t0 = datetime.now(UTC) - timedelta(hours=len(summaries))
    for i, s in enumerate(summaries):
        ts = (t0 + timedelta(hours=i)).isoformat()
        conn.execute("INSERT INTO runs VALUES (?,?,?)", (ts, ts, json.dumps(s)))


def test_source_health_flags_failures_and_drops(tmp_path, monkeypatch):
    cfg = make_cfg(sources={"fake": {"enabled": True}, "other": {"enabled": True}})
    monkeypatch.setattr(pipeline, "SOURCES", {"fake": FakeSource, "other": FakeSource})
    conn = db.connect(tmp_path)
    ok = {"found": 400}
    _log_runs(conn, [{"fake": ok, "other": ok}] * 5 + [{"fake": "search failed: HTTP 403", "other": {"found": 3}},
                                                       {"retracted": 0, "emailed": 0}])   # a digest-only run
    warnings = pipeline.source_health(conn, cfg)
    assert len(warnings) == 2
    assert warnings[0].startswith("fake: the last 1 run failed (search failed: HTTP 403)")
    assert "found 3 listings" in warnings[1] and "usually about 400" in warnings[1]


def test_source_health_quiet_when_normal(tmp_path, monkeypatch):
    cfg = make_cfg(sources={"fake": {"enabled": True}})
    monkeypatch.setattr(pipeline, "SOURCES", {"fake": FakeSource})
    conn = db.connect(tmp_path)
    _log_runs(conn, [{"fake": {"found": n}} for n in (400, 380, 420, 390)])
    assert pipeline.source_health(conn, cfg) == []

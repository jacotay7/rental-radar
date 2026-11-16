"""Command line: the setup wizard, doctor, scheduling output and day-to-day commands. No network."""
import json

import pytest

from rental_radar import cli, db, geo, http, pipeline
from rental_radar.config import load_config, validate
from rental_radar.sources import craigslist
from tests.conftest import listing

MATCH = {"lat": 41.8789, "lon": -87.6366, "matched": "233 S WACKER DR, CHICAGO, IL, 60606", "city": "Chicago",
         "state": "IL", "zip": "60606"}


@pytest.fixture
def search_dir(tmp_path, monkeypatch):
    """A search folder created by the wizard, with one channel configured."""
    monkeypatch.setattr(geo, "census_match", lambda addr: MATCH)
    monkeypatch.setattr(craigslist, "nearest_site", lambda lat, lon: {
        "site": "chicago", "name": "chicago", "timezone": "America/Chicago", "km": 3.0})
    for k in ("DISCORD_WEBHOOK_URL", "SLACK_WEBHOOK_URL", "TELEGRAM_BOT_TOKEN", "SMTP_USER", "GMAIL_USER"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("NTFY_URL", "https://ntfy.example/topic")
    d = tmp_path / "my search"                       # a space in the path, on purpose
    cli.main(["init", "--dir", str(d), "--yes", "--destination", "233 S Wacker Dr, Chicago, IL",
              "--max-rent", "2200", "--min-beds", "2", "--dog-weight", "60", "--move-in", "2030-03-01"])
    return d


def run(search_dir, *argv):
    cli.main(["--config", str(search_dir / "config.yaml"), *argv])


def test_wizard_writes_a_valid_config(search_dir):
    cfg = load_config(search_dir / "config.yaml")
    assert validate(cfg) == [] and cfg["_unknown"] == []
    assert cfg["destination"]["lat"] == 41.8789 and cfg["search"]["state"] == "IL"
    assert cfg["budget"] == {**cfg["budget"], "target": 2100, "max": 2200, "hard_max": 2300}
    assert cfg["pets"]["dog"] and cfg["pets"]["dog_weight_lb"] == 60 and cfg["search"]["cities"] == ["Chicago"]
    src = cfg["sources"]["craigslist"]
    assert (src["enabled"], src["site"], src["postal"]) == (True, "chicago", "60606")
    assert cfg["schedule"]["timezone"] == "America/Chicago" and cfg["email"]["to"] == []
    assert (search_dir / ".env").stat().st_mode & 0o777 == 0o600
    with pytest.raises(SystemExit, match="already exists"):
        cli.main(["init", "--dir", str(search_dir), "--yes", "--destination", "x"])


def test_init_from_example(tmp_path):
    cli.main(["init", "--dir", str(tmp_path), "--example", "east-bay"])
    cfg = load_config(tmp_path / "config.yaml")
    assert validate(cfg) == [] and len(cfg["_areas"]["neighborhoods"]) > 40
    with pytest.raises(SystemExit, match="No example named"):
        cli.main(["init", "--dir", str(tmp_path / "x"), "--example", "nowhere"])


def test_doctor_passes_and_flags_typos(search_dir, capsys):
    with pytest.raises(SystemExit) as e:
        run(search_dir, "doctor", "--offline")
    out = capsys.readouterr().out
    assert e.value.code == 0 and "notification channels: ntfy" in out and "All good." in out
    with open(search_dir / "config.yaml", "a") as f:
        f.write("\nscorng: {email_threshold: 60}\n")
    with pytest.raises(SystemExit):
        run(search_dir, "doctor", "--offline")
    assert "unknown setting `scorng`" in capsys.readouterr().out


def test_doctor_fails_without_a_channel(search_dir, capsys, monkeypatch):
    monkeypatch.delenv("NTFY_URL")
    with pytest.raises(SystemExit) as e:
        run(search_dir, "doctor", "--offline")
    assert e.value.code == 1 and "notification channels: none" in capsys.readouterr().out


def test_schedule_output_quotes_paths_with_spaces(search_dir, capsys):
    run(search_dir, "schedule", "cron")
    cron = capsys.readouterr().out
    conf = str(search_dir / "config.yaml")
    assert "CRON_TZ=America/Chicago" in cron and f"--config '{conf}' run --mode alerts" in cron
    assert "run --mode digest --no-collect" in cron
    cfg = load_config(search_dir / "config.yaml")
    units = cli._units(cfg)
    assert set(units) == {"rental-radar-collect", "rental-radar-digest"}
    service = cli.UNIT_SERVICE.format(python="/usr/bin/python3", config=conf, workdir=cfg["_dir"],
                                      **units["rental-radar-collect"])
    assert f'--config "{conf}" run --mode alerts' in service
    assert "OnCalendar=*-*-* 06..22:05:00 America/Chicago" in cli.UNIT_TIMER.format(**units["rental-radar-collect"])


def test_daily_commands(search_dir, capsys, monkeypatch):
    cfg = load_config(search_dir / "config.yaml")
    conn = db.connect(cfg["_data_dir"])
    good = dict(housing_type="house", beds=3, sqft=1500, lat=41.88, lon=-87.64, price=2000,
                body="Large dogs welcome. Private fenced yard. Available mar 1.")
    db.upsert_listing(conn, listing(uid="t:1", **good), True)
    other = {**good, "lat": 41.89, "price": 2100, "body": "No pets."}
    db.upsert_listing(conn, listing(uid="t:2", title="Other place", **other), True)
    conn.commit()
    monkeypatch.setattr(geo, "drive_minutes", lambda conn, pts, cfg: [8.0] * len(pts))
    monkeypatch.setattr(pipeline.verify, "verify_all", lambda *a: 0)
    monkeypatch.setattr(pipeline.verify, "check_links", lambda *a: 0)
    monkeypatch.setattr(pipeline.sales, "refresh", lambda *a, **k: 0)
    run(search_dir, "rescore")
    assert "1 unemailed listings" in capsys.readouterr().out
    run(search_dir, "top")
    top = capsys.readouterr().out.splitlines()
    assert top[0].endswith("t:1") and top[1].endswith("t:2")
    run(search_dir, "show", "t:1")
    assert "Explicitly welcomes large dogs" in capsys.readouterr().out
    run(search_dir, "mark", "t:1", "rejected")
    run(search_dir, "top")
    assert "✗" in capsys.readouterr().out.splitlines()[1]
    assert pipeline.pending(conn, cfg) == []
    run(search_dir, "mark", "t:1", "none")
    run(search_dir, "run", "--dry-run", "--no-collect")
    out = capsys.readouterr().out
    assert json.loads(out[out.index("{"):])["emailed"] == 0 and (search_dir / "data" / "preview.html").exists()
    assert (search_dir / "data" / "preview.chat.txt").read_text().startswith("⭐")
    run(search_dir, "baseline")
    assert pipeline.pending(conn, cfg) == []


def test_demo_command(tmp_path, capsys):
    cli.main(["demo", "--no-open", "--out", str(tmp_path / "d.html")])
    assert "would be sent" in capsys.readouterr().out and (tmp_path / "d.html").exists()


# ---------------------------------------------------------------- geo / http plumbing

class Resp:
    def __init__(self, status=200, data=None):
        self.status_code, self._data = status, data

    def json(self):
        return self._data


def test_drive_minutes_caches_and_survives_an_outage(tmp_path, monkeypatch):
    cfg = load_config("examples/minimal/config.yaml")
    cfg["services"]["osrm_url"] = "https://osrm.example/"
    conn = db.connect(tmp_path)
    urls = []

    def get(url, **kw):
        urls.append(url)
        return Resp(data={"durations": [[600.0], [None]]})      # rows follow the sorted points

    monkeypatch.setattr(geo.http, "get", get)
    pts = [(41.9, -87.65), (41.8, -87.6)]
    assert geo.drive_minutes(conn, pts, cfg) == [None, 10.0]
    assert urls[0].startswith("https://osrm.example/table/v1/driving/-87.6000,41.8000;-87.6500,41.9000;")
    monkeypatch.setattr(geo.http, "get", lambda url, **kw: (_ for _ in ()).throw(OSError("down")))
    assert geo.drive_minutes(conn, pts, cfg) == [None, 10.0]             # one from cache, the other still unknown
    c = geo.commute(41.9, -87.65, None, cfg)
    assert c["drive"] > 0 and c["best"] == c["drive"]                      # straight-line fallback


def test_http_get_backs_off_and_spaces_requests(monkeypatch):
    sleeps, statuses = [], [503, 200, 200]
    monkeypatch.setattr(http.time, "sleep", lambda s: sleeps.append(round(s, 1)))
    monkeypatch.setattr(http._session, "get", lambda url, **kw: Resp(statuses.pop(0)))
    http._last_call.pop("slow.example", None)
    assert http.get("https://slow.example/a", min_interval=2).status_code == 200
    assert sleeps == [10]                                                  # backed off after the 503
    http.get("https://slow.example/b", min_interval=2)
    assert 1.5 < sleeps[-1] <= 2                                           # waited out the per-host interval

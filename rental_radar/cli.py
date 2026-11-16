"""`radar` command line: setup (init, doctor, schedule) and day-to-day use (run, top, show, mark, ...)."""
import argparse
import json
import logging
import math
import os
import shutil
import subprocess
import sys
import traceback
from datetime import date
from pathlib import Path
from string import Template

from . import __version__, db, emailer, geo, pipeline, templates
from .config import EXAMPLES_DIR, config_path, imap_credentials, load_config, smtp_credentials, validate

log = logging.getLogger("radar")


# ---------------------------------------------------------------- helpers

def _conn(cfg):
    return db.connect(cfg["_data_dir"])


def _ask(prompt: str, default=None, assume_yes=False, cast=str):
    if assume_yes or not sys.stdin.isatty():
        return default
    suffix = f" [{default}]" if default not in (None, "") else ""
    while True:
        raw = input(f"{prompt}{suffix}: ").strip()
        if not raw:
            return default
        try:
            return cast(raw)
        except ValueError:
            print("  Please enter a valid value.")


def _yes_no(prompt: str, default: bool, assume_yes=False) -> bool:
    ans = _ask(prompt + " (y/n)", "y" if default else "n", assume_yes)
    return str(ans).lower().startswith("y")


def _round(x, step=50):
    return int(round(x / step) * step)


def _yaml_list(items) -> str:
    return "[" + ", ".join(json.dumps(i) for i in items) + "]" if items else "[]"


# ---------------------------------------------------------------- setup commands

def cmd_init(args):
    """Create config.yaml, areas.yaml, portals.yaml and .env in the target directory."""
    outdir = Path(args.dir).expanduser().resolve()
    outdir.mkdir(parents=True, exist_ok=True)
    cfg_file = outdir / "config.yaml"
    if cfg_file.exists() and not args.force:
        sys.exit(f"{cfg_file} already exists (use --force to overwrite).")

    if args.example:
        src = EXAMPLES_DIR / args.example
        if not src.is_dir():
            options = ", ".join(sorted(p.name for p in EXAMPLES_DIR.iterdir() if p.is_dir()))
            sys.exit(f"No example named {args.example!r}. Available: {options}")
        for f in src.iterdir():
            if f.is_file() and f.suffix in (".yaml", ".yml", ".md"):
                shutil.copy(f, outdir / f.name)
        _write_env(outdir)
        print(f"Copied the {args.example} example into {outdir}.\n"
              "Next: edit destination + email.to in config.yaml, fill in .env, then run `radar doctor`.")
        return

    y = args.yes
    print("Rental Radar setup. Press Enter to accept a [default]. You can change everything later in config.yaml.\n")
    label = args.label or _ask("Name for this search (used in email subjects)", "Rental search", y)

    dest_address = args.destination or _ask("Address you commute to (work, school...)", None, False)
    match = geo.census_match(dest_address) if dest_address else None
    if match:
        print(f"  Found: {match['matched']} ({match['lat']:.5f}, {match['lon']:.5f})")
        lat, lon = match["lat"], match["lon"]
    else:
        if dest_address:
            print("  The US Census geocoder couldn't find that address. Enter coordinates instead "
                  "(right-click the spot in Google Maps to copy them).")
        lat = _ask("Destination latitude", None, False, float)
        lon = _ask("Destination longitude", None, False, float)
        if lat is None or lon is None:
            sys.exit("A destination is required for commute scoring.")
    state = args.state or (match and match.get("state")) or _ask("Two-letter state", "CA", y)
    dest_label = args.dest_label or _ask("Short label for it", "work", y)

    radius = args.radius_km or _ask("Search radius around it, in km", 20, y, float)
    dlat = radius / 111.0
    dlon = radius / (111.0 * math.cos(math.radians(lat)))
    default_city = (match or {}).get("city") or ""
    cities_raw = args.cities or _ask("Cities to search on Realtor.com (comma-separated)", default_city, y)
    cities = [c.strip() for c in (cities_raw or "").split(",") if c.strip()]

    max_rent = args.max_rent or _ask("Maximum monthly rent", 3000, y, int)
    target = args.target or _ask("Target rent (full marks at or below)", _round(max_rent * 0.95), y, int)
    hard_max = args.hard_max or _ask("Absolute ceiling for exceptional places", _round(max_rent * 1.05), y, int)
    min_beds = args.min_beds if args.min_beds is not None else _ask("Minimum bedrooms", 1, y, int)
    ideal_beds = args.ideal_beds or _ask("Ideal bedrooms", max(min_beds, 2), y, int)
    move_in = args.move_in or _ask("Move-in date YYYY-MM-DD (blank = flexible)", "", y)
    if move_in:
        date.fromisoformat(move_in)

    dog = args.dog_weight is not None or (not y and _yes_no("Do you have a dog?", False, y))
    dog_weight = args.dog_weight or (_ask("Dog's weight in lb", 50, y, int) if dog else None)
    cat = args.cat or (not y and _yes_no("Do you have a cat?", False, y))
    max_commute = args.max_commute or _ask("Longest acceptable one-way commute, minutes", 35, y, int)

    emails = args.email or _ask("Email address(es) to send results to (comma-separated)", "", y)
    email_to = [e.strip() for e in (emails or "").split(",") if e.strip()]
    smtp_host = args.smtp_host or _ask("SMTP server for sending (Gmail default)", "smtp.gmail.com", y)
    smtp_port = 465 if smtp_host == "smtp.gmail.com" else _ask("SMTP port (465 SSL / 587 STARTTLS)", 587, y, int)

    cl_site = args.craigslist_site if args.craigslist_site is not None else _ask(
        "Craigslist site subdomain, e.g. sfbay / chicago / seattle (blank = skip Craigslist)", "", y)
    cl_postal = (match or {}).get("zip")

    values = dict(
        label=label, state=state.upper(), move_in=f'"{move_in}"' if move_in else "null",
        south=round(lat - dlat, 3), north=round(lat + dlat, 3), west=round(lon - dlon, 3), east=round(lon + dlon, 3),
        cities=_yaml_list(cities), alert_radius=max(50, round(radius * 2)),
        dest_label=dest_label, dest_address=(match or {}).get("matched") or dest_address or "",
        dest_lat=round(lat, 5), dest_lon=round(lon, 5),
        target=target, max_rent=max_rent, hard_max=max(hard_max, max_rent),
        min_beds=min_beds, ideal_beds=max(ideal_beds, min_beds),
        min_sqft=300 + 250 * min_beds, good_sqft=500 + 250 * ideal_beds,
        dog=str(bool(dog)).lower(), dog_weight=dog_weight or "null", cat=str(bool(cat)).lower(),
        pets_weight=15 if (dog or cat) else 0, max_commute=max_commute,
        email_to=_yaml_list(email_to), smtp_host=smtp_host, smtp_port=smtp_port,
        cl_enabled=str(bool(cl_site)).lower(), cl_site=json.dumps(cl_site) if cl_site else "null",
        cl_postal=json.dumps(cl_postal) if cl_postal else "null", cl_distance=max(5, round(radius / 1.609)),
        timezone="null",
    )
    cfg_file.write_text(Template(templates.CONFIG).safe_substitute(values))
    for name, body in (("areas.yaml", templates.AREAS), ("portals.yaml", templates.PORTALS)):
        if not (outdir / name).exists() or args.force:
            (outdir / name).write_text(body)
    _write_env(outdir)
    print(f"\nWrote {cfg_file}, areas.yaml, portals.yaml and .env in {outdir}.")
    print("Next steps:\n"
          "  1. Put your SMTP login in .env (Gmail: create an app password).\n"
          "  2. Rate your neighborhoods in areas.yaml (docs/regions.md), the biggest lever on quality.\n"
          "  3. radar doctor              # check everything\n"
          "  4. radar run --no-email      # first collection, writes data/preview.html\n"
          "  5. radar schedule install    # hourly top picks + daily digest")


def _write_env(target: Path):
    env = target / ".env"
    if not env.exists():
        env.write_text(templates.ENV)
        env.chmod(0o600)


def cmd_geocode(args):
    m = geo.census_match(args.address)
    if not m:
        sys.exit("Not found by the US Census geocoder. Try adding the city and state, or copy coordinates from a map.")
    print(f"{m['matched']}\nlat: {m['lat']:.5f}\nlon: {m['lon']:.5f}")


def cmd_doctor(args):
    ok = True

    def line(status, msg):
        nonlocal ok
        ok &= status != "FAIL"
        print(f"[{status:^4}] {msg}")

    try:
        cfg = load_config(args.config)
    except Exception as e:
        line("FAIL", f"config: {e}")
        sys.exit(1)
    line("ok", f"config: {cfg['_path']}")
    for err in validate(cfg):
        line("FAIL", err)
    b, d = cfg["search"]["bbox"], cfg["destination"]
    if b and d.get("lat") is not None and not (b["south"] <= d["lat"] <= b["north"] and b["west"] <= d["lon"] <= b["east"]):
        line("warn", "destination is outside search.bbox (fine if intended)")
    areas = cfg["_areas"]
    n_nb = len(areas["neighborhoods"])
    line("ok" if n_nb else "warn", f"areas: {n_nb} neighborhoods, {len(areas['city_defaults'])} city defaults, "
         f"{len(areas['transit_stations'])} transit stations, {len(areas['dog_parks'])} dog parks"
         + ("" if n_nb else " (every listing gets the default neighborhood score; see docs/regions.md)"))
    enabled = [k for k, v in cfg["sources"].items() if v.get("enabled")]
    line("ok" if enabled else "FAIL", f"sources enabled: {', '.join(enabled) or 'none'}")
    user, pw = smtp_credentials()
    if not (user and pw):
        line("FAIL", "SMTP_USER / SMTP_PASSWORD missing from .env")
    elif args.smtp:
        import smtplib
        import ssl
        try:
            e = cfg["email"]
            if int(e["smtp_port"]) == 465:
                s = smtplib.SMTP_SSL(e["smtp_host"], e["smtp_port"], context=ssl.create_default_context(), timeout=20)
            else:
                s = smtplib.SMTP(e["smtp_host"], e["smtp_port"], timeout=20)
                s.starttls(context=ssl.create_default_context())
            s.login(user, pw.replace(" ", ""))
            s.quit()
            line("ok", f"SMTP login to {e['smtp_host']} works")
        except Exception as ex:
            line("FAIL", f"SMTP login failed: {ex}")
    else:
        line("ok", "SMTP credentials present (add --smtp to test the login)")
    line("ok" if cfg["email"]["to"] else "FAIL", f"email.to: {len(cfg['email']['to'] or [])} recipient(s)")
    if "alerts" in enabled:
        iu, ip = imap_credentials()
        if not (iu and ip):
            line("FAIL", "alerts enabled but no IMAP credentials")
        else:
            import imaplib
            try:
                m = imaplib.IMAP4_SSL(cfg["sources"]["alerts"]["imap_host"])
                m.login(iu, ip.replace(" ", ""))
                m.logout()
                line("ok", "IMAP login works (alerts)")
            except Exception as ex:
                line("FAIL", f"IMAP login failed: {ex}")
    if "pm-portal" in enabled:
        import yaml
        pf = Path(cfg["_dir"]) / cfg["sources"]["pm-portal"]["file"]
        n = len((yaml.safe_load(pf.read_text()) or {}).get("portals") or []) if pf.exists() else 0
        line("ok" if n else "warn", f"pm-portal: {n} portals in {pf.name}")
    if not args.offline:
        from . import http
        checks = {"OSRM routing": "https://router.project-osrm.org/",
                  "US Census geocoder": "https://geocoding.geo.census.gov/geocoder/"}
        if "craigslist" in enabled:
            checks["Craigslist"] = "https://sapi.craigslist.org/web/v8/postings/search/full"
        if "redfin" in enabled:
            checks["Redfin"] = "https://www.redfin.com/"
        if "realtor" in enabled:
            checks["Realtor.com"] = "https://www.realtor.com/"
        for name, url in checks.items():
            try:
                r = http.get(url, timeout=15, retries=0, min_interval=0)
                line("ok" if r.status_code < 500 else "warn", f"reachable: {name} (HTTP {r.status_code})")
            except Exception as ex:
                line("warn", f"unreachable: {name} ({ex.__class__.__name__})")
    conn = _conn(cfg)
    n = conn.execute("SELECT COUNT(*) FROM listings").fetchone()[0]
    last = conn.execute("SELECT finished, summary FROM runs ORDER BY finished DESC LIMIT 1").fetchone()
    line("ok", f"database: {n} listings in {cfg['_data_dir']}/rentals.db"
         + (f"; last run {last['finished']}" if last else "; no runs yet"))
    if shutil.which("systemctl"):
        prefix = cfg["schedule"]["unit_prefix"]
        r = subprocess.run(["systemctl", "--user", "list-timers", "--all", f"{prefix}*"], capture_output=True, text=True)
        active = [ln for ln in r.stdout.splitlines() if prefix in ln]
        line("ok" if active else "warn", f"schedule: {len(active)} timer(s) installed"
             + ("" if active else " (run `radar schedule install`)"))
    print("\nAll good." if ok else "\nFix the FAIL lines above, then run `radar doctor` again.")
    sys.exit(0 if ok else 1)


# ---------------------------------------------------------------- scheduling

UNIT_SERVICE = """[Unit]
Description=Rental Radar: {what}
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
WorkingDirectory={workdir}
ExecStart={python} -m rental_radar --config {config} {cmd}
TimeoutStartSec={timeout}
"""
UNIT_TIMER = """[Unit]
Description=Rental Radar: {what}

[Timer]
OnCalendar={calendar}
Persistent=true
RandomizedDelaySec={jitter}

[Install]
WantedBy=timers.target
"""


def _units(cfg):
    s = cfg["schedule"]
    tz = f" {s['timezone']}" if s.get("timezone") else ""
    p = s["unit_prefix"]
    return {
        f"{p}-collect": dict(what="collect + score; email top picks right away", cmd="run --mode alerts",
                             calendar=s["collect"] + tz, timeout=3000, jitter=180),
        f"{p}-digest": dict(what="daily digest email", cmd="run --mode digest --no-collect",
                            calendar=s["digest"] + tz, timeout=900, jitter=0),
    }


def cmd_schedule(args):
    cfg = load_config(args.config)
    unit_dir = Path.home() / ".config/systemd/user"
    units = _units(cfg)
    if args.action == "cron":
        py, conf = sys.executable, cfg["_path"]
        log_file = Path(cfg["_data_dir"]) / "cron.log"
        tz = cfg["schedule"].get("timezone")
        print("# Add with `crontab -e`. Adjust the times to match schedule.collect / schedule.digest.")
        if tz:
            print(f"CRON_TZ={tz}")
        print(f"5 6-22 * * * {py} -m rental_radar --config {conf} run --mode alerts >> {log_file} 2>&1")
        print(f"30 7 * * * {py} -m rental_radar --config {conf} run --mode digest --no-collect >> {log_file} 2>&1")
        return
    if not shutil.which("systemctl"):
        sys.exit("systemd isn't available here. Use `radar schedule cron` to get crontab lines instead.")
    if args.action == "install":
        unit_dir.mkdir(parents=True, exist_ok=True)
        for name, u in units.items():
            (unit_dir / f"{name}.service").write_text(UNIT_SERVICE.format(
                python=sys.executable, config=cfg["_path"], workdir=cfg["_dir"], **u))
            (unit_dir / f"{name}.timer").write_text(UNIT_TIMER.format(**u))
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
        subprocess.run(["systemctl", "--user", "enable", "--now", *[f"{n}.timer" for n in units]], check=True)
        r = subprocess.run(["loginctl", "enable-linger", os.environ.get("USER", "")], capture_output=True)
        if r.returncode:
            print(f"Note: run `sudo loginctl enable-linger {os.environ.get('USER', '$USER')}` so the timers "
                  "keep running while you're logged out.")
        print("Installed. Logs: journalctl --user -u " + next(iter(units)) + " -n 50")
    elif args.action == "remove":
        subprocess.run(["systemctl", "--user", "disable", "--now", *[f"{n}.timer" for n in units]])
        for name in units:
            for ext in ("service", "timer"):
                (unit_dir / f"{name}.{ext}").unlink(missing_ok=True)
        subprocess.run(["systemctl", "--user", "daemon-reload"])
        print("Removed. Your config and data are untouched.")
        return
    subprocess.run(["systemctl", "--user", "list-timers", "--all", f"{cfg['schedule']['unit_prefix']}*"])


# ---------------------------------------------------------------- daily use

def cmd_run(args):
    cfg = load_config(args.config)
    errs = validate(cfg)
    if errs:
        sys.exit("Config problems:\n  " + "\n  ".join(errs))
    stats = pipeline.run(_conn(cfg), cfg, mode=args.mode, collect_listings=not args.no_collect,
                         send=not args.no_email, detail_limit=args.detail_limit, preview_path=args.preview)
    print(json.dumps(stats, indent=1))


def cmd_rescore(args):
    cfg = load_config(args.config)
    conn = _conn(cfg)
    pipeline.score_all(conn, cfg)
    pipeline.scam_checks(conn, cfg)
    n = len(pipeline.pending(conn, cfg))
    print(f"Rescored. {n} unemailed listings at/above threshold {cfg['scoring']['email_threshold']}.")


def cmd_top(args):
    cfg = load_config(args.config)
    conn = _conn(cfg)
    rows = conn.execute(
        "SELECT * FROM listings WHERE status!='gone' AND dup_of IS NULL AND score IS NOT NULL "
        "AND (? OR detail_fetched=1) ORDER BY score DESC LIMIT ?", (int(args.all), args.n)).fetchall()
    for r in rows:
        d, a = json.loads(r["data"]), json.loads(r["analysis"])
        c = a["commute"]
        tr = c["transit"]["minutes"] if c.get("transit") else "-"
        flag = "E" if r["emailed_at"] else " "
        print(f"{r['score']:5.1f} {flag} ${d.get('price') or 0:>5,} {d.get('beds') if d.get('beds') is not None else '?'}bd "
              f"{(d.get('sqft') or 0):>5}sf drv{c['drive']:>3} trn{tr!s:>3} "
              f"{a['neighborhood']['name'][:28]:28} {(d.get('housing_type') or '')[:10]:10} {r['uid']}")


def cmd_show(args):
    cfg = load_config(args.config)
    r = db.get_listing(_conn(cfg), args.uid)
    if not r:
        sys.exit(f"not found: {args.uid}")
    d, a = json.loads(r["data"]), json.loads(r["analysis"] or "{}")
    print(emailer.card_text(r, d, a, cfg) if a else json.dumps(d, indent=1))
    if a:
        print(f"\nBase {a.get('base')} · adjustments {a.get('adjustments')} · components {a['components']}")
    if args.raw:
        print(json.dumps({"data": d, "analysis": a}, indent=1))


def cmd_preview(args):
    cfg = load_config(args.config)
    conn = _conn(cfg)
    items = pipeline.pending(conn, cfg, include_emailed=True)[: args.n]
    if not items:
        sys.exit("No listings above the threshold yet. Run `radar run --no-email` first.")
    msg = emailer.build_email(items, cfg, args.subject_prefix, args.intro)
    out = Path(args.out or Path(cfg["_data_dir"]) / "preview.html")
    out.write_text(msg.get_body(("html",)).get_content())
    print(f"Wrote {len(items)} listings to {out}")
    if args.send:
        emailer.send(msg, cfg)
        print("Sent.")


def cmd_baseline(args):
    """Mark every current above-threshold listing as already emailed (e.g. after a starter digest)."""
    cfg = load_config(args.config)
    conn = _conn(cfg)
    items = pipeline.pending(conn, cfg)
    ts = db.now()
    conn.executemany("UPDATE listings SET emailed_at=? WHERE uid=?", [(ts, r["uid"]) for r, _, _ in items])
    conn.commit()
    print(f"Marked {len(items)} listings as already emailed.")


def cmd_mark(args):
    cfg = load_config(args.config)
    conn = _conn(cfg)
    cur = conn.execute("UPDATE listings SET status=? WHERE uid=?", (args.status, args.uid))
    conn.commit()
    print("Updated." if cur.rowcount else f"not found: {args.uid}")


def cmd_test_email(args):
    cfg = load_config(args.config)
    msg = emailer.message(f"{cfg['search']['label']}: test email",
                          "If you can read this, Rental Radar can reach you.", None, cfg)
    emailer.send(msg, cfg)
    print("Sent a test email to", msg["To"])


# ---------------------------------------------------------------- entry point

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="radar", description="Rental Radar: collect, score, scam-screen and email "
                                                          "rental listings.")
    p.add_argument("--config", help="path to config.yaml (default: ./config.yaml or $RADAR_CONFIG)")
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("--version", action="version", version=f"rental-radar {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    i = sub.add_parser("init", help="create config.yaml and friends (interactive wizard)")
    i.add_argument("--dir", default=".", help="directory to create the files in (default: current)")
    i.add_argument("--example", help="copy a worked example instead (e.g. east-bay)")
    i.add_argument("--force", action="store_true", help="overwrite existing files")
    i.add_argument("--yes", "-y", action="store_true", help="accept defaults for anything not given as a flag")
    for flag, kw in [("--label", {}), ("--destination", {}), ("--dest-label", {}), ("--state", {}),
                     ("--radius-km", {"type": float}), ("--cities", {}), ("--max-rent", {"type": int}),
                     ("--target", {"type": int}), ("--hard-max", {"type": int}), ("--min-beds", {"type": int}),
                     ("--ideal-beds", {"type": int}), ("--move-in", {}), ("--dog-weight", {"type": int}),
                     ("--max-commute", {"type": int}), ("--email", {}), ("--smtp-host", {}),
                     ("--craigslist-site", {})]:
        i.add_argument(flag, **kw)
    i.add_argument("--cat", action="store_true")

    g = sub.add_parser("geocode", help="look up an address's coordinates (US Census geocoder)")
    g.add_argument("address")

    dr = sub.add_parser("doctor", help="check config, credentials, network and schedule")
    dr.add_argument("--smtp", action="store_true", help="also test the SMTP login")
    dr.add_argument("--offline", action="store_true", help="skip network checks")

    sc = sub.add_parser("schedule", help="install/remove systemd timers, or print crontab lines")
    sc.add_argument("action", choices=["install", "remove", "status", "cron"])

    r = sub.add_parser("run", help="collect, score, and email new matches")
    r.add_argument("--mode", choices=["all", "alerts", "digest"], default="all",
                   help="alerts: email only top picks; digest: everything new above the threshold plus the "
                        "running top list; all (default): everything new above the threshold")
    r.add_argument("--no-email", action="store_true", help="write preview HTML instead of sending")
    r.add_argument("--preview", help="path for preview HTML (with --no-email)")
    r.add_argument("--no-collect", action="store_true", help="skip collection; just score and email")
    r.add_argument("--detail-limit", type=int, default=300, help="max detail pages per source per run")

    t = sub.add_parser("top", help="list the best-scoring active listings")
    t.add_argument("-n", type=int, default=25)
    t.add_argument("--all", action="store_true", help="include listings without details fetched")

    s = sub.add_parser("show", help="pros/cons and score breakdown for one listing")
    s.add_argument("uid")
    s.add_argument("--raw", action="store_true")

    pv = sub.add_parser("preview", help="render (and optionally send) an email of current top matches")
    pv.add_argument("-n", type=int, default=15)
    pv.add_argument("--out")
    pv.add_argument("--send", action="store_true")
    pv.add_argument("--subject-prefix", default="")
    pv.add_argument("--intro", default="", help="note shown at the top of the email")

    m = sub.add_parser("mark", help="mark a listing interested / rejected / active")
    m.add_argument("uid")
    m.add_argument("status", choices=["interested", "rejected", "active"])

    sub.add_parser("test-email", help="send a test email")
    sub.add_parser("rescore", help="re-score everything (after editing config.yaml or areas.yaml)")
    sub.add_parser("baseline", help="mark all current matches as already emailed")
    return p


COMMANDS = {"init": cmd_init, "geocode": cmd_geocode, "doctor": cmd_doctor, "schedule": cmd_schedule,
            "run": cmd_run, "top": cmd_top, "show": cmd_show, "preview": cmd_preview, "mark": cmd_mark,
            "test-email": cmd_test_email, "rescore": cmd_rescore, "baseline": cmd_baseline}


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.config:
        args.config = str(config_path(args.config))
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    try:
        COMMANDS[args.cmd](args)
    except (SystemExit, KeyboardInterrupt):
        raise
    except FileNotFoundError as e:
        sys.exit(str(e))
    except Exception:
        log.error(traceback.format_exc())
        sys.exit(1)


if __name__ == "__main__":
    main()

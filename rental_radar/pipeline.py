"""The run cycle: collect -> score -> scam checks -> retractions -> email. See docs/architecture.md."""
import contextlib
import json
import logging
import statistics
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from . import analyze, db, emailer, geo, notify, sales, verify
from .config import move_in
from .sources import SOURCES, Incomplete

log = logging.getLogger("radar")


def in_bbox(d, cfg) -> bool:
    b = cfg["search"]["bbox"]
    return d.get("lat") is not None and d.get("lon") is not None and \
        b["south"] <= d["lat"] <= b["north"] and b["west"] <= d["lon"] <= b["east"]


def in_area(name, d, cfg) -> bool:
    """Scraped sources stay inside the search box. Saved-search alerts cover areas you drew yourself on the
    site, so keep anything within `alert_radius_km` of the destination and let commute scoring judge it."""
    if name != "alerts":
        return in_bbox(d, cfg)
    dest = cfg["destination"]
    return d.get("lat") is not None and \
        geo.km(d["lat"], d["lon"], dest["lat"], dest["lon"]) <= cfg["search"]["alert_radius_km"]


def optimistic(d, ff, cfg) -> float:
    """Upper-bound score before fetching details: assume the details will be good news."""
    extra = " large dogs welcome cats ok private fenced yard"
    mi = move_in(cfg)
    if mi:
        extra += f" available {mi:%b} {mi.day}"
    score, _ = analyze.analyze(dict(d, body=(d.get("body") or "") + extra), ff, cfg)
    return score


MAX_DETAIL_ERRORS = 5     # consecutive failed detail fetches before giving up on a source for this run


def _last_collected(conn, name) -> datetime | None:
    """When this source last completed a search (from the run log)."""
    for r in conn.execute("SELECT finished, summary FROM runs ORDER BY finished DESC LIMIT 200"):
        if isinstance(json.loads(r["summary"]).get(name), dict):
            return datetime.fromisoformat(r["finished"])
    return None


def _mark_gone(conn, name, found, seen) -> str | None:
    """Listings missing from a complete search are gone (rented/removed). Returns a reason when marking
    was skipped or limited because the results can't be trusted to be complete."""
    rows = conn.execute("SELECT uid FROM listings WHERE source=? AND status!='gone'", (name,)).fetchall()
    keep: tuple[str, ...] = ()
    reason = None
    if isinstance(found, Incomplete):
        reason = found.reason or "partial results"
        if found.unknown_prefixes is None:
            return reason
        keep = found.unknown_prefixes
    elif len(rows) >= 20 and len(seen) < 0.25 * len(rows):
        # A sudden collapse is far more likely a broken or blocked source than everything renting at once
        return f"found {len(seen)} but {len(rows)} were active; not marking any as gone"
    for row in rows:
        if row["uid"] not in seen and not (keep and row["uid"].startswith(keep)):
            conn.execute("UPDATE listings SET status='gone' WHERE uid=?", (row["uid"],))
    return reason


def collect(conn, cfg, detail_limit: int) -> dict:
    stats = {}
    for name, mod in SOURCES.items():
        src = cfg["sources"].get(name, {})
        if not src.get("enabled"):
            continue
        every = src.get("interval_hours") or 0
        last = _last_collected(conn, name) if every else None
        if last and datetime.now(UTC) - last < timedelta(hours=every) - timedelta(minutes=10):
            log.info("%s: searched %s; next search is %sh after that (interval_hours)", name, last, every)
            continue
        try:
            results = mod.search(cfg, conn)
            found = [d for d in results if in_area(name, d, cfg)]
        except Exception as e:
            log.error("%s search failed: %s", name, e)
            stats[name] = f"search failed: {e}"
            continue
        seen = {d["uid"] for d in found}
        new = sum(db.upsert_listing(conn, d, detail_fetched=False) for d in found)
        incomplete = None
        if getattr(mod, "MARK_GONE", True):
            partial = Incomplete(found, results.unknown_prefixes, results.reason) \
                if isinstance(results, Incomplete) else found
            incomplete = _mark_gone(conn, name, partial, seen)
            if incomplete:
                log.warning("%s: %s", name, incomplete)
        conn.commit()

        # Fetch details only for promising, not-yet-detailed listings
        rows = [db.get_listing(conn, uid) for uid in seen]
        ds = [json.loads(r["data"]) for r in rows if not r["detail_fetched"]]
        ffs = geo.drive_minutes(conn, [(d["lat"], d["lon"]) for d in ds], cfg)
        cands = sorted(((optimistic(d, ff, cfg), d) for d, ff in zip(ds, ffs)), key=lambda x: -x[0])
        cands = [(s, d) for s, d in cands if s >= cfg["scoring"]["email_threshold"]]
        fetched = gone = errors = streak = 0
        for _, d in cands[:detail_limit]:
            try:
                full = mod.fetch_detail(d, cfg)
            except Exception as e:
                log.warning("detail failed %s: %s", d["uid"], e)
                errors += 1
                streak += 1
                if streak >= MAX_DETAIL_ERRORS:
                    log.error("%s: %d detail pages in a row failed; stopping detail fetches for this run "
                              "(they will be retried next run)", name, streak)
                    break
                continue
            streak = 0
            if full is None:
                conn.execute("UPDATE listings SET status='gone' WHERE uid=?", (d["uid"],))
                gone += 1
            else:
                db.upsert_listing(conn, full, detail_fetched=True)
                fetched += 1
            if (fetched + gone) % 25 == 0:
                conn.commit()
                log.info("%s: %d details fetched", name, fetched)
        conn.commit()
        stats[name] = {"found": len(found), "new": new, "promising": len(cands), "details": fetched,
                       "deleted_posts": gone, "deferred": max(0, len(cands) - detail_limit)}
        if errors:
            stats[name]["detail_errors"] = errors
        if incomplete:
            stats[name]["incomplete"] = incomplete
        log.info("%s: %s", name, stats[name])
    return stats


def score_all(conn, cfg) -> int:
    """Re-score every active listing and mark cross-posts / reposts as duplicates of the best copy.
    Returns how many listings had no routed drive time (their commute is a straight-line estimate)."""
    rows = db.active_listings(conn)
    ds = [json.loads(r["data"]) for r in rows]
    ffs = geo.drive_minutes(conn, [(d["lat"], d["lon"]) for d in ds], cfg)
    scored = []
    for r, d, ff in zip(rows, ds, ffs):
        s, a = analyze.analyze(d, ff, cfg)
        scored.append((s, r, d, a))
    # Canonical copy: already emailed > detail fetched > higher score
    canon: dict = {}
    scored.sort(key=lambda x: (-int(bool(x[1]["emailed_at"])), -x[1]["detail_fetched"], -x[0]))
    for s, r, d, a in scored:
        keys = [("loc", round(d["lat"], 3), round(d["lon"], 3), d.get("beds"), round((d.get("price") or 0) / 100))]
        if d.get("title"):
            keys.append(("title", d["title"].strip().lower(), d.get("price")))
        dup_of = next((canon[k] for k in keys if k in canon), None)
        for k in keys:
            canon.setdefault(k, r["uid"])
        db.save_analysis(conn, r["uid"], a, s, dup_of)
    conn.commit()
    return sum(ff is None for ff in ffs)


def scam_checks(conn, cfg) -> int:
    """Sale-history index, address/property checks, and posting-link checks; then re-score."""
    if cfg["search"].get("cities"):
        try:
            sales.refresh(conn, cfg["search"]["cities"], cfg["search"]["state"])
        except Exception as e:
            log.warning("sale index refresh failed: %s", e)
    verify.verify_all(conn, cfg, cfg["scoring"]["email_threshold"] - 15)
    verify.check_links(conn, cfg)
    return score_all(conn, cfg)


def pending(conn, cfg, include_emailed=False, min_score=None):
    q = """SELECT * FROM listings WHERE status='active' AND mark IS NOT 'rejected' AND dup_of IS NULL
           AND score >= ? AND detail_fetched=1 {} ORDER BY score DESC"""
    q = q.format("" if include_emailed else "AND emailed_at IS NULL")
    rows = conn.execute(q, (min_score if min_score is not None else cfg["scoring"]["email_threshold"],)).fetchall()
    return [(r, json.loads(r["data"]), json.loads(r["analysis"])) for r in rows]


def top_open(conn, cfg, n=20):
    """Best listings still on the market (emailed before or not), for the digest's running list.
    Alert-email listings can't be re-checked, so they drop off after 21 days."""
    since = (datetime.now(UTC) - timedelta(days=21)).isoformat()
    scraped = tuple(k for k, m in SOURCES.items() if getattr(m, "MARK_GONE", True))
    rows = conn.execute(
        f"""SELECT * FROM listings WHERE status='active' AND mark IS NOT 'rejected' AND dup_of IS NULL AND detail_fetched=1
           AND score >= ? AND (source IN ({','.join('?' * len(scraped))}) OR last_seen >= ?)
           ORDER BY score DESC""", (cfg["scoring"]["email_threshold"] - 7, *scraped, since)).fetchall()
    out = []
    for r in rows:
        d, a = json.loads(r["data"]), json.loads(r["analysis"])
        if a.get("scam_level") in ("likely", "suspicious") or a.get("address_fake") or d.get("retracted_at"):
            continue
        out.append((r, d, a))
    return out[:n]


def retractions(conn, cfg, send=True, preview_path: Path | None = None) -> int:
    """Warn recipients about previously emailed listings that now look like scams."""
    items = []
    for r in conn.execute("SELECT * FROM listings WHERE emailed_at IS NOT NULL AND analysis IS NOT NULL").fetchall():
        d, a = json.loads(r["data"]), json.loads(r["analysis"])
        if a.get("scam_level") in ("likely", "suspicious") and not d.get("retracted_at") and r["mark"] != "rejected":
            items.append((r, d, a))
    if not items:
        return 0
    msg = emailer.build_retraction(items, cfg)
    if not send:
        out = (preview_path or Path(cfg["_data_dir"]) / "preview.html").with_suffix(".retraction.html")
        out.write_text(msg.get_body(("html",)).get_content())
        log.info("Retraction preview (%d listings) written to %s", len(items), out)
        return len(items)
    notify.send_retraction(items, cfg)
    for r, d, _ in items:
        d["retracted_at"] = db.now()
        conn.execute("UPDATE listings SET data=? WHERE uid=?", (json.dumps(d), r["uid"]))
    conn.commit()
    log.info("Sent scam retraction for %d listings", len(items))
    return len(items)


def source_health(conn, cfg, days: int = 14) -> list[str]:
    """Plain-English warnings for sources that failed or suddenly found far fewer listings than usual.

    Scrapers break silently when a site changes, so this compares each source's latest collection run
    with its own recent history (from the `runs` table) and the digest email shows the result."""
    since = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    runs = [json.loads(r["summary"]) for r in conn.execute(
        "SELECT summary FROM runs WHERE finished >= ? ORDER BY finished DESC", (since,))]
    warnings = []
    for name in SOURCES:
        if not cfg["sources"].get(name, {}).get("enabled"):
            continue
        history = [r[name] for r in runs if name in r]
        if not history:
            continue
        latest = history[0]
        if isinstance(latest, str):
            streak = next((i for i, h in enumerate(history) if not isinstance(h, str)), len(history))
            warnings.append(f"{name}: the last {streak} run{'s' if streak > 1 else ''} failed ({latest[:160]}). "
                            "The site may have changed or be blocking requests; run `radar doctor` and check the log.")
            continue
        if latest.get("incomplete"):
            warnings.append(f"{name}: results were incomplete ({latest['incomplete'][:160]}).")
        if latest.get("detail_errors", 0) >= MAX_DETAIL_ERRORS:
            warnings.append(f"{name}: {latest['detail_errors']} listing pages failed to load in the latest run. "
                            "The site may be blocking or rate-limiting requests.")
        prev = [h["found"] for h in history[1:] if isinstance(h, dict)]
        if len(prev) >= 3:
            typical = statistics.median(prev)
            if typical >= 20 and latest["found"] < 0.25 * typical:
                warnings.append(f"{name}: found {latest['found']} listings in the latest run (usually about "
                                f"{typical:.0f}). The site may have changed; run `radar doctor` and check the log.")
    scored = next((r for r in runs if "commute_estimated" in r), None)
    if scored and scored["commute_estimated"] and scored["commute_estimated"] >= 0.2 * max(1, scored.get("scored", 0)):
        warnings.append(f"routing: {scored['commute_estimated']} listings have straight-line commute estimates "
                        "because the routing service (OSRM) didn't answer. Drive times may be off; see "
                        "`services.osrm_url` in docs/configuration.md.")
    return warnings


@contextlib.contextmanager
def run_lock(cfg, wait_seconds: int = 3600):
    """One run per search folder at a time. A second run (say the digest while collection is still going)
    waits its turn instead of colliding in the database."""
    import fcntl
    path = Path(cfg["_data_dir"]) / "run.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as fh:
        deadline = time.monotonic() + wait_seconds
        waiting = False
        while True:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise RuntimeError(f"another radar run has held {path} for over {wait_seconds // 60} minutes; "
                                       "giving up") from None
                if not waiting:
                    log.info("Another radar run is in progress for this search; waiting for it to finish")
                    waiting = True
                time.sleep(1)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def run(conn, cfg, mode="all", collect_listings=True, send=True, detail_limit=300, preview_path=None) -> dict:
    with run_lock(cfg):
        return _run(conn, cfg, mode, collect_listings, send, detail_limit, preview_path)


def _run(conn, cfg, mode, collect_listings, send, detail_limit, preview_path) -> dict:
    started = db.now()
    stats = collect(conn, cfg, detail_limit) if collect_listings else {}
    score_all(conn, cfg)
    stats["commute_estimated"] = scam_checks(conn, cfg)
    stats["scored"] = conn.execute("SELECT COUNT(*) FROM listings WHERE status!='gone'").fetchone()[0]
    stats["retracted"] = retractions(conn, cfg, send, preview_path)
    top = []
    if mode == "alerts":
        # Between digests, only top picks go out right away; the rest wait for the digest
        items = pending(conn, cfg, min_score=cfg["scoring"]["top_pick_threshold"])[: cfg["email"]["max_per_email"]]
    else:
        items = pending(conn, cfg)[: cfg["email"]["digest_max"]]
        if mode == "digest":
            top = top_open(conn, cfg, cfg["email"]["digest_top"])
    stats["emailed"] = len(items)
    # Record this run first so the health check sees its results
    conn.execute("INSERT INTO runs VALUES (?,?,?)", (started, db.now(), json.dumps(stats)))
    conn.commit()
    warnings = source_health(conn, cfg) if mode != "alerts" else []
    for w in warnings:
        log.warning("source health: %s", w)
    if items or top or warnings:
        if not send:
            msg = emailer.build_email(items, cfg, kind=mode, top=top, warnings=warnings)
            out = Path(preview_path or Path(cfg["_data_dir"]) / "preview.html")
            out.write_text(msg.get_body(("html",)).get_content())
            out.with_suffix(".chat.txt").write_text(notify.chat_preview(items, cfg, top, warnings))
            log.info("Preview (%d listings) written to %s (chat version: %s)", len(items), out,
                     out.with_suffix(".chat.txt").name)
            stats["emailed"] = 0
            stats["preview"] = str(out)
        else:
            stats["channels"] = notify.send_matches(items, cfg, kind=mode, top=top, warnings=warnings)
            ts = db.now()
            conn.executemany("UPDATE listings SET emailed_at=? WHERE uid=?", [(ts, r["uid"]) for r, _, _ in items])
            log.info("Emailed %d listings", len(items))
    conn.execute("UPDATE runs SET summary=? WHERE started=?", (json.dumps(stats), started))
    conn.commit()
    return stats

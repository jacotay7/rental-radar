# AGENTS.md

Instructions for AI coding agents (Claude Code, Codex, Cursor, Copilot, ...) working in this repo,
whether you're changing the code or helping a person run their own rental search with it.

## What this is

Rental Radar is a self-hosted rental search. `radar run` collects listings from several sources,
scores them 0–100 against the user's config, screens them for scams, and sends the best ones by email
and/or Discord, Slack, ntfy or Telegram. A user's search is a **folder** (config.yaml, areas.yaml, portals.yaml, .env, data/) separate
from this code. Start with [README.md](README.md), then [docs/architecture.md](docs/architecture.md).

## Commands

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"   # or: uv venv && uv pip install -e ".[dev]"
.venv/bin/pytest -q                       # offline test suite; run before and after every change
.venv/bin/radar --config <dir>/config.yaml doctor --offline
.venv/bin/radar demo --no-open --chat      # offline sample output, no config needed
.venv/bin/radar --config <dir>/config.yaml run --dry-run --detail-limit 20   # live, but sends nothing
.venv/bin/radar --config <dir>/config.yaml rescore && .venv/bin/radar --config <dir>/config.yaml top
```

## Code map

| Path | What |
|---|---|
| `rental_radar/pipeline.py` | run cycle: collect → score → scam checks → retractions → notify; source health |
| `rental_radar/analyze.py` | **the scoring model** and pros/cons; scam heuristics (`scam_check`, `hijack_signals`, `verify_flags`) |
| `rental_radar/config.py` | `DEFAULTS` (every config key and its default), `finalize`, `validate` |
| `rental_radar/geo.py` | geocoding, neighborhoods, OSRM drive time, transit, bike |
| `rental_radar/sales.py`, `verify.py` | sale-history index, copied-text detection, address and property checks |
| `rental_radar/emailer.py` | email rendering and SMTP |
| `rental_radar/notify.py` | delivery channels (email, Discord, Slack, ntfy, Telegram); chat formatting |
| `rental_radar/demo.py` | `radar demo` sample listings |
| `rental_radar/sources/` | one module per source; registry in `sources/__init__.py` |
| `rental_radar/cli.py`, `templates.py` | `radar` command, `init` wizard templates |
| `examples/` | `east-bay` (complete), `minimal` |
| `docs/` | architecture, scoring, scam-detection, sources, configuration, regions, saved-search-alerts |
| `tests/` | pytest; `conftest.make_cfg()` builds a config without files |
| `.claude/skills/` | `/new-search`, `/tune-search`: the setup and tuning playbooks as Claude Code skills |

## Hard rules

1. **Never commit or print secrets or personal data.** `.env`, users' `config.yaml` / `areas.yaml` /
   `portals.yaml`, and `data/` are git-ignored; keep it that way. Use `you@example.com` and public
   landmarks in examples, docs and tests. Don't paste real listing contact details into fixtures.
2. **Never send a user's personal details to external services.** Don't put names or email addresses
   in User-Agent strings, URL params or request bodies (`http.TOOL_UA` is deliberately anonymous). The
   only things sent out are listing addresses and coordinates being looked up.
3. **No anti-bot circumvention.** Don't add browser-fingerprint impersonation (curl_cffi, tls-client),
   stealth or undetected headless browsers, CAPTCHA solvers, or proxy rotation, and don't "try it
   quickly to see". Sites that block automation (Zillow, HotPads, Trulia, Apartments.com) are covered
   through their alert emails (`sources/alerts.py`). If a source starts blocking, disable it and tell
   the user.
4. **Be polite to sources.** All HTTP goes through `rental_radar.http.get` with a `min_interval`
   (≥ 1 s for HTML pages). Don't parallelize requests to one host or shorten the schedule below
   hourly.
5. **Don't send messages the user didn't ask for.** Use `--dry-run` (writes `data/preview.html` and
   `preview.chat.txt`) while developing or testing. `radar test-notify`, `preview --send` and a live
   `run` message real people on every configured channel: ask first. Never ask users to paste webhook
   URLs, tokens or passwords into the chat; they put them in `.env` themselves. The same goes for `radar schedule install/remove` (they change the user's systemd
   setup).
6. **Don't silently weaken scam checks.** If one misfires on a genuine listing, narrow it (and add a
   test with the false positive), rather than removing it. Explain the trade-off to the user.

## Conventions

- Python ≥ 3.11, standard library + `requests`, `pyyaml`, `homeharvest`. Ask before adding
  dependencies.
- Every user-facing knob goes in `config.DEFAULTS` with a comment, in `docs/configuration.md`, and (if
  common) in `templates.CONFIG`. No hard-coded cities, states, prices or coordinates in code.
  Region data belongs in `areas.yaml` / `portals.yaml`.
- Scoring components return 0..1 and append pros/cons as side effects. Warnings go at the front of
  `cons` (`cons.insert(0, ...)`). Write pros and cons as short, specific, plain-English facts the user
  can act on.
- Regexes run on the lower-cased text from `analyze.listing_text()`.
- Keep the docs in sync. A change to scoring needs `docs/scoring.md`; to scam checks,
  `docs/scam-detection.md`; to sources, `docs/sources.md` and the README table; to config keys,
  `docs/configuration.md`.
- Add a line under `## [Unreleased]` in `CHANGELOG.md` for every user-visible change.
- Add or adjust a test for every behavior change. Tests must stay offline: monkeypatch
  `geo.drive_minutes` and use fixtures.

## Playbooks

### Set up a search for someone
(Claude Code: the `/new-search` skill is this playbook in full.) Interview them (one short batch of questions, not a form), then run `radar init` with flags
(`radar init --help`):

1. **Destination** they commute to, and the **max one-way commute**. Do they drive, take transit,
   bike? Can someone drop them at a station?
2. **Budget**: target, ceiling, and the stretch they'd consider for an exceptional place (`hard_max`).
3. **Bedrooms**: minimum and ideal. Home types they'd accept, and which they prefer.
4. **Household**: pets (dog weight matters: many leases cap at 25–50 lb), kids (yard, stairs,
   laundry), mobility (stairs).
5. **Move-in date**, and how flexible it is.
6. **Areas**: neighborhoods they love, ones to avoid, and what "good neighborhood" means to them.
7. **Where results go**: Discord, Slack, ntfy (phone push), Telegram and/or email. Walk them through
   [docs/notifications.md](docs/notifications.md); they paste the secret into `.env` themselves.

Then translate their priorities into `scoring.weights` (ranked priorities → weights roughly
20/17/14/12/10/8/5), fill `areas.yaml` (next playbook), run `radar doctor`, run
`radar run --dry-run`, show them the top of `radar top`, and adjust before scheduling anything.

### Build areas.yaml for a new region
1. Read [docs/regions.md](docs/regions.md) and use `examples/east-bay/areas.yaml` as the model.
2. List the neighborhoods inside their `bbox` that have rental housing they could afford, typically
   25–60 entries. Get center coordinates (`radar geocode` or a map) and a radius that roughly covers
   each.
3. Score each 1–10 on **their** stated priorities, relative to each other, using the full range.
   Research crime per capita from official city/police data, school ratings, walkability, noise and
   environmental issues (freeways, industry, flood or wildfire zones), and local discussion. Put
   concrete, listing-independent facts in `pros`/`cons`, and don't restate commute.
4. Add `city_defaults` for every city in the box, and dog parks if they have a dog.
5. Write a header comment saying what the scores weigh, when they were made, and that they're
   approximate. Show the user the list with scores and **ask them to correct it**: their knowledge
   beats yours.
6. For transit: stations, coordinates and ride times to the exit station(s) near the destination,
   from the agency's timetables.

### Find property-manager portals
Follow [docs/regions.md](docs/regions.md#finding-property-manager-portals). Open each candidate URL
and confirm it loads and lists homes in their region before adding it. Aim for the managers with the
most single-family homes. Report how many you verified versus guessed (only add verified ones).

### Turn feedback into config
(Claude Code: `/tune-search`.) Use the table in [docs/scoring.md](docs/scoring.md#tuning-recipes). Workflow:
`radar show <uid>` on an example they disagree with → identify the component or adjustment
responsible → change the smallest knob that fixes it → `radar rescore` → show the before/after rank of
a few listings → confirm with the user. Prefer config changes over code changes. When feedback
reveals a missing signal (e.g. "it doesn't notice there's no parking"), add it in `analyze.py` with a
test.

### Investigate a suspected scam
`radar show <uid> --raw` shows `address_check`, `property_check`, `sale_match`, `link_removed` and
all flags. Check the address in Street View, search it for sale and sold history, compare rent to
similar homes, and look for the description elsewhere. If it is a scam the checks missed, add the
signal (generalized, not address-specific) with a test, and `radar mark <uid> rejected`. The next run
sends a retraction if it was already emailed and now scores as likely or suspicious.

### Add a source or fix a broken one
Follow [docs/sources.md](docs/sources.md#adding-a-source). Save an anonymized response sample as a
fixture and write the parser test first. Keep requests minimal (search pages first; detail pages only
via the existing optimistic-score budget).

### Stop a search
`radar --config <dir>/config.yaml schedule remove` (keeps config and data). Confirm with the user
first; they may want a final digest.

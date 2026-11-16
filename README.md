# Rental Radar 🏠📡

**Stop refreshing rental sites. Let the good listings come to you, and leave the scams behind.**

Rental Radar is a small, self-hosted program that searches for a rental for you around the clock.
Every hour it pulls listings from Craigslist, Redfin, Realtor.com, local property-management companies
and your Zillow/HotPads/Trulia saved-search alerts. It scores each one against what *you* care
about: budget, commute, bedrooms, yard, pets, the neighborhoods you love and the ones you don't. It
checks each listing for the tell-tale signs of rental fraud, and then emails you:

- ⭐ **Top-pick alerts within the hour** when something great appears, because good places go fast.
- 📬 **A daily digest** with every new match, plus a running "top 20 still available" shortlist.
- ⚠️ **Scam retractions** if something it already sent you later turns out to be fake.

Every listing comes with a plain-English **pros and cons** list ("Private fenced yard", "Dog weight
limit 40 lb: your 65 lb dog is over it", "Only 2 bedrooms (you prefer 3)"), the commute by car and by
transit, the lister's contact details, and one-click Map / Street View / route links.

It was built for a real cross-country move, searching remotely for a family home with a big dog. Along
the way it caught two convincing scams: a fake street number pasted onto a just-sold home's listing,
and a withdrawn $1.9M for-sale listing reposted as a $3,000 rental. It has since been turned into a
general tool that works anywhere in the US.

```
⭐ East Bay rentals: top pick: 1 new match: best $3,850 · 3bd/2ba · 1,450 sqft · Albany (score 86)

[86] $3,850 · 3bd/2ba · 1,450 sqft · Albany
Drive ~19 min (rush hour est.) · BART ~24 min (walk at El Cerrito Plaza → 12th St Oakland + walk)
PROS:
  + $3,850/mo is within your $4,000 target
  + Two good commute options: ~19 min drive or ~24 min by BART
  + Explicitly welcomes large dogs
  + Private/fenced yard
  + 3 bedrooms
  + Whole house
  + Available Mar 1: lines up with your Mar 1 move
  + Albany: Small-town feel, excellent schools, very close to Point Isabel
CONS:
  - Scam check: posted on Craigslist (anyone can list there, no owner verification)
  - Street parking only
CONTACT:
  Phone: (510) 555-0142
  Use the 'reply' button on the post (email/phone/text options)
```

## Why it's different

| | Typical listing-site alerts | Rental Radar |
|---|---|---|
| Sources | One site | Craigslist, Redfin, Realtor.com, property-manager portals, and alert emails from Zillow, HotPads, Trulia, Apartments.com, Zumper and more, de-duplicated |
| Ranking | Newest first | A 0–100 score from *your* weighted priorities, with the reasons spelled out |
| Commute | Distance as the crow flies | Rush-hour driving (OSRM road routing), plus optional train/metro trips modeled station by station |
| Neighborhoods | None | Your own ratings and notes, with avoid/favorite lists |
| Pets | "Pets allowed" checkbox | Reads weight limits, "small dogs only", pet rent and deposits from the text |
| Scams | Up to you | Fake-address check, a sale-history index that remembers withdrawn listings, copied-description detection, rent-vs-home-value check, a dozen weighted warning signs, and retraction emails |
| Privacy | Your search lives on their servers | Runs on your machine. No accounts, no API keys, no telemetry |

## Quick start

Requirements: Python 3.11+, Linux or macOS, a US search area, and an email account that can send over
SMTP (a Gmail app password works).

```bash
git clone https://github.com/jacotay7/rental-radar.git
cd rental-radar
python3 -m venv .venv && .venv/bin/pip install -e .      # or: uv venv && uv pip install -e .
source .venv/bin/activate

mkdir -p ~/my-search && cd ~/my-search
radar init                 # 2-minute wizard: destination, budget, beds, pets, move-in, email
$EDITOR .env               # SMTP_USER / SMTP_PASSWORD (see "Email setup" below)
radar doctor --smtp        # checks config, credentials, network
radar run --no-email       # first collection; open data/preview.html to see what you'd get
radar test-email           # make sure mail arrives
radar schedule install     # hourly top-pick alerts + daily digest (systemd), or: radar schedule cron
```

Want to see a complete setup first? `radar init --example east-bay` copies the original configuration:
51 rated neighborhoods, BART modeling, 34 property managers.

## Setup in detail

### 1. Install

```bash
git clone https://github.com/jacotay7/rental-radar.git && cd rental-radar
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/radar --version
```

Each search lives in its own folder (config + data), separate from the code. You can run several
searches (say, one for you and one for a relative) from one checkout. Every command takes
`--config path/to/config.yaml`, or reads `./config.yaml`, or `$RADAR_CONFIG`.

### 2. Create your search: `radar init`

The wizard asks for:

| Question | Used for |
|---|---|
| Address you commute to | Commute scoring. Geocoded with the free US Census geocoder |
| Search radius | Initial bounding box (tighten it later) |
| Cities | Realtor.com searches + the sale-history scam index |
| Max rent (+ target, hard max) | Price score; nothing above `hard_max` is collected |
| Min / ideal bedrooms | Size score and hard filter |
| Move-in date | Bonus for listings available around then |
| Dog (weight) / cat | Source filters and pet-policy scoring |
| Max commute | Commute score and hard cap |
| Recipients, SMTP server | Where results go |
| Craigslist site | e.g. `sfbay`, `chicago`, `seattle`, `newyork`, `losangeles` (the subdomain of craigslist.org) |

It writes four files into the folder: `config.yaml`, `areas.yaml`, `portals.yaml` and `.env`.
Everything can be answered with flags for scripted setups (`radar init --help`, `--yes` accepts
defaults).

### 3. Email setup (`.env`)

```bash
SMTP_USER=you@gmail.com
SMTP_PASSWORD=abcdefghijklmnop
```

- **Gmail:** turn on 2-Step Verification, then create an app password at
  <https://myaccount.google.com/apppasswords>. Port 465 is the default.
- **Other providers:** set `email.smtp_host` and `email.smtp_port` in `config.yaml` (587 uses
  STARTTLS). Fastmail, iCloud, Outlook/Microsoft 365 and most others work with an app password.
- Recipients go in `email.to` in `config.yaml`. The sender can be one of them.
- `.env` is created with `chmod 600` and is git-ignored. Never commit it.

### 4. Rate your neighborhoods (`areas.yaml`): the biggest quality lever

Without ratings every listing gets the same neighborhood score, and the ranking leans on price,
commute and size. Add your areas with a 1–10 score and short pros/cons:

```yaml
neighborhoods:
  - {name: "Old Town", lat: 39.7990, lon: -89.6440, radius_km: 1.0, score: 8.0,
     pros: "Walkable shops, farmers market", cons: "Weekend noise"}
city_defaults: {springfield: 6.5}
```

[docs/regions.md](docs/regions.md) explains how to build this (an AI agent can draft it for you; see
[AGENTS.md](AGENTS.md)). The same file holds transit stations (for train/metro commutes) and dog parks.

### 5. Check, preview, go live

```bash
radar doctor --smtp          # every line should be "ok"
radar run --no-email         # collects everything; writes data/preview.html instead of emailing
radar top                    # ranked list in the terminal
radar show craigslist:7912345678   # full pros/cons + score breakdown for one listing
radar preview --send --intro "Starter digest: the best of what's out there right now"
radar baseline               # mark everything current as "already sent" so only new matches email
radar schedule install       # hourly collection + daily digest
```

### 6. Scheduling

`radar schedule install` creates two systemd **user** timers from the `schedule` block of your config:

| Timer | Default | Does |
|---|---|---|
| `rental-radar-collect` | hourly, 6:05am–10:05pm | collect + score; emails **top picks** (score ≥ `top_pick_threshold`) right away |
| `rental-radar-digest` | daily 7:30am | emails every other new match ≥ `email_threshold`, plus the top-20 still-available list |

Times use `schedule.timezone` (e.g. `America/Chicago`), or the machine's zone if unset. Use
`radar schedule status` to see them and `radar schedule remove` to stop everything (config and data
are kept). Logs: `journalctl --user -u rental-radar-collect -n 50`. On macOS or without systemd,
`radar schedule cron` prints equivalent crontab lines.

Found a place? `radar schedule remove`. 🎉

## Commands

| Command | What it does |
|---|---|
| `radar init [--example east-bay]` | Create a search folder (wizard or worked example) |
| `radar doctor [--smtp] [--offline]` | Check config, credentials, network, schedule |
| `radar geocode "<address>"` | Coordinates for an address (for config / areas) |
| `radar run [--mode alerts\|digest\|all] [--no-email] [--no-collect]` | One full cycle |
| `radar top [-n 40] [--all]` | Ranked list of active listings (`E` = already emailed) |
| `radar show <uid> [--raw]` | Pros/cons, contact, score components and adjustments |
| `radar mark <uid> interested\|rejected\|active` | Rejected listings are never emailed again |
| `radar rescore` | Re-score everything after editing `config.yaml` or `areas.yaml` |
| `radar preview [--send] [--intro "..."]` | Render (and optionally send) the current best matches |
| `radar baseline` | Mark all current matches as already emailed |
| `radar test-email` | Send a test message |
| `radar schedule install\|remove\|status\|cron` | Manage the timers |

## How it works

```
 Craigslist ─┐
 Redfin ─────┤                    ┌─ commute (OSRM drive, transit, bike)
 Realtor.com ┼─▶ collect ─▶ enrich ┼─ neighborhood (areas.yaml)          ┌─▶ ⭐ top-pick alert (hourly)
 PM portals ─┤   (dedupe,  │       └─ pets, yard, size, type, stairs...   │
 Alert email ┘    detail)  ▼                                              ├─▶ 📬 daily digest
                   scam checks ─▶ score 0-100 + pros/cons ─▶ email ──────┤
     (address, sale history, copied text,                                 └─▶ ⚠️ scam retraction
      rent vs. value, warning signs)
```

- [docs/architecture.md](docs/architecture.md): the run cycle, data model, de-duplication, storage
- [docs/scoring.md](docs/scoring.md): every score component, weights, adjustments, hard caps, and how to tune them
- [docs/scam-detection.md](docs/scam-detection.md): the fraud checks and why each one exists
- [docs/sources.md](docs/sources.md): each listing source, its limits, and how to add a new one
- [docs/configuration.md](docs/configuration.md): full `config.yaml` reference
- [docs/regions.md](docs/regions.md): building `areas.yaml` and `portals.yaml` for your area
- [docs/saved-search-alerts.md](docs/saved-search-alerts.md): pulling in Zillow, HotPads, Trulia and others through their alert emails

## Sources

| Source | How | Notes |
|---|---|---|
| Craigslist | Site's JSON search API + posting pages | Pet filters applied; price bands split to get past the result cap |
| Redfin | Rentals API, tiled over your bounding box | Also carries many listings syndicated from other rental feeds; per-unit pricing for buildings |
| Realtor.com | [HomeHarvest](https://pypi.org/project/homeharvest/) library, per city | Agent-listed houses, structured pet policies, home value estimates |
| Property managers | Public AppFolio / Buildium / Rentvine vacancy pages | Where many single-family homes are listed *only*. You list them in `portals.yaml` |
| Saved-search alerts | Reads alert emails over IMAP (read-only) | Zillow, HotPads, Trulia, Apartments.com, Zumper, PadMapper, Rent.com, Homes.com... |

Zillow-family sites and Apartments.com block automated access. Rental Radar does **not** try to get
around that; it reads the alert emails those sites send you instead.

## Tuning it to you

The first emails will be about 80% right. Make them yours:

1. Run `radar show <uid>` on a listing that's ranked wrong, and look at the components and adjustments.
2. Change the matching knob in `config.yaml`. For example: raise `scoring.weights.yard` if yards matter
   more; add a neighborhood to `scoring.avoid_areas`; set `walkup_penalty: 0` if stairs don't bother
   you; re-rate an area in `areas.yaml`.
3. Run `radar rescore`, then `radar top` to see the new order right away.

[docs/scoring.md](docs/scoring.md#tuning-recipes) has a feedback → knob table.

## Working with an AI coding agent

This repo is set up for agents like Claude Code, Codex, Cursor and Copilot. [AGENTS.md](AGENTS.md)
gives them the map, the rules (privacy, no anti-bot circumvention, no surprise emails), and playbooks
for the common jobs: interviewing you to set up a search, researching neighborhood ratings for a new
city, finding local property-manager portals, turning feedback like "too many apartments" into config
changes, and adding sources. Try asking:

> "Set up Rental Radar for me. I'm moving to Denver in June, working downtown, two kids and a cat,
> up to $3,200."

## Limitations and responsible use

- **US only.** Address checks use the US Census geocoder and the sources are US sites.
- **Be a good citizen.** Requests are rate-limited per host and the schedule is hourly. Keep it that
  way, keep it personal, and respect each site's terms. If a site blocks automated access, use its
  alert emails instead.
- **Scores are opinions.** Neighborhood ratings come from you (or the example's authors), and the
  scam checks are heuristics. A clean score is not a guarantee: never pay a deposit before a tour
  (in person or live video) and a signed lease.
- **Sites change.** Sources scrape public pages and unofficial APIs that can change without notice.
  `radar doctor` and the per-source counts in each run's log show when one breaks. See
  [docs/sources.md](docs/sources.md).

## Project layout

```
rental_radar/
  cli.py          radar command (init, doctor, schedule, run, top, show, ...)
  pipeline.py     collect -> score -> scam checks -> retractions -> email
  analyze.py      scoring model, pros/cons, scam heuristics
  geo.py          geocoding, neighborhoods, drive/transit/bike commute
  sales.py        sale-history index + copied-description matching
  verify.py       address existence, property records, posting-removed checks
  emailer.py      HTML/text emails, SMTP
  config.py       defaults, loading, validation
  sources/        craigslist, redfin, realtor, portals (AppFolio/Buildium/Rentvine), alerts (IMAP)
examples/         east-bay (complete), minimal (Chicago, essentials only)
docs/             architecture, scoring, scam detection, sources, configuration, regions, alerts
tests/            pytest suite (offline)
```

## Contributing

Issues and pull requests are welcome, especially new region examples (`examples/<region>/`), new
property-manager platforms, and alert-email formats. Run `pytest` before sending changes, and see
[AGENTS.md](AGENTS.md) for conventions. Please don't submit changes that evade bot protection.

## License

MIT. See [LICENSE](LICENSE).

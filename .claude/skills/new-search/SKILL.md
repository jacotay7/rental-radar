---
name: new-search
description: Set up a new Rental Radar search end to end. Interviews the user, creates the search folder with `radar init`, researches and rates neighborhoods, sets up notifications, does a dry run and tunes it. Use when someone wants to start a rental search, says "set this up for me", or runs /new-search.
---

# Set up a new rental search

You are setting up Rental Radar for someone who may not be technical. Do the work for them, explain in
plain words, and keep their personal details private (AGENTS.md "Hard rules" apply throughout).

## 0. Make sure it runs
- In this repo: `.venv/bin/radar --version`. If that fails:
  `python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"` (or `uv venv && uv pip install -e ".[dev]"`).
- Use `.venv/bin/radar` (or `radar` if it's on PATH) for every command below.
- If they haven't seen output yet, run `radar demo` so they know what they're getting.

## 1. Interview (one message, not a form)
Ask everything below in a single friendly message. Accept partial answers and fill gaps with defaults.
1. Where do they commute to (address), how (drive / transit / bike), and the longest acceptable one-way
   commute?
2. Budget: comfortable target, ceiling, and a stretch for an exceptional place.
3. Bedrooms: minimum and ideal. Home types they'd accept or prefer (house, townhouse, apartment...).
4. Household: dog (weight!), cat, kids, stairs OK?
5. Move-in date and flexibility.
6. Neighborhoods or towns they love or want to avoid, and what "good area" means to them (safety,
   schools, walkability, quiet...).
7. Where results should go: Discord, Slack, ntfy (phone push), Telegram, and/or email.

## 2. Create the search folder
Searches live in `searches/<short-name>/` (git-ignored). Run `radar init` non-interactively:

```bash
radar init --dir searches/<name> --yes --label "<Name> rentals" \
  --destination "<address>" --dest-label "<work|campus|...>" --radius-km <15-30> \
  --cities "<City A>, <City B>, ..." --max-rent <ceiling> --target <target> --hard-max <stretch> \
  --min-beds <n> --ideal-beds <n> --move-in <YYYY-MM-DD> [--dog-weight <lb>] [--cat] \
  --max-commute <minutes> [--email "<addr1>, <addr2>"]
```

It geocodes the address, picks the nearest Craigslist site and the timezone. Read the generated
`config.yaml` and adjust:
- `scoring.weights`: rank their priorities and map to roughly 20/17/14/12/10/8/5.
- `scoring.home_types` (e.g. `{apartment: 0.4}`), `walkup_penalty` (0 if stairs are fine),
  `prefer_areas` / `avoid_areas`.
- `search.cities`: every city in the box with rentals they'd consider (Realtor.com searches these).
- Transit: if they ride a train or metro, set `commute.transit` and add stations to `areas.yaml`
  (docs/regions.md#transit).

## 3. Rate neighborhoods (the biggest quality lever)
Follow AGENTS.md "Build areas.yaml for a new region" and docs/regions.md. Use web research:
25–60 neighborhoods inside `search.bbox`, each with coordinates (`radar geocode "<place>"`), a radius, a
1–10 score on *their* priorities using the whole range, and short listing-independent pros/cons. Add
`city_defaults` for every city, and dog parks if they have a dog. Then **show them a table of the scores
and ask for corrections**. Their local knowledge beats your research.

Optional: find property-manager portals (AGENTS.md playbook). Only add ones you opened and confirmed list
homes in their area, and say how many you verified.

## 4. Notifications
For the channel(s) they chose, walk them through getting the secret, and have them paste it into
`searches/<name>/.env` themselves. **Never ask them to paste a webhook URL, token or password into the
chat.** docs/notifications.md has step-by-step instructions for each channel. Email needs `email.to` in
config.yaml plus SMTP credentials (Gmail requires an app password).

## 5. Check, dry run, calibrate
```bash
radar --config searches/<name>/config.yaml doctor
radar --config searches/<name>/config.yaml run --dry-run --detail-limit 40
radar --config searches/<name>/config.yaml top -n 20
```
A first run takes a few minutes. Show them the top ~10 with one line each, and open
`searches/<name>/data/preview.html` for them. Ask what looks wrong and fix it with the
/tune-search approach. Calibrate the thresholds: if more than ~30 listings score at or above
`email_threshold`, raise it; if fewer than ~3 do, look at `radar top --all` and lower it or widen the
search.

## 6. Go live (ask first)
Ask before each of these, since they message real people or change their system:
- `radar --config searches/<name>/config.yaml test-notify`
- `radar --config searches/<name>/config.yaml baseline`: optional, so the first digest only has listings
  that are actually new.
- `radar --config searches/<name>/config.yaml schedule install` (Linux/systemd), or
  `schedule cron` for crontab lines (macOS). The machine has to be on for runs to happen.

Finish with a short summary: where the folder is, what will arrive and when, and how to change things
(say "run /tune-search", or edit config.yaml and run `radar rescore`).

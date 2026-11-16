# Configuration reference

A search is a folder containing:

| File | Purpose | Commit it? |
|---|---|---|
| `config.yaml` | everything below | your choice (contains recipient emails) |
| `areas.yaml` | neighborhood ratings, transit stations, dog parks ([regions.md](regions.md)) | yes |
| `portals.yaml` | property-manager portals ([sources.md](sources.md#property-manager-portals-sourcesportalspy--portalsyaml)) | yes |
| `.env` | webhook URLs, SMTP / IMAP credentials | **never** |
| `data/` | SQLite database, previews | no |

`radar` finds the config through `--config PATH`, then `$RADAR_CONFIG`, then `./config.yaml`. Paths
inside the config are relative to its folder. Anything you leave out falls back to
`rental_radar/config.py: DEFAULTS`, so a working config can be [short](../examples/minimal/config.yaml).
`radar doctor` validates it.

## `search`

| Key | Default | Meaning |
|---|---|---|
| `label` | `"Rental search"` | Name used in email subjects |
| `state` | `"CA"` | Two-letter US state: address parsing, Realtor.com searches |
| `bbox` | required | `{south, north, west, east}` in degrees. Scraped listings outside it are dropped; Redfin searches exactly this box |
| `cities` | `[]` | Cities searched on Realtor.com and indexed for sale history. Needed if `realtor` is enabled |
| `alert_radius_km` | `50` | Alert-email listings are kept within this distance of the destination |
| `move_in` | `null` | `YYYY-MM-DD`; enables the availability adjustment |

## `destination`

| Key | Meaning |
|---|---|
| `label` | e.g. `work`, `campus`, `downtown`; appears in pros and email links |
| `address` | for the "Route to ..." link (optional) |
| `lat`, `lon` | required (`radar geocode "<address>"`) |

## `budget`

| Key | Default | Meaning |
|---|---|---|
| `great` | 87.5% of `target` | full price score at or below |
| `target` | required | still a "pro" up to here (score 0.8) |
| `max` | required | your ceiling (score 0.5) |
| `hard_max` | required | nothing above this is collected |
| `over_max_min_base` | `80` | listings over `max` are only emailed if their base score reaches this |

## `size`

| Key | Default | Meaning |
|---|---|---|
| `min_beds` | `1` | hard filter (sources) and cap (score ≤ 30 below it) |
| `ideal_beds` | `2` | full bedroom marks |
| `below_ideal_penalty` | `6` | points off between min and ideal |
| `tiny_sqft` / `great_sqft` | `600` / `1300` | ends of the square-footage curve |
| `min_sqft` / `good_sqft` | `750` / `1000` | thresholds for the "Small" con and "Roomy" pro |

## `pets`

| Key | Default | Meaning |
|---|---|---|
| `dog` | `false` | filter sources to dog-friendly listings and score dog policy |
| `dog_weight_lb` | `null` | weight limits below this are dealbreakers; ≥ 40 earns the "large dogs welcome" bonus |
| `cat` | `false` | score cat policy (and filter Craigslist) |

With neither, the `pets` component is dropped from scoring.

## `commute`

| Key | Default | Meaning |
|---|---|---|
| `max_minutes` | `35` | best of drive/transit; beyond `+5` caps the score at 40 |
| `great_minutes` | `12` | full commute marks |
| `rush_hour_factor` | `1.35` | multiplier on free-flow drive time |
| `parking_minutes` | `3` | added to every drive |
| `bike_max_km` | `6` | show a bike estimate within this distance |
| `transit.enabled` | `false` | model a train/metro commute |
| `transit.name` | `"Transit"` | shown in emails ("BART", "the L", "Metro") |
| `transit.avg_wait` | `6` | minutes |
| `transit.walk_km` | `1.0` | walk to stations this close, otherwise drop-off/drive |
| `transit.max_access_minutes` | `12` | skip stations farther than this by car |
| `transit.exits` | `{}` | `{CODE: {name, last_mile_minutes, last_mile_mode}}`: the station(s) near your destination. Ride times to each exit go in `areas.yaml` |

## `scoring`

| Key | Default | Meaning |
|---|---|---|
| `weights` | price 15, commute 15, pets 10, neighborhood 20, size 15, home_type 12, yard 8 | relative importance (normalized; 0 disables) |
| `email_threshold` | `72` | daily digest |
| `top_pick_threshold` | `80` | instant email |
| `walkup_penalty` | `8` | stairs (0 disables) |
| `avoid_areas` / `avoid_area_penalty` | `[]` / `10` | substring match on neighborhood name |
| `prefer_areas` / `prefer_area_bonus` | `[]` / `4` | |
| `low_area_score` | `5.5` | areas below this get a warning con |
| `home_types` | `{}` | override entries of the home-type table, e.g. `{condo: 0.7}` |
| `availability` | see below | `early_days 21, late_days 10, match_bonus 5, early_penalty 3, late_penalty 8, available_now_penalty 2` |

## `scam`

| Key | Default | Meaning |
|---|---|---|
| `rent_to_value_likely_pct` | `0.20` | monthly rent below this % of estimated value = likely scam |
| `rent_to_value_caution_pct` | `0.24` | below this = warning sign |
| `too_cheap_rent` | 45% of `budget.target` | any 2BR+ below this is flagged |
| `too_cheap_house_rent` | 65% of `budget.target` | 3BR+ house in a well-rated area below this is flagged |
| `low_rent_per_sqft` | `2.0` | $/sq ft warning (1,200+ sq ft, top-rated area, no value estimate) |
| `very_low_rent_per_sqft` | `1.6` | $/sq ft "verify" (1,500+ sq ft anywhere) |

Set the `too_cheap_*` values from your market: roughly half of typical rent for that size. See
[scam-detection.md](scam-detection.md).

## `email`

| Key | Default | Meaning |
|---|---|---|
| `to` | `[]` | recipients (empty = no email; use chat/push channels instead) |
| `from` | `SMTP_USER` | sender address |
| `smtp_host` / `smtp_port` | `smtp.gmail.com` / `465` | 465 = implicit TLS, anything else = STARTTLS |
| `max_per_email` | `15` | top-pick alerts |
| `digest_max` | `30` | new listings per digest |
| `digest_top` | `20` | length of the "still available" list |
| `safety_tips` | `[]` | extra region-specific tips (county assessor URL, deposit laws, ...) |

## `notify`

Chat and push channels are switched on by their secrets in `.env` (below). Email is on when `email.to`
is set. See [notifications.md](notifications.md).

| Key | Default | Meaning |
|---|---|---|
| `max_listings` | `10` | new listings (and "still available" rows) per chat message batch; the email has everything |

## `sources`

`craigslist: true` is shorthand for `craigslist: {enabled: true}`.

| Source | Keys (defaults) |
|---|---|
| `craigslist` | `enabled` (false), `site`, `postal`, `distance_mi` (20) |
| `redfin` | `enabled` (true), `market` (null) |
| `realtor` | `enabled` (true) |
| `pm-portal` | `enabled` (false), `file` (`portals.yaml`) |
| `alerts` | `enabled` (false), `imap_host` (`imap.gmail.com`), `lookback_days` (14), `filtered_sites`, `site_housing` ({}), `extra_senders` ({}) |

## `schedule`

Used by `radar schedule install` (systemd user timers) and `radar schedule cron`.

| Key | Default | Meaning |
|---|---|---|
| `unit_prefix` | `rental-radar` | change it to run several searches side by side |
| `collect` | `*-*-* 06..22:05:00` | systemd `OnCalendar` for collection + top-pick alerts |
| `digest` | `*-*-* 07:30:00` | daily digest |
| `timezone` | machine zone | e.g. `America/New_York` |

Re-run `radar schedule install` after changing these.

## Other

| Key | Default | Meaning |
|---|---|---|
| `areas_file` | `areas.yaml` | |
| `data_dir` | `data` | database and previews |

## `.env`

```bash
# Chat / push channels (each one is optional; see notifications.md)
DISCORD_WEBHOOK_URL=
SLACK_WEBHOOK_URL=
NTFY_URL=https://ntfy.sh/your-long-random-topic-name
NTFY_TOKEN=                  # only for a private ntfy server
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
# Email
SMTP_USER=you@gmail.com
SMTP_PASSWORD=app-password
# optional, for reading alert emails from a different mailbox:
IMAP_USER=
IMAP_PASSWORD=
```

`GMAIL_USER` / `GMAIL_APP_PASSWORD` are accepted as fallbacks. Real environment variables take
precedence over `.env`.

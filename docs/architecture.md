# Architecture

Rental Radar is a single Python package with no server and no daemon. A scheduler (systemd timers or
cron) runs `radar run` periodically; each run is a short batch job that reads and writes one SQLite
database.

## The run cycle (`pipeline.run`)

```
collect ──▶ score_all ──▶ scam_checks ──▶ retractions ──▶ select ──▶ notify
```

1. **Collect** (`pipeline.collect`). For each enabled source in `sources/`:
   1. `search(cfg, conn)` returns normalized listings (search-level fields only).
   2. Drop anything outside `search.bbox`. Alert-email listings instead have to be within
      `alert_radius_km` of the destination, because you drew their area yourself on the site.
   3. Upsert into `listings`. A listing that was in the database but missing from this search is
      marked `gone` (rented or removed), except for sources with `MARK_GONE = False` (alert emails don't
      repeat).
   4. Compute an **optimistic score** for each listing without details. Fetch `fetch_detail()` for those
      that could reach `email_threshold`, best first, up to `--detail-limit`. A detail fetch that finds
      the post deleted marks it `gone`.
2. **Score** (`pipeline.score_all`). Re-score every non-gone listing with `analyze.analyze`, then run
   de-duplication (see [scoring.md](scoring.md#de-duplication)).
3. **Scam checks** (`pipeline.scam_checks`): refresh the sale-history index (about daily), verify
   addresses and look up property records for plausible candidates, re-open DIY-platform postings,
   then re-score. See [scam-detection.md](scam-detection.md).
4. **Retractions**: send a warning about anything already sent that now looks like a scam.
5. **Select**:
   - `--mode alerts` (hourly): un-emailed listings ≥ `top_pick_threshold`, up to `max_per_email`.
   - `--mode digest` (daily, usually with `--no-collect`): un-emailed listings ≥ `email_threshold`,
     up to `digest_max`, plus the top `digest_top` still-available listings (emailed or not, excluding
     scam levels *likely/suspicious*; alert-email listings expire after 21 days because they can't be
     re-checked).
   - `--mode all`: like digest but without the running list.
6. **Notify** every active channel (`notify.py`: email, Discord, Slack, ntfy, Telegram), then stamp
   `emailed_at` (it means "sent" on any channel). One channel failing doesn't block the others. A run
   fails only if every channel fails, so nothing is wrongly marked as sent. With `--dry-run`
   (`--no-email`), the email goes to `data/preview.html` and the chat version to
   `data/preview.chat.txt`, and nothing is stamped.

Each run appends a JSON summary (per-source found / new / details / deleted counts, number sent) to
the `runs` table and prints it. **Source health** (`pipeline.source_health`) compares each source's
latest collection with its own last 14 days. If the latest run failed, or found under 25% of its usual
count (when it usually finds 20 or more), a warning goes into the daily digest and `radar doctor`.

## Data model

### Normalized listing (`listings.data`)

Sources must produce dicts with these keys (missing values are `None`):

| Key | Meaning |
|---|---|
| `uid` | `"<source>:<id>"`, unique and stable across runs |
| `source`, `source_id`, `url`, `title` | identity and link |
| `price`, `price_max` | monthly rent; `price_max` when a building advertises a range |
| `beds`, `baths`, `sqft` | `beds = 0` for studios |
| `lat`, `lon` | **required** (listings without coordinates are dropped) |
| `address`, `city` | `"123 Main St, City, ST 12345"` when known |
| `housing_type` | lower-case: house, townhouse, duplex, condo, apartment, in-law, flat, ... (see `analyze.HOME_TYPES`) |
| `body`, `attrs`, `amenities` | free text the analyzers read (lower-cased together) |
| `available_text` | e.g. `"available mar 1"` |
| `posted_at` | ISO timestamp |
| `photos` | list of URLs (the first one is shown in the email) |
| `dogs_ok`, `cats_ok`, `dog_policy_unknown` | pet policy as reported by the source |
| `feed`, `estimated_value` | upstream feed code (DIY-platform detection) and home value, when known |
| `contact` | `{name, phone, email, links, reply_url, external_url, note}` |
| `no_matching_unit`, `unit_name`, `matching_units` | building listings: whether a unit fits your size and budget |

Fields added by checks: `address_check`, `property_check`, `sale_match`, `link_removed`,
`retracted_at`. They're preserved when a source re-reports the listing.

### Analysis (`listings.analysis`)

`{components, base, adjustments, neighborhood, commute, pet_flags, scam_flags, scam_level,
caution_flags, pros, cons}`. It's recomputed on every run and never edited by hand.

### Tables (`data/rentals.db`)

| Table | Contents |
|---|---|
| `listings` | one row per listing: data, analysis, score, first/last seen, `detail_fetched`, `emailed_at`, `dup_of`, `status` (`active`, `gone`, `rejected`, `interested`) |
| `cache` | drive times (`drive:<dest>:<lat,lon>`) and geocodes (`geo:<address>`), including misses |
| `sale_listings` | the sale-history index |
| `runs` | per-run summary JSON |
| `meta` | `sales_refreshed` timestamp |

Delete `data/` to start over. Nothing else depends on it.

## Modules

| Module | Responsibility |
|---|---|
| `cli.py` | argument parsing, `init` wizard, `doctor`, `schedule`, day-to-day commands |
| `pipeline.py` | the run cycle above |
| `config.py` | `DEFAULTS`, loading/merging, derived defaults (`finalize`), `validate`, `.env` |
| `analyze.py` | the scoring model, pros/cons, scam heuristics |
| `geo.py` | distance, Census geocoding (cached), neighborhoods, drive (OSRM), transit, bike |
| `sales.py` | sale-history refresh, street keys, shingle text index |
| `verify.py` | address existence, property records, posting-removed checks |
| `emailer.py` | HTML/text rendering, safety tips, SMTP |
| `notify.py` | channel selection, chat formatting (Discord/Slack/Telegram/ntfy) and delivery |
| `demo.py` | `radar demo`: made-up listings scored with the East Bay example, offline |
| `addresses.py` | state-parameterized street-address regexes |
| `http.py` | shared session, per-host rate limiting, retries |
| `sources/*` | one module per source (see [sources.md](sources.md)) |

## External services

| Service | Used for | Key needed |
|---|---|---|
| OSRM public demo server (`router.project-osrm.org`) | drive times | no (fair use: batched 80 per request and cached) |
| US Census geocoder | address → coordinates, city lookup | no |
| OpenStreetMap Nominatim | second-opinion address check | no (≤ 1 request/second, identified User-Agent) |
| Realtor.com via HomeHarvest | rentals, property records, sale history | no |
| Craigslist, Redfin, AppFolio/Buildium/Rentvine | listings | no |
| Your SMTP / IMAP server | sending email, reading alert emails | your credentials |
| Discord / Slack / Telegram / ntfy (optional) | chat and push notifications | your webhook URL or token |
| Craigslist area list (`reference.craigslist.org`) | `radar init` suggests the nearest site | no |

No personal data is sent to any of these beyond the addresses and coordinates being looked up.
`http.TOOL_UA` deliberately contains no name or email address.

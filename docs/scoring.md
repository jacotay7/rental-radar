# Scoring and ranking

Every listing gets a score from 0 to 100, plus a list of human-readable pros and cons explaining it.
The score decides what gets emailed:

| Score | What happens |
|---|---|
| ≥ `top_pick_threshold` (80) | Emailed within the hour as a ⭐ top pick |
| ≥ `email_threshold` (72) | Included in the next daily digest |
| ≥ `email_threshold − 7` | Eligible for the digest's "top N still available" list (if it passes the scam checks) |
| below | Kept and visible with `radar top`, never emailed |

All of it lives in [`rental_radar/analyze.py`](../rental_radar/analyze.py), in one function per
component. Everything is re-scored on every run, so config changes apply right away (`radar rescore`).

## The formula

```
base  = 100 × Σ (weight_k × component_k) / Σ weight_k        (components are 0..1)

score = base
      + availability adjustment          (−8 .. +5)
      + area adjustment                  (−avoid_area_penalty .. +prefer_area_bonus)
      − below-ideal-bedrooms penalty     (size.below_ideal_penalty)
      − stairs/walk-up penalty           (scoring.walkup_penalty, half for a 2nd-floor unit)
      − scam penalty                     (25 if any scam flag)
      − verify penalty                   (sum of "double-check this" signals)

then hard caps are applied (below), then clamped to 0..100.
```

Weights are relative. They're normalized, so `{price: 1, commute: 1}` and `{price: 50, commute: 50}`
score the same. A weight of 0 removes a component. The `pets` component is also removed automatically
when `pets.dog` and `pets.cat` are both false.

## Components (each 0..1)

### Price, `price`
A piecewise-linear curve through four budget points:

| Rent | Score |
|---|---|
| ≤ `budget.great` (default 87.5% of target) | 1.0 |
| `great` → `target` | 1.0 → 0.8 |
| `target` → `max` | 0.8 → 0.5 |
| `max` → `hard_max` | 0.45 → 0.1 |
| no price listed | 0.4 |

Nothing above `hard_max` is collected at all. A listing between `max` and `hard_max` is only emailed
if its `base` is at least `budget.over_max_min_base` (80): it has to be exceptional to justify the
stretch.

### Commute, `commute`
For each listing we estimate three trips to `destination`:

- **Drive:** free-flow road time from the public [OSRM](https://project-osrm.org/) router
  × `rush_hour_factor` (1.35) + `parking_minutes` (3). Results are cached per location. If OSRM is
  down: straight-line distance × 1.3 at about 60 km/h, × the rush factor, + 5.
- **Transit** (optional, `commute.transit.enabled`): for each station in `areas.yaml` within 10 km:
  - *access:* a walk if the station is within `walk_km` (12 min/km with a 1.3× detour factor),
    otherwise a drop-off or drive (35 km/h with a 1.4× detour, + 2 min), skipped above
    `max_access_minutes`;
  - then `avg_wait` + the station's ride time to each exit station + that exit's
    `last_mile_minutes`. The best combination wins.
- **Bike:** only within `bike_max_km`, at 15 km/h with a 1.3× detour.

`best` = min(drive, transit). Score: 1.0 at ≤ `great_minutes`, falling linearly to 0.4 at
`max_minutes`, then from 0.3 to 0 over the next 10 minutes. There's +0.1 when both driving and transit
are viable (a backup option is worth something). A `best` more than `max_minutes + 5` caps the score
at 40.

### Pets, `pets`
Dog (when `pets.dog`):

| Signal | Effect |
|---|---|
| Source says dogs OK / unknown / no | base 0.5 / 0.35 / 0.25 |
| Weight limit in text below `dog_weight_lb` | 0.05 and **dealbreaker** |
| Weight limit within 5 lb above it | −0.2 (bring a vet weight record) |
| "no dogs", "no pets", "small dogs only", "cats only", feed pet policy "no dogs" | ≤ 0.1 and **dealbreaker** |
| "large dogs welcome", "all sizes", "no weight limit" (dogs ≥ 40 lb) | +0.3 |
| otherwise "dogs welcome / dog-friendly" | +0.1 |
| on-site dog run / wash / park | +0.1 |
| house, duplex, townhouse, in-law, cottage (somewhere to go outside) | +0.15 |
| a dog park from `areas.yaml` within 1.5 km | +0.1 |

Cat (when `pets.cat`): "no cats" / "no pets" / "dogs only" is a dealbreaker; "cats OK" is +0.2 (+0.05
when you also have a dog). Without a dog, the base is 0.6.

Pet rent, pet deposits, breed restrictions and "case-by-case" policies are listed under *Pet policy
notes* in the email. A **dealbreaker** caps the score at `email_threshold − 10`, so the listing is
never emailed but stays visible in `radar top` (feeds are sometimes wrong).

### Size, `size`
`0.6 × bedrooms + 0.4 × square footage`:

- bedrooms: 1.0 at ≥ `ideal_beds`, 0.3 from `min_beds` up to ideal, 0 below `min_beds` (and the total
  is capped at 30).
- sqft: linear from 0.1 at `tiny_sqft` to 1.0 at `great_sqft`; 0.5 if not listed.

There's also a flat `below_ideal_penalty` (6 points) for fewer than `ideal_beds` bedrooms. Being "one
bedroom short" usually matters more than a linear score suggests.

### Home type, `home_type`
From the `HOME_TYPES` table (override any entry with `scoring.home_types`):

| house | house/townhouse | cottage | duplex, townhouse | multi-family, flat | in-law | condo | loft | apartment | apartment building | unknown |
|---|---|---|---|---|---|---|---|---|---|---|
| 1.0 | 0.85 | 0.8 | 0.75 | 0.65 | 0.6 | 0.45 | 0.3 | 0.25 | 0.2 | 0.4 |

Posters often mislabel, so explicit text wins. "Single-family", "entire/detached house" raises it to
0.9; "duplex" or "townhouse" raises it to 0.75. A "house" whose address has a unit number is treated as
part of a house (≤ 0.6). If you *prefer* apartments, flip the table, e.g.
`home_types: {apartment: 1.0, house: 0.5}`.

### Yard, `yard`
"No yard" → 0 · private / fenced / large / enclosed yard → 1.0 · shared yard → 0.55 ("shared fenced
yard" counts as shared, not private) · yard or garden mentioned → 0.7 · only a patio, deck or balcony →
0.3 · a house that doesn't mention one → 0.5 · otherwise 0.

### Neighborhood, `neighborhood`
The listing is matched to the closest neighborhood in `areas.yaml`, measured relative to its radius:

- inside the radius: that neighborhood's 1–10 score;
- within 1.8× the radius: "near X", score − 0.5;
- otherwise: the listing's city in `city_defaults`, else `default_score` (5.5).

Converted with `(score − 3) / 6`: 3 → 0, 4.5 → 0.25, 6 → 0.5, 9+ → 1.0. Areas rated below
`low_area_score` (5.5) get a warning con. The neighborhood's own pros and cons from `areas.yaml` are
copied into the listing's pros/cons.

If `areas.yaml` has no neighborhoods and no `city_defaults`, this component is dropped (like `pets`
with no pets) and the other weights are renormalized. Otherwise every listing would get the same
default score, which squashes the whole ranking toward the middle. `radar doctor` warns about it.

## Adjustments

| Adjustment | Rule |
|---|---|
| **Availability** (when `search.move_in` is set) | The date is parsed from "available Mar 1", "available 3/1" or "available 2027-03-01" (the year is inferred as the one closest to your move-in). Within `early_days` (21) before to `late_days` (10) after: **+5**. Earlier: **−3** (you'd pay rent before moving). Later: **−8**. "Available now" when move-in is more than `early_days` away: **−2** (ask if they'll hold it). |
| **Area** | Neighborhood name contains an `avoid_areas` entry: **−10** (`avoid_area_penalty`). Contains a `prefer_areas` entry: **+4** (`prefer_area_bonus`). |
| **Bedrooms** | `min_beds ≤ beds < ideal_beds`: **−`below_ideal_penalty`**. |
| **Stairs** | Walk-up, "no elevator", 3rd floor or higher, "flights of stairs": **−`walkup_penalty`** (8). 2nd-floor / upstairs unit: half. Exempt: houses, townhouses (ground-level entry), buildings with an elevator, "ground-floor entry". |
| **Scam / verify** | See [scam-detection.md](scam-detection.md). |

## Hard caps

Applied after everything else. A listing that fails one of these can't be rescued by great components:

| Condition | Cap |
|---|---|
| fewer than `min_beds` | 30 |
| best commute > `max_minutes + 5` | 40 |
| building has no unit with ≥ `min_beds` within budget | 35 |
| any **likely scam** flag | 40 |
| verify penalty ≥ 10, or scam level *suspicious* | `email_threshold − 1` (not emailed) |
| scam level *caution* | `top_pick_threshold − 1` (digest only, never an instant alert) |
| posted address doesn't exist | `email_threshold − 10` |
| pet dealbreaker | `email_threshold − 10` |
| over `budget.max` and `base` < `over_max_min_base` | `email_threshold − 1` |

## Pros and cons

Each component appends plain-English reasons as it scores, so the email always explains itself.
Warnings are inserted at the top of the cons list (⚠ LIKELY SCAM, ADDRESS NOT FOUND, avoid-list areas,
low-rated areas). `radar show <uid>` prints the same list, plus `base`, every adjustment and every
component value.

## De-duplication

The same home is often posted on several sites, or reposted with a new ID. Listings sharing either
key below are grouped:

- location rounded to ~100 m + bedrooms + price rounded to $100, or
- identical title + price.

Within a group, the canonical copy is the one already emailed, else the one with full details, else
the highest score. The others get `dup_of` set and are never emailed.

## Detail-fetch budget

Fetching a listing's full page is the slowest and politest-to-avoid step, so before fetching, each
new listing gets an **optimistic** score that assumes the details will be good news ("large dogs
welcome, private fenced yard, available on your move-in date"). Only listings whose optimistic score
reaches `email_threshold` are fetched, best first, up to `--detail-limit` (300) per source per run.
The rest are skipped, because they couldn't make the cut even in the best case.

## Tuning recipes

| Feedback | Knob |
|---|---|
| "Too many apartments" | raise `weights.home_type`, or lower `home_types.apartment` |
| "I'd take an apartment in a great area over a house in a meh one" | raise `weights.neighborhood`, lower `weights.home_type` |
| "Never show me X neighborhood" | add it to `scoring.avoid_areas` (substring match) and maybe raise `avoid_area_penalty` |
| "I love X" | `prefer_areas`, or raise its score in `areas.yaml` |
| "Stairs are fine" | `walkup_penalty: 0` |
| "2BR is fine too" | `ideal_beds: 2`, or `below_ideal_penalty: 0` |
| "Too many emails" | raise `email_threshold` (digest) and/or `top_pick_threshold` (instant) |
| "Not enough emails" | lower them, widen `bbox`, add cities/sources, check `radar top --all` for what's just under |
| "Commute estimates feel short" | raise `rush_hour_factor` (1.5 for bad traffic) or `parking_minutes` |
| "Price matters less than space" | lower `weights.price`, raise `weights.size`, raise `great_sqft` |
| "Moving date is flexible" | `search.move_in: null` |

After editing: `radar rescore && radar top`.

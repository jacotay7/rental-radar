# Setting up your region

Rental Radar's code knows nothing about any particular city. Local knowledge lives in two files
next to your `config.yaml`:

- `areas.yaml`: neighborhood ratings, city defaults, transit stations, dog parks
- `portals.yaml`: local property-management companies

[`examples/east-bay/`](../examples/east-bay/) is a complete worked example (51 neighborhoods, 20
BART stations, 15 dog parks, 34 managers).

## areas.yaml

```yaml
default_score: 5.5            # anything unmatched and not in city_defaults

city_defaults:                # lower-case city name -> 1-10
  springfield: 6.5
  shelbyville: 7.5

neighborhoods:
  - name: "Old Town"
    lat: 39.7990
    lon: -89.6440
    radius_km: 1.0
    score: 8.0
    pros: "Walkable shops, farmers market, near the river trail"
    cons: "Weekend bar noise; street parking only"

transit_stations: []          # see "Transit" below
dog_parks:
  - {name: "Riverside Dog Park", lat: 39.81, lon: -89.66}
```

### How matching works
A listing goes to the neighborhood whose center is closest **relative to its radius** (distance ÷
radius). Inside the radius it gets that score. Up to 1.8× the radius it's "near X" at 0.5 lower.
Beyond that it falls back to `city_defaults` (by the listing's city), then `default_score`. Circles
are a crude model, so for oddly shaped areas use several smaller entries with the same name.

### Rating neighborhoods well
- **Use the whole 1–10 range, relative to each other.** The score maps to the component as
  `(score − 3) / 6`: 9+ is full marks, 6 is half, and 3 or below is zero. If everything is 7–8, the
  neighborhood weight barely matters.
- **Decide what the number means for you** and write it at the top of the file (e.g. "safety first,
  then walkability, then schools"). The example's header says what its scores blend.
- **Pros/cons should be things a listing can't tell you:** "near the lake loop", "freeway noise on
  the west edge", "hilly, wildfire zone". Don't restate commute; that's computed per listing.
- Areas below `scoring.low_area_score` (5.5) get an explicit warning con. Use `avoid_areas` in the
  config for hard no's and `prefer_areas` for favorites.
- Sources worth combining: city crime maps (per-capita, by beat or district), school ratings, Walk
  Score, local subreddit and forum threads ("which neighborhoods should I avoid"), Street View
  walk-throughs, and anyone you know there. Scores are opinions; say so in the file.
- `radar geocode "Main St & 5th Ave, Springfield, IL"` gives you coordinates, and so does
  right-click → copy in Google Maps.

### Transit
Model a train/metro commute by listing stations and the ride time from each to the "exit" station(s)
near your destination:

```yaml
# areas.yaml
transit_stations:
  - {code: NTH, name: "North Station", lat: 39.83, lon: -89.65, rides: {CTR: 14}}
  - {code: MID, name: "Midtown",       lat: 39.81, lon: -89.65, rides: {CTR: 6}}
  - {code: CTR, name: "Center",        lat: 39.80, lon: -89.65, rides: {}}
```
```yaml
# config.yaml
commute:
  transit:
    enabled: true
    name: "Metro"
    exits:
      CTR: {name: "Center", last_mile_minutes: 5, last_mile_mode: walk}
```

Ride minutes come from the agency's timetable (or a trip planner). If two exits are both reasonable
(e.g. a closer station on a slower line), list both: the best total wins. Access is a walk within
`walk_km`, otherwise a drop-off or drive of up to `max_access_minutes`. Set `walk_km: 2` and
`max_access_minutes: 0` if nobody will drive you to a station.

### Dog parks
Off-leash parks and dog-friendly trails. A listing within 1.5 km of one gets a pet bonus and a pro.
Only used when `pets.dog` is true.

## Finding property-manager portals

Many single-family rentals are listed only on a local manager's own site, and most of those sites use
AppFolio, Buildium or Rentvine.

1. Search for `"property management" <your city>` and `"homes for rent" <city> "appfolio"` (likewise
   `"managebuilding"` and `"rentvine"`).
2. Open each manager's "Available rentals" or "Vacancies" page. If the URL is
   `https://NAME.appfolio.com/listings`, `https://NAME.managebuilding.com/Resident/public/rentals` or
   `https://NAME.rentvine.com/...`, then `NAME` is the `sub`.
3. Check the listings are actually in your region. Guessed subdomains often belong to a namesake
   company in another state.
4. Add `- {name: ..., platform: appfolio|buildium|rentvine, sub: NAME}` and set
   `sources.pm-portal.enabled: true`.

Managers whose sites use other platforms (Propertyware, Rent Manager, custom sites) aren't supported
yet. See [sources.md](sources.md#adding-a-source) if you'd like to add one.

## Region-specific email tips
`email.safety_tips` adds lines to the safety checklist in every email. Good candidates are your county
assessor's property search URL, your state's real-estate license lookup, and local deposit or
application-fee laws.

## Contributing a region
If you build a solid `areas.yaml` and `portals.yaml` for a metro area, consider contributing them as
`examples/<region>/` (config with placeholder email and destination, areas, portals, and a header
comment explaining what the scores mean and when they were made).

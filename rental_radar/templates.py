"""Files written by `radar init`. ${placeholders} are filled from the wizard's answers."""

CONFIG = """\
# Rental Radar configuration. Every run re-reads this file; run `radar rescore` after editing to see
# the effect right away. Anything left out falls back to the defaults in rental_radar/config.py.
# Full reference: docs/configuration.md. Scoring model: docs/scoring.md.

search:
  label: "${label}"          # appears in email subjects
  state: "${state}"                         # two-letter US state
  move_in: ${move_in}               # YYYY-MM-DD, or null to ignore availability dates
  # Scraped listings outside this box are dropped. Generated from your destination + radius;
  # tighten it to the area you actually want.
  bbox:
    south: ${south}
    north: ${north}
    west: ${west}
    east: ${east}
  # Cities searched on Realtor.com and indexed for the sale-history scam check.
  cities: ${cities}
  alert_radius_km: ${alert_radius}            # saved-search alert listings are kept within this distance

destination:                   # where you commute to
  label: "${dest_label}"
  address: "${dest_address}"
  lat: ${dest_lat}
  lon: ${dest_lon}

budget:
  target: ${target}             # full marks at/below ~87.5% of this, still good up to it
  max: ${max_rent}                # your stated ceiling
  hard_max: ${hard_max}           # never collected above this; max..hard_max must be exceptional

size:
  min_beds: ${min_beds}
  ideal_beds: ${ideal_beds}
  below_ideal_penalty: 6       # points off when min_beds <= beds < ideal_beds
  min_sqft: ${min_sqft}               # below this is a con
  good_sqft: ${good_sqft}              # at/above this is a pro

pets:
  dog: ${dog}
  dog_weight_lb: ${dog_weight}          # weight limits below this are dealbreakers
  cat: ${cat}

commute:
  max_minutes: ${max_commute}              # best of drive / transit
  rush_hour_factor: 1.35       # free-flow drive time x this = rush-hour estimate
  transit:
    enabled: false             # see docs/regions.md to model a train/metro commute
    name: "Transit"
    avg_wait: 6
    exits: {}                  # {STATION_CODE: {name: ..., last_mile_minutes: 8, last_mile_mode: walk}}

scoring:
  weights:                     # relative importance; normalized automatically (0 = ignore)
    price: 15
    commute: 15
    pets: ${pets_weight}
    neighborhood: 20
    size: 15
    home_type: 12              # house > duplex/townhouse > condo > apartment
    yard: 8
  email_threshold: 72          # new listings at/above this go in the daily digest
  top_pick_threshold: 80       # ... at/above this are emailed right away
  walkup_penalty: 8            # walk-up / 3rd floor+ (half for 2nd floor); 0 to ignore stairs
  avoid_areas: []              # neighborhood names (substring match) you don't want
  prefer_areas: []             # neighborhood names you love

email:
  to: ${email_to}
  # Sender credentials live in .env (SMTP_USER, SMTP_PASSWORD)
  smtp_host: "${smtp_host}"
  smtp_port: ${smtp_port}

sources:
  craigslist:
    enabled: ${cl_enabled}
    site: ${cl_site}                 # subdomain of craigslist.org, e.g. sfbay, chicago, seattle
    postal: ${cl_postal}               # a ZIP near the center of your search
    distance_mi: ${cl_distance}
  redfin:
    enabled: true
  realtor:
    enabled: true
  pm-portal:
    enabled: false             # list property managers in portals.yaml, then enable (docs/sources.md)
    file: portals.yaml
  alerts:
    enabled: false             # read Zillow/HotPads/... alert emails over IMAP (docs/saved-search-alerts.md)
    imap_host: "imap.gmail.com"

areas_file: areas.yaml         # neighborhood ratings, transit stations, dog parks (docs/regions.md)

schedule:
  collect: "*-*-* 06..22:05:00"   # systemd OnCalendar: hourly 6am-10pm
  digest: "*-*-* 07:30:00"        # daily digest
  timezone: ${timezone}
"""

AREAS = """\
# Neighborhood ratings and local reference data for scoring. See docs/regions.md.
# Scores are 1-10: your blend of safety, walkability, schools, noise, character. 6 is "fine".
# Listings are matched to the nearest neighborhood relative to its radius; anything outside every
# radius falls back to its city's default, then to default_score.

default_score: 5.5

city_defaults: {}
#  springfield: 6.5

neighborhoods: []
#  - name: Old Town
#    lat: 39.7990
#    lon: -89.6440
#    radius_km: 1.0
#    score: 8.0
#    pros: "Walkable shops, farmers market"
#    cons: "Weekend noise"

# Only used when commute.transit.enabled is true. `rides` = ride minutes to each exit station
# listed under commute.transit.exits in config.yaml.
transit_stations: []
#  - {code: CTR, name: Center St, lat: 39.80, lon: -89.65, rides: {DTN: 9}}

# Off-leash parks and dog-friendly trails (a small bonus for dog owners when within 1.5 km).
dog_parks: []
#  - {name: Riverside Dog Park, lat: 39.81, lon: -89.66}
"""

PORTALS = """\
# Property-manager vacancy portals. Many single-family rentals are only listed on these.
# platform: appfolio -> https://<sub>.appfolio.com/listings
#           buildium -> https://<sub>.managebuilding.com/Resident/public/rentals
#           rentvine -> https://<sub>.rentvine.com/api/public/listings
# Listings outside your search box are dropped, so statewide managers are fine.
# Verify each subdomain in a browser first: common names often belong to companies in other states.
# Then set sources.pm-portal.enabled: true in config.yaml. See docs/sources.md.
portals: []
#  - {name: Example Property Management, platform: appfolio, sub: exampleprop}
"""

ENV = """\
# Secrets for Rental Radar. Never commit this file (it is in .gitignore).
# SMTP account that SENDS the emails. For Gmail: turn on 2-Step Verification, then create an
# app password at https://myaccount.google.com/apppasswords and paste it below.
SMTP_USER=
SMTP_PASSWORD=
# Optional: a different mailbox to read saved-search alert emails from (defaults to the SMTP account).
# IMAP_USER=
# IMAP_PASSWORD=
"""

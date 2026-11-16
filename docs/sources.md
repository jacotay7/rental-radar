# Listing sources

Each source is a module in [`rental_radar/sources/`](../rental_radar/sources/) that turns one website
into [normalized listings](architecture.md#normalized-listing-listingsdata). Enable and configure
them under `sources:` in `config.yaml`.

| Source | Config key | Covers | Typical volume |
|---|---|---|---|
| Craigslist | `craigslist` | owner-posted rentals, small landlords | high |
| Redfin | `redfin` | agent and management listings, plus syndicated rental feeds | high |
| Realtor.com | `realtor` | agent-listed houses, with pet policies and value estimates | medium |
| Property-manager portals | `pm-portal` | single-family homes from local managers | low but high quality |
| Saved-search alert emails | `alerts` | Zillow, HotPads, Trulia, Apartments.com, Zumper, ... | depends on your saved searches |

## Craigslist (`sources/craigslist.py`)

- **Search:** the JSON API the Craigslist website itself uses (`sapi.craigslist.org`, path `apa`),
  around `postal` within `distance_mi`, with `min_bedrooms`, `max_price = hard_max`, and
  `pets_dog` / `pets_cat` when you have pets.
- **Result cap:** 360 per query. When a query hits the cap, its price band is split in half and both
  halves are re-queried, so every listing is reached.
- **Details:** the posting page gives the body, attributes (housing type, laundry, parking,
  availability, "dogs are OK"), map address, coordinates, and phone/email/links in the text.
  A deleted or expired post marks the listing `gone`.
- **Config:** `site` is the craigslist.org subdomain for your region (`sfbay`, `losangeles`,
  `seattle`, `chicago`, `newyork`, `boston`, `denver`, `austin`, ...). Open craigslist.org and pick your
  city; the subdomain is in the URL.
- **Scam checks:** Craigslist addresses are typed in by the poster, so they get the address-existence
  check.

## Redfin (`sources/redfin.py`)

- **Search:** Redfin's rentals search API over a polygon (your `search.bbox`), with `min_num_beds`,
  `max_price` and `dogs_allowed` (for dog owners).
- **Result cap:** 350 per query. A tile that hits the cap is split into four quadrants, recursively.
- **Details:** amenities, plus **floor plans** for buildings. The best-fitting available unit (most
  bedrooms, then most sqft, then cheapest) within your size and budget sets the price and size. A
  building with no fitting unit gets `no_matching_unit` (score capped at 35).
- **Config:** `market` is optional (Redfin's market slug, e.g. `sanfrancisco`). Usually unnecessary.
- Many Redfin rentals are syndicated from other feeds; `feed` records the original source, which helps
  spot DIY-platform posts.

## Realtor.com (`sources/realtor.py`)

- Uses the open-source [HomeHarvest](https://github.com/ZacharyHampton/HomeHarvest) library, one
  search per entry in `search.cities` (`"<city>, <state>"`), with `beds_min` and `price_max`.
- Search results already include full details: description, structured **pet policy** (dogs, large
  dogs, cats), agent and office contacts, photos, and Realtor.com's **estimated home value** (used by
  the rent-vs-value scam check).
- The same library powers the property-record lookups and the sale-history index. That's why
  `search.cities` matters even if you disable this source.

## Property-manager portals (`sources/portals.py` + `portals.yaml`)

In many markets most single-family rentals are handled by local property-management companies, and
some appear **only** on the manager's own site. Most managers use a hosted platform with a public
vacancy page:

| Platform | Vacancy page | Parsed from |
|---|---|---|
| AppFolio | `https://<sub>.appfolio.com/listings` | map-marker JSON + listing cards; detail page for description, phone, pet policy |
| Buildium | `https://<sub>.managebuilding.com/Resident/public/rentals` | listing cards (addresses geocoded); detail page for description and features |
| Rentvine | `https://<sub>.rentvine.com/api/public/listings` | public JSON (including pet flags and application links) |

`portals.yaml`:

```yaml
portals:
  - {name: Example Property Management, platform: appfolio, sub: exampleprop}
```

Listings outside your bounding box are dropped, so a statewide manager is fine. See
[regions.md](regions.md#finding-property-manager-portals) for how to find your area's managers.
Verify each subdomain in a browser: generic names often belong to a company in another state.

## Saved-search alert emails (`sources/alerts.py`)

Zillow, HotPads, Trulia and Apartments.com block automated access. Rental Radar doesn't try to get
around that. Instead you create saved searches on those sites with instant email alerts, and Rental
Radar reads the alert emails over IMAP.

- **Read-only:** messages are fetched with `BODY.PEEK`, so nothing is marked read, moved or deleted.
- **Gmail** gets a server-side search across All Mail. Other IMAP servers get a `SINCE` search of
  the inbox, filtered by sender.
- **Parsing:** a card parser (price line → facts → address line, with the link and photo taken from
  the HTML just before the address) handles Zillow, Zumper and most newsletter templates. A generic
  "one listing per street address" parser is the fallback.
- **Trust:** emails from sites listed in `alerts.filtered_sites` are treated as matches for a saved
  search with your pet filter (`dogs_ok`). Others, like Zumper's "recommended for you", are
  `dog_policy_unknown`. Zillow is checked per email ("instant updates" or "saved search" means
  filtered). Per-room pricing (< $600/bedroom) is skipped.
- `alerts.site_housing` assigns a home type to a site's alerts when your saved search filtered on
  it, e.g. `{zillow: house/townhouse}`.
- `alerts.extra_senders` adds local senders such as a neighborhood newsletter with housing ads.

Setup walkthrough: [saved-search-alerts.md](saved-search-alerts.md).

## Sources that are deliberately not supported

| Site | Why | Alternative |
|---|---|---|
| Zillow, HotPads, Trulia, Apartments.com (direct) | Bot protection (PerimeterX / Akamai) | Alert emails |
| Facebook Marketplace / groups | Login required; scam-heavy | Check by hand, carefully |
| Nextdoor | Login + neighborhood-gated | Ask a local |

**Do not add code that evades bot protection**: no browser-fingerprint impersonation, stealth
headless browsers, CAPTCHA solving, or rotating proxies. It breaks those sites' terms, and it isn't
needed: alert emails cover the same inventory.

## Adding a source

1. Create `rental_radar/sources/<name>.py` with:
   ```python
   MARK_GONE = True   # False if a listing's absence from one search doesn't mean it's gone

   def search(cfg, conn=None) -> list[dict]:
       """Return normalized listings. Respect cfg['budget']['hard_max'], cfg['size']['min_beds'],
       cfg['pets'] and cfg['search']['bbox'] where the site can filter server-side."""

   def fetch_detail(listing: dict, cfg) -> dict | None:
       """Return the listing with body/attrs/contact filled in, or None if the post is gone.
       Return the listing unchanged if search results are already complete."""
   ```
2. Use `rental_radar.http.get(url, min_interval=...)` for every request (per-host rate limiting and
   backoff). Keep `min_interval` ≥ 1 second for HTML pages.
3. `uid` must be `"<name>:<stable id>"`, and `lat`/`lon` are required (use `geo.geocode(conn, address)`
   when the site only gives addresses).
4. Register it in `SOURCES` in `sources/__init__.py` and give it a default block in
   `config.DEFAULTS["sources"]` (`enabled: False` until it's proven).
5. If its addresses are typed in by posters, add it to `verify.CHECK_SOURCES`. If they come from a
   geocoded feed, add it to `GEOCODED_SOURCES`.
6. Add a parser test with a saved, anonymized fixture under `tests/fixtures/`.
7. Document it in this file and in the README's source table.

## When a source breaks

Sites change their markup and APIs. Rental Radar notices: when a source's latest run failed, or found
less than a quarter of its usual count, the daily digest gets a "⚠ Source problems" banner and
`radar doctor` prints a warning. Run `radar -v run --dry-run --no-collect` to rule out config problems,
then `radar -v run --dry-run` and look at that source's log lines.
Fix the parser against a fresh sample of the page, add the sample as a test fixture, and keep
everything else working while that source is disabled (`enabled: false`).

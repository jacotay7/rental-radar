# Saved-search alert emails

Zillow and its sister sites (HotPads, Trulia), plus Apartments.com, have the largest rental
inventories, and they block automated access. They will, however, email you new matches the moment
they're posted. Rental Radar reads those emails (read-only) and scores every listing in them like any
other.

About 20 minutes of one-time setup.

## 1. Choose the mailbox

Use the account in `.env` (`SMTP_USER`), or set `IMAP_USER` / `IMAP_PASSWORD` for a separate one. Sign
up on each site with that address, or forward the alerts to it. Alerts stay unread, so you can still
read them yourself.

Gmail: the same app password works for IMAP. Other providers: set `sources.alerts.imap_host`
(e.g. `imap.fastmail.com`, `outlook.office365.com`, `imap.mail.me.com`).

## 2. Create saved searches

Use the same filters everywhere a site offers them:

| Filter | Value |
|---|---|
| Area | Draw your search area on the map (it can be wider than `bbox`; alert listings are kept within `alert_radius_km` of your destination) |
| Price | up to your `hard_max` |
| Beds | your `min_beds`+ |
| Home type | what you'd actually rent (e.g. houses + townhomes) |
| Pets | your pet filter ("Allows large dogs", "Cats allowed") |
| Alert frequency | **Instant** |

| Site | How |
|---|---|
| **Zillow** | Rent → set filters → *Save search* → email frequency *Instantly* |
| **HotPads** | set filters → *Save search* → email alerts on |
| **Trulia** | set filters → *Save search* → notifications *Instant* |
| **Apartments.com** | set filters (property type, pets) → *Save search* |
| **Zumper / PadMapper** | set filters → *Save search* / *Get alerts* |
| Redfin, Realtor.com | not needed: collected directly |

## 3. Tell Rental Radar what your searches filter on

```yaml
sources:
  alerts:
    enabled: true
    imap_host: imap.gmail.com
    lookback_days: 14
    # Sites whose saved searches use your pet filter (their listings count as pet-friendly):
    filtered_sites: [zillow, hotpads, trulia, apartments.com, redfin-alert, realtor-alert]
    # If a site's saved search was limited to a home type, say so:
    site_housing: {zillow: "house/townhouse"}
    # Local newsletters with housing ads, by sender domain:
    extra_senders: {examplenewsletter.org: newsletter}
```

Emails that are recommendations rather than saved-search matches (Zumper's "trending near you",
Zillow's "homes you might like") are detected and treated as *pet policy unknown*.

## 4. Check it

```bash
radar doctor            # "IMAP login works (alerts)"
radar -v run --no-email # look for "alerts: N emails scanned, M listings parsed"
radar top --all | grep -E "zillow|hotpads|trulia|zumper"
```

If a site's emails parse to zero listings, its template probably differs from the ones the card
parser knows. Save an example email's HTML (strip personal details), add it as a fixture in
`tests/fixtures/`, and adjust `sources/alerts.py` (see `tests/test_alerts.py`).

## Worth doing by hand

Not automatable, and sometimes the best leads:

- **A "housing wanted" post** on Craigslist and local community newsletters: short and friendly, with
  your move date, household, and a pet photo. Landlords with unlisted homes do reply.
- **Local parents' or neighborhood newsletters** often carry landlord-direct listings. If they arrive
  by email, add the sender to `extra_senders`.
- **Facebook groups and Marketplace**: check a couple of times a week. This is where most fake
  listings live, so be skeptical.

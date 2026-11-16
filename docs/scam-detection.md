# Scam detection

Rental fraud mostly targets people searching remotely or in a hurry, which describes most people
using a tool like this. The scams are good. They use real photos, real descriptions, real addresses
(or nearly real ones), and prices just low enough to make you move fast. Rental Radar layers several
independent checks, so that no single gap lets a fake listing through, and it tells you *why* it is
suspicious instead of silently hiding it.

The code is in [`analyze.py`](../rental_radar/analyze.py) (`scam_check`, `hijack_signals`,
`verify_flags`), [`verify.py`](../rental_radar/verify.py) and [`sales.py`](../rental_radar/sales.py).

## The two scams that shaped this

**1. The fake street number.** A Craigslist post offered a 3-bedroom house in a desirable
neighborhood. The description was copied word for word from the *sale* listing of a nearby home that
had just sold for well over a million dollars. The scammer had changed the street number: "4127 Elm"
instead of "127 Elm". The map pin was roughly right, and nothing looked off until someone dropped into Street
View and found no such house.
→ Led to the **address-existence check** and **copied-description detection**.

**2. The hijacked for-sale listing.** A large house at about half the going rent, with "all utilities
included", posted on a do-it-yourself landlord platform and syndicated to bigger portals. It was a
copy of a for-sale listing (asking close to $2M) that had been *withdrawn* days earlier. A live "is this address for
sale?" lookup therefore came back clean. The post had two different phone numbers and a description
that broke off mid-sentence into "To apply...". It was taken down a day after it went up.
→ Led to the **sale-history index** (which remembers withdrawn listings), the **rent-vs-value check**,
the **weighted warning signs**, the **posting-removed check**, and **retraction emails**.

## Layer 1: classic scam language and impossible prices (`scam_check`)

Any of these is a **likely scam** flag (−25 points, score capped at 40):

- wire transfer, Western Union, MoneyGram, gift cards
- owner abroad, "out of the country", missionary, deployed
- keys will be mailed
- can't show the property
- "God bless" (common in scam scripts)
- asks for a deposit before a viewing or to "hold" the unit
- a 3BR+ **house** in an area rated ≥ 7 renting below `scam.too_cheap_house_rent` (default 65% of
  your target budget)
- any 2BR+ below `scam.too_cheap_rent` (default 45% of target)

Set the two price thresholds from your local market (roughly half of typical rent for that size) for
best results. The East Bay example sets them explicitly.

## Layer 2: does the address exist? (`verify.check`)

Run on Craigslist listings, where the poster types the address in (feeds like Realtor.com geocode
their own). For every listing close enough to be emailed (score ≥ `email_threshold − 15`):

1. Build a full address from the posting body or the map-address field (reverse-geocoding the city
   from the pin if needed).
2. Look it up in the **US Census geocoder**, then in **OpenStreetMap Nominatim** as a second opinion
   (exact house-number match required).
3. Neither finds it → **ADDRESS NOT FOUND** (−20 points, capped at `email_threshold − 10`: never
   emailed). Found more than 1.5 km from the posting's map pin → **−10**, "verify the real location".

## Layer 3: what is this property, really? (`verify.property_record`)

For listings with a verified address that aren't from Realtor.com itself, we look the address up on
Realtor.com (via HomeHarvest):

| Finding | Effect |
|---|---|
| currently **for sale / pending** | −15, "⚠ listed FOR SALE" |
| **sold** within 150 days | −8, "confirm the landlord is the new owner (county records)" |
| also listed **for rent at the same price** by an independent source | **pro**: "a good sign it's genuine" (not counted if both copies come from the same DIY post) |
| an **estimated value** | used by the rent-vs-value check below |

## Layer 4: the sale-history index (`sales.py`)

Live lookups miss withdrawn listings, and withdrawn listings are exactly what hijackers copy. So once
a day (`REFRESH_HOURS = 20`) Rental Radar downloads **every for-sale, pending and recently sold
listing** in your `search.cities` into its own `sale_listings` table and keeps them, including after
they disappear from the market. The first run backfills a year of sales; later runs add 14 days.

Every candidate rental is then matched against it in two ways:

- **By address:** normalized "street key" (`123 main st`, units kept: `2 oak way #106`, so one
  condo's sale doesn't flag its neighbor's rental), within 500 m.
- **By copied text:** descriptions are split into 8-word shingles. A rental that shares ≥ 4
  shingles making up ≥ 15% of its text with one sale listing is "copied from" it. Shingles that appear
  in more than 3 sale listings are ignored as boilerplate.

## Layer 5: hijacked-listing signals (`hijack_signals`)

**Strong signals** (any one = likely scam: −25, capped at 40, never emailed):

| Signal | Why |
|---|---|
| Monthly rent < `rent_to_value_likely_pct` (0.20%) of the home's estimated value | Genuine single-family rents rarely go much below ~0.25%/month of value; 0.15% means "$3,000 for a $2M house" |
| Address is **for sale right now** *and* (rent < 0.3% of list price *or* posted on a DIY platform) | The textbook hijack |
| Description copied from the sale listing of a **different** address | The fake-street-number scam |
| Description copied from this address's sale listing, and it **sold within a year** | The new owner isn't renting it through a copy-paste of the old marketing |

**Warning signs** are weighted and added up. Each alone is common and innocent; together they are the
pattern:

| Weight | Signal |
|---|---|
| 2 | for sale right now, but rent isn't suspiciously low and it's not a DIY post ("confirm with the listing agent") |
| 1 | listed for sale until recently (within 180 days) |
| 1 | description copied from this home's own for-sale listing |
| 1 | rent < `rent_to_value_caution_pct` (0.24%) of value |
| 1–1.5 | $/sq ft far below market in a top-rated area (no value estimate available) |
| 1 | all utilities included for a whole house |
| 1 | description cut off mid-sentence with "To apply / Contact the landlord" spliced in |
| 1 | reads like a for-sale listing (2+ of: "primary suite", "chef's kitchen", luxury appliance brands, "offered at", buyers/sellers/escrow/disclosures, "seismic retrofit", "entertainer's dream", ...) |
| 1 | two or more different phone numbers |
| 1 | the original posting on a DIY site was **taken down within 7 days** (checked live; platforms pull reported fraud fast) |
| 0.5 | posted on a DIY platform (TurboTenant, Avail, RentRedi) or Craigslist, where anyone can list without owner verification |
| 0.5 | "available immediately" |

| Total weight | Level | Effect |
|---|---|---|
| ≥ 2 | **caution** | "⚠ SEVERAL SCAM WARNING SIGNS" con; capped below `top_pick_threshold` (digest only, flagged ⚠ in the top list) |
| ≥ 3 | **suspicious** | capped below `email_threshold`: not emailed |
| any strong signal | **likely** | capped at 40 |

**Exemption:** if the rental's contact name matches the brokerage that has the home for sale, it's a
genuine rent-or-buy offer. The sale match is dropped and a pro is added instead.

## Layer 6: verify flags (data problems that look like fraud)

| Signal | Penalty |
|---|---|
| ≥ 1,500 sq ft at < `very_low_rent_per_sqft` ($1.60/sq ft) | −6 "could be rooms-for-rent, a data error, or a hijacked listing" |
| auto-generated aggregator text and no direct contact | −4 |
| text describes studios/1BRs while the feed says 2BR+ | −30 |
| a room in a shared home rather than a whole unit | −30 |

A verify penalty of ≥ 10 keeps the listing out of email.

## Retractions

Checks keep running on listings already emailed: sale history refreshes daily, and postings get
re-opened. If something you were sent later reaches **likely** or **suspicious**, the next run sends a
**"⚠ SCAM WARNING"** email listing exactly why, with advice: don't send money or documents; if you
already did, call your bank and report it at reportfraud.ftc.gov. Each listing is retracted once
(`retracted_at`). Use `radar mark <uid> rejected` to suppress one.

## What it can't catch

- A scammer using a real, never-for-sale address with original photos. Mitigation: the email's
  safety checklist (county assessor lookup, live video tour, never pay before a lease).
- Facebook Marketplace and other closed platforms (not collected).
- Anything outside the US Census address data.

Treat a clean result as "no known red flags", not "verified". The checks are tuned to stay out of
your way on real listings: most genuine listings carry zero or one warning sign.

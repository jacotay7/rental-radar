"""Listing sources. Each module exposes:

    search(cfg, conn) -> list[dict]        # normalized listings (see docs/sources.md for the schema)
    fetch_detail(listing, cfg) -> dict | None   # fuller record, or None if the post is gone
    MARK_GONE: bool (optional, default True)    # absent from a later search = rented/removed

Register new sources in SOURCES below and give them a `sources.<name>` block in config.
"""
from . import alerts, craigslist, portals, realtor, redfin

SOURCES = {
    "craigslist": craigslist,
    "redfin": redfin,
    "realtor": realtor,
    "pm-portal": portals,
    "alerts": alerts,
}

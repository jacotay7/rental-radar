"""Listing sources. Each module exposes:

    search(cfg, conn) -> list[dict]        # normalized listings (see docs/sources.md for the schema)
    fetch_detail(listing, cfg) -> dict | None   # fuller record, or None if the post is gone
    MARK_GONE: bool (optional, default True)    # absent from a later search = rented/removed

Register new sources in SOURCES below and give them a `sources.<name>` block in config.

A search that only partly worked (one city or one portal failed) returns `Incomplete(found, ...)`
so the pipeline doesn't mistake the missing listings for rented ones.
"""


class Incomplete(list):
    """Search results that are known to be partial.

    `unknown_prefixes`: uid prefixes whose listings couldn't be checked this run (they keep their
    status). None means nothing from this source should be marked gone this run."""

    def __init__(self, found=(), unknown_prefixes: tuple[str, ...] | None = None, reason: str = ""):
        super().__init__(found)
        self.unknown_prefixes = unknown_prefixes
        self.reason = reason


from . import alerts, craigslist, portals, realtor, redfin  # noqa: E402  (they import Incomplete)

SOURCES = {
    "craigslist": craigslist,
    "redfin": redfin,
    "realtor": realtor,
    "pm-portal": portals,
    "alerts": alerts,
}

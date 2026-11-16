# Changelog

All notable changes to Rental Radar. The format follows [Keep a Changelog](https://keepachangelog.com/),
and versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.3.0] - 2026-11-16

### Fixed
- A failed Discord, Slack or Telegram delivery could write the webhook URL (with its secret token) into the
  log. Errors now name only the host.
- Marking a listing `rejected` or `interested` is now permanent. Before, the mark was lost if the listing
  dropped out of one search and came back.
- "Small dogs OK" was treated as a dealbreaker for every dog owner; now only when your dog is over 25 lb.
- A blocked or rate-limited detail page (HTTP 403/429) was saved as "details fetched" with no description
  and never retried. It is now an error, retried next run, and detail fetching stops after 5 failures in a row.
- A property with no Realtor.com record was looked up again on every run.
- Craigslist contact emails no longer pick up a trailing full stop.
- Systemd units and crontab lines now work when the config path contains spaces.

### Added
- Only one run per search at a time: the digest waits for a collection that is still going.
- Listings are no longer marked "gone" when a search only partly worked (one city or portal failed) or
  suddenly returns far fewer results than usual. The digest reports incomplete results.
- `interval_hours` per source. Property-manager portals default to every 6 hours instead of hourly.
- `services.osrm_url` and `services.nominatim_url`, for people running their own instances. The digest
  warns when drive times fell back to straight-line estimates.
- `radar doctor` warns about settings it doesn't recognize (typos).
- Alert emails must really come from the site's domain and pass the mail server's SPF/DKIM/DMARC checks.
  Only http(s) links are kept. Gmail's "All Mail" folder is found in any language.
- Database schema versioning with automatic migration of older databases.
- `SECURITY.md`, a terms-of-use notice in the README and the setup wizard, pinned dependency ranges,
  `ruff` and a coverage floor in CI.
- Tests for every source parser, the address checks, the setup wizard, scheduling output and the
  day-to-day commands (35 → 74 tests, 52% → 78% coverage). The test suite blocks network access.

### Changed
- Scam checks moved from `analyze.py` to their own module, `scam.py`.
- `radar mark <uid> none` clears a mark (`active` still works).

## [0.2.0] - 2026-11-16

### Added
- `radar demo`: scores a handful of made-up listings and opens the results email in your browser. No
  setup or network needed. `--chat` also prints the chat/push version.
- Chat and push notifications: Discord, Slack, ntfy and Telegram, each turned on by one line in `.env`.
  Every configured channel gets every message, and one failing channel doesn't block the others. See
  `docs/notifications.md`.
- `radar test-notify` sends a test message to every channel (`test-email` still works as an alias).
- `radar run --dry-run`, an alias for `--no-email`. It now also writes `data/preview.chat.txt`.
- Source health warnings: when a source fails, or finds under a quarter of its usual listings, the
  daily digest shows a "Source problems" banner and `radar doctor` warns.
- `radar init` suggests the nearest Craigslist site and fills in the schedule timezone.
- Claude Code skills: `/new-search` (guided setup) and `/tune-search` (feedback → config).
- Install with `uv tool install` or `pipx install` straight from GitHub. The examples are now bundled in
  the package.
- Issue templates for broken sources, new regions and bugs.

### Changed
- If `areas.yaml` rates no neighborhoods or cities, the neighborhood weight is dropped instead of giving
  every listing the same default score.
- Email is optional: `email.to` is empty in the examples, so a webhook alone is enough to get started.
- `radar doctor` reports notification channels instead of requiring SMTP.

## [0.1.0] - 2026-11-16

### Added
- First public release: collection from Craigslist, Redfin, Realtor.com, property-manager portals
  (AppFolio, Buildium, Rentvine) and saved-search alert emails. Configurable 0–100 scoring with pros and
  cons, scam detection with retractions, an email digest and top-pick alerts, systemd/cron scheduling,
  the `radar init` wizard, and the East Bay and minimal examples.

[0.3.0]: https://github.com/jacotay7/rental-radar/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/jacotay7/rental-radar/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/jacotay7/rental-radar/releases/tag/v0.1.0

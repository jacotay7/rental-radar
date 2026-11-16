# Changelog

All notable changes to Rental Radar. The format follows [Keep a Changelog](https://keepachangelog.com/),
and versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

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

[0.2.0]: https://github.com/jacotay7/rental-radar/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/jacotay7/rental-radar/releases/tag/v0.1.0

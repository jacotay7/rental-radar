# Security

## Reporting a problem

Please report security problems privately, not in a public issue: use GitHub's **Report a
vulnerability** button under this repository's *Security* tab. You should get a reply within a week.
Include what you found, how to reproduce it, and the version (`radar --version`). Never include real
credentials, webhook URLs or personal addresses in a report.

Things worth reporting: a way for a listing, an alert email or a web page to run code, read files or
leak credentials; secrets appearing in logs, previews or messages; anything that sends personal data
to a third party.

## What Rental Radar handles, and how

| Data | Where it lives | Notes |
|---|---|---|
| SMTP / IMAP passwords, webhook URLs, bot tokens | `.env` next to your `config.yaml` (created `chmod 600`, git-ignored) | Never logged. Network errors report only the host name, because webhook URLs contain their token |
| Your destination, budget, recipients | `config.yaml` (git-ignored at the repo root and under `searches/`) | Not sent anywhere |
| Listings and run history | `data/rentals.db` (SQLite) | Local only |
| Listing addresses and coordinates | sent to the US Census geocoder, OpenStreetMap Nominatim, OSRM and Realtor.com | Needed for address checks and commute times. No names or email addresses are sent, and the User-Agent contains no personal details |

## Things to know before you run it

- **An email app password opens the whole mailbox.** If you use the saved-search alerts source,
  consider a dedicated mailbox (see `docs/saved-search-alerts.md`). Rental Radar only reads, and only
  messages from alert senders that pass your mail server's SPF/DKIM/DMARC checks.
- **Listing content is untrusted.** Text and links from listings are HTML-escaped in emails, and only
  `http(s)` links are kept from alert emails. Still, treat every listing link as you would any link
  from a stranger. The scam checks are heuristics and will miss things.
- **Webhooks are bearer secrets.** Anyone with your Discord, Slack or ntfy URL can post to your
  channel (and with ntfy, read it). If one leaks, delete it and create a new one.
- **Previews load remote images.** `data/preview.html` and the emails load listing photos from the
  listing sites, as any email with pictures does.

## Supported versions

Only the latest release gets fixes.

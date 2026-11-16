# Notifications

Rental Radar sends each batch of results to every channel you've set up:

| Channel | Setup | Good for |
|---|---|---|
| **Discord** | 1 minute, one URL | a shared server or channel with your partner or housemates |
| **ntfy** | 1 minute, no account | push notifications on your phone |
| **Slack** | 3 minutes, one URL | a workspace you already use |
| **Telegram** | 3 minutes, token + chat ID | phone notifications, group chats |
| **Email** | 5–10 minutes (app password) | the richest format: photos, full pros/cons, safety checklist |

Chat and push messages are compact. Each new listing gets its score, a link, the commute, the top 4 pros
and top 3 cons. The daily digest also lists the best listings still available. Email carries
everything. You can use several channels at once (for example ntfy for instant top picks plus email for
the digest). Every active channel gets everything.

Secrets go in `.env` next to your `config.yaml`. Webhook URLs and tokens let anyone holding them post to
your channel, so treat them like passwords: don't commit them, and don't paste them into chats
(including AI assistants).

After setting one up:

```bash
radar doctor        # "notification channels: discord, email"
radar test-notify   # sends a test message to every channel
radar demo --chat   # see what the chat version of a digest looks like
```

`radar run --dry-run` sends nothing. It writes `data/preview.html` (email) and `data/preview.chat.txt`
(chat version) instead.

## Discord
1. In your server: **Server Settings → Integrations → Webhooks → New Webhook**. You need the *Manage
   Webhooks* permission; on your own server you have it.
2. Pick the channel, optionally name it "Rental Radar", and **Copy Webhook URL**.
3. In `.env`: `DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...`

Long digests are split across several messages (Discord allows 2,000 characters each). Link previews
are suppressed so the channel stays readable.

## ntfy (phone push, no account)
[ntfy](https://ntfy.sh) is a free, open-source push service.
1. Install the ntfy app (Android or iOS), or use the web app at ntfy.sh/app.
2. Choose a **long, unguessable topic name**, e.g. `rentals-` plus a random string. Anyone who knows the
   name can read the topic, so treat it like a password.
3. Subscribe to that topic in the app.
4. In `.env`: `NTFY_URL=https://ntfy.sh/your-long-random-topic-name`

Top picks are sent at high priority, other updates at normal priority, and scam retractions at urgent
priority. Tapping a notification opens the best listing. If you run your own ntfy server, use its URL,
and set `NTFY_TOKEN=` if it requires an access token.

## Slack
1. Go to api.slack.com/apps → **Create New App → From scratch**, name it and pick your workspace.
2. **Incoming Webhooks** → turn it on → **Add New Webhook to Workspace** → pick a channel → copy the
   URL.
3. In `.env`: `SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...`

## Telegram
1. Message **@BotFather** → `/newbot` → follow the prompts → copy the token.
2. Send your new bot any message (or add it to a group and post there).
3. Open `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser and find `"chat":{"id":...}`.
   Group IDs are negative numbers.
4. In `.env`:
   ```bash
   TELEGRAM_BOT_TOKEN=123456:ABC...
   TELEGRAM_CHAT_ID=123456789
   ```

## Email
1. In `config.yaml`: `email.to: ["you@example.com", "partner@example.com"]`.
2. In `.env`: the account that **sends** the email.
   - **Gmail**: turn on 2-Step Verification, create an app password at
     myaccount.google.com/apppasswords, then set `SMTP_USER=you@gmail.com` and
     `SMTP_PASSWORD=<the 16-letter app password>`.
   - **Other providers**: set `email.smtp_host` and `email.smtp_port` in `config.yaml` (465 = SSL, 587 =
     STARTTLS). Fastmail, iCloud and Outlook all need an app password too.
3. `radar doctor --smtp` tests the login.

To stop emailing, empty `email.to`. To stop a chat channel, remove its line from `.env`.

## What gets sent when
| Message | When | Contents |
|---|---|---|
| Top pick alert | each collection run (hourly by default) | new listings scoring at or above `top_pick_threshold` |
| Daily digest | once a day | new listings at or above `email_threshold`, the best ones still available, and any source problems |
| Scam retraction | as soon as it's detected | a listing you were sent earlier now looks like a scam |

If a delivery fails on one channel, the others still go out and the error is logged. Listings count as
sent if at least one channel succeeded.

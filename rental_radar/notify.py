"""Delivery channels: email (SMTP) plus chat/push webhooks (Discord, Slack, ntfy, Telegram).

A channel is active when it is configured: email when `email.to` is set, the others when their secret
is in `.env`. Every active channel gets every message. Chat channels get a compact version of the
email: warnings, new listings with their top pros and cons, and the "still available" list.

Webhook URLs and bot tokens are secrets (anyone holding one can post to your channel). Never log them.
"""
import html
import logging
import time
from urllib.parse import urlsplit

import requests

from . import emailer, http
from .config import secret

log = logging.getLogger("radar")

CHAT = ("discord", "slack", "ntfy", "telegram")
SAFETY = "Never pay or send ID before a live tour and a signed lease."


def channels(cfg) -> list[str]:
    out = ["email"] if cfg["email"].get("to") else []
    if secret("DISCORD_WEBHOOK_URL"):
        out.append("discord")
    if secret("SLACK_WEBHOOK_URL"):
        out.append("slack")
    if secret("NTFY_URL"):
        out.append("ntfy")
    if secret("TELEGRAM_BOT_TOKEN") and secret("TELEGRAM_CHAT_ID"):
        out.append("telegram")
    return out


# ---------------------------------------------------------------- formatting

class Fmt:
    """Plain text (ntfy). Subclasses add each chat app's markup."""
    limit = 3800                       # bytes per message (ntfy's limit is 4096)

    def esc(self, s):
        return str(s or "")

    def bold(self, s):
        return self.esc(s)

    def link(self, text, url):
        return f"{self.esc(text)}\n{url}"


class Discord(Fmt):
    limit = 1900                       # 2000 characters

    def esc(self, s):
        s = str(s or "")
        for ch in "\\*_~`|[]<>#":
            s = s.replace(ch, "\\" + ch)
        return s

    def bold(self, s):
        return f"**{self.esc(s)}**"

    def link(self, text, url):
        return f"[{self.esc(text)}](<{url}>)"   # <...> stops Discord from unfurling every link


class Slack(Fmt):
    limit = 3500

    def esc(self, s):
        return str(s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def bold(self, s):
        return f"*{self.esc(s)}*"

    def link(self, text, url):
        return f"<{url}|{self.esc(text)}>"


class Telegram(Fmt):
    limit = 3800                       # 4096 characters

    def esc(self, s):
        return html.escape(str(s or ""), quote=False)

    def bold(self, s):
        return f"<b>{self.esc(s)}</b>"

    def link(self, text, url):
        return f'<a href="{html.escape(url)}">{self.esc(text)}</a>'


FORMATS = {"discord": Discord(), "slack": Slack(), "ntfy": Fmt(), "telegram": Telegram()}


def listing_block(row, d, a, cfg, f: Fmt) -> str:
    star = "⭐ " if row["score"] >= cfg["scoring"]["top_pick_threshold"] else ""
    score = f.bold(f"{row['score']:.0f}")
    lines = [f"{star}{score} · {f.link(emailer.headline(d, a), d['url'])}"]
    if d.get("address"):
        lines.append(f.esc(d["address"]))
    lines.append(f.esc(emailer.commute_line(a, cfg)))
    lines += [f"+ {f.esc(p)}" for p in a["pros"][:4]]
    lines += [f"− {f.esc(c)}" for c in a["cons"][:3]]
    return "\n".join(lines)


def top_block(top, new_uids, cfg, f: Fmt) -> str:
    n = cfg["notify"]["max_listings"]
    lines = [f.bold(f"Top {min(len(top), n)} still available")]
    for i, (r, d, a) in enumerate(top[:n], 1):
        new = "NEW " if r["uid"] in new_uids else ""
        warn = " ⚠" if a.get("scam_level") == "caution" else ""
        lines.append(f"{i}. {r['score']:.0f} · {new}{f.link(emailer.headline(d, a), d['url'])}{warn}")
    return "\n".join(lines)


def match_blocks(items, cfg, f: Fmt, top=None, warnings=None) -> list[str]:
    """The pieces of a chat notification, in order. `pack` joins them into messages."""
    n = cfg["notify"]["max_listings"]
    blocks = [f"⚠ {f.bold('Source problem')}: {f.esc(w)}" for w in warnings or []]
    blocks += [listing_block(r, d, a, cfg, f) for r, d, a in items[:n]]
    if len(items) > n:
        blocks.append(f.esc(f"…and {len(items) - n} more in the email / `radar top`."))
    if top:
        blocks.append(top_block(top, {r["uid"] for r, _, _ in items}, cfg, f))
    blocks.append(f.esc(SAFETY))
    return blocks


def retraction_blocks(items, f: Fmt) -> list[str]:
    blocks = [f.bold("⚠ SCAM WARNING: listings we sent you earlier now look like scams. Don't send money, an "
                     "application fee or ID. If you already did, call your bank and report it at "
                     "reportfraud.ftc.gov.")]
    for _, d, a in items:
        why = (a.get("scam_flags") or []) + (a.get("caution_flags") or [])
        blocks.append("\n".join([f.link(emailer.headline(d, a), d["url"]), f.esc(d.get("address") or "")]
                                + [f"− {f.esc(w)}" for w in why]))
    return blocks


def pack(blocks: list[str], limit: int) -> list[str]:
    """Join blocks into as few messages as fit under `limit` bytes; never split a block unless it alone is
    too long."""
    msgs, cur = [], ""
    for b in blocks:
        if len(b.encode()) > limit:
            b = b.encode()[: limit - 3].decode(errors="ignore") + "…"
        joined = f"{cur}\n\n{b}" if cur else b
        if len(joined.encode()) > limit and cur:
            msgs.append(cur)
            cur = b
        else:
            cur = joined
    if cur:
        msgs.append(cur)
    return msgs


# ---------------------------------------------------------------- senders

def _post(url, **kw):
    try:
        r = requests.post(url, timeout=30, headers={"User-Agent": http.TOOL_UA, **kw.pop("headers", {})}, **kw)
    except requests.RequestException as e:
        # requests puts the full URL (which contains the webhook secret / bot token) in its error text
        raise RuntimeError(f"could not reach {urlsplit(url).hostname} ({type(e).__name__})") from None
    if r.status_code >= 300:
        # The URL holds the secret, so report only the status and the service's own error text.
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
    return r


def _discord(title, msgs, click):
    url = secret("DISCORD_WEBHOOK_URL")
    for i, m in enumerate(msgs):
        body = f"{Discord().bold(title)}\n{m}" if i == 0 else m
        _post(url, json={"content": body[:2000], "username": "Rental Radar", "allowed_mentions": {"parse": []}})
        time.sleep(1)                  # webhooks allow ~5 messages per 2 s


def _slack(title, msgs, click):
    url = secret("SLACK_WEBHOOK_URL")
    for i, m in enumerate(msgs):
        body = f"{Slack().bold(title)}\n{m}" if i == 0 else m
        _post(url, json={"text": body, "unfurl_links": False, "unfurl_media": False})
        time.sleep(1)


def _ntfy(title, msgs, click, priority=3):
    raw = secret("NTFY_URL")
    parts = urlsplit(raw)
    topic = parts.path.strip("/")
    if not topic:
        raise RuntimeError("NTFY_URL needs a topic, e.g. https://ntfy.sh/some-long-random-name")
    headers = {"Authorization": f"Bearer {secret('NTFY_TOKEN')}"} if secret("NTFY_TOKEN") else {}
    for i, m in enumerate(msgs):
        payload = {"topic": topic, "message": m, "title": title if i == 0 else f"{title} ({i + 1})",
                   "tags": ["house"], "priority": priority}
        if click:
            payload["click"] = click
        _post(f"{parts.scheme}://{parts.netloc}/", json=payload, headers=headers)


def _telegram(title, msgs, click):
    url = f"https://api.telegram.org/bot{secret('TELEGRAM_BOT_TOKEN')}/sendMessage"
    for i, m in enumerate(msgs):
        body = f"{Telegram().bold(title)}\n{m}" if i == 0 else m
        _post(url, json={"chat_id": secret("TELEGRAM_CHAT_ID"), "text": body, "parse_mode": "HTML",
                         "link_preview_options": {"is_disabled": True}})
        time.sleep(1)


SENDERS = {"discord": _discord, "slack": _slack, "ntfy": _ntfy, "telegram": _telegram}


def _deliver(cfg, title, email_msg, blocks_for, click=None, priority=3) -> list[str]:
    """Send to every active channel. One failing channel doesn't stop the others; raises only if all fail."""
    active = channels(cfg)
    if not active:
        raise RuntimeError("No notification channel is set up: add email.to in config.yaml, or a webhook "
                           "(DISCORD_WEBHOOK_URL, SLACK_WEBHOOK_URL, NTFY_URL, TELEGRAM_BOT_TOKEN) in .env")
    done, errors = [], []
    for ch in active:
        try:
            if ch == "email":
                emailer.send(email_msg(), cfg)
            else:
                f = FORMATS[ch]
                msgs = pack(blocks_for(f), f.limit)
                if ch == "ntfy":
                    _ntfy(title, msgs, click, priority)
                else:
                    SENDERS[ch](title, msgs, click)
            done.append(ch)
        except Exception as e:
            log.error("%s notification failed: %s", ch, e)
            errors.append(f"{ch}: {e}")
    if not done:
        raise RuntimeError("every notification channel failed: " + "; ".join(errors))
    return done


def send_matches(items, cfg, kind="all", top=None, warnings=None) -> list[str]:
    """New matches (+ the digest's still-available list and source warnings) to every channel."""
    title = emailer.subject_line(items, cfg, kind, top, warnings)
    click = items[0][1]["url"] if items else None
    top_pick = bool(items) and items[0][0]["score"] >= cfg["scoring"]["top_pick_threshold"]
    return _deliver(cfg, title, lambda: emailer.build_email(items, cfg, kind=kind, top=top, warnings=warnings),
                    lambda f: match_blocks(items, cfg, f, top, warnings), click, 4 if top_pick else 3)


def send_retraction(items, cfg) -> list[str]:
    msg = emailer.build_retraction(items, cfg)
    return _deliver(cfg, msg["Subject"], lambda: msg, lambda f: retraction_blocks(items, f), items[0][1]["url"], 5)


def send_test(cfg) -> list[str]:
    title = f"{cfg['search']['label']}: test notification"
    text = "If you can read this, Rental Radar can reach you."
    return _deliver(cfg, title, lambda: emailer.message(title, text, None, cfg), lambda f: [f.esc(text)])


def chat_preview(items, cfg, top=None, warnings=None) -> str:
    """Plain-text rendering of the chat version, written next to the HTML preview."""
    return "\n\n".join(match_blocks(items, cfg, Fmt(), top, warnings))

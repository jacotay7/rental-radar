"""Chat/push notifications: formatting, message packing, and channel selection. No network."""
from rental_radar import analyze, notify
from tests.conftest import listing, make_cfg


def _items(cfg, n=3):
    out = []
    for i in range(n):
        d = listing(uid=f"t:{i}", url=f"https://example.com/{i}", title=f"Home {i} *bold* <b>",
                    housing_type="house", beds=3, sqft=1500, body="Large dogs welcome. Private fenced yard.")
        s, a = analyze.analyze(d, 8, cfg)
        out.append(({"uid": d["uid"], "score": s, "emailed_at": None}, d, a))
    return out


def test_channels_follow_secrets(monkeypatch):
    for k in ("DISCORD_WEBHOOK_URL", "SLACK_WEBHOOK_URL", "NTFY_URL", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        monkeypatch.delenv(k, raising=False)
    assert notify.channels(make_cfg(email={"to": []})) == []
    monkeypatch.setenv("NTFY_URL", "https://ntfy.sh/abc")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")                  # no chat id yet: not active
    assert notify.channels(make_cfg()) == ["email", "ntfy"]


def test_pack_respects_limits():
    blocks = ["a" * 900, "b" * 900, "c" * 900, "é" * 3000]
    msgs = notify.pack(blocks, 1900)
    assert all(len(m.encode()) <= 1900 for m in msgs)
    assert msgs[0].count("a") == 900 and msgs[0].count("b") == 900 and len(msgs) == 3


def test_formats_escape_and_link():
    cfg = make_cfg()
    items = _items(cfg, 1)
    d = items[0][1]
    discord = "\n\n".join(notify.match_blocks(items, cfg, notify.Discord()))
    assert f"(<{d['url']}>)" in discord
    slack = "\n\n".join(notify.match_blocks(items, cfg, notify.Slack()))
    assert f"<{d['url']}|" in slack
    tg = "\n\n".join(notify.match_blocks(items, cfg, notify.Telegram()))
    assert f'<a href="{d["url"]}">' in tg


def test_send_matches_posts_to_each_channel(monkeypatch):
    for k in ("DISCORD_WEBHOOK_URL", "SLACK_WEBHOOK_URL", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("NTFY_URL", "https://ntfy.example/secret-topic")
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.example/webhook")
    monkeypatch.setattr(notify.time, "sleep", lambda s: None)
    posts = []

    class R:
        status_code, text = 200, "ok"

    monkeypatch.setattr(notify.requests, "post", lambda url, **kw: posts.append((url, kw)) or R())
    cfg = make_cfg(email={"to": []})
    items = _items(cfg, 12)
    done = notify.send_matches(items, cfg, kind="digest", top=items[:3], warnings=["redfin: found 0 listings"])
    assert done == ["discord", "ntfy"]
    ntfy = [kw["json"] for url, kw in posts if url == "https://ntfy.example/"]
    assert ntfy[0]["topic"] == "secret-topic" and ntfy[0]["click"] == items[0][1]["url"]
    assert "Source problem" in ntfy[0]["message"]
    disc = [kw["json"]["content"] for url, kw in posts if "discord" in url]
    assert all(len(c) <= 2000 for c in disc) and "…and 2 more" in "".join(disc)


def test_all_channels_failing_raises(monkeypatch):
    monkeypatch.setenv("NTFY_URL", "https://ntfy.example/t")
    for k in ("DISCORD_WEBHOOK_URL", "SLACK_WEBHOOK_URL", "TELEGRAM_BOT_TOKEN"):
        monkeypatch.delenv(k, raising=False)

    class R:
        status_code, text = 500, "boom"

    monkeypatch.setattr(notify.requests, "post", lambda url, **kw: R())
    cfg = make_cfg(email={"to": []})
    try:
        notify.send_test(cfg)
    except RuntimeError as e:
        assert "every notification channel failed" in str(e)
    else:
        raise AssertionError("expected RuntimeError")

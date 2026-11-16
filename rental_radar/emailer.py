"""Render listings as HTML + plain-text email and send over SMTP."""
import html
import smtplib
import ssl
from datetime import date
from email.message import EmailMessage
from urllib.parse import quote_plus

from .config import smtp_credentials

SAFETY_TIPS = [
    "Never wire money, pay with gift cards/crypto, or pay a deposit before a live tour and a signed lease.",
    "Verify the lister owns or manages the property: look the address up on the county assessor/recorder site, "
    "and check an agent's or manager's license with your state's real-estate licensing board.",
    "Reverse-image-search a photo or two; scammers copy real listings and post them at lower prices.",
    "A house renting far below its neighbors is usually a hijacked for-sale listing: search the address for a "
    "recent sale listing and call that listing agent (found on the brokerage's own site) before contacting the "
    "'landlord'.",
    "If you can't visit in person, ask for a live video walk-through: open closets and windows, walk outside to "
    "the yard, show the street sign and the house number.",
]


def _e(s) -> str:
    return html.escape(str(s or ""))


def headline(d, a) -> str:
    bits = [f"${d['price']:,}" if d.get("price") else "price?"]
    if d.get("beds") is not None:
        bits.append(f"{d['beds']}bd" + (f"/{d['baths']:g}ba" if d.get("baths") else ""))
    if d.get("sqft"):
        bits.append(f"{d['sqft']:,} sqft")
    bits.append(a["neighborhood"]["name"])
    return " · ".join(bits)


def contact_lines(d) -> list[str]:
    c = d.get("contact") or {}
    out = []
    if c.get("name") and not (d.get("address") or "").startswith(c["name"]):
        out.append(f"Lister: {c['name']}")
    if c.get("phone"):
        ph = "".join(ch for ch in c["phone"] if ch.isdigit())
        pretty = f"({ph[:3]}) {ph[3:6]}-{ph[6:10]}" if len(ph) == 10 else c["phone"]
        out.append(f"Phone: {pretty}")
    if c.get("email"):
        out.append(f"Email: {c['email']}")
    for link in c.get("links") or []:
        out.append(f"Lister link (apply/tour): {link}")
    if c.get("reply_url"):
        out.append(f"Reply: {c['reply_url']}")
    if c.get("external_url"):
        out.append(f"Also listed at: {c['external_url']}")
    if c.get("note"):
        out.append(c["note"])
    return out


def commute_line(a, cfg) -> str:
    c = a["commute"]
    s = f"Drive ~{c['drive']} min (rush hour est.)"
    if c.get("transit"):
        t = c["transit"]
        name = cfg["commute"]["transit"].get("name", "Transit")
        s += f" · {name} ~{t['minutes']} min ({t['access']} at {t['station']} → {t['exit']} + {t['last_mile']})"
    if c.get("bike"):
        s += f" · Bike ~{c['bike']} min"
    return s


def card_html(row, d, a, cfg, top_pick) -> str:
    photo = ""
    if d.get("photos"):
        photo = (f'<img src="{_e(d["photos"][0])}" width="560" '
                 f'style="max-width:100%;border-radius:6px;display:block;margin:8px 0">')
    # Typed-in addresses are often just a street line, so pin by coordinates instead
    addr = d["address"] if d.get("address") and "," in d["address"] else f"{d['lat']},{d['lon']}"
    maps = f"https://www.google.com/maps/search/?api=1&query={quote_plus(addr)}"
    sv = f"https://www.google.com/maps/@?api=1&map_action=pano&viewpoint={d['lat']},{d['lon']}"
    dest = cfg["destination"]
    dest_q = dest.get("address") or f"{dest['lat']},{dest['lon']}"
    route = f"https://www.google.com/maps/dir/?api=1&origin={d['lat']},{d['lon']}&destination={quote_plus(dest_q)}"
    comp_str = " · ".join(f"{k} {int(v * 100)}" for k, v in a["components"].items())
    thr, top_thr = cfg["scoring"]["email_threshold"], cfg["scoring"]["top_pick_threshold"]
    badge_color = "#1b7f3b" if row["score"] >= top_thr - 5 else ("#b26a00" if row["score"] >= thr - 10 else "#777")
    pros = "".join(f"<li>{_e(p)}</li>" for p in a["pros"]) or "<li>(none detected)</li>"
    cons = "".join(f"<li>{_e(c)}</li>" for c in a["cons"]) or "<li>(none detected)</li>"
    contact = "<br>".join(_e(x) for x in contact_lines(d))
    flags = ""
    if a.get("pet_flags"):
        flags = f"<p style='margin:4px 0'><b>Pet policy notes:</b> {_e('; '.join(a['pet_flags']))}</p>"
    tag = "⭐ TOP PICK · " if top_pick else ""
    return f"""
<div style="border:1px solid #ddd;border-radius:8px;padding:14px;margin:14px 0;font-family:Arial,sans-serif">
  <div style="font-size:13px;color:#666">{tag}{_e(d['source'].title())} · {_e(d.get('housing_type') or '')}</div>
  <div style="font-size:18px;font-weight:bold;margin:4px 0">
    <span style="background:{badge_color};color:#fff;border-radius:4px;padding:2px 8px;margin-right:6px">{row['score']:.0f}</span>
    <a href="{_e(d['url'])}">{_e(headline(d, a))}</a>
  </div>
  <div style="font-size:14px;color:#333">{_e(d.get('title'))}</div>
  <div style="font-size:13px;color:#555">{_e(d.get('address') or '')}</div>
  {photo}
  <p style="margin:6px 0"><b>Commute:</b> {_e(commute_line(a, cfg))}</p>
  {flags}
  <table width="100%" style="font-size:14px"><tr valign="top">
    <td width="50%"><b style="color:#1b7f3b">Pros</b><ul style="margin:4px 0;padding-left:18px">{pros}</ul></td>
    <td width="50%"><b style="color:#b00020">Cons</b><ul style="margin:4px 0;padding-left:18px">{cons}</ul></td>
  </tr></table>
  <p style="margin:6px 0;font-size:14px"><b>Contact:</b><br>{contact}</p>
  <p style="margin:6px 0;font-size:13px">
    <a href="{_e(d['url'])}">Listing</a> · <a href="{_e(maps)}">Map</a> · <a href="{_e(sv)}">Street View</a> ·
    <a href="{_e(route)}">Route to {_e(dest.get('label') or 'destination')}</a>
  </p>
  <div style="font-size:11px;color:#888">Score parts (0-100): {_e(comp_str)} · id {_e(row['uid'])}</div>
</div>"""


def card_text(row, d, a, cfg) -> str:
    lines = [f"[{row['score']:.0f}] {headline(d, a)}", d.get("title") or "", d.get("address") or "",
             d.get("url") or "", commute_line(a, cfg), "PROS:"] + [f"  + {p}" for p in a["pros"]] + ["CONS:"] + \
            [f"  - {c}" for c in a["cons"]] + ["CONTACT:"] + [f"  {x}" for x in contact_lines(d)]
    return "\n".join(lines)


def _top_table(top, new_uids, cfg) -> tuple[str, str]:
    """Compact ranked table (HTML, text) of the best listings still on the market."""
    tname = cfg["commute"]["transit"].get("name", "Transit")
    rows_html, rows_text = [], []
    for i, (r, d, a) in enumerate(top, 1):
        c = a["commute"]
        is_new = r["uid"] in new_uids
        warn = " ⚠" if a.get("scam_level") == "caution" else ""
        transit = f" · {tname} ~{c['transit']['minutes']} min" if c.get("transit") else ""
        rows_html.append(
            f'<tr style="border-top:1px solid #eee"><td>{i}</td><td><b>{r["score"]:.0f}</b></td>'
            f'<td>{"<b style=color:#1b7f3b>NEW</b> " if is_new else ""}<a href="{_e(d["url"])}">{_e(headline(d, a))}</a>'
            f'{warn}<br><span style="color:#666">{_e(d.get("address") or d.get("title") or "")} · '
            f'drive ~{c["drive"]} min{transit}</span></td></tr>')
        rows_text.append(f"{i:>2}. [{r['score']:.0f}] {'NEW ' if is_new else ''}{headline(d, a)}{warn} | "
                         f"{d.get('address') or ''} | {d['url']}")
    html_ = ('<table width="100%" cellpadding="4" style="font-size:13px;border-collapse:collapse">'
             + "".join(rows_html) + "</table>")
    return html_, "\n".join(rows_text)


def _criteria(cfg) -> str:
    bits = [f"rent ≤ ${cfg['budget']['max']:,}", f"{cfg['size']['min_beds']}BR+"]
    pets = cfg["pets"]
    if pets.get("dog"):
        bits.append("dogs OK")
    if pets.get("cat"):
        bits.append("cats OK")
    bits.append(f"commute ≤ {cfg['commute']['max_minutes']} min to {cfg['destination'].get('label') or 'destination'}")
    return ", ".join(bits)


def tips(cfg) -> list[str]:
    return SAFETY_TIPS + list(cfg["email"].get("safety_tips") or [])


def subject_line(items, cfg, kind="all", top=None, warnings=None, prefix="") -> str:
    """Email subject, also used as the title of chat notifications."""
    label = cfg["search"]["label"]
    top_t = cfg["scoring"]["top_pick_threshold"]
    top, warnings = top or [], warnings or []
    if kind == "alerts":
        prefix = prefix or f"⭐ {label}: top pick: "
    elif kind == "digest":
        today = date.today()
        prefix = prefix or f"{label} digest ({today:%b} {today.day}): "
    else:
        prefix = prefix or f"{label}: "
    n = len(items)
    if items:
        best_row, best_d, best_a = items[0]
        star = "⭐ " if best_row["score"] >= top_t and kind != "alerts" else ""
        subject = prefix + (f"{star}{n} new match{'es' if n > 1 else ''}: best {headline(best_d, best_a)} "
                            f"(score {best_row['score']:.0f})")
    else:
        subject = prefix + f"no new matches · top {len(top)} still available"
    if warnings:
        subject += f" · ⚠ {len(warnings)} source problem{'s' if len(warnings) > 1 else ''}"
    return subject


def build_email(items, cfg, subject_prefix: str = "", intro: str = "", kind: str = "all", top=None,
                warnings=None) -> EmailMessage:
    """items: (row, data, analysis) tuples sorted best-first (may be empty for a digest with only `top`).
    top: best still-open listings for the daily digest's running list.
    warnings: source-health problems (pipeline.source_health) shown in a banner at the top."""
    warnings = warnings or []
    top_t = cfg["scoring"]["top_pick_threshold"]
    top = top or []
    n = len(items)
    subject = subject_line(items, cfg, kind, top, warnings, subject_prefix)
    cards = "".join(card_html(r, d, a, cfg, r["score"] >= top_t) for r, d, a in items)
    tip_html = "".join(f"<li>{_e(t)}</li>" for t in tips(cfg))
    new_html = ("""<h2 style="font-size:18px;margin:16px 0 4px">New since the last digest</h2>""" if top else "") + (
        f"""<p style="font-size:14px">{n} new listing{'s' if n > 1 else ''} scored ≥ {cfg['scoring']['email_threshold']}
({_e(_criteria(cfg))}).</p>"""
        if items else '<p style="font-size:14px">No new listings scored above the threshold since the last digest.</p>')
    top_html = top_text = ""
    if top:
        th, tt = _top_table(top, {r["uid"] for r, _, _ in items}, cfg)
        top_html = (f'<h2 style="font-size:18px;margin:20px 0 4px">Top {len(top)} still available</h2>'
                    '<p style="font-size:13px;color:#555;margin:0 0 6px">Best of everything being tracked that is '
                    'still listed (⚠ = scam warning signs, check first). Full details were in earlier emails.</p>' + th)
        top_text = f"\n\nTOP {len(top)} STILL AVAILABLE:\n" + tt
    warn_html = ""
    if warnings:
        warn_html = ('<div style="font-size:14px;background:#fde7ea;padding:8px 12px;border-radius:6px">'
                     '<b>⚠ Source problems</b> (results may be incomplete)<ul style="margin:4px 0">'
                     + "".join(f"<li>{_e(w)}</li>" for w in warnings) + "</ul></div>")
    body = f"""<html><body style="max-width:640px;margin:auto;font-family:Arial,sans-serif">
{f'<p style="font-size:14px;background:#fff6e0;padding:8px;border-radius:6px">{_e(intro)}</p>' if intro else ''}
{warn_html}
{new_html}
{cards}
{top_html}
<div style="font-size:12px;color:#555;border-top:1px solid #ddd;margin-top:16px;padding-top:8px">
<b>Rental safety checklist</b><ul>{tip_html}</ul>
Neighborhood scores are rough judgments. Check the specific block on a crime map and in Street View.
</div></body></html>"""
    warn_text = "".join(f"⚠ SOURCE PROBLEM: {w}\n" for w in warnings) + ("\n" if warnings else "")
    text = warn_text + "\n\n".join(card_text(r, d, a, cfg) for r, d, a in items) + top_text + "\n\nSAFETY:\n" + \
        "\n".join(f"- {t}" for t in tips(cfg))
    return message(subject, text, body, cfg)


def build_retraction(items, cfg) -> EmailMessage:
    """Warning about listings emailed earlier that now look like scams."""
    n = len(items)
    first = items[0][1]
    subject = (f"⚠ SCAM WARNING: {'a listing' if n == 1 else f'{n} listings'} we sent you "
               f"({first.get('address') or first.get('title')}{'' if n == 1 else ', ...'})")
    blocks_html, blocks_text = [], []
    for r, d, a in items:
        why = (a.get("scam_flags") or []) + (a.get("caution_flags") or [])
        sent = (r["emailed_at"] or "")[:10]
        blocks_html.append(
            f'<div style="border:2px solid #b00020;border-radius:8px;padding:12px;margin:12px 0">'
            f'<div style="font-size:16px;font-weight:bold">{_e(headline(d, a))}</div>'
            f'<div style="font-size:13px;color:#555">{_e(d.get("address") or d.get("title"))} · sent {_e(sent)} · '
            f'<a href="{_e(d["url"])}">listing</a></div>'
            f'<p style="margin:6px 0"><b>Why we now think it\'s a scam:</b></p><ul style="margin:0">'
            + "".join(f"<li>{_e(w)}</li>" for w in why) + "</ul></div>")
        blocks_text.append(f"{headline(d, a)} | {d.get('address') or d.get('title')} | sent {sent}\n{d['url']}\n"
                           + "\n".join(f"  - {w}" for w in why))
    advice = ("Don't send money, an application fee, or ID documents to anyone connected with "
              f"{'this listing' if n == 1 else 'these listings'}. If you already did, contact your bank right away and "
              "report it at reportfraud.ftc.gov and to the platform it was posted on.")
    body = f"""<html><body style="max-width:640px;margin:auto;font-family:Arial,sans-serif">
<p style="font-size:15px;background:#fde7ea;padding:10px;border-radius:6px"><b>Correction:</b> we emailed you
{'this listing' if n == 1 else 'these listings'} earlier, but new checks show {'it is' if n == 1 else 'they are'} most
likely a scam. {_e(advice)}</p>
{''.join(blocks_html)}
</body></html>"""
    text = "CORRECTION: earlier listing(s) now look like scams.\n" + advice + "\n\n" + "\n\n".join(blocks_text)
    return message(subject, text, body, cfg)


def message(subject, text, body_html, cfg) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["To"] = recipients(cfg)
    msg["From"] = cfg["email"].get("from") or smtp_credentials()[0] or "rental-radar@localhost"
    msg.set_content(text)
    if body_html:
        msg.add_alternative(body_html, subtype="html")
    return msg


def recipients(cfg) -> str:
    to = cfg["email"]["to"]
    return ", ".join(to) if isinstance(to, list) else str(to)


def send(msg: EmailMessage, cfg) -> None:
    user, pw = smtp_credentials()
    if not user or not pw:
        raise RuntimeError("SMTP_USER / SMTP_PASSWORD are not set (put them in .env next to config.yaml)")
    if not cfg["email"]["to"]:
        raise RuntimeError("email.to is empty in config.yaml")
    e = cfg["email"]
    ctx = ssl.create_default_context()
    if int(e["smtp_port"]) == 465:
        with smtplib.SMTP_SSL(e["smtp_host"], e["smtp_port"], context=ctx, timeout=60) as s:
            s.login(user, pw.replace(" ", ""))
            s.send_message(msg)
    else:
        with smtplib.SMTP(e["smtp_host"], e["smtp_port"], timeout=60) as s:
            s.starttls(context=ctx)
            s.login(user, pw.replace(" ", ""))
            s.send_message(msg)

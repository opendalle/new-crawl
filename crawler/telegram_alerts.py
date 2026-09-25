"""
Telegram Alert Bot — Nexus Asia CRE Intel v4.0
================================================
Sends real-time CRE signal alerts to your Telegram.

Setup (one-time):
1. Message @BotFather on Telegram → /newbot → get BOT_TOKEN
2. Message your bot once → get CHAT_ID from:
   https://api.telegram.org/bot{TOKEN}/getUpdates
3. Add to GitHub Secrets:
   TELEGRAM_BOT_TOKEN = your token
   TELEGRAM_CHAT_ID   = your chat id

What gets alerted:
- HIGH/CRITICAL urgency signals
- Confidence >= 70
- Filters out duplicates within 24h
- Rich formatted message with all signal details
"""

import html
import os
import time
import requests
from datetime import datetime, timedelta, timezone

DASHBOARD_URL = os.environ.get("DASHBOARD_URL", "")
IST = timezone(timedelta(hours=5, minutes=30))
MAX_ALERTS_PER_RUN = int(os.environ.get("TELEGRAM_MAX_ALERTS", "25"))
_sent = 0


def _e(s) -> str:
    """Escape for Telegram HTML parse mode (company names contain &, < …)."""
    return html.escape(str(s or ""), quote=True)

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "")
TELEGRAM_API       = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

# Alert only these urgency levels
ALERT_URGENCY = {"HIGH", "CRITICAL"}
MIN_CONFIDENCE = 65

# Country flags
FLAGS = {
    "India": "🇮🇳", "USA": "🇺🇸", "UK": "🇬🇧", "Singapore": "🇸🇬",
    "UAE": "🇦🇪", "Japan": "🇯🇵", "Australia": "🇦🇺", "Hong Kong": "🇭🇰",
    "South Korea": "🇰🇷", "Malaysia": "🇲🇾", "Germany": "🇩🇪",
    "Canada": "🇨🇦", "Global": "🌐",
}

# Signal type emoji
SIGNAL_EMOJI = {
    "OFFICE": "🏢", "LEASE": "📋", "EXPAND": "📈", "FUNDING": "💰",
    "HIRING": "👥", "WAREHOUSE": "🏭", "DATA CENTRE": "🖥️",
    "RELOCATE": "🔄", "DISTRESS": "🚨", "FILING": "📄", "REIT": "🏗️",
}


def _is_configured() -> bool:
    return bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)


def send_message(text: str, parse_mode: str = "HTML") -> bool:
    """Send a message to the configured Telegram chat."""
    if not _is_configured():
        print("[Telegram] Not configured — skipping alert")
        return False
    try:
        resp = requests.post(
            f"{TELEGRAM_API}/sendMessage",
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "parse_mode": parse_mode,
                "disable_web_page_preview": True,
            },
            timeout=10,
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        print(f"[Telegram] Send error: {e}")
        return False


def format_signal_alert(company_name: str, signal: dict) -> str:
    """Format a signal into a rich Telegram message."""
    stype   = (signal.get("signal_type") or "OFFICE").upper()
    urgency = (signal.get("urgency") or "MEDIUM").upper()
    conf    = int(signal.get("confidence") or signal.get("confidence_score") or 0)
    loc     = signal.get("location") or "—"
    country = signal.get("country") or "India"
    summary = signal.get("summary") or ""
    why     = signal.get("why_cre") or ""
    evidence = signal.get("evidence") or ""
    sqft    = signal.get("sqft") or ""
    url     = signal.get("source_url") or ""
    
    flag    = FLAGS.get(country, "🌐")
    emoji   = SIGNAL_EMOJI.get(stype, "📡")
    
    urgency_icon = "🚨" if urgency == "CRITICAL" else "🔴" if urgency == "HIGH" else "🟡"
    conf_bar = "█" * (conf // 20) + "░" * (5 - conf // 20)
    
    lines = [
        f"{urgency_icon} <b>{urgency} SIGNAL — {stype}</b> {emoji}",
        f"",
        f"🏢 <b>{_e(company_name)}</b>",
        f"📍 {_e(loc)}  {flag} {_e(country)}",
    ]
    
    if sqft:
        lines.append(f"📐 {sqft:,} sq ft" if isinstance(sqft, int) else f"📐 {sqft} sq ft")
    
    lines += [
        f"🎯 Confidence: <code>{conf}</code>  [{conf_bar}]",
        f"",
        f"💡 {_e(summary[:300])}",
    ]

    if evidence:
        lines.append(f"📑 <i>“{_e(evidence[:350])}”</i>")
    if why:
        lines.append(f"→ {_e(why)}")

    links = []
    if url:
        links.append(f"<a href='{_e(url)}'>Source</a>")
    if DASHBOARD_URL:
        links.append(f"<a href='{_e(DASHBOARD_URL)}'>Dashboard</a>")
    if links:
        lines += ["", "🔗 " + "  |  ".join(links)]

    lines += [
        "",
        f"<code>⬡ NEXUS ASIA · {datetime.now(IST).strftime('%H:%M IST · %d %b %Y')}</code>",
    ]
    
    return "\n".join(lines)


def format_digest(signals: list) -> str:
    """Format a daily digest of top signals."""
    if not signals:
        return "⬡ <b>NEXUS ASIA — Daily Digest</b>\n\nNo new signals in the last 24 hours."
    
    lines = [
        f"⬡ <b>NEXUS ASIA — CRE Daily Digest</b>",
        f"<code>{datetime.now(IST).strftime('%d %b %Y · %H:%M IST')}</code>",
        f"",
        f"📊 <b>{len(signals)} new signals</b> in last 24h",
        f"",
    ]
    
    # Group by country
    by_country = {}
    for s in signals:
        co = s.get("country", "India")
        by_country.setdefault(co, []).append(s)
    
    for country, sigs in sorted(by_country.items(), key=lambda x: -len(x[1])):
        flag = FLAGS.get(country, "🌐")
        lines.append(f"{flag} <b>{country}</b> — {len(sigs)} signals")
        for s in sigs[:3]:
            co = s.get("companies", {})
            name = co.get("company_name", "?") if isinstance(co, dict) else "?"
            stype = (s.get("signal_type") or "?").upper()
            emoji = SIGNAL_EMOJI.get(stype, "📡")
            lines.append(f"  {emoji} {_e(name[:28])} — {stype}")
        if len(sigs) > 3:
            lines.append(f"  <i>+{len(sigs)-3} more...</i>")
        lines.append("")
    
    if DASHBOARD_URL:
        lines.append(f"🔗 <a href='{_e(DASHBOARD_URL)}'>Open Nexus Terminal</a>")
    return "\n".join(lines)


def send_signal_alert(company_name: str, signal: dict) -> bool:
    """Send alert for a single high-priority signal (capped per run)."""
    global _sent
    if _sent >= MAX_ALERTS_PER_RUN:
        return False
    urgency = (signal.get("urgency") or signal.get("priority_level") or "MEDIUM").upper()
    conf    = signal.get("confidence") or signal.get("confidence_score") or 0
    
    if urgency not in ALERT_URGENCY:
        return False
    if conf < MIN_CONFIDENCE:
        return False
    
    msg = format_signal_alert(company_name, signal)
    ok  = send_message(msg)

    if ok:
        _sent += 1
        print(f"[Telegram] ✓ Alert sent: {company_name[:30]} — {signal.get('signal_type')}")
    
    # Small delay to avoid flood
    time.sleep(0.3)
    return ok


def send_startup_message(signal_count: int = 0):
    """Send a message when crawler starts."""
    if not _is_configured():
        return
    send_message(
        f"⬡ <b>NEXUS ASIA Crawler Started</b>\n"
        f"<code>{datetime.now(IST).strftime('%d %b %Y · %H:%M IST')}</code>\n"
        f"Processing {signal_count} items..."
    )


def send_completion_message(stats: dict):
    """Send crawler completion summary."""
    if not _is_configured():
        return
    
    saved   = stats.get("saved", 0)
    seen    = stats.get("seen", 0)
    high    = stats.get("high_priority", 0)
    
    send_message(
        f"⬡ <b>NEXUS ASIA Crawl Complete</b>\n"
        f"<code>{datetime.now(IST).strftime('%d %b %Y · %H:%M IST')}</code>\n\n"
        f"✅ {saved} new signals saved\n"
        f"👁 {seen} items processed\n"
        f"📅 {stats.get('events', 0)} forward events\n"
        f"🔴 {high} high priority"
        + (f"\n\n🔗 <a href='{_e(DASHBOARD_URL)}'>Open Terminal</a>" if DASHBOARD_URL else "")
    )

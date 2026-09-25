"""
News: publisher RSS feeds + Google News RSS search queries.

All feeds/queries live in config/sources.json and config/news_queries.json,
so adding a market is a config change. Dead feeds are reported in the run
health report (feeds that returned 0 entries or an HTTP error).
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus

import feedparser

from crawler.http_client import HttpClient
from crawler.items import make_item

MAX_AGE_HOURS = 72

GNEWS_LOCALES = {
    "IN": ("en-IN", "IN", "IN:en"), "US": ("en-US", "US", "US:en"), "GB": ("en-GB", "GB", "GB:en"),
    "SG": ("en-SG", "SG", "SG:en"), "AE": ("en-AE", "AE", "AE:en"), "AU": ("en-AU", "AU", "AU:en"),
    "JP": ("en", "JP", "JP:en"), "HK": ("en-HK", "HK", "HK:en"), "KR": ("en", "KR", "KR:en"),
    "MY": ("en-MY", "MY", "MY:en"), "CA": ("en-CA", "CA", "CA:en"), "DE": ("en", "DE", "DE:en"),
    "FR": ("en", "FR", "FR:en"), "ID": ("en-ID", "ID", "ID:en"), "VN": ("en", "VN", "VN:en"),
}


def gnews_url(q: str, locale: str = "IN", when: str = "3d") -> str:
    hl, gl, ceid = GNEWS_LOCALES.get(locale, GNEWS_LOCALES["US"])
    query = f"{q} when:{when}" if when else q
    return f"https://news.google.com/rss/search?q={quote_plus(query)}&hl={hl}&gl={gl}&ceid={ceid}"


def _published(entry) -> datetime | None:
    for field in ("published_parsed", "updated_parsed"):
        t = entry.get(field)
        if t:
            try:
                return datetime(*t[:6], tzinfo=timezone.utc)
            except Exception:
                pass
    raw = entry.get("published") or entry.get("updated")
    if raw:
        try:
            return parsedate_to_datetime(raw).astimezone(timezone.utc)
        except Exception:
            pass
    return None   # unknown date — v4 pretended it was "now"; we don't


def _strip_html(s: str) -> str:
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = re.sub(r"&nbsp;|&#160;", " ", s)
    return re.sub(r"\s+", " ", s).strip()


class NewsCrawler:
    def __init__(self, feeds: list[dict], queries: list[dict], http: HttpClient | None = None,
                 max_age_hours: int = MAX_AGE_HOURS):
        self.feeds = feeds
        self.queries = queries
        self.http = http or HttpClient(extra_headers={"Accept": "application/rss+xml, application/xml, text/xml"})
        self.max_age = timedelta(hours=max_age_hours)
        self.health = {"feeds": 0, "feeds_ok": 0, "dead_feeds": [], "entries": 0, "kept": 0}

    def _fetch(self, url):
        res = self.http.get(url, quiet=True, max_bytes=5_000_000, deadline=25)
        if not res or res.status >= 400 or not res.content:
            return None
        return feedparser.parse(res.content)

    def crawl(self) -> list[dict]:
        items = []
        cutoff = datetime.now(timezone.utc) - self.max_age
        jobs = [dict(f, _kind="feed") for f in self.feeds] + [dict(q, _kind="query") for q in self.queries]
        for spec in jobs:
            if spec.get("enabled") is False:
                continue
            url = spec.get("url") or gnews_url(spec["q"], spec.get("locale", "IN"), spec.get("when", "3d"))
            label = spec.get("label") or url[:60]
            self.health["feeds"] += 1
            feed = self._fetch(url)
            if feed is None or not feed.entries:
                self.health["dead_feeds"].append(label)
                continue
            self.health["feeds_ok"] += 1
            kept = 0
            for e in feed.entries:
                self.health["entries"] += 1
                pub = _published(e)
                if pub and pub < cutoff:
                    continue
                title = _strip_html(e.get("title", ""))
                publisher = ""
                if spec["_kind"] == "query" and " - " in title:
                    title, publisher = title.rsplit(" - ", 1)
                summary = _strip_html(e.get("summary", ""))
                if summary.startswith(title[:40]):
                    summary = summary[len(title):].strip(" -–")
                items.append(make_item(
                    kind="news",
                    source=spec.get("source_tag") or ("GNEWS_" + re.sub(r"\W+", "_", label.upper())
                                                      if spec["_kind"] == "query" else "RSS"),
                    url=e.get("link", ""), title=title, text=summary,
                    published_at=pub.isoformat() if pub else None,
                    country=spec.get("country", "India"), region=spec.get("region"),
                    publisher=publisher or (feed.feed.get("title", "") if hasattr(feed, "feed") else ""),
                    signal_type_hint=spec.get("signal_hint"), why_cre_hint=spec.get("why", ""),
                    confidence_boost=spec.get("confidence_boost", 0), urgency_hint=spec.get("urgency"),
                    tier1=bool(spec.get("tier1")), query_label=label))
                kept += 1
            self.health["kept"] += kept
        print(f"[News] {self.health['feeds_ok']}/{self.health['feeds']} feeds OK, "
              f"{self.health['kept']} fresh items; dead: {len(self.health['dead_feeds'])}")
        return items

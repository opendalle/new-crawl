"""
Shared HTTP client for every crawler.

Why this exists (lessons from v4):
  * Every request has a hard (connect, read) timeout AND an overall deadline
    for streamed downloads, so a slow PDF server can never hang the run.
  * Per-host rate limiting (SEC asks for <= 10 req/s; exchanges get throttled
    much harder than that).
  * robots.txt is honoured for generic website crawling (IR pages). Official
    APIs (SEC, exchange JSON APIs, ATS APIs) are called as documented.
  * Downloads are size-capped so one 300 MB annual report can't eat the job.
"""
from __future__ import annotations

import os
import time
import threading
import urllib.robotparser
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

DEFAULT_TIMEOUT = (10, 20)          # (connect, read) seconds
DEFAULT_MAX_BYTES = 15 * 1024 * 1024  # 15 MB per document
DEFAULT_DEADLINE = 45               # seconds for a whole streamed download

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# Minimum seconds between two requests to the same host.
HOST_MIN_INTERVAL = {
    "www.sec.gov": 0.15,
    "efts.sec.gov": 0.15,
    "data.sec.gov": 0.15,
    "api.bseindia.com": 1.0,
    "www.bseindia.com": 1.0,
    "www.nseindia.com": 1.5,
    "nsearchives.nseindia.com": 1.0,
    "news.google.com": 1.0,
}
GENERIC_MIN_INTERVAL = 1.0


@dataclass
class FetchResult:
    url: str
    status: int
    content: bytes
    content_type: str
    truncated: bool = False

    @property
    def text(self) -> str:
        enc = "utf-8"
        if "charset=" in self.content_type:
            enc = self.content_type.split("charset=")[-1].split(";")[0].strip() or "utf-8"
        return self.content.decode(enc, errors="replace")


class HttpClient:
    def __init__(self, user_agent: str | None = None, extra_headers: dict | None = None,
                 max_retries: int = 2, respect_robots: bool = False):
        self.session = requests.Session()
        retry = Retry(total=max_retries, backoff_factor=1.5,
                      status_forcelist=[429, 500, 502, 503, 504],
                      allowed_methods=["GET", "HEAD", "POST"],
                      respect_retry_after_header=True)
        adapter = HTTPAdapter(max_retries=retry, pool_maxsize=8)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)
        self.session.headers.update({
            "User-Agent": user_agent or BROWSER_UA,
            "Accept-Language": "en-US,en;q=0.8",
        })
        if extra_headers:
            self.session.headers.update(extra_headers)
        self.respect_robots = respect_robots
        self._robots: dict[str, Optional[urllib.robotparser.RobotFileParser]] = {}
        self._last_hit: dict[str, float] = {}
        self._lock = threading.Lock()
        self.stats = {"requests": 0, "errors": 0, "robots_blocked": 0, "bytes": 0}

    # ── politeness ──────────────────────────────────────────────────────────
    def _throttle(self, url: str) -> None:
        host = urlparse(url).netloc
        gap = HOST_MIN_INTERVAL.get(host, GENERIC_MIN_INTERVAL)
        with self._lock:
            last = self._last_hit.get(host, 0.0)
            wait = gap - (time.monotonic() - last)
            if wait > 0:
                time.sleep(wait)
            self._last_hit[host] = time.monotonic()

    def allowed_by_robots(self, url: str) -> bool:
        if not self.respect_robots:
            return True
        p = urlparse(url)
        base = f"{p.scheme}://{p.netloc}"
        if base not in self._robots:
            rp = urllib.robotparser.RobotFileParser()
            try:
                r = self.session.get(base + "/robots.txt", timeout=(5, 10))
                if r.status_code >= 400:
                    rp = None  # no robots.txt → allowed
                else:
                    rp.parse(r.text.splitlines())
            except Exception:
                rp = None
            self._robots[base] = rp
        rp = self._robots[base]
        if rp is None:
            return True
        ua = self.session.headers.get("User-Agent", "*")
        return rp.can_fetch(ua, url)

    # ── requests ────────────────────────────────────────────────────────────
    def get(self, url: str, params: dict | None = None, headers: dict | None = None,
            timeout=DEFAULT_TIMEOUT, max_bytes: int = DEFAULT_MAX_BYTES,
            deadline: float = DEFAULT_DEADLINE, quiet: bool = False) -> Optional[FetchResult]:
        """GET with streaming, byte cap and wall-clock deadline. Never raises."""
        if not self.allowed_by_robots(url):
            self.stats["robots_blocked"] += 1
            if not quiet:
                print(f"[http] robots.txt disallows {url[:100]}")
            return None
        self._throttle(url)
        self.stats["requests"] += 1
        started = time.monotonic()
        try:
            with self.session.get(url, params=params, headers=headers, timeout=timeout,
                                  stream=True, allow_redirects=True) as r:
                if r.status_code >= 400:
                    self.stats["errors"] += 1
                    if not quiet:
                        print(f"[http] {r.status_code} {url[:100]}")
                    return FetchResult(r.url, r.status_code, b"", r.headers.get("Content-Type", ""))
                buf = bytearray()
                truncated = False
                for chunk in r.iter_content(chunk_size=64 * 1024):
                    if not chunk:
                        continue
                    buf.extend(chunk)
                    if len(buf) > max_bytes:
                        truncated = True
                        break
                    if time.monotonic() - started > deadline:
                        truncated = True
                        if not quiet:
                            print(f"[http] deadline hit after {len(buf)} bytes: {url[:90]}")
                        break
                self.stats["bytes"] += len(buf)
                return FetchResult(r.url, r.status_code, bytes(buf),
                                   r.headers.get("Content-Type", ""), truncated)
        except Exception as e:
            self.stats["errors"] += 1
            if not quiet:
                print(f"[http] ERR {url[:100]}: {type(e).__name__}: {str(e)[:120]}")
            return None

    def get_json(self, url: str, params: dict | None = None, headers: dict | None = None,
                 quiet: bool = False):
        res = self.get(url, params=params, headers=headers, quiet=quiet,
                       max_bytes=25 * 1024 * 1024)
        if not res or res.status >= 400 or not res.content:
            return None
        try:
            import json
            return json.loads(res.text)
        except Exception as e:
            if not quiet:
                snippet = res.text[:120].replace("\n", " ")
                print(f"[http] non-JSON response from {url[:80]}: {e} | {snippet}")
            return None


def sec_user_agent() -> str:
    """
    SEC requires a descriptive UA with contact e-mail:
    https://www.sec.gov/os/accessing-edgar-data
    Set SEC_USER_AGENT="Your Company Name admin@yourdomain.com".
    """
    ua = os.environ.get("SEC_USER_AGENT", "").strip()
    if not ua:
        ua = "NexusAsia CRE Research (set SEC_USER_AGENT env var)"
    return ua

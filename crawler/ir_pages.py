"""
Company website crawler — minutes, meeting outcomes, AGM proceedings,
earnings-call transcripts and investor decks posted on investor-relations
pages.

Works from an explicit list in config/watchlist.json → "ir_pages"
(no blind spidering of the internet). For each page:
  1. robots.txt is checked (HttpClient(respect_robots=True))
  2. links to documents whose link text / filename match DOC_PATTERNS are collected
  3. documents not already in the `documents` table are emitted for download
The pipeline then downloads, extracts text and looks for CRE evidence.

Note for India: SEBI LODR Reg 30/46 already requires listed companies to file
board outcomes, AGM proceedings and call transcripts with BSE/NSE, so the
exchange crawlers catch those for every listed company. This crawler is for
unlisted companies, foreign parents, and anything posted only on the website.
"""
from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from crawler.http_client import HttpClient
from crawler.items import make_item

DOC_PATTERNS = re.compile(
    r"minutes|outcome|proceeding|general meeting|\bagm\b|\begm\b|board meeting|transcript|"
    r"earnings call|conference call|concall|postal ballot|scrutini[sz]er|investor presentation|"
    r"analyst meet|press release|intimation|disclosure under regulation 30", re.I)
DOC_EXT = re.compile(r"\.(pdf|htm|html|txt)(\?|$)", re.I)


class IRPageCrawler:
    def __init__(self, pages: list[dict], store=None, http: HttpClient | None = None):
        self.pages = [p for p in pages if p.get("enabled", True)]
        self.store = store
        self.http = http or HttpClient(respect_robots=True)
        self.health = {"pages": 0, "pages_ok": 0, "links_found": 0, "new_docs": 0}

    def crawl(self) -> list[dict]:
        items = []
        for p in self.pages:
            self.health["pages"] += 1
            res = self.http.get(p["url"], headers={"Accept": "text/html,application/xhtml+xml"})
            if not res or res.status >= 400 or not res.content:
                print(f"[IR] {p['company']}: could not load {p['url']}")
                continue
            self.health["pages_ok"] += 1
            soup = BeautifulSoup(res.text, "lxml")
            host = urlparse(p["url"]).netloc
            found = []
            for a in soup.find_all("a", href=True):
                href = urljoin(res.url, a["href"].strip())
                label = " ".join(a.get_text(" ", strip=True).split())[:250]
                if not href.startswith("http"):
                    continue
                blob = f"{label} {href.rsplit('/', 1)[-1]}"
                if not DOC_PATTERNS.search(blob):
                    continue
                if not (DOC_EXT.search(href) or urlparse(href).netloc == host):
                    continue
                found.append((href, label or href.rsplit("/", 1)[-1]))
            # de-dup, keep page order (IR pages usually list newest first)
            seen, uniq = set(), []
            for href, label in found:
                if href not in seen:
                    seen.add(href)
                    uniq.append((href, label))
            self.health["links_found"] += len(uniq)
            new = 0
            for href, label in uniq:
                if new >= int(p.get("max_docs", 10)):
                    break
                if self.store and self.store.has_document(href):
                    continue
                new += 1
                items.append(make_item(
                    kind="web", source="IR_PAGE", url=href, doc_url=href, title=label,
                    company=p["company"], country=p.get("country", "India"),
                    category=label, extra={"ir_page": p["url"]}))
            self.health["new_docs"] += new
            print(f"[IR] {p['company']}: {len(uniq)} meeting/disclosure docs listed, {new} new")
        return items

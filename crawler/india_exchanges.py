"""
BSE + NSE crawlers — every corporate announcement, not just headlines.

BSE  : api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w
       - one calendar day per request (multi-day ranges come back as `{}`)
       - 50 rows/page, total in Table1[0].ROWCNT → we paginate all pages
       - attachment PDFs: www.bseindia.com/xml-data/corpfiling/AttachLive/<ATTACHMENTNAME>
                          (older ones move to …/AttachHis/)
NSE  : www.nseindia.com/api/corporate-announcements   (needs cookies from homepage)
       www.nseindia.com/api/corporate-board-meetings  (forward calendar)
       attachments are full URLs on nsearchives.nseindia.com

These are the endpoints the exchanges' own websites call. They are not a
contracted API: they rate-limit, may block datacenter IPs, and field names
can change. Parsers therefore read several candidate keys, and the first page
of every response is written to data/raw/ for inspection.
"""
from __future__ import annotations

import re
import time
from datetime import date, datetime, timedelta, timezone
from typing import Iterable, Optional

from crawler.http_client import HttpClient
from crawler.items import make_item, dump_raw


def _first(d: dict, *keys, default=""):
    for k in keys:
        v = d.get(k)
        if v not in (None, "", "-", "null"):
            return v
    return default


def _parse_dt(value: str) -> Optional[str]:
    """Exchange timestamps → ISO (assumed IST when naive)."""
    if not value:
        return None
    v = str(value).strip().replace("T", " ").split(".")[0]
    ist = timezone(timedelta(hours=5, minutes=30))
    for fmt in ("%Y-%m-%d %H:%M:%S", "%d-%b-%Y %H:%M:%S", "%d-%m-%Y %H:%M:%S", "%d-%b-%Y",
                "%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y %H:%M:%S", "%d %b %Y %H:%M:%S"):
        try:
            return datetime.strptime(v, fmt).replace(tzinfo=ist).isoformat()
        except ValueError:
            continue
    return None


def _days(days_back: int) -> Iterable[date]:
    today = datetime.now(timezone(timedelta(hours=5, minutes=30))).date()
    for i in range(days_back, -1, -1):
        yield today - timedelta(days=i)


# ════════════════════════════════════════════════════════════════════════════
class BSEAnnouncements:
    API = "https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w"
    ATTACH_LIVE = "https://www.bseindia.com/xml-data/corpfiling/AttachLive/"
    ATTACH_HIS = "https://www.bseindia.com/xml-data/corpfiling/AttachHis/"
    HEADERS = {
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://www.bseindia.com",
        "Referer": "https://www.bseindia.com/",
    }

    def __init__(self, http: HttpClient | None = None, max_pages_per_day: int = 40):
        self.http = http or HttpClient(extra_headers=self.HEADERS)
        self.max_pages = max_pages_per_day
        self.health = {"days": 0, "pages": 0, "rows": 0, "errors": 0}

    def crawl(self, days_back: int = 1) -> list[dict]:
        items: list[dict] = []
        dumped = False
        for d in _days(days_back):
            ds = d.strftime("%Y%m%d")
            total = None
            page = 1
            self.health["days"] += 1
            while page <= self.max_pages:
                params = {"pageno": page, "strCat": "-1", "strPrevDate": ds, "strScrip": "",
                          "strSearch": "P", "strToDate": ds, "strType": "C", "subcategory": "-1"}
                data = self.http.get_json(self.API, params=params)
                self.health["pages"] += 1
                if not isinstance(data, dict):
                    self.health["errors"] += 1
                    break
                if not dumped:
                    dump_raw("bse_announcements", data)
                    dumped = True
                rows = data.get("Table") or []
                if total is None:
                    try:
                        total = int((data.get("Table1") or [{}])[0].get("ROWCNT", 0))
                    except (ValueError, TypeError, IndexError):
                        total = 0
                if not rows:
                    break
                for r in rows:
                    items.append(self._to_item(r))
                self.health["rows"] += len(rows)
                if total and page * 50 >= total:
                    break
                page += 1
            print(f"[BSE] {d}: {total or 0} announcements, {page} page(s)")
        return items

    def _to_item(self, r: dict) -> dict:
        subject = _first(r, "NEWSSUB", "HEADLINE", "SUBJECT")
        headline = _first(r, "HEADLINE", "NEWSSUB")
        company = _first(r, "SLONGNAME", "SHORTNAME", "COMPANYNAME")
        attach = _first(r, "ATTACHMENTNAME")
        scrip = str(_first(r, "SCRIP_CD", "SCRIPCODE"))
        newsid = str(_first(r, "NEWSID"))
        doc_url = (self.ATTACH_LIVE + attach) if attach else ""
        page_url = "https://www.bseindia.com/corporates/ann.html"
        text = headline if headline and headline != subject else ""
        more = _first(r, "MORE", "NEWS_DETAILS")
        if more:
            text = f"{text} {re.sub(r'<[^>]+>', ' ', str(more))}".strip()
        return make_item(
            kind="filing", source="BSE", url=doc_url or f"{page_url}#{newsid}", doc_url=doc_url,
            title=subject, text=text, company=company,
            published_at=_parse_dt(_first(r, "NEWS_DT", "DT_TM", "DissemDT")),
            country="India", category=_first(r, "CATEGORYNAME", "SUBCATNAME"),
            ids={"bse_code": scrip} if scrip else {},
            extra={"newsid": newsid, "alt_doc_url": (self.ATTACH_HIS + attach) if attach else "",
                   "subcategory": _first(r, "SUBCATNAME")},
            exchange="BSE",
        )


# ════════════════════════════════════════════════════════════════════════════
class _NSEBase:
    HOME = "https://www.nseindia.com/"
    WARM = "https://www.nseindia.com/companies-listing/corporate-filings-announcements"
    HEADERS = {
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://www.nseindia.com/companies-listing/corporate-filings-announcements",
    }

    def __init__(self, http: HttpClient | None = None):
        self.http = http or HttpClient(extra_headers=self.HEADERS)
        self._warmed = False
        self.health = {"requests": 0, "rows": 0, "errors": 0}

    def _warm(self):
        if self._warmed:
            return
        # NSE sets bot-management cookies on the HTML pages; the JSON API
        # returns 401/403 without them.
        self.http.get(self.HOME, headers={"Accept": "text/html"}, quiet=True)
        self.http.get(self.WARM, headers={"Accept": "text/html"}, quiet=True)
        self._warmed = True

    def _get(self, path: str, params: dict):
        self._warm()
        self.health["requests"] += 1
        data = self.http.get_json("https://www.nseindia.com/api/" + path, params=params)
        if data is None:
            # one retry with fresh cookies
            self._warmed = False
            time.sleep(3)
            self._warm()
            data = self.http.get_json("https://www.nseindia.com/api/" + path, params=params)
        if data is None:
            self.health["errors"] += 1
        return data


class NSEAnnouncements(_NSEBase):
    def crawl(self, days_back: int = 1) -> list[dict]:
        today = datetime.now(timezone(timedelta(hours=5, minutes=30))).date()
        params = {"index": "equities",
                  "from_date": (today - timedelta(days=days_back)).strftime("%d-%m-%Y"),
                  "to_date": today.strftime("%d-%m-%Y")}
        data = self._get("corporate-announcements", params)
        dump_raw("nse_announcements", data)
        rows = data if isinstance(data, list) else (data or {}).get("data", []) if isinstance(data, dict) else []
        items = []
        for r in rows or []:
            if not isinstance(r, dict):
                continue
            subject = _first(r, "desc", "subject")
            detail = _first(r, "attchmntText", "details")
            company = _first(r, "sm_name", "company", "symbol")
            symbol = _first(r, "symbol")
            doc_url = _first(r, "attchmntFile")
            if doc_url and doc_url.startswith("/"):
                doc_url = "https://nsearchives.nseindia.com" + doc_url
            items.append(make_item(
                kind="filing", source="NSE", url=doc_url or
                f"https://www.nseindia.com/get-quotes/equity?symbol={symbol}#{_first(r, 'seq_id')}",
                doc_url=doc_url, title=subject, text=detail, company=company,
                published_at=_parse_dt(_first(r, "an_dt", "sort_date", "exchdisstime")),
                country="India", category=_first(r, "smIndustry"),
                ids={"nse_symbol": symbol} if symbol else {}, exchange="NSE"))
        self.health["rows"] += len(items)
        print(f"[NSE] announcements: {len(items)}")
        return items


class NSEBoardMeetings(_NSEBase):
    """Forward calendar: board meetings already notified to NSE, with purpose."""

    def crawl(self, days_ahead: int = 45) -> list[dict]:
        today = datetime.now(timezone(timedelta(hours=5, minutes=30))).date()
        params = {"index": "equities", "from_date": today.strftime("%d-%m-%Y"),
                  "to_date": (today + timedelta(days=days_ahead)).strftime("%d-%m-%Y")}
        data = self._get("corporate-board-meetings", params)
        dump_raw("nse_board_meetings", data)
        rows = data if isinstance(data, list) else (data or {}).get("data", []) if isinstance(data, dict) else []
        items = []
        for r in rows or []:
            if not isinstance(r, dict):
                continue
            symbol = _first(r, "bm_symbol", "symbol")
            company = _first(r, "sm_name", "bm_company", "company", default=symbol)
            purpose = _first(r, "bm_purpose", "purpose")
            desc = _first(r, "bm_desc", "desc")
            bm_date = _first(r, "bm_date", "meetingDate")
            ev_date = None
            for fmt in ("%d-%b-%Y", "%d-%m-%Y", "%Y-%m-%d"):
                try:
                    ev_date = datetime.strptime(str(bm_date)[:11], fmt).date().isoformat()
                    break
                except ValueError:
                    continue
            doc_url = _first(r, "attachment", "bm_attachment")
            items.append(make_item(
                kind="event", source="NSE_BOARD_MEETINGS",
                url=doc_url or f"https://www.nseindia.com/get-quotes/equity?symbol={symbol}",
                doc_url=doc_url, title=f"Board meeting: {purpose}".strip(), text=desc,
                company=company, published_at=_parse_dt(_first(r, "bm_timestamp")),
                country="India", ids={"nse_symbol": symbol} if symbol else {},
                event_type="BOARD_MEETING", event_date=ev_date, purpose=f"{purpose}. {desc}".strip(". "),
                exchange="NSE"))
        self.health["rows"] += len(items)
        print(f"[NSE] forthcoming board meetings: {len(items)}")
        return items


class BSEResultCalendar:
    """Forthcoming results dates (BSE 'Corpforthresults'). Field names are read
    defensively because this endpoint is less documented."""
    API = "https://api.bseindia.com/BseIndiaAPI/api/Corpforthresults/w"

    def __init__(self, http: HttpClient | None = None):
        self.http = http or HttpClient(extra_headers=BSEAnnouncements.HEADERS)

    def crawl(self, days_ahead: int = 30) -> list[dict]:
        today = datetime.now(timezone(timedelta(hours=5, minutes=30))).date()
        params = {"fromdate": today.strftime("%Y%m%d"),
                  "todate": (today + timedelta(days=days_ahead)).strftime("%Y%m%d"), "scripcode": ""}
        data = self.http.get_json(self.API, params=params)
        dump_raw("bse_result_calendar", data)
        rows = data if isinstance(data, list) else (data or {}).get("Table", []) if isinstance(data, dict) else []
        items = []
        for r in rows or []:
            if not isinstance(r, dict):
                continue
            name = _first(r, "Long_Name", "LONG_NAME", "short_name", "SHORT_NAME", "scrip_name")
            scrip = str(_first(r, "scrip_Code", "SCRIP_CODE", "scrip_code"))
            raw_date = _first(r, "meeting_date", "MEETING_DATE", "Meeting_Date", "result_date")
            ev_date = None
            for fmt in ("%d %b %Y", "%d-%b-%Y", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%d/%m/%Y"):
                try:
                    ev_date = datetime.strptime(str(raw_date)[:19], fmt).date().isoformat()
                    break
                except ValueError:
                    continue
            if not name:
                continue
            items.append(make_item(
                kind="event", source="BSE_RESULT_CALENDAR",
                url=f"https://www.bseindia.com/corporates/Forth_Results.html#{scrip}",
                title="Financial results (scheduled)", company=name, country="India",
                ids={"bse_code": scrip} if scrip else {}, event_type="RESULTS", event_date=ev_date,
                purpose="Financial results", exchange="BSE"))
        print(f"[BSE] forthcoming results: {len(items)}")
        return items

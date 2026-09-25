"""
Offline test harness. HttpClient.get is replaced by a router that serves
SYNTHETIC fixtures shaped like the real APIs (shapes verified live on
2026-09-23 for SEC FTS, Greenhouse, Lever, Ashby; BSE/NSE shapes from the
fields the exchanges' own sites use). Company names are fictional.
"""
import io
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from crawler.http_client import FetchResult, HttpClient  # noqa: E402

TODAY = datetime.now(timezone(timedelta(hours=5, minutes=30))).date()
NEXT_WEEK = TODAY + timedelta(days=7)


def make_pdf(lines):
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas
    except ImportError:
        pytest.skip("reportlab not installed")
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    y = 800
    for ln in lines:
        c.drawString(40, y, ln)
        y -= 16
    c.save()
    return buf.getvalue()


BOARD_OUTCOME_PDF_LINES = [
    "Regd. Office: 5th Floor, Acme House, Pune 411001. CIN: L12345MH2001PLC123456 Tel: 020-1234",
    "Sub: Outcome of Board Meeting held today",
    "The Board of Directors at its meeting held today has approved the following:",
    "1. Execution of a lease deed for 1,25,000 sq ft of office space at Hinjewadi Phase 2, Pune",
    "for a period of 9 years for the Company's new technology centre.",
    "2. Appointment of Mr. X as an Independent Director.",
]
RESULTS_PDF_LINES = ["Unaudited financial results for the quarter.", "Revenue grew 12 percent."]


def bse_payload():
    return {"Table": [
        {"NEWSID": "n1", "SCRIP_CD": 500001, "NEWSSUB": "Acme Infra Ltd - Outcome of Board Meeting",
         "HEADLINE": "Outcome of Board Meeting held on today", "SLONGNAME": "Acme Infra Ltd",
         "CATEGORYNAME": "Board Meeting", "ATTACHMENTNAME": "outcome1.pdf",
         "NEWS_DT": f"{TODAY.isoformat()}T15:30:00.1"},
        {"NEWSID": "n2", "SCRIP_CD": 500002, "NEWSSUB": "Beta Foods Ltd - Board Meeting Intimation for Fund Raising",
         "HEADLINE": f"Meeting of the Board of Directors is scheduled on {NEXT_WEEK.strftime('%d/%m/%Y')} "
                     "to consider raising of funds by way of QIP", "SLONGNAME": "Beta Foods Ltd",
         "CATEGORYNAME": "Board Meeting", "ATTACHMENTNAME": "", "NEWS_DT": f"{TODAY.isoformat()}T11:00:00"},
        {"NEWSID": "n3", "SCRIP_CD": 500003, "NEWSSUB": "Gamma Tech Ltd - Financial Results",
         "HEADLINE": "Financial Results for quarter", "SLONGNAME": "Gamma Tech Ltd",
         "CATEGORYNAME": "Result", "ATTACHMENTNAME": "results3.pdf", "NEWS_DT": f"{TODAY.isoformat()}T10:00:00"},
        {"NEWSID": "n4", "SCRIP_CD": 500004, "NEWSSUB": "Delta Steels Ltd - Admission of application under IBC by NCLT",
         "HEADLINE": "Corporate insolvency resolution process initiated", "SLONGNAME": "Delta Steels Ltd",
         "CATEGORYNAME": "Company Update", "ATTACHMENTNAME": "", "NEWS_DT": f"{TODAY.isoformat()}T09:00:00"},
    ], "Table1": [{"ROWCNT": 4}]}


def nse_ann_payload():
    return [{"symbol": "EPSILON", "desc": "Change in Registered Office Address",
             "attchmntText": "Epsilon Retail Limited has informed the Exchange regarding shifting of registered "
                             "office to Tower B, 12th floor, BKC, Mumbai with effect from October 1.",
             "sm_name": "Epsilon Retail Limited", "attchmntFile": "https://nsearchives.nseindia.com/corporate/eps1.pdf",
             "an_dt": TODAY.strftime("%d-%b-%Y") + " 14:02:11"}]


def nse_bm_payload():
    return [{"bm_symbol": "ZETA", "sm_name": "Zeta Logistics Limited", "bm_purpose": "Fund Raising",
             "bm_desc": "To consider acquisition of land parcel for warehouse and raising of funds",
             "bm_date": NEXT_WEEK.strftime("%d-%b-%Y"), "attachment": ""}]


def sec_fts_payload():
    return {"took": 5, "timed_out": False, "hits": {"total": {"value": 1, "relation": "eq"}, "hits": [{
        "_index": "edgar_file", "_id": "0001234567-26-000011:ex101.htm", "_score": 9.1,
        "_source": {"ciks": ["0001234567"], "display_names": ["Omega Robotics, Inc. (OMGA) (CIK 0001234567)"],
                    "root_forms": ["8-K"], "file_date": TODAY.isoformat(), "form": "8-K",
                    "adsh": "0001234567-26-000011", "file_type": "EX-10.1", "items": ["1.01", "9.01"],
                    "biz_locations": ["Austin, TX"], "biz_states": ["TX"]}}]}}


SEC_EX10_HTML = """<html><body><p>LEASE AGREEMENT</p>
<p>This Lease Agreement is entered into between Landlord LLC and Omega Robotics, Inc. The Premises consist of
approximately 212,000 rentable square feet located at 500 Congress Avenue, Austin, Texas.</p>
<p>The term shall be 126 months.</p></body></html>"""

FORM_D_IDX = """Description:           Daily Index of EDGAR Dissemination Feed by Form Type
--------------------------------------------------------------------------------------------------
D                Sigma Property Fund LP                                        1999999     {d}  edgar/data/1999999/0001999999-26-000001.txt
D                Tiny Startup Inc                                              1888888     {d}  edgar/data/1888888/0001888888-26-000001.txt
8-K              Something Else Corp                                           1777777     {d}  edgar/data/1777777/0001777777-26-000001.txt
"""

FORM_D_XML = """<?xml version="1.0"?><edgarSubmission><primaryIssuer><entityName>Sigma Property Fund LP</entityName>
<issuerAddress><city>Dallas</city><stateOrCountryDescription>TEXAS</stateOrCountryDescription></issuerAddress></primaryIssuer>
<offeringData><industryGroup><industryGroupType>Commercial</industryGroupType></industryGroup>
<typeOfFiling><newOrAmendment><isAmendment>false</isAmendment></newOrAmendment><dateOfFirstSale><value>2026-09-10</value></dateOfFirstSale></typeOfFiling>
<offeringSalesAmounts><totalOfferingAmount>75000000</totalOfferingAmount><totalAmountSold>40000000</totalAmountSold></offeringSalesAmounts>
</offeringData></edgarSubmission>"""

FORM_D_XML_SMALL = FORM_D_XML.replace("Sigma Property Fund LP", "Tiny Startup Inc").replace(
    "Commercial", "Other Technology").replace("75000000", "900000").replace("40000000", "500000")

GREENHOUSE = {"jobs": [
    {"id": 1, "title": "Workplace Experience Manager", "absolute_url": "https://example-jobs/1",
     "location": {"name": "Bengaluru, India"}, "first_published": "2026-09-20T10:00:00-04:00"},
] + [{"id": 100 + i, "title": f"Software Engineer {i}", "absolute_url": f"https://example-jobs/{100+i}",
      "location": {"name": "Bengaluru, India"}, "first_published": "2026-09-20T10:00:00-04:00"} for i in range(30)]
  + [{"id": 900, "title": "Account Executive", "absolute_url": "https://example-jobs/900",
      "location": {"name": "Indianapolis, IN"}, "first_published": "2026-09-20T10:00:00-04:00"}]}

RSS = """<?xml version="1.0"?><rss version="2.0"><channel><title>Test Feed</title>
<item><title>Kappa Systems leases 2 lakh sq ft office in Whitefield, Bengaluru - Realty News</title>
<link>https://news.example/kappa</link><pubDate>{pub}</pubDate>
<description>Kappa Systems has leased 2 lakh sq ft of office space in Whitefield, Bengaluru for its new global capability centre.</description></item>
<item><title>Cricket: India win the series</title><link>https://news.example/cricket</link><pubDate>{pub}</pubDate>
<description>India won the cricket tournament.</description></item>
<item><title>Why office rents are rising</title><link>https://news.example/why</link><pubDate>{pub}</pubDate>
<description>Office rents in Mumbai rose as demand for sq ft grew.</description></item>
</channel></rss>"""


class Router:
    def __init__(self):
        self.calls = []
        pub = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")
        d = TODAY.strftime("%Y%m%d")
        self.routes = [
            (r"AnnSubCategoryGetData", lambda u: self._json(bse_payload())),
            (r"AttachLive/outcome1\.pdf", lambda u: self._bin(make_pdf(BOARD_OUTCOME_PDF_LINES), "application/pdf")),
            (r"AttachLive/results3\.pdf", lambda u: self._bin(make_pdf(RESULTS_PDF_LINES), "application/pdf")),
            (r"api/corporate-announcements", lambda u: self._json(nse_ann_payload())),
            (r"api/corporate-board-meetings", lambda u: self._json(nse_bm_payload())),
            (r"nsearchives.*eps1\.pdf", lambda u: self._bin(make_pdf(["Shifting of registered office to Tower B, BKC, Mumbai."]), "application/pdf")),
            (r"nseindia\.com/($|companies-listing)", lambda u: self._bin(b"<html></html>", "text/html")),
            (r"Corpforthresults", lambda u: self._json({"Table": []})),
            (r"efts\.sec\.gov", lambda u: self._json(sec_fts_payload())),
            (r"1234567/000123456726000011/ex101\.htm", lambda u: self._bin(SEC_EX10_HTML.encode(), "text/html")),
            (r"daily-index/.*form\.(\d+)\.idx", lambda u: self._bin(FORM_D_IDX.format(d=d).encode(), "text/plain")
             if d in u else self._bin(b"", "text/plain", 404)),
            (r"1999999/.*/primary_doc\.xml", lambda u: self._bin(FORM_D_XML.encode(), "application/xml")),
            (r"1888888/.*/primary_doc\.xml", lambda u: self._bin(FORM_D_XML_SMALL.encode(), "application/xml")),
            (r"boards-api\.greenhouse\.io", lambda u: self._json(GREENHOUSE)),
            (r"news\.example|rss|news\.google", lambda u: self._bin(RSS.format(pub=pub).encode(), "application/rss+xml")),
        ]

    @staticmethod
    def _json(obj):
        return FetchResult("", 200, json.dumps(obj).encode(), "application/json")

    @staticmethod
    def _bin(b, ct, status=200):
        return FetchResult("", status, b, ct)

    def __call__(self, client, url, params=None, headers=None, **kw):
        from urllib.parse import urlencode
        full = url + ("?" + urlencode(params) if params else "")
        self.calls.append(full)
        for rx, fn in self.routes:
            if re.search(rx, full):
                res = fn(full)
                res.url = url
                return res
        return FetchResult(url, 404, b"", "text/plain")


@pytest.fixture
def router(monkeypatch):
    r = Router()
    monkeypatch.setattr(HttpClient, "get", lambda self, url, **kw: r(self, url, **kw))
    monkeypatch.setattr("time.sleep", lambda s: None)
    return r


@pytest.fixture
def tmp_workdir(tmp_path, monkeypatch):
    """Run the pipeline inside a temp dir that has the real config/ folder."""
    import shutil
    shutil.copytree(os.path.join(ROOT, "config"), tmp_path / "config")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("NEXUS_SQLITE_PATH", str(tmp_path / "data" / "test.db"))
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_KEY", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    return tmp_path

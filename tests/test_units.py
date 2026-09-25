import re
from datetime import date

from nlp.filing_intel import classify_filing, cre_sentences, meeting_date, parse_area_sqft
from nlp.grounding import ground_llm_result
from nlp.text_cleaner import normalize_company_name
from crawler.sec_edgar import _clean_name


def test_classify():
    assert classify_filing("Outcome of Board Meeting") == "BOARD_OUTCOME"
    assert classify_filing("Board Meeting Intimation for Fund Raising") == "BOARD_INTIMATION"
    assert classify_filing("Intimation of execution of Lease Deed") == "PROPERTY"
    assert classify_filing("Transcript of Earnings Call") == "TRANSCRIPT"
    assert classify_filing("Incorporation of Wholly Owned Subsidiary") == "INCORPORATION"
    assert classify_filing("Finland operations update") != "PROPERTY"   # 'land' needs a word boundary


def test_cre_sentences_skip_letterhead():
    text = ("Registered Office: 3rd Floor, Tower A, Andheri, Mumbai 400059. CIN L1234 Tel 022-1234. "
            "The Board approved taking on lease 45,000 sq ft of office space in Thane for the new delivery centre. "
            "The Board noted the results.")
    s = cre_sentences(text)
    assert len(s) == 1 and "45,000 sq ft" in s[0]


def test_area_and_dates():
    assert parse_area_sqft("approx 2.5 lakh sq ft") == 250000
    assert parse_area_sqft("1.2 million square feet") == 1200000
    assert parse_area_sqft("10,000 sq. m.") == 107639
    assert parse_area_sqft("5 sq ft") is None
    assert meeting_date("meeting to be held on 30th September, 2026", date(2026, 9, 23)) == date(2026, 9, 30)
    assert meeting_date("held on 30/09/2026", date(2026, 9, 23)) == date(2026, 9, 30)
    assert meeting_date("FY 2020 results", date(2026, 9, 23)) is None


def test_names():
    assert normalize_company_name("Infosys Limited") == normalize_company_name("INFOSYS LTD.") == "infosys"
    assert _clean_name("SI-BONE, Inc. (SIBN) (CIK 0001459839)") == ("SI-BONE, Inc.", "SIBN")


def test_grounding_rejects_invented_company_and_numbers():
    src = "Kappa Systems has leased 50,000 sq ft of office space in Pune."
    ok, notes = ground_llm_result({"is_cre_relevant": True, "company_name": "Kappa Systems",
                                   "evidence_quote": "Kappa Systems has leased 50,000 sq ft of office space",
                                   "sqft": 80000, "location": "Mumbai"}, src)
    assert ok and ok["sqft"] is None and ok["location"] is None
    assert "sqft_dropped" in notes and "location_dropped" in notes
    bad, notes = ground_llm_result({"is_cre_relevant": True, "company_name": "Kappa Systems",
                                    "evidence_quote": "Kappa Systems signed a 9-year lease in Pune for 50,000 sq ft"}, src)
    assert bad is None and notes == ["evidence_not_in_source"]


def test_gemini_extractor_uses_grounding(monkeypatch):
    from nlp.gemini_nlp import GeminiExtractor
    g = GeminiExtractor(api_key="test")
    monkeypatch.setattr(g, "_call", lambda prompt: '{"is_cre_relevant": true, "company_name": "Made Up Corp",'
                                                   ' "evidence_quote": "Kappa Systems has leased 50,000 sq ft"}')
    assert g.extract("Kappa Systems leases office", "Kappa Systems has leased 50,000 sq ft in Pune.") is None
    assert g.stats["rejected"] == {"company_not_in_source": 1}


def test_ir_page_finds_meeting_docs(router):
    from crawler.http_client import FetchResult
    from crawler.ir_pages import IRPageCrawler
    html = b"""<html><body>
      <a href="/docs/outcome-of-board-meeting-sep-2026.pdf">Outcome of Board Meeting - 20 Sep 2026</a>
      <a href="/docs/agm-proceedings-2026.pdf">Proceedings of 25th AGM</a>
      <a href="/docs/transcript-q1.pdf">Earnings call transcript Q1</a>
      <a href="/careers">Careers</a><a href="/docs/brochure.pdf">Product brochure</a></body></html>"""
    router.routes.insert(0, (r"ir\.example/investors", lambda u: FetchResult(u, 200, html, "text/html")))
    items = IRPageCrawler([{"company": "Iota Ltd", "url": "https://ir.example/investors", "max_docs": 5}]).crawl()
    urls = [i["doc_url"] for i in items]
    assert urls == ["https://ir.example/docs/outcome-of-board-meeting-sep-2026.pdf",
                    "https://ir.example/docs/agm-proceedings-2026.pdf",
                    "https://ir.example/docs/transcript-q1.pdf"]


def test_bse_paginates_all_pages(router):
    from crawler.http_client import FetchResult
    from crawler.india_exchanges import BSEAnnouncements
    import json

    def page(u):
        n = int(re.search(r"pageno=(\d+)", u).group(1))
        rows = [{"NEWSID": f"{n}-{i}", "SCRIP_CD": 1, "NEWSSUB": f"x {n}-{i}", "SLONGNAME": "A Ltd",
                 "ATTACHMENTNAME": f"{n}-{i}.pdf"} for i in range(50 if n < 3 else 20)]
        return FetchResult(u, 200, json.dumps({"Table": rows, "Table1": [{"ROWCNT": 120}]}).encode(), "application/json")
    router.routes.insert(0, (r"AnnSubCategoryGetData", page))
    items = BSEAnnouncements().crawl(days_back=0)
    assert len(items) == 120 and len({i["url"] for i in items}) == 120

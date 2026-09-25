import json
import sqlite3

from conftest import NEXT_WEEK


def _rows(db, sql):
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    return [dict(r) for r in con.execute(sql).fetchall()]


def test_end_to_end_offline(router, tmp_workdir):
    from pipeline import Pipeline
    p = Pipeline(sources=["bse", "nse", "nse_board_meetings", "sec_fts", "sec_form_d", "ats", "news"],
                 days_back=0, db="sqlite", use_llm=False)
    # keep the news crawl small: only one feed, no Google queries
    import pipeline as pl
    orig_load = pl._load
    pl._load = lambda path, default: ({"rss_feeds": [{"url": "https://news.example/rss", "label": "t"}]}
                                      if path.endswith("sources.json") else
                                      [] if path.endswith("news_queries.json") else orig_load(path, default))
    try:
        p.run()
    finally:
        pl._load = orig_load
    db = str(tmp_workdir / "data" / "test.db")
    sig = _rows(db, "SELECT s.*, c.company_name FROM signals s JOIN companies c USING(company_id)")
    by_co = {s["company_name"]: s for s in sig}

    # every signal has non-empty evidence
    assert sig and all(s["evidence"] for s in sig)

    # BSE board outcome → LEASE with sq ft parsed from the PDF text (and the letterhead ignored)
    acme = by_co["Acme Infra Ltd"]
    assert acme["signal_type"] == "LEASE"
    assert acme["sqft"] == 125000
    assert "Hinjewadi" in acme["evidence"] and "CIN" not in acme["evidence"]
    assert acme["category"] == "BOARD_OUTCOME"

    # results filing with no property language → no signal
    assert "Gamma Tech Ltd" not in by_co

    # distress subject line → DISTRESS signal quoting the subject
    assert by_co["Delta Steels Ltd"]["signal_type"] == "DISTRESS"

    # NSE registered office shift → RELOCATE
    assert by_co["Epsilon Retail Limited"]["signal_type"] == "RELOCATE"

    # SEC EX-10.1 lease → LEASE, 212,000 sq ft, correct EDGAR archive URL
    omega = by_co["Omega Robotics, Inc."]
    assert omega["signal_type"] == "LEASE" and omega["sqft"] == 212000
    assert omega["source_url"] == ("https://www.sec.gov/Archives/edgar/data/1234567/"
                                   "000123456726000011/ex101.htm")

    # Form D: real-estate fund kept, tiny tech raise dropped
    assert by_co["Sigma Property Fund LP"]["signal_type"] == "FUNDING"
    assert "totalAmountSold: 40000000" in by_co["Sigma Property Fund LP"]["evidence"]
    assert "Tiny Startup Inc" not in by_co

    # ATS: facilities role becomes a signal; first run is baseline (no surge yet)
    assert any("Workplace Experience Manager" in s["evidence"] for s in sig)

    # News: CRE article kept with company from headline; cricket + no-company article dropped
    assert by_co["Kappa Systems"]["sqft"] == 200000
    assert not any("cricket" in (s["title"] or "").lower() for s in sig)

    # Forward events: BSE intimation (date parsed) + NSE board meeting calendar
    ev = _rows(db, "SELECT e.*, c.company_name FROM forward_events e JOIN companies c USING(company_id)")
    evc = {e["company_name"]: e for e in ev}
    assert evc["Beta Foods Ltd"]["event_date"] == NEXT_WEEK.isoformat()
    assert evc["Beta Foods Ltd"]["cre_relevant"] == 1
    assert evc["Zeta Logistics Limited"]["event_date"] == NEXT_WEEK.isoformat()

    # documents table keeps the text we read (audit trail)
    docs = _rows(db, "SELECT url, text_status FROM documents")
    assert any(d["url"].endswith("outcome1.pdf") and d["text_status"] == "ok" for d in docs)

    # health report written
    rep = json.load(open(tmp_workdir / "data" / "run_report.json"))
    assert rep["sources"]["bse"]["ok"] and rep["sources"]["bse"]["items"] == 4


def test_second_run_is_idempotent_and_detects_surge(router, tmp_workdir):
    from pipeline import Pipeline
    import conftest
    Pipeline(sources=["bse", "ats"], days_back=0, db="sqlite", use_llm=False).run()
    db = str(tmp_workdir / "data" / "test.db")
    n1 = _rows(db, "SELECT COUNT(*) n FROM signals")[0]["n"]

    # second run: same filings (no duplicates); job board grew in Bengaluru 31 → 61
    extra = [{"id": 5000 + i, "title": f"Engineer {i}", "absolute_url": f"https://example-jobs/{5000+i}",
              "location": {"name": "Bengaluru, India"}} for i in range(30)]
    conftest.GREENHOUSE["jobs"].extend(extra)
    try:
        Pipeline(sources=["bse", "ats"], days_back=0, db="sqlite", use_llm=False).run()
    finally:
        del conftest.GREENHOUSE["jobs"][-30:]
    rows = _rows(db, "SELECT * FROM signals")
    surges = [r for r in rows if "hiring surge" in (r["title"] or "")]
    assert surges, "expected a measured hiring surge on the second snapshot"
    assert any("(was " in s["evidence"] for s in surges)
    # 'india' must not match 'Indianapolis'
    assert all("indianapolis" not in (s["evidence"] or "").lower() for s in surges)
    assert len(rows) == n1 + len(surges)          # nothing else was re-inserted
    assert len([r for r in rows if r["title"] and "Outcome of Board" in r["title"]]) == 1

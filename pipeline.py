"""
NEXUS PROP INTEL v5 — pipeline.

  crawl  → items (filing | news | event | jobs | web)
  read   → download + extract attachment text for filings worth reading
  decide → signal only if a verbatim evidence sentence supports it
  store  → companies, signals, forward_events, documents, job_snapshots
  score  → lead score per touched company from its last 90 days of signals
  report → data/run_report.json + crawl_runs table + Telegram summary
"""
from __future__ import annotations

import json
import os
import time
from collections import Counter
from datetime import date, datetime, timezone

from crawler.ats_jobs import ATSJobsCrawler
from crawler.documents import extract_any
from crawler.http_client import HttpClient, sec_user_agent
from crawler.india_exchanges import (BSEAnnouncements, BSEResultCalendar, NSEAnnouncements,
                                     NSEBoardMeetings)
from crawler.ir_pages import IRPageCrawler
from crawler.news_feeds import NewsCrawler
from crawler.sec_edgar import SECFormD, SECFullTextSearch
from crawler import telegram_alerts as tg
from database.store import get_store, sha1
from nlp.cre_filter import is_cre_relevant
from nlp.cre_intent import analyze_cre_intent, detect_country, detect_region
from nlp.enrichment import enrich_signal
from nlp.entity_extractor import extract_entities, title_company
from nlp.filing_intel import (DOC_WORTHY, classify_filing, cre_sentences, meeting_date,
                              parse_area_sqft, purpose_is_cre, signal_type_for)
from nlp.gemini_nlp import GeminiExtractor
from nlp.signal_classifier import classify_signal
from nlp.text_cleaner import clean_text, deduplicate
from scoring.lead_scorer import compute_lead_score

ALL_SOURCES = ["bse", "nse", "nse_board_meetings", "bse_results", "sec_fts", "sec_form_d",
               "ats", "ir", "news"]

DOC_PRIORITY = ["PROPERTY", "AGREEMENT", "BOARD_OUTCOME", "MINUTES", "AGM_EGM", "CAPEX",
                "ACQUISITION", "INCORPORATION", "TRANSCRIPT", "DISTRESS", "FUND_RAISE", "OTHER"]

CATEGORY_BASE_CONF = {
    "PROPERTY": 75, "AGREEMENT": 70, "BOARD_OUTCOME": 70, "MINUTES": 70, "AGM_EGM": 65,
    "TRANSCRIPT": 65, "CAPEX": 65, "INCORPORATION": 60, "ACQUISITION": 60, "DISTRESS": 70,
    "FUND_RAISE": 45, "FORM_D": 55, "OTHER": 55,
}
CATEGORY_WHY = {
    "PROPERTY": "Filing describes a lease / premises / land / relocation event",
    "AGREEMENT": "Disclosed agreement references property or space",
    "BOARD_OUTCOME": "Board meeting outcome contains a property / space decision",
    "MINUTES": "Meeting minutes reference property / space",
    "AGM_EGM": "Shareholder meeting document references property / space",
    "TRANSCRIPT": "Management commentary on space / facilities in earnings call",
    "CAPEX": "Capex / capacity expansion disclosed",
    "INCORPORATION": "New subsidiary incorporated — new registered office and possibly operating space",
    "ACQUISITION": "Acquisition disclosed with property / site component",
    "DISTRESS": "Insolvency / default disclosure — possible surrender or distressed asset",
    "FUND_RAISE": "Fund raise disclosed — capital for expansion (proxy signal)",
    "FORM_D": "Private placement reported to SEC (Form D)",
}
JUNK_NAMES = ["href=", "&#", "cin:", "dalal street", "listing department", "compliance officer",
              "unknown company", "bse limited", "national stock exchange"]


def _load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


class Pipeline:
    def __init__(self, sources=None, days_back=1, db=None, read_docs=True, use_llm=True,
                 max_docs=None, dry_run=False):
        self.sources = sources or ALL_SOURCES
        self.days_back = days_back
        self.read_docs = read_docs
        self.dry_run = dry_run
        self.store = get_store(db)
        self.watch = _load("config/watchlist.json", {})
        self.limits = self.watch.get("limits", {})
        self.max_docs = max_docs if max_docs is not None else int(self.limits.get("doc_downloads_per_run", 120))
        self.llm = GeminiExtractor() if use_llm else None
        self.doc_http = HttpClient()
        self.sec_http = HttpClient(user_agent=sec_user_agent())
        self.health: dict = {}
        self.stats = Counter()
        self.touched_companies: set[str] = set()
        self.docs_read = 0

    # ════════════════════════════════════════════════════════════════════════
    def crawl(self) -> list[dict]:
        items: list[dict] = []

        def run(name, fn):
            if name not in self.sources:
                return
            print(f"\n[Pipeline] ===== {name.upper()} =====")
            t0 = time.monotonic()
            try:
                out, health = fn()
                health = health or {}
                items.extend(out)
                all_failed = not out and (
                    health.get("errors", 0) > 0
                    or (health.get("boards", 0) and not health.get("boards_ok"))
                    or (health.get("feeds", 0) and not health.get("feeds_ok"))
                    or (health.get("pages", 0) and not health.get("pages_ok")))
                self.health[name] = {"ok": not all_failed, "items": len(out),
                                     "secs": round(time.monotonic() - t0, 1), **health}
                if all_failed:
                    self.health[name]["error"] = "every request failed (blocked, offline or API changed) — see log"
            except Exception as e:
                import traceback
                traceback.print_exc()
                self.health[name] = {"ok": False, "error": f"{type(e).__name__}: {e}"[:300],
                                     "secs": round(time.monotonic() - t0, 1)}

        L = self.limits

        def bse():
            c = BSEAnnouncements(max_pages_per_day=int(L.get("bse_max_pages_per_day", 40)))
            return c.crawl(self.days_back), c.health

        def nse():
            c = NSEAnnouncements()
            return c.crawl(self.days_back), c.health

        def nse_bm():
            c = NSEBoardMeetings()
            return c.crawl(45), c.health

        def bse_res():
            return BSEResultCalendar().crawl(30), {}

        def sec_fts():
            c = SECFullTextSearch(http=self.sec_http, queries=self.watch.get("sec_fulltext_queries"),
                                  max_hits_per_query=int(L.get("sec_fts_max_hits_per_query", 100)))
            return c.crawl(max(self.days_back, 2)), c.health

        def sec_d():
            c = SECFormD(http=self.sec_http, max_forms=int(L.get("sec_form_d_max_forms", 250)),
                         min_amount_usd=float(L.get("sec_form_d_min_usd", 10_000_000)))
            return c.crawl(self.days_back), c.health

        def ats():
            c = ATSJobsCrawler(self.watch.get("ats_boards", []), self.watch.get("focus_locations", []),
                               store=None if self.dry_run else self.store)
            return c.crawl(), c.health

        def ir():
            c = IRPageCrawler(self.watch.get("ir_pages", []), store=self.store)
            return c.crawl(), c.health

        def news():
            feeds = _load("config/sources.json", {}).get("rss_feeds", [])
            queries = _load("config/news_queries.json", [])
            c = NewsCrawler(feeds, queries, max_age_hours=int(L.get("news_max_age_hours", 72)))
            return c.crawl(), c.health

        run("bse", bse)
        run("nse", nse)
        run("nse_board_meetings", nse_bm)
        run("bse_results", bse_res)
        run("sec_fts", sec_fts)
        run("sec_form_d", sec_d)
        run("ats", ats)
        run("ir", ir)
        run("news", news)
        items = deduplicate(items, key="url")
        print(f"\n[Pipeline] {len(items)} unique items: "
              + ", ".join(f"{k}={v}" for k, v in Counter(i['kind'] for i in items).items()))
        return items

    # ════════════════════════════════════════════════════════════════════════
    def process(self, items: list[dict]) -> None:
        tg.send_startup_message(len(items))
        # 1) forward events first (cheap, no downloads)
        for it in [i for i in items if i["kind"] == "event"]:
            self._save_event(it)
        # 2) filings & website docs — highest-value categories first so the
        #    download budget is spent where it matters
        filings = [i for i in items if i["kind"] in ("filing", "web")]
        for f in filings:
            f["_cat"] = "FORM_D" if f.get("category") == "FORM_D" else classify_filing(
                f["title"] + " " + f.get("text", "")[:300], f.get("category", ""))
        filings.sort(key=lambda f: DOC_PRIORITY.index(f["_cat"]) if f["_cat"] in DOC_PRIORITY else 99)
        self.stats["filings"] = len(filings)
        print(f"\n[Pipeline] filing categories: {dict(Counter(f['_cat'] for f in filings))}")
        for f in filings:
            self._process_filing(f)
        # 3) jobs
        for j in [i for i in items if i["kind"] == "jobs"]:
            self._process_jobs(j)
        # 4) news
        news = [i for i in items if i["kind"] == "news"]
        self.stats["news"] = len(news)
        llm_budget = int(self.limits.get("llm_max_items", 120))
        for n in news:
            self._process_news(n, use_llm=llm_budget > 0)
            if self.llm and self.llm.enabled and n.get("_llm_called"):
                llm_budget -= 1

    # ── events ──────────────────────────────────────────────────────────────
    def _save_event(self, it):
        company = it.get("company_hint")
        if not company:
            return
        purpose = it.get("purpose") or it["title"]
        ev = {"event_key": sha1(f"{it['source']}|{company}|{it.get('event_type')}|{it.get('event_date')}|{purpose}"),
              "event_type": it.get("event_type", "EVENT"), "event_date": it.get("event_date"),
              "purpose": purpose[:1000], "cre_relevant": purpose_is_cre(purpose),
              "source_url": it["url"], "data_source": it["source"], "country": it.get("country", "India")}
        if self.dry_run:
            self.stats["events"] += 1
            return
        cid = self.store.upsert_company(company, it.get("country", "India"), it.get("ids"))
        if self.store.upsert_forward_event(cid, ev):
            self.stats["events"] += 1

    # ── filings ─────────────────────────────────────────────────────────────
    def _read_doc(self, f) -> str:
        url = f.get("doc_url")
        if not (self.read_docs and url) or self.docs_read >= self.max_docs:
            return ""
        if f["_cat"] not in DOC_WORTHY and f["kind"] != "web" and f["source"] != "SEC_EDGAR":
            return ""
        if not self.dry_run and self.store.has_document(url):
            self.stats["docs_already_read"] += 1
            return ""   # read in an earlier run; its signals already exist
        http = self.sec_http if "sec.gov" in url else self.doc_http
        res = http.get(url, headers={"Accept": "application/pdf,text/html,*/*"})
        alt = (f.get("extra") or {}).get("alt_doc_url")
        if (not res or res.status >= 400) and alt:
            res = http.get(alt, headers={"Accept": "application/pdf,*/*"})
        self.docs_read += 1
        if not res or res.status >= 400 or not res.content:
            self.stats["docs_failed"] += 1
            return ""
        doc = extract_any(res.content, res.content_type, url)
        self.stats[f"docs_{doc.status.split(':')[0]}"] += 1
        if not self.dry_run:
            self.store.save_document({"url": url, "source": f["source"], "company_name": f.get("company_hint"),
                                      "title": f["title"], "published_at": f.get("published_at"),
                                      "content_type": res.content_type[:100], "text_status": doc.status,
                                      "pages_read": doc.pages_read, "text": doc.text})
        return doc.text

    def _process_filing(self, f):
        cat = f["_cat"]
        company = f.get("company_hint")
        if not company:
            self.stats["filing_no_company"] += 1
            return
        head = f"{f['title']}. {f.get('text', '')}".strip()

        # Board meeting intimations → forward calendar (the "before it happens" feed)
        if cat == "BOARD_INTIMATION":
            ref = None
            try:
                ref = datetime.fromisoformat(f["published_at"]).date() if f.get("published_at") else None
            except ValueError:
                pass
            md = meeting_date(head, ref or date.today())
            self._save_event({**f, "kind": "event", "event_type": "BOARD_MEETING",
                              "event_date": md.isoformat() if md else None, "purpose": head[:1000]})
            return

        doc_text = self._read_doc(f)
        # evidence comes from the document body when we have it; the subject
        # line / API summary is the fallback
        evidence_list = (cre_sentences(doc_text) if doc_text else []) or cre_sentences(head)
        extraction = "rule"

        if cat == "FORM_D":
            evidence_list = [f["extra"].get("structured_evidence") or f.get("text", "")]
            extraction = "structured"
        elif not evidence_list:
            if cat in ("DISTRESS", "FUND_RAISE") and f["source"] != "SEC_EDGAR":
                evidence_list = [f["title"]]          # the subject line itself is the fact
            else:
                self.stats["filing_no_evidence"] += 1
                return

        evidence = " … ".join(evidence_list[:2])[:1200]
        sig_type = f.get("signal_type_hint") or signal_type_for(cat, evidence)
        if f["source"] == "SEC_EDGAR" and sig_type == "FILING":
            sig_type = signal_type_for("PROPERTY", evidence)
        sqft = parse_area_sqft(evidence)
        conf = CATEGORY_BASE_CONF.get(cat, 55)
        extra = f.get("extra") or {}
        if cat == "FORM_D" and str(extra.get("industry", "")).lower() in SECFormD.REAL_ESTATE_GROUPS:
            conf += 10
        if sqft:
            conf += 10
        if set(extra.get("items") or []) & {"1.01", "2.01"}:
            conf += 5
        urgency = "HIGH" if (cat == "DISTRESS" or (sqft or 0) >= 100_000) else "MEDIUM"
        locs = extract_entities(evidence)["locations"] if evidence else []
        signal = {
            "signal_type": sig_type, "title": f["title"],
            "summary": f"{f['title']}"[:300],
            "why_cre": CATEGORY_WHY.get(cat, "Filing contains property / space language"),
            "evidence": evidence, "source_url": f["url"], "doc_url": f.get("doc_url"),
            "data_source": f["source"], "exchange": f.get("exchange"), "category": cat,
            "location": (locs[0] if locs else f.get("location_hint")) or None,
            "country": f.get("country", "India"), "region": f.get("region") or f.get("country", "India"),
            "confidence": min(conf, 95), "urgency": urgency, "sqft": sqft,
            "published_at": f.get("published_at"), "extraction": extraction,
            "source_key": f["source_key"],
        }
        if cat == "FORM_D":
            sold = extra.get("amount_sold") or 0
            if sold:
                signal["funding_amount"] = f"${sold:,.0f}"
            signal["funding_round"] = "Form D"
        self._save(company, signal, f.get("ids"))

    # ── jobs ────────────────────────────────────────────────────────────────
    def _process_jobs(self, j):
        ev = j.get("evidence_override") or j["text"]
        signal = {
            "signal_type": j.get("signal_type_hint") or "HIRING", "title": j["title"],
            "summary": j["title"], "why_cre": j.get("why_cre_hint") or
            "Measured change in the company's own job board (open roles by location)",
            "evidence": ev, "source_url": j["url"], "data_source": j["source"], "category": "JOBS",
            "location": j.get("location_hint"), "country": j.get("country", "Global"),
            "region": j.get("region") or j.get("country", "Global"),
            "confidence": min(55 + int(j.get("confidence_boost", 0)), 90),
            "urgency": "HIGH" if "surge" in j["title"] and j.get("confidence_boost", 0) >= 10 else "MEDIUM",
            "headcount": j.get("headcount_hint"), "published_at": j.get("published_at"),
            "extraction": "computed", "source_key": j["source_key"],
        }
        self._save(j["company_hint"], signal, None)

    # ── news ────────────────────────────────────────────────────────────────
    def _process_news(self, a, use_llm=True):
        title = a["title"]
        text = clean_text(a.get("text", ""))
        combined = f"{title}. {text}".strip()
        if len(combined) < 30:
            self.stats["news_too_short"] += 1
            return
        art_country, art_region = a.get("country", "India"), a.get("region") or a.get("country", "India")
        country = detect_country(combined) if detect_country(combined) != "India" or art_country == "India" else art_country
        region = detect_region(combined, art_region)

        signal = None
        company = None
        llm = None
        # cheap rule pre-filter first, so the LLM quota is only spent on plausible items
        plausible = bool(a.get("tier1") or analyze_cre_intent(title, combined) or is_cre_relevant(title, text)[0])
        if not plausible:
            self.stats["news_not_cre"] += 1
            return
        if use_llm and self.llm and self.llm.enabled:
            a["_llm_called"] = True
            llm = self.llm.extract(title, text)
            if llm is None:
                self.stats["news_llm_rejected"] += 1

        if llm:
            company = llm["company_name"]
            signal = {"signal_type": llm.get("signal_type") or "OFFICE",
                      "confidence": int(llm.get("confidence") or 60), "urgency": "MEDIUM",
                      "why_cre": a.get("why_cre_hint") or ("Planned (not yet executed)" if llm.get("is_future_plan") else ""),
                      "evidence": llm["evidence_quote"], "location": llm.get("location"),
                      "sqft": llm.get("sqft"), "headcount": llm.get("headcount"), "extraction": "llm_verified"}
            if llm.get("country"):
                country = llm["country"]
        else:
            # rule path (same layers as v4, but evidence is now mandatory)
            intent = analyze_cre_intent(title, combined)
            classified = None
            relevant, conf, _ = is_cre_relevant(title, text)
            if relevant:
                classified = classify_signal({"title": title, "text": text})
            # an explicit transaction (lease/office/relocation with an area) beats an inferred intent
            if intent and classified and classified["signal_type"] in ("LEASE", "OFFICE", "RELOCATE") \
                    and classified.get("sqft_mentioned"):
                intent = None
            tier1 = a.get("tier1") and a.get("signal_type_hint")
            if not (intent or classified or tier1):
                self.stats["news_not_cre"] += 1
                return
            ev_sents = cre_sentences(combined, limit=1)
            evidence = ev_sents[0] if ev_sents else title
            if intent:
                signal = {"signal_type": intent["signal_type"], "confidence": intent["confidence_score"],
                          "urgency": intent.get("urgency", "MEDIUM"), "why_cre": intent.get("why_cre", "")}
            elif classified:
                signal = {"signal_type": classified["signal_type"], "confidence": classified["confidence_score"],
                          "urgency": "MEDIUM", "why_cre": a.get("why_cre_hint", "")}
            else:
                signal = {"signal_type": a["signal_type_hint"], "confidence": 55 + int(a.get("confidence_boost", 0) or 0) // 2,
                          "urgency": a.get("urgency_hint") or "MEDIUM", "why_cre": a.get("why_cre_hint", "")}
            signal.update(evidence=evidence, extraction="rule")
            company = title_company(title) or a.get("company_hint")
            if not company:
                ents = extract_entities(combined)
                company = ents["companies"][0] if ents["companies"] else None
                signal["location"] = ents["locations"][0] if ents["locations"] else None
            else:
                ents = extract_entities(combined)
                signal["location"] = ents["locations"][0] if ents["locations"] else None

        if not company or any(j in company.lower() for j in JUNK_NAMES):
            self.stats["news_no_company"] += 1
            return
        signal.update({
            "title": title, "summary": (text or title)[:400], "source_url": a["url"],
            "data_source": a["source"], "country": country, "region": region,
            "published_at": a.get("published_at"), "category": "NEWS", "source_key": a["source_key"],
        })
        signal["confidence"] = max(0, min(int(signal.get("confidence") or 0), 95))
        # enrichment reads only title + evidence, so numbers stay grounded
        signal = enrich_signal(signal, {"title": title, "text": signal.get("evidence", ""),
                                        "published_at": a.get("published_at")})
        self._save(company, signal, None)

    # ── persistence ─────────────────────────────────────────────────────────
    def _save(self, company, signal, ids):
        company = (company or "").strip()
        if len(company) < 2 or any(j in company.lower() for j in JUNK_NAMES):
            self.stats["rejected_company"] += 1
            return
        signal["signal_type"] = str(signal.get("signal_type") or "OFFICE").upper()
        signal["urgency"] = str(signal.get("urgency") or "MEDIUM").upper()
        self.stats[f"type_{signal['signal_type']}"] += 1
        if self.dry_run:
            self.stats["signals_new"] += 1
            self._dry.append({"company": company, **signal})
            return
        try:
            cid = self.store.upsert_company(company, signal.get("country", "India"), ids)
            _, created = self.store.insert_signal(cid, signal)
        except Exception as e:
            self.stats["db_errors"] += 1
            print(f"[DB] ERR {company[:30]}: {e}")
            return
        if created:
            self.stats["signals_new"] += 1
            self.touched_companies.add(cid)
            print(f"[+] {signal['signal_type']:<11} {company[:34]:<34} conf={signal['confidence']:<3} "
                  f"{signal['data_source'][:22]:<22} “{(signal.get('evidence') or '')[:70]}”")
            if signal["urgency"] in ("HIGH", "CRITICAL"):
                self.stats["high_priority"] += 1
            tg.send_signal_alert(company, signal)
        else:
            self.stats["signals_duplicate"] += 1

    def score(self):
        for cid in self.touched_companies:
            try:
                self.store.upsert_lead_score(cid, compute_lead_score(self.store.recent_signals(cid, 90)))
            except Exception as e:
                print(f"[Score] {cid}: {e}")

    # ════════════════════════════════════════════════════════════════════════
    def run(self) -> dict:
        started = datetime.now(timezone.utc).isoformat()
        self._dry: list = []
        if not self.dry_run:
            self.store.ping()
        print(f"[Pipeline] store={self.store.name} sources={','.join(self.sources)} days_back={self.days_back} "
              f"docs={'on' if self.read_docs else 'off'} llm={'on' if self.llm and self.llm.enabled else 'off'}")
        items = self.crawl()
        self.process(items)
        if not self.dry_run:
            self.score()
        report = {"started_at": started, "finished_at": datetime.now(timezone.utc).isoformat(),
                  "store": self.store.name, "sources": self.health, "stats": dict(self.stats),
                  "llm": self.llm.stats if self.llm and self.llm.enabled else None,
                  "docs_read": self.docs_read}
        os.makedirs("data", exist_ok=True)
        with open("data/run_report.json", "w") as fh:
            json.dump(report, fh, indent=2, default=str)
        if self.dry_run:
            with open("data/dry_run_signals.json", "w") as fh:
                json.dump(self._dry, fh, indent=2, default=str, ensure_ascii=False)
        else:
            try:
                self.store.save_run(started, report)
            except Exception as e:
                print(f"[Pipeline] could not save run report: {e}")
        tg.send_completion_message({"saved": self.stats["signals_new"], "seen": len(items),
                                    "high_priority": self.stats["high_priority"], "events": self.stats["events"]})
        self._print_summary(report)
        return report

    @staticmethod
    def _print_summary(r):
        print("\n[Pipeline] ════ SOURCE HEALTH ════")
        for name, h in r["sources"].items():
            flag = "OK " if h.get("ok") and h.get("items") else ("EMPTY" if h.get("ok") else "FAIL")
            print(f"  {flag:<5} {name:<20} items={h.get('items', 0):<5} {h.get('secs', 0)}s "
                  f"{h.get('error', '')}")
        s = r["stats"]
        print(f"""[Pipeline] ════ RESULT ════
  new signals        : {s.get('signals_new', 0)}  (duplicates skipped: {s.get('signals_duplicate', 0)})
  forward events     : {s.get('events', 0)}
  filings processed  : {s.get('filings', 0)}  (no evidence → no signal: {s.get('filing_no_evidence', 0)})
  documents read     : {r['docs_read']}
  news processed     : {s.get('news', 0)}  (not CRE: {s.get('news_not_cre', 0)}, no company: {s.get('news_no_company', 0)})
  report             : data/run_report.json""")

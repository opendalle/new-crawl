# NEXUS PROP INTEL v5: CRE intelligence from primary sources

This repo crawls exchange filings, SEC filings, board-meeting calendars, meeting minutes and transcripts, company job boards and news. It turns them into **commercial real estate demand signals**. Every signal stores the **exact sentence from the source** that justifies it.

```
BSE ─┐   NSE ─┐   NSE board-meeting calendar ─┐   SEC full-text (8-K, EX-10) ─┐   SEC Form D ─┐
     │        │                               │                               │              │
     ▼        ▼                               ▼                               ▼              ▼
  classify filing ─► download PDF/HTML ─► pull verbatim CRE sentences ─► signal (+evidence, sq ft, doc link)
                                                  │ none found → no signal (document still archived)
Job boards (Greenhouse/Lever/Ashby) ─► snapshot ─► measured surge vs last run / facilities roles
Company IR pages ─► minutes / outcomes / transcripts ─► same document path as filings
RSS + Google News (104 queries) ─► rules (+ optional Gemini, grounded) ─► signal only with a quote + company
                                                  ▼
                        Supabase (or SQLite) ─► dashboard (Vercel) · Telegram alerts · lead scores
```

## What each source really gives you

| Ask | What this repo does | Honest limit |
|---|---|---|
| **BSE / NSE filings** | Every announcement for the day, all pages (BSE returns 50 per page, one day per query). Reads the attached PDF for outcomes, minutes, AGM proceedings, transcripts, agreements, acquisitions, capex, property and distress filings. | These are the JSON endpoints the exchange websites use, not a contracted API. NSE (and sometimes BSE) blocks cloud IPs. See [India exchanges from the cloud](#india-exchanges-from-the-cloud). |
| **SEC / EDGAR** | Full-text search over 8-Ks and their exhibits. EX-10 exhibits are the signed lease or purchase agreements. Parses 8-K item codes (1.01 agreements, 2.01 asset deals, 2.05 exits). Also parses every Form D private placement filed that day. | Needs `SEC_USER_AGENT` with a contact e-mail (SEC rule). The crawler stays under SEC's 10 requests/second limit. |
| **Minutes of meetings** | (1) Indian listed companies must file board outcomes, AGM proceedings and call transcripts on BSE/NSE, and those PDFs are read. (2) `config/watchlist.json → ir_pages` scans the company websites you list for minutes, outcomes and transcripts, and respects robots.txt. | Scanned PDFs with no text layer are archived with `text_status=no_text_layer`. There is no OCR, so nothing is invented from them. |
| **"News before it happens"** | Nothing can scrape the future. You get the **earliest public traces**: board meetings notified to the exchange days ahead with their agenda (UPCOMING tab), 8-Ks (due within 4 business days of the event), Form D (due within 15 days of the first sale), hiring surges measured from job boards, and news queries for forward-looking language ("plans to open", "to set up", "signed MoU"). | These are leading indicators, not certainties. Forward-looking news is labelled as a plan. |
| **LinkedIn hiring** | **Not scraped.** It breaks LinkedIn's terms and gets blocked. Instead the crawler uses companies' own public job boards (Greenhouse, Lever, Ashby), stores a snapshot every run, and flags a surge only when open roles in a city actually rise. Facilities, workplace and real-estate hires are flagged role by role. | You choose which companies to watch (`ats_boards`). The first run only records a baseline. |
| **Term sheets** | Private documents, so they are never public. The public traces are Form D (amount, date of first sale, industry), 8-K Item 1.01 plus EX-10 exhibits (the actual agreements), BSE/NSE agreement and MoU disclosures, and "term sheet" news queries. | If a deal is never filed or reported, no crawler can see it. |

## No-hallucination rules (enforced in code, covered by tests)

1. **No evidence, no signal.** A filing only becomes a signal if `nlp/filing_intel.cre_sentences()` finds a sentence about premises, a lease, land, sq ft, relocation or capex in the document. Letterhead and footer addresses (CIN, PIN codes, "Regd. Office:") are filtered out.
2. **Numbers come from the evidence.** Sq ft is parsed only from the quoted sentence ("1,25,000 sq ft", "2 lakh sq ft", "212,000 rentable square feet", sq m converted).
3. **The LLM is optional and checked.** Gemini (news only) must return an `evidence_quote`. The result is thrown away unless that quote *and* the company name appear in the article. Any sq ft, headcount or location it returns that isn't in the text is dropped (`nlp/grounding.py`).
4. **Unknown stays unknown.** No publish date means `published_at` stays null (v4 wrote "now"). No company means no signal (v4 saved "Unknown Company").
5. **Audit trail.** Every document read is archived in the `documents` table. Every run writes a per-source health report (`data/run_report.json`, the `crawl_runs` table and the dashboard's SOURCE HEALTH tab). The first raw API page for each source goes to `data/raw/` so schema changes are easy to diagnose.

## Setup (about 15 minutes)

> **Your project-specific checklist is in [SETUP.md](SETUP.md).** The dashboard is already pointed at `esnugiumktntfmvxkvwa.supabase.co`.

### 0. Rotate the leaked keys first
v4 had a **Supabase service_role key in `index.html`** and a **Gemini API key** in three files. Both are in your GitHub history, so removing them from the code isn't enough.
- Supabase → Project Settings → API → roll the JWT secret / service_role key.
- Google AI Studio → delete the old key and create a new one.

### 1. Database
Supabase → SQL Editor → paste and run `database/schema.sql`. It's safe to run on top of the v4 tables because it only adds columns, tables and views.

### 2. GitHub Actions secrets (repo → Settings → Secrets and variables → Actions)
| Secret | Value |
|---|---|
| `SUPABASE_URL` | `https://<project>.supabase.co` |
| `SUPABASE_SERVICE_ROLE_KEY` | the new service_role key (the crawler needs write access) |
| `SEC_USER_AGENT` | `Nexus Asia Research you@yourdomain.com` |
| `GEMINI_API_KEY` | optional |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | optional alerts |

Optional variables: `DASHBOARD_URL`, `GEMINI_MODEL` (default `gemini-2.5-flash`).

The workflow `.github/workflows/crawl.yml` runs the offline tests, then crawls every 2 hours. You can also start it by hand (Actions → Run workflow) and pick sources and days.

### 3. Dashboard (Vercel)
Import the repo. The dashboard reads `nexus-config.js` (URL + anon key). Env vars `SUPABASE_URL` and `SUPABASE_ANON_KEY` override it (**the anon key only**; `/api/config` refuses a service_role key) and optionally `GEMINI_API_KEY` for the chat. `index.html` contains no keys. The chat runs server-side in `api/chat.js` and answers only from stored signals, with citations.

### 4. Choose what to watch: `config/watchlist.json`
- `ats_boards`: add companies whose job boards you want tracked (the slug from `boards.greenhouse.io/<slug>`, `jobs.lever.co/<slug>` or `jobs.ashbyhq.com/<slug>`). The 6 included boards were checked live on 2026-09-23.
- `ir_pages`: add investor-relations or disclosure page URLs and set `enabled: true`.
- `sec_fulltext_queries`, `limits`: tune volume.
- `config/news_queries.json` (104 Google News queries) and `config/sources.json` (31 RSS feeds): add markets by editing config, no code changes.

## Run locally

```bash
pip install -r requirements-dev.txt
python -m pytest -q tests                       # offline tests (synthetic fixtures)
cp .env.example .env                            # fill in, or skip to use SQLite
python main.py --db sqlite --days 1             # everything → data/nexus.db
python main.py --sources bse,nse --days 3       # India exchanges only
python main.py --sources sec_fts,sec_form_d     # US only
python main.py --dry-run --sources news         # nothing written; see data/dry_run_signals.json
python tools/export_json.py && python -m http.server 8000   # open http://localhost:8000/?local=1
```

## India exchanges from the cloud
NSE's bot protection usually rejects requests from data-centre IPs, including GitHub-hosted runners. BSE sometimes does too. If SOURCE HEALTH shows `nse` or `bse` as FAILED:
- Run a **self-hosted GitHub runner** on any machine or VPS in India. Change `runs-on: ubuntu-latest` to `runs-on: self-hosted` and keep the same workflow. Or run `python main.py --sources bse,nse,nse_board_meetings` from cron on that machine with the same `.env`.
- Don't rotate proxies to get around the block.

## Layout

```
main.py                    CLI
pipeline.py                orchestration: crawl → read docs → evidence → store → score → report
crawler/http_client.py     timeouts, per-host rate limits, robots.txt, capped streaming downloads
crawler/india_exchanges.py BSE announcements (paginated), NSE announcements, NSE board meetings, BSE results calendar
crawler/sec_edgar.py       EDGAR full-text search (fixed field mapping) + Form D
crawler/ats_jobs.py        Greenhouse / Lever / Ashby snapshots, surges, facilities roles
crawler/ir_pages.py        company IR pages → minutes / outcomes / transcripts
crawler/news_feeds.py      RSS + Google News queries (config-driven)
crawler/documents.py       PDF/HTML text extraction with page and time caps
nlp/filing_intel.py        filing categories, evidence sentences, sq ft, meeting dates
nlp/grounding.py           LLM output verification
database/schema.sql        Supabase schema (idempotent) · database/store.py  Supabase REST + SQLite
api/config.js, api/chat.js Vercel functions (no keys in the browser)
index.html                 dashboard: signals with evidence · UPCOMING calendar · SOURCE HEALTH
tests/                     offline end-to-end tests with synthetic fixtures (fictional companies)
```

## What was verified and what wasn't
- **Checked live on 2026-09-23:** SEC full-text search response fields, Greenhouse, Lever and Ashby job-board responses, and the six watchlist boards.
- **Built from the exchanges' own site calls and public client libraries, not checked live from this build environment:** BSE `AnnSubCategoryGetData` (date format `YYYYMMDD`, 50 rows per page, `Table1.ROWCNT`) and NSE `corporate-announcements` / `corporate-board-meetings`. The parsers try several field names for each value. Your first Actions run's `data/raw/*.json` artifact shows the live shapes.
- **Tested offline:** the whole pipeline end-to-end (10 tests), including idempotent re-runs, hiring-surge detection, letterhead filtering, and LLM-grounding rejection.

## Upgrading from v4
- Removed: LinkedIn scraping, the Naukri HTML scraper, the broken SEC parser, and hard-coded keys.
- Company names are now normalised case-insensitively. Old rows keep working. New rows for the same company may be created once under the lowercase key.
- `SUPABASE_KEY` still works as an alias for `SUPABASE_SERVICE_ROLE_KEY`.

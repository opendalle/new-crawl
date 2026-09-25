-- ============================================================================
-- NEXUS PROP INTEL v5 — Supabase / Postgres schema
-- Safe to run on a fresh project AND on top of the v3/v4 schema (idempotent).
-- Run the whole file in Supabase → SQL Editor.
-- ============================================================================

-- ── companies ───────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS companies (
    company_id      UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    company_name    TEXT NOT NULL,
    normalized_name TEXT UNIQUE,
    industry        TEXT,
    website         TEXT,
    hq_location     TEXT,
    country         TEXT DEFAULT 'India',
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
ALTER TABLE companies ADD COLUMN IF NOT EXISTS bse_code   TEXT;
ALTER TABLE companies ADD COLUMN IF NOT EXISTS nse_symbol TEXT;
ALTER TABLE companies ADD COLUMN IF NOT EXISTS sec_cik    TEXT;

-- ── signals ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS signals (
    signal_id        UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    company_id       UUID REFERENCES companies(company_id) ON DELETE CASCADE,
    signal_type      TEXT NOT NULL,
    space_type       TEXT,
    location         TEXT,
    country          TEXT DEFAULT 'India',
    region           TEXT DEFAULT 'India',
    confidence_score NUMERIC(5,2),
    urgency          TEXT DEFAULT 'MEDIUM',
    summary          TEXT,
    why_cre          TEXT,
    source_url       TEXT,
    data_source      TEXT,
    published_at     TIMESTAMPTZ,
    funding_amount   TEXT,
    funding_round    TEXT,
    headcount        INTEGER,
    sqft             INTEGER,
    created_at       TIMESTAMPTZ DEFAULT NOW()
);
-- v5 additions: every signal carries verbatim evidence from its source
ALTER TABLE signals ADD COLUMN IF NOT EXISTS source_key  TEXT;     -- stable dedupe key
ALTER TABLE signals ADD COLUMN IF NOT EXISTS evidence    TEXT;     -- exact quote from source
ALTER TABLE signals ADD COLUMN IF NOT EXISTS doc_url     TEXT;     -- attachment / filing doc
ALTER TABLE signals ADD COLUMN IF NOT EXISTS category    TEXT;     -- filing category
ALTER TABLE signals ADD COLUMN IF NOT EXISTS event_date  DATE;     -- scheduled date, if any
ALTER TABLE signals ADD COLUMN IF NOT EXISTS extraction  TEXT;     -- rule | llm_verified | computed
ALTER TABLE signals ADD COLUMN IF NOT EXISTS exchange    TEXT;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS title       TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS uq_signals_source_company ON signals(source_key, company_id);
CREATE INDEX IF NOT EXISTS idx_signals_company    ON signals(company_id);
CREATE INDEX IF NOT EXISTS idx_signals_type       ON signals(signal_type);
CREATE INDEX IF NOT EXISTS idx_signals_country    ON signals(country);
CREATE INDEX IF NOT EXISTS idx_signals_created    ON signals(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_signals_source_url ON signals(source_url);
CREATE INDEX IF NOT EXISTS idx_signals_confidence ON signals(confidence_score DESC);

-- ── lead_scores ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS lead_scores (
    company_id      UUID REFERENCES companies(company_id) ON DELETE CASCADE PRIMARY KEY,
    score           INTEGER DEFAULT 0,
    signal_count    INTEGER DEFAULT 0,
    priority_level  TEXT DEFAULT 'LOW',
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ── forward_events: things scheduled to happen (board meetings, results…) ──
CREATE TABLE IF NOT EXISTS forward_events (
    event_id     UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    event_key    TEXT UNIQUE NOT NULL,
    company_id   UUID REFERENCES companies(company_id) ON DELETE CASCADE,
    event_type   TEXT NOT NULL,          -- BOARD_MEETING | RESULTS | AGM | EGM | ...
    event_date   DATE,
    purpose      TEXT,                   -- verbatim purpose / agenda from the exchange
    cre_relevant BOOLEAN DEFAULT FALSE,
    source_url   TEXT,
    data_source  TEXT,
    country      TEXT DEFAULT 'India',
    created_at   TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_fe_date ON forward_events(event_date);

-- ── documents: raw text of every filing / minutes PDF we read (audit trail) ─
CREATE TABLE IF NOT EXISTS documents (
    doc_key      TEXT PRIMARY KEY,       -- sha1(url)
    url          TEXT NOT NULL,
    source       TEXT,
    company_name TEXT,
    title        TEXT,
    published_at TIMESTAMPTZ,
    content_type TEXT,
    text_status  TEXT,
    pages_read   INTEGER,
    text         TEXT,
    fetched_at   TIMESTAMPTZ DEFAULT NOW()
);

-- ── job_snapshots: ATS job-board counts over time (hiring surge = delta) ────
CREATE TABLE IF NOT EXISTS job_snapshots (
    snapshot_id  UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    company_name TEXT NOT NULL,
    ats          TEXT NOT NULL,
    board        TEXT NOT NULL,
    taken_at     TIMESTAMPTZ DEFAULT NOW(),
    total_jobs   INTEGER,
    by_location  JSONB,
    role_hits    JSONB,
    board_url    TEXT
);
CREATE INDEX IF NOT EXISTS idx_js_board ON job_snapshots(ats, board, taken_at DESC);

-- ── crawl_runs: per-run health report (which sources worked) ───────────────
CREATE TABLE IF NOT EXISTS crawl_runs (
    run_id      UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    started_at  TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    report      JSONB
);

-- ── full-text search ────────────────────────────────────────────────────────
ALTER TABLE signals ADD COLUMN IF NOT EXISTS fts tsvector
    GENERATED ALWAYS AS (
        to_tsvector('english',
            coalesce(signal_type,'') || ' ' || coalesce(location,'') || ' ' ||
            coalesce(summary,'') || ' ' || coalesce(why_cre,''))
    ) STORED;
CREATE INDEX IF NOT EXISTS signals_fts_idx ON signals USING GIN(fts);

ALTER TABLE companies ADD COLUMN IF NOT EXISTS fts tsvector
    GENERATED ALWAYS AS (
        to_tsvector('english',
            coalesce(company_name,'') || ' ' || coalesce(industry,'') || ' ' ||
            coalesce(hq_location,''))
    ) STORED;
CREATE INDEX IF NOT EXISTS companies_fts_idx ON companies USING GIN(fts);

-- ── Row-level security ──────────────────────────────────────────────────────
-- The public dashboard uses the ANON key: read-only on curated tables.
-- The crawler uses the SERVICE_ROLE key (GitHub secret only — never in HTML).
ALTER TABLE companies      ENABLE ROW LEVEL SECURITY;
ALTER TABLE signals        ENABLE ROW LEVEL SECURITY;
ALTER TABLE lead_scores    ENABLE ROW LEVEL SECURITY;
ALTER TABLE forward_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE job_snapshots  ENABLE ROW LEVEL SECURITY;
ALTER TABLE documents      ENABLE ROW LEVEL SECURITY;   -- no anon policy: private
ALTER TABLE crawl_runs     ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "anon_read_companies"      ON companies;
DROP POLICY IF EXISTS "anon_read_signals"        ON signals;
DROP POLICY IF EXISTS "anon_read_lead_scores"    ON lead_scores;
DROP POLICY IF EXISTS "anon_read_forward_events" ON forward_events;
DROP POLICY IF EXISTS "anon_read_job_snapshots"  ON job_snapshots;
DROP POLICY IF EXISTS "anon_read_crawl_runs"     ON crawl_runs;

CREATE POLICY "anon_read_companies"      ON companies      FOR SELECT USING (true);
CREATE POLICY "anon_read_signals"        ON signals        FOR SELECT USING (true);
CREATE POLICY "anon_read_lead_scores"    ON lead_scores    FOR SELECT USING (true);
CREATE POLICY "anon_read_forward_events" ON forward_events FOR SELECT USING (true);
CREATE POLICY "anon_read_job_snapshots"  ON job_snapshots  FOR SELECT USING (true);
CREATE POLICY "anon_read_crawl_runs"     ON crawl_runs     FOR SELECT USING (true);

-- Handy view for the dashboard: signal + company + lead score in one call
CREATE OR REPLACE VIEW signal_feed AS
SELECT s.*, c.company_name, c.bse_code, c.nse_symbol, c.sec_cik,
       l.score AS lead_score, l.priority_level
FROM signals s
LEFT JOIN companies   c ON c.company_id = s.company_id
LEFT JOIN lead_scores l ON l.company_id = s.company_id;
ALTER VIEW signal_feed SET (security_invoker = on);

CREATE OR REPLACE VIEW event_calendar AS
SELECT e.*, c.company_name, c.nse_symbol, c.bse_code
FROM forward_events e
LEFT JOIN companies c ON c.company_id = e.company_id;
ALTER VIEW event_calendar SET (security_invoker = on);

"""
Storage layer — one interface, two backends.

  SupabaseStore : production. Talks to PostgREST over HTTPS with the
                  SERVICE_ROLE key (set as a GitHub Actions secret only).
  SQLiteStore   : local runs / tests / "no cloud account yet". Same tables.

Pick with NEXUS_DB=supabase|sqlite (default: supabase if SUPABASE_URL and a
key are set, else sqlite at data/nexus.db).
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from nlp.text_cleaner import normalize_company_name

SIGNAL_COLUMNS = [
    "signal_type", "space_type", "location", "country", "region", "confidence_score",
    "urgency", "summary", "why_cre", "source_url", "data_source", "published_at",
    "funding_amount", "funding_round", "headcount", "sqft", "source_key", "evidence",
    "doc_url", "category", "event_date", "extraction", "exchange", "title",
]


def sha1(s: str) -> str:
    return hashlib.sha1((s or "").encode("utf-8", errors="ignore")).hexdigest()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _signal_row(company_id: str, s: dict) -> dict:
    row = {k: s.get(k) for k in SIGNAL_COLUMNS}
    row["company_id"] = company_id
    row["confidence_score"] = int(s.get("confidence_score", s.get("confidence", 0)) or 0)
    row["summary"] = (row.get("summary") or "")[:500]
    row["why_cre"] = (row.get("why_cre") or "")[:300]
    row["evidence"] = (row.get("evidence") or "")[:1200]
    row["title"] = (row.get("title") or "")[:300]
    if not row.get("source_key"):
        row["source_key"] = sha1(row.get("source_url") or row["summary"])
    return row


class BaseStore:
    name = "base"

    def ping(self) -> bool: ...
    def upsert_company(self, name: str, country: str = "India", ids: dict | None = None) -> str: ...
    def insert_signal(self, company_id: str, signal: dict) -> tuple[Optional[str], bool]: ...
    def upsert_forward_event(self, company_id: Optional[str], ev: dict) -> bool: ...
    def has_document(self, url: str) -> bool: ...
    def save_document(self, doc: dict) -> None: ...
    def last_job_snapshot(self, ats: str, board: str) -> Optional[dict]: ...
    def save_job_snapshot(self, snap: dict) -> None: ...
    def recent_signals(self, company_id: str, days: int = 90) -> list[dict]: ...
    def upsert_lead_score(self, company_id: str, data: dict) -> None: ...
    def save_run(self, started_at: str, report: dict) -> None: ...


# ════════════════════════════════════════════════════════════════════════════
# SQLite
# ════════════════════════════════════════════════════════════════════════════
SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
  company_id TEXT PRIMARY KEY, company_name TEXT NOT NULL, normalized_name TEXT UNIQUE,
  industry TEXT, website TEXT, hq_location TEXT, country TEXT DEFAULT 'India',
  bse_code TEXT, nse_symbol TEXT, sec_cik TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS signals (
  signal_id TEXT PRIMARY KEY, company_id TEXT, signal_type TEXT NOT NULL, space_type TEXT,
  location TEXT, country TEXT, region TEXT, confidence_score REAL, urgency TEXT,
  summary TEXT, why_cre TEXT, source_url TEXT, data_source TEXT, published_at TEXT,
  funding_amount TEXT, funding_round TEXT, headcount INTEGER, sqft INTEGER,
  source_key TEXT, evidence TEXT, doc_url TEXT, category TEXT, event_date TEXT,
  extraction TEXT, exchange TEXT, title TEXT, created_at TEXT,
  UNIQUE(source_key, company_id));
CREATE TABLE IF NOT EXISTS lead_scores (
  company_id TEXT PRIMARY KEY, score INTEGER, signal_count INTEGER,
  priority_level TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS forward_events (
  event_id TEXT PRIMARY KEY, event_key TEXT UNIQUE NOT NULL, company_id TEXT,
  event_type TEXT NOT NULL, event_date TEXT, purpose TEXT, cre_relevant INTEGER,
  source_url TEXT, data_source TEXT, country TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS documents (
  doc_key TEXT PRIMARY KEY, url TEXT NOT NULL, source TEXT, company_name TEXT, title TEXT,
  published_at TEXT, content_type TEXT, text_status TEXT, pages_read INTEGER, text TEXT,
  fetched_at TEXT);
CREATE TABLE IF NOT EXISTS job_snapshots (
  snapshot_id TEXT PRIMARY KEY, company_name TEXT, ats TEXT, board TEXT, taken_at TEXT,
  total_jobs INTEGER, by_location TEXT, role_hits TEXT, board_url TEXT);
CREATE TABLE IF NOT EXISTS crawl_runs (
  run_id TEXT PRIMARY KEY, started_at TEXT, finished_at TEXT, report TEXT);
"""


class SQLiteStore(BaseStore):
    name = "sqlite"

    def __init__(self, path: str = "data/nexus.db"):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.path = path
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SQLITE_SCHEMA)
        self.db.commit()

    def ping(self) -> bool:
        self.db.execute("SELECT 1")
        return True

    def upsert_company(self, name, country="India", ids=None):
        ids = ids or {}
        norm = normalize_company_name(name).lower()
        row = self.db.execute("SELECT company_id FROM companies WHERE normalized_name=?", (norm,)).fetchone()
        if row:
            cid = row["company_id"]
            for col in ("bse_code", "nse_symbol", "sec_cik"):
                if ids.get(col):
                    self.db.execute(f"UPDATE companies SET {col}=COALESCE({col}, ?) WHERE company_id=?",
                                    (str(ids[col]), cid))
            self.db.commit()
            return cid
        cid = str(uuid.uuid4())
        self.db.execute(
            "INSERT INTO companies(company_id,company_name,normalized_name,country,bse_code,nse_symbol,sec_cik,created_at)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (cid, name.strip(), norm, country, ids.get("bse_code"), ids.get("nse_symbol"),
             ids.get("sec_cik"), now_iso()))
        self.db.commit()
        return cid

    def insert_signal(self, company_id, signal):
        row = _signal_row(company_id, signal)
        row["signal_id"] = str(uuid.uuid4())
        row["created_at"] = now_iso()
        cols = list(row.keys())
        cur = self.db.execute(
            f"INSERT OR IGNORE INTO signals({','.join(cols)}) VALUES({','.join('?' * len(cols))})",
            [row[c] for c in cols])
        self.db.commit()
        return (row["signal_id"], True) if cur.rowcount else (None, False)

    def upsert_forward_event(self, company_id, ev):
        cur = self.db.execute(
            "INSERT OR IGNORE INTO forward_events(event_id,event_key,company_id,event_type,event_date,"
            "purpose,cre_relevant,source_url,data_source,country,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (str(uuid.uuid4()), ev["event_key"], company_id, ev["event_type"], ev.get("event_date"),
             ev.get("purpose"), int(bool(ev.get("cre_relevant"))), ev.get("source_url"),
             ev.get("data_source"), ev.get("country", "India"), now_iso()))
        self.db.commit()
        return bool(cur.rowcount)

    def has_document(self, url):
        return self.db.execute("SELECT 1 FROM documents WHERE doc_key=?", (sha1(url),)).fetchone() is not None

    def save_document(self, doc):
        self.db.execute(
            "INSERT OR REPLACE INTO documents(doc_key,url,source,company_name,title,published_at,"
            "content_type,text_status,pages_read,text,fetched_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (sha1(doc["url"]), doc["url"], doc.get("source"), doc.get("company_name"), doc.get("title"),
             doc.get("published_at"), doc.get("content_type"), doc.get("text_status"),
             doc.get("pages_read"), doc.get("text"), now_iso()))
        self.db.commit()

    def last_job_snapshot(self, ats, board):
        r = self.db.execute("SELECT * FROM job_snapshots WHERE ats=? AND board=? ORDER BY taken_at DESC LIMIT 1",
                            (ats, board)).fetchone()
        if not r:
            return None
        d = dict(r)
        d["by_location"] = json.loads(d["by_location"] or "{}")
        d["role_hits"] = json.loads(d["role_hits"] or "{}")
        return d

    def save_job_snapshot(self, snap):
        self.db.execute(
            "INSERT INTO job_snapshots(snapshot_id,company_name,ats,board,taken_at,total_jobs,by_location,role_hits,board_url)"
            " VALUES(?,?,?,?,?,?,?,?,?)",
            (str(uuid.uuid4()), snap["company_name"], snap["ats"], snap["board"], snap.get("taken_at", now_iso()),
             snap["total_jobs"], json.dumps(snap.get("by_location", {})), json.dumps(snap.get("role_hits", {})),
             snap.get("board_url")))
        self.db.commit()

    def recent_signals(self, company_id, days=90):
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        rows = self.db.execute("SELECT * FROM signals WHERE company_id=? AND created_at>=?", (company_id, since)).fetchall()
        return [dict(r) for r in rows]

    def upsert_lead_score(self, company_id, data):
        self.db.execute(
            "INSERT OR REPLACE INTO lead_scores(company_id,score,signal_count,priority_level,updated_at) VALUES(?,?,?,?,?)",
            (company_id, data["score"], data["signal_count"], data["priority_level"], now_iso()))
        self.db.commit()

    def save_run(self, started_at, report):
        self.db.execute("INSERT INTO crawl_runs(run_id,started_at,finished_at,report) VALUES(?,?,?,?)",
                        (str(uuid.uuid4()), started_at, now_iso(), json.dumps(report, default=str)))
        self.db.commit()


# ════════════════════════════════════════════════════════════════════════════
# Supabase (PostgREST over plain HTTPS — no SDK version drift)
# ════════════════════════════════════════════════════════════════════════════
class SupabaseStore(BaseStore):
    name = "supabase"

    def __init__(self, url: str, key: str):
        self.base = url.rstrip("/") + "/rest/v1"
        self.s = requests.Session()
        self.s.headers.update({
            "apikey": key, "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        })
        self._doc_cache: set[str] = set()

    def _req(self, method, table, params=None, body=None, prefer=None):
        headers = {"Prefer": prefer} if prefer else {}
        r = self.s.request(method, f"{self.base}/{table}", params=params,
                           data=json.dumps(body, default=str) if body is not None else None,
                           headers=headers, timeout=(10, 30))
        if r.status_code >= 400:
            raise RuntimeError(f"Supabase {method} {table} → {r.status_code}: {r.text[:300]}")
        return r.json() if r.text else []

    def ping(self):
        self._req("GET", "companies", params={"select": "company_id", "limit": "1"})
        return True

    def upsert_company(self, name, country="India", ids=None):
        ids = {k: str(v) for k, v in (ids or {}).items() if v}
        norm = normalize_company_name(name).lower()
        found = self._req("GET", "companies", params={"select": "company_id,bse_code,nse_symbol,sec_cik",
                                                      "normalized_name": f"eq.{norm}", "limit": "1"})
        if found:
            cid = found[0]["company_id"]
            patch = {k: v for k, v in ids.items() if not found[0].get(k)}
            if patch:
                self._req("PATCH", "companies", params={"company_id": f"eq.{cid}"}, body=patch)
            return cid
        body = {"company_name": name.strip(), "normalized_name": norm, "country": country, **ids}
        res = self._req("POST", "companies", params={"on_conflict": "normalized_name"}, body=body,
                        prefer="resolution=merge-duplicates,return=representation")
        return res[0]["company_id"]

    def insert_signal(self, company_id, signal):
        row = _signal_row(company_id, signal)
        res = self._req("POST", "signals", params={"on_conflict": "source_key,company_id"}, body=row,
                        prefer="resolution=ignore-duplicates,return=representation")
        return (res[0]["signal_id"], True) if res else (None, False)

    def upsert_forward_event(self, company_id, ev):
        body = {k: ev.get(k) for k in ("event_key", "event_type", "event_date", "purpose",
                                       "cre_relevant", "source_url", "data_source", "country")}
        body["company_id"] = company_id
        res = self._req("POST", "forward_events", params={"on_conflict": "event_key"}, body=body,
                        prefer="resolution=ignore-duplicates,return=representation")
        return bool(res)

    def has_document(self, url):
        key = sha1(url)
        if key in self._doc_cache:
            return True
        res = self._req("GET", "documents", params={"select": "doc_key", "doc_key": f"eq.{key}", "limit": "1"})
        if res:
            self._doc_cache.add(key)
        return bool(res)

    def save_document(self, doc):
        body = {k: doc.get(k) for k in ("url", "source", "company_name", "title", "published_at",
                                        "content_type", "text_status", "pages_read", "text")}
        body["doc_key"] = sha1(doc["url"])
        body["text"] = (body.get("text") or "").replace("\x00", " ")
        self._req("POST", "documents", params={"on_conflict": "doc_key"}, body=body,
                  prefer="resolution=merge-duplicates,return=minimal")
        self._doc_cache.add(body["doc_key"])

    def last_job_snapshot(self, ats, board):
        res = self._req("GET", "job_snapshots", params={"select": "*", "ats": f"eq.{ats}", "board": f"eq.{board}",
                                                        "order": "taken_at.desc", "limit": "1"})
        return res[0] if res else None

    def save_job_snapshot(self, snap):
        body = {k: snap.get(k) for k in ("company_name", "ats", "board", "total_jobs", "by_location",
                                         "role_hits", "board_url")}
        self._req("POST", "job_snapshots", body=body, prefer="return=minimal")

    def recent_signals(self, company_id, days=90):
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        return self._req("GET", "signals", params={"select": "signal_type,urgency,confidence_score",
                                                   "company_id": f"eq.{company_id}",
                                                   "created_at": f"gte.{since}"})

    def upsert_lead_score(self, company_id, data):
        body = {"company_id": company_id, "score": data["score"], "signal_count": data["signal_count"],
                "priority_level": data["priority_level"], "updated_at": now_iso()}
        self._req("POST", "lead_scores", params={"on_conflict": "company_id"}, body=body,
                  prefer="resolution=merge-duplicates,return=minimal")

    def save_run(self, started_at, report):
        self._req("POST", "crawl_runs", body={"started_at": started_at, "finished_at": now_iso(),
                                              "report": report}, prefer="return=minimal")


def get_store(kind: str | None = None) -> BaseStore:
    kind = (kind or os.environ.get("NEXUS_DB") or "").lower()
    url = os.environ.get("SUPABASE_URL") or os.environ.get("NEXT_PUBLIC_SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_KEY")
    if kind == "supabase" or (not kind and url and key):
        if not (url and key):
            raise ValueError("NEXUS_DB=supabase needs SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY (or SUPABASE_KEY)")
        return SupabaseStore(url, key)
    return SQLiteStore(os.environ.get("NEXUS_SQLITE_PATH", "data/nexus.db"))

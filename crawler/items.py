"""
The one record shape every crawler emits.

kind:
  filing  — exchange / regulator filing (BSE, NSE, SEC). Has an issuer name
            straight from the regulator, so company attribution is reliable.
  news    — RSS / Google News article. Company must be extracted from text.
  event   — something scheduled in the future (board meeting, results date).
  jobs    — computed from ATS job-board snapshots.
  web     — document found on a company website (IR page, minutes).
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Any


def make_item(*, kind: str, source: str, url: str, title: str, text: str = "",
              company: str = "", published_at: str | None = None, doc_url: str = "",
              country: str = "India", region: str | None = None, category: str = "",
              ids: dict | None = None, extra: dict | None = None, **hints: Any) -> dict:
    item = {
        "kind": kind,
        "source": source,
        "url": url or doc_url,
        "doc_url": doc_url,
        "title": (title or "").strip(),
        "text": (text or "").strip(),
        "company_hint": (company or "").strip(),
        "published_at": published_at,
        "country": country,
        "region": region or country,
        "category": category,
        "ids": ids or {},
        "extra": extra or {},
    }
    item.update(hints)  # signal_type_hint, why_cre_hint, confidence_boost, urgency_hint…
    basis = item.pop("source_key_override", None) or f"{source}|{item['url']}|{item['title']}"
    item["source_key"] = hashlib.sha1(basis.encode()).hexdigest()
    return item


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def dump_raw(source: str, payload: Any) -> None:
    """Save the raw API response of the first page per source per run to
    data/raw/ so schema drift can be diagnosed from the Actions artifact."""
    if os.environ.get("NEXUS_DUMP_RAW", "1") != "1":
        return
    try:
        os.makedirs("data/raw", exist_ok=True)
        path = f"data/raw/{source.lower()}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1, default=str)
    except Exception:
        pass

"""
Optional Gemini extraction for NEWS items (filings don't need it: the issuer
name comes from the regulator and evidence comes from the document).

  * Off unless GEMINI_API_KEY is set in the environment. No key in code.
  * The model must return an `evidence_quote` copied from the article; the
    result is thrown away unless that quote, the company name, and any
    numbers are found in the source text (nlp/grounding.py).
  * Model name configurable: GEMINI_MODEL (default gemini-2.5-flash).
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Optional

import requests

from nlp.grounding import ground_llm_result

API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

PROMPT = """You extract commercial-real-estate (CRE) demand signals from ONE news item.
Use ONLY the text below. Do not use outside knowledge. If a field is not stated in
the text, return null for it. Respond with JSON only.

TEXT:
<<<
{text}
>>>

JSON schema:
{{
  "is_cre_relevant": boolean,        // true only if the text describes a space/property/site event or plan
  "company_name": string|null,       // the occupier/tenant/buyer, spelled exactly as in TEXT
  "signal_type": "OFFICE"|"LEASE"|"EXPAND"|"FUNDING"|"HIRING"|"WAREHOUSE"|"DATA CENTRE"|"RELOCATE"|"DISTRESS"|"FILING",
  "location": string|null,           // city/micro-market exactly as written in TEXT
  "country": string|null,
  "sqft": number|null,               // only if an area is stated in TEXT
  "headcount": number|null,          // only if a number of jobs/seats is stated in TEXT
  "is_future_plan": boolean,         // true if TEXT describes something planned, not done
  "evidence_quote": string,          // an exact sentence copied verbatim from TEXT that supports the signal
  "confidence": integer              // 0-100: 90+ only if company, location and area are all stated
}}"""


class GeminiExtractor:
    def __init__(self, api_key: str | None = None, model: str | None = None, max_rpm: int | None = None):
        self.key = api_key or os.environ.get("GEMINI_API_KEY", "")
        self.model = model or os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
        max_rpm = max_rpm or int(os.environ.get("GEMINI_RPM", "10"))   # free tier ≈ 10 rpm
        self.min_gap = 60.0 / max(1, max_rpm)
        self._last = 0.0
        self.stats = {"calls": 0, "ok": 0, "grounded": 0, "rejected": {}, "errors": 0}

    @property
    def enabled(self) -> bool:
        return bool(self.key)

    def _call(self, prompt: str) -> Optional[str]:
        wait = self.min_gap - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()
        self.stats["calls"] += 1
        try:
            r = requests.post(
                f"{API_BASE}/{self.model}:generateContent",
                headers={"x-goog-api-key": self.key, "Content-Type": "application/json"},
                json={"contents": [{"parts": [{"text": prompt}]}],
                      "generationConfig": {"temperature": 0, "maxOutputTokens": 600,
                                           "responseMimeType": "application/json"}},
                timeout=(10, 30))
            if r.status_code == 429:
                time.sleep(20)
                return None
            r.raise_for_status()
            return r.json()["candidates"][0]["content"]["parts"][0]["text"]
        except Exception as e:
            self.stats["errors"] += 1
            print(f"[Gemini] error: {type(e).__name__}: {str(e)[:120]}")
            return None

    def extract(self, title: str, text: str) -> Optional[dict]:
        source = f"{title}\n\n{text}".strip()[:3000]
        raw = self._call(PROMPT.format(text=source))
        if not raw:
            return None
        try:
            raw = re.sub(r"^```(?:json)?|```$", "", raw.strip()).strip()
            result = json.loads(raw)
        except json.JSONDecodeError:
            self.stats["errors"] += 1
            return None
        self.stats["ok"] += 1
        grounded, notes = ground_llm_result(result, source)
        if grounded is None:
            reason = notes[0] if notes else "unknown"
            self.stats["rejected"][reason] = self.stats["rejected"].get(reason, 0) + 1
            return None
        self.stats["grounded"] += 1
        grounded["_grounding_notes"] = notes
        return grounded

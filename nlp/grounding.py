"""
Anti-hallucination checks. Any value that came from an LLM must be traceable
to the source text, or it is dropped (field) / rejected (whole signal).
"""
from __future__ import annotations

import re
import unicodedata

from nlp.text_cleaner import normalize_company_name


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "").lower()
    s = s.replace("’", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", s).strip()


def quote_in_source(quote: str, source: str, min_len: int = 20) -> bool:
    """The evidence quote must appear (whitespace/case-insensitive) in the source."""
    q, s = _norm(quote), _norm(source)
    if len(q) < min_len:
        return False
    if q in s:
        return True
    # tolerate the model trimming punctuation at the ends
    q2 = q.strip(" .,:;\"'")
    return len(q2) >= min_len and q2 in s


def company_in_source(company: str, source: str) -> bool:
    if not company:
        return False
    s = _norm(source)
    if _norm(company) in s:
        return True
    core = normalize_company_name(company)
    return len(core) >= 3 and core in normalize_company_name(source)


def number_in_source(value, source: str) -> bool:
    """sqft / headcount must literally appear (allowing 1,00,000 / 100,000 / 1 lakh forms)."""
    if value in (None, "", 0):
        return False
    try:
        n = int(float(str(value).replace(",", "")))
    except ValueError:
        return False
    s = _norm(source)
    candidates = set()   # word forms; plain digits are matched as whole tokens below
    if n % 100_000 == 0:
        candidates.add(f"{n // 100_000} lakh")
    if n % 1_000_000 == 0:
        candidates |= {f"{n // 1_000_000} million", f"{n // 1_000_000} mn"}
    if n >= 100_000 and (n % 10_000 == 0):
        candidates.add(f"{n / 100_000:g} lakh")
    if n >= 1_000_000 and (n % 100_000 == 0):
        candidates |= {f"{n / 1_000_000:g} million", f"{n / 1_000_000:g} mn"}
    # Indian grouping 1,00,000 → strip separators inside number tokens only
    tokens = {re.sub(r"[,]", "", t) for t in re.findall(r"\d[\d,]*", s)}
    words = any(re.search(r"(?<![\d.])" + re.escape(c) + r"\b", s) for c in candidates)
    return words or str(n) in tokens


def ground_llm_result(result: dict, source_text: str) -> tuple[dict | None, list[str]]:
    """Return (cleaned_result, notes). None if the core claim isn't grounded."""
    notes = []
    if not result or not result.get("is_cre_relevant"):
        return None, ["not_relevant"]
    ev = result.get("evidence_quote") or ""
    if not quote_in_source(ev, source_text):
        return None, ["evidence_not_in_source"]
    co = result.get("company_name")
    if not company_in_source(co, source_text):
        return None, ["company_not_in_source"]
    out = dict(result)
    if out.get("sqft") and not number_in_source(out["sqft"], source_text):
        notes.append("sqft_dropped")
        out["sqft"] = None
    if out.get("headcount") and not number_in_source(out["headcount"], source_text):
        notes.append("headcount_dropped")
        out["headcount"] = None
    loc = out.get("location")
    if loc and _norm(loc) not in _norm(source_text):
        notes.append("location_dropped")
        out["location"] = None
    return out, notes

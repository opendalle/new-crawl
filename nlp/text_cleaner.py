import hashlib
import re
import unicodedata

_SUFFIXES = [
    r"\bprivate\s+limited\b", r"\bpvt\.?\s*ltd\.?\b", r"\blimited\b", r"\bltd\.?\b",
    r"\bpvt\.?\b", r"\binc\.?\b", r"\bincorporated\b", r"\bcorporation\b", r"\bcorp\.?\b",
    r"\bllp\b", r"\bllc\b", r"\bplc\b", r"\bco\.?\b$", r"\bholdings?\b$", r"\bthe\b",
]


def clean_text(text: str) -> str:
    """Collapse whitespace. Keeps non-ASCII (₹, é, CJK) — v4 stripped it, which
    destroyed rupee amounts and non-English company names."""
    text = unicodedata.normalize("NFKC", text or "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;|&#160;", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def deduplicate(records: list, key="url") -> list:
    seen = set()
    unique = []
    for r in records:
        val = r.get(key) or r.get("title", "")
        identifier = hashlib.md5(val.encode()).hexdigest()
        if identifier not in seen:
            seen.add(identifier)
            unique.append(r)
    return unique


def normalize_company_name(name: str) -> str:
    """'Infosys Limited' / 'INFOSYS LTD.' / 'Infosys Ltd' → 'infosys'."""
    n = unicodedata.normalize("NFKC", name or "").lower()
    n = re.sub(r"\(.*?\)", " ", n)        # drop '(CIK 000…)', '(INFY)'
    for s in _SUFFIXES:
        n = re.sub(s, " ", n)
    n = re.sub(r"[^\w&]+", " ", n)
    return re.sub(r"\s+", " ", n).strip()

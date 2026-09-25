"""
Filing intelligence — turns exchange / SEC filings and meeting documents into
CRE signals, **only** with verbatim evidence.

Principle: nothing here generates facts. It (1) classifies a filing from its
own subject line, (2) pulls sentences out of the document text, (3) parses
numbers/dates that literally appear in those sentences. If no qualifying
sentence exists, no signal is produced.
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Optional

# ── 1. Filing categories (matched on subject/headline/category text) ─────────
CATEGORY_RULES: list[tuple[str, list[str]]] = [
    ("DISTRESS", ["insolvency", "nclt", "cirp", "resolution professional", "liquidation",
                  "winding up", "sarfaesi", "one time settlement", "default in payment",
                  "defaults on", "interim resolution", "ibc"]),
    ("BOARD_OUTCOME", ["outcome of board", "outcome of the board", "board meeting outcome",
                       "outcome of meeting", "decisions taken", "outcome of the meeting"]),
    ("BOARD_INTIMATION", ["board meeting intimation", "intimation of board meeting",
                          "prior intimation", "notice of board meeting", "board meeting to be held",
                          "meeting of the board of directors is scheduled", "board meeting on"]),
    ("MINUTES", ["minutes of", "minutes of the"]),
    ("AGM_EGM", ["annual general meeting", "extra-ordinary general meeting",
                 "extraordinary general meeting", " agm", " egm", "postal ballot", "scrutinizer",
                 "proceedings of", "voting results"]),
    ("TRANSCRIPT", ["transcript", "earnings call", "conference call", "concall"]),
    ("INVESTOR_MEET", ["analyst meet", "investor meet", "analyst / investor", "investor conference"]),
    ("AGREEMENT", ["agreement", "memorandum of understanding", " mou", "term sheet", "binding",
                   "letter of intent", " loi", "definitive"]),
    ("ACQUISITION", ["acquisition", "acquire", "amalgamation", "merger", "scheme of arrangement",
                     "takeover", "stake in", "purchase of"]),
    ("INCORPORATION", ["incorporation of", "wholly owned subsidiary", "wholly-owned subsidiary",
                       "new subsidiary", "step-down subsidiary", "step down subsidiary"]),
    ("FUND_RAISE", ["fund raising", "fund-raising", "fundraise", "raising of funds", "qip",
                    "qualified institutions placement", "preferential issue", "preferential allotment",
                    "rights issue", "non-convertible debentures", " ncd", "private placement", "form d"]),
    ("PROPERTY", ["lease", "leave and license", "leave & license", "premises", "office space",
                  "land", "property", "sq ft", "sq. ft", "square feet", "campus",
                  "relocation", "shifting of registered office", "change in registered office",
                  "change of registered office", "warehouse", "data centre", "data center"]),
    ("CAPEX", ["capex", "capital expenditure", "capacity expansion", "new plant", "new facility",
               "commencement of commercial production", "commercial operations", "expansion"]),
    ("RESULTS", ["financial results", "quarterly results", "audited results", "unaudited results"]),
]

# categories where the attached document is worth downloading & reading
DOC_WORTHY = {"DISTRESS", "BOARD_OUTCOME", "MINUTES", "AGM_EGM", "TRANSCRIPT", "AGREEMENT",
              "ACQUISITION", "INCORPORATION", "FUND_RAISE", "PROPERTY", "CAPEX"}


def _kw_regex(kw: str) -> re.Pattern:
    kw = kw.strip()
    # short tokens (land, lease, mou, qip, agm…) need both word boundaries;
    # longer stems ('acquire', 'relocation') only a left boundary so plurals match
    right = r"(?![a-z])" if len(kw) <= 5 else ""
    return re.compile(r"(?<![a-z])" + re.escape(kw) + right)


_CATEGORY_RX = [(cat, [_kw_regex(k) for k in kws]) for cat, kws in CATEGORY_RULES]
_CATEGORY_RX_EXTRA = {"PROPERTY": [_kw_regex(k) for k in ("leased", "leasing", "lands")]}


def classify_filing(subject: str, category: str = "") -> str:
    t = f" {subject} {category} ".lower()
    for cat, rxs in _CATEGORY_RX:
        if any(rx.search(t) for rx in rxs + _CATEGORY_RX_EXTRA.get(cat, [])):
            return cat
    return "OTHER"


# ── 2. CRE evidence sentences ────────────────────────────────────────────────
_AREA_ADJ = r"(?:(?:rentable|usable|leasable|gross|net|carpet|built[- ]up|super built[- ]up|chargeable|office)\s+)?"
SQFT_RE = re.compile(
    r"(\d[\d,]*(?:\.\d+)?)\s*(lakh|lac|million|mn)?\s*" + _AREA_ADJ +
    r"(?:sq\.?\s*ft\.?|sqft|sq\.?\s*feet|square\s*feet|square\s*foot|sft)(?![a-z])",
    re.I)
SQM_RE = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(?:sq\.?\s*m(?:etres?|eters?)?\b|square\s*met(?:re|er)s?)", re.I)
ACRE_RE = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*acres?\b", re.I)

STRONG_CRE = [
    "lease deed", "lease agreement", "leave and license", "leave & license", "leave and licence",
    "office space", "office premises", "commercial premises", "corporate office", "new office",
    "registered office", "headquarters", "campus", "tech park", "it park", "sez",
    "land parcel", "acres", "warehouse", "logistics park", "fulfilment", "fulfillment",
    "data centre", "data center", "manufacturing facility", "new plant", "greenfield",
    "brownfield", "lessor", "lessee", "rental", "sub-lease", "sublease", "tower", "floors",
    "built-up area", "carpet area", "chargeable area", "leasable area", "square feet", "sq ft",
    "sq. ft", "sqft", "relocat", "shifting of", "capex", "capacity expansion", "co-working",
    "coworking", "flex space", "global capability", "delivery centre", "development centre",
]
WEAK_ONLY = {"tower", "floors", "rental", "capex"}   # need a second term to count

ADDRESS_NOISE = re.compile(
    r"(\bcin\b|\bl\d{5}[a-z]{2}\d{4}|\bpin\s*:?\s*\d{6}|\b\d{6}\b.*\b(tel|phone|fax|email|e-mail)\b|"
    r"\btel\b\s*[:.]|\bfax\b|www\.|@[a-z0-9-]+\.|dalal street|phiroze jeejeebhoy|"
    r"exchange plaza|bandra kurla complex,\s*bandra\s*\(e\)|listing department|"
    r"scrip code|symbol\s*:)", re.I)

ADDRESS_LEAD = re.compile(r"^\W*(regd\.?|registered|corporate|head|admin\.?|works)\s*(office|off\.)\s*[:\-–]", re.I)
PIN_CODE = re.compile(r"(?<!\d)[1-9]\d{2}\s?\d{3}(?!\d)")
ACTION_VERB = re.compile(r"\b(approv|execut|enter|sign|acquir|lease[ds]?\b|leasing|shift|relocat|purchas|took|taken|"
                         r"allot|let out|license|licence|rent|occup|move|set up|setting up|open|inaugurat|commission)", re.I)

SENT_SPLIT = re.compile(r"(?<=[.;!?])\s+(?=[A-Z0-9(\"'])|\n{2,}|\n(?=\s*(?:\d+\.|[a-z]\)|\(|•|-)\s)")


def split_sentences(text: str) -> list[str]:
    text = re.sub(r"[ \t]+", " ", text or "")
    parts = SENT_SPLIT.split(text)
    out = []
    for p in parts:
        p = re.sub(r"\s*\n\s*", " ", p).strip()
        if 25 <= len(p) <= 900:
            out.append(p)
    return out


def cre_sentences(text: str, limit: int = 3) -> list[str]:
    """Return up to `limit` verbatim sentences from `text` that carry a CRE fact."""
    scored = []
    for i, s in enumerate(split_sentences(text)):
        low = s.lower()
        if ADDRESS_NOISE.search(low) or ADDRESS_LEAD.search(s):
            continue
        if PIN_CODE.search(s) and not ACTION_VERB.search(s):
            continue   # a bare postal address (letterhead / footer), not an event
        hits = [k for k in STRONG_CRE if k in low]
        has_area = bool(SQFT_RE.search(s) or SQM_RE.search(s) or ACRE_RE.search(s))
        strong = [h for h in hits if h not in WEAK_ONLY]
        if not strong and not has_area:
            continue
        if not strong and len(hits) < 2 and not has_area:
            continue
        score = len(hits) * 2 + (6 if has_area else 0) + (3 if re.search(r"\b(approv|execut|enter|sign|acquir|lease|let)\w*", low) else 0)
        scored.append((score, i, s))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [s for _, _, s in scored[:limit]]


def parse_area_sqft(text: str) -> Optional[int]:
    m = SQFT_RE.search(text or "")
    if m:
        try:
            v = float(m.group(1).replace(",", ""))
        except ValueError:
            return None
        mult = {"lakh": 100_000, "lac": 100_000, "million": 1_000_000, "mn": 1_000_000}.get((m.group(2) or "").lower(), 1)
        v *= mult
        return int(v) if 100 <= v <= 50_000_000 else None
    m = SQM_RE.search(text or "")
    if m:
        try:
            v = float(m.group(1).replace(",", "")) * 10.7639
        except ValueError:
            return None
        return int(v) if 100 <= v <= 50_000_000 else None
    return None


# ── 3. Dates (for board meeting intimations etc.) ────────────────────────────
MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"], 1)}
MONTHS.update({k[:3]: v for k, v in list(MONTHS.items())})
MONTHS["sept"] = 9

DATE_PATTERNS = [
    (re.compile(r"\b(\d{1,2})[-/.](\d{1,2})[-/.](20\d{2})\b"), "dmy"),
    (re.compile(r"\b(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})\b"), "ymd"),
    (re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(?:day\s+of\s+)?([A-Za-z]{3,9})[,.]?\s+(20\d{2})\b"), "dMy"),
    (re.compile(r"\b([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(20\d{2})\b"), "Mdy"),
    (re.compile(r"\b(\d{1,2})[-\s]([A-Za-z]{3})[-\s](20\d{2})\b"), "dMy"),
]


def find_dates(text: str) -> list[date]:
    out = []
    for rx, kind in DATE_PATTERNS:
        for m in rx.finditer(text or ""):
            try:
                if kind == "dmy":
                    d = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
                elif kind == "ymd":
                    d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                elif kind == "dMy":
                    mon = MONTHS.get(m.group(2).lower())
                    if not mon:
                        continue
                    d = date(int(m.group(3)), mon, int(m.group(1)))
                else:
                    mon = MONTHS.get(m.group(1).lower().rstrip("."))
                    if not mon:
                        continue
                    d = date(int(m.group(3)), mon, int(m.group(2)))
                out.append((m.start(), d))
            except ValueError:
                continue
    out.sort()
    return [d for _, d in out]


def meeting_date(text: str, reference: Optional[date] = None) -> Optional[date]:
    """First date in text that is on/after the filing date (i.e. the scheduled meeting)."""
    ref = reference or date.today()
    for d in find_dates(text):
        if ref - timedelta(days=1) <= d <= ref + timedelta(days=120):
            return d
    return None


# ── 4. Purpose keywords that make a forward event CRE-relevant ───────────────
CRE_PURPOSE = [
    "fund", "raise", "qip", "preferential", "rights issue", "debenture", "acquisition", "acquire",
    "merger", "amalgamation", "subsidiary", "capex", "expansion", "property", "land", "lease",
    "premises", "office", "registered office", "real estate", "facility", "plant", "investment",
    "joint venture", "scheme of arrangement", "buyback", "divest", "slump sale", "sale of",
]


def purpose_is_cre(purpose: str) -> bool:
    p = (purpose or "").lower()
    return any(k in p for k in CRE_PURPOSE)


# ── 5. Signal type from evidence ─────────────────────────────────────────────
def signal_type_for(category: str, evidence: str) -> str:
    e = (evidence or "").lower()
    if category == "DISTRESS":
        return "DISTRESS"
    if category == "FUND_RAISE":
        return "FUNDING"
    if any(k in e for k in ["data centre", "data center", "hyperscale", "colocation"]):
        return "DATA CENTRE"
    if any(k in e for k in ["warehouse", "logistics park", "fulfilment", "fulfillment", "distribution centre"]):
        return "WAREHOUSE"
    if any(k in e for k in ["relocat", "shifting of registered office", "change in registered office",
                            "change of registered office", "shifting of"]):
        return "RELOCATE"
    if any(k in e for k in ["lease deed", "lease agreement", "leave and licen", "leave & licen", "lessee",
                            "lessor", "sub-lease", "sublease", "leased", "lease"]):
        return "LEASE"
    if category in ("INCORPORATION", "CAPEX", "ACQUISITION"):
        return "EXPAND"
    if any(k in e for k in ["office", "campus", "headquarters", "tech park", "it park"]):
        return "OFFICE"
    return "FILING"


def first_sentence(text: str, n: int = 240) -> str:
    s = split_sentences(text)
    return (s[0] if s else (text or ""))[:n]

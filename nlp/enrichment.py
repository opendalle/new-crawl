"""
Signal Enrichment — Nexus Asia CRE Intel
==========================================
Extracts structured data from signal text:
- Funding: amount (₹/$ normalized to Cr), round type (Series A/B/C etc.)
- Hiring: headcount numbers
- Sqft: area figures
- Timeline: urgency hints from dates/quarters

Used to enrich signals BEFORE saving to DB.
"""

import re
from typing import Optional

# ── FUNDING EXTRACTION ────────────────────────────────────────────────────────

ROUND_PATTERNS = [
    (r'\bpre[-\s]?seed\b', 'Pre-Seed'),
    (r'\bseed\s+round\b|\bseed\s+funding\b|\bseed\b', 'Seed'),
    (r'\bseries\s+a\b', 'Series A'),
    (r'\bseries\s+b\b', 'Series B'),
    (r'\bseries\s+c\b', 'Series C'),
    (r'\bseries\s+d\b', 'Series D'),
    (r'\bseries\s+e\b', 'Series E'),
    (r'\bpre[-\s]?ipo\b', 'Pre-IPO'),
    (r'\bbridge\s+round\b|\bbridge\s+funding\b', 'Bridge'),
    (r'\bgrowth\s+round\b|\bgrowth\s+equity\b', 'Growth'),
    (r'\bdebt\s+funding\b|\bdebt\s+round\b', 'Debt'),
    (r'\bventure\s+debt\b', 'Venture Debt'),
    (r'\bpe\s+round\b|\bprivate\s+equity\b', 'PE'),
    (r'\bipo\b', 'IPO'),
]

# Amount patterns — handles ₹, $, Cr, Mn, lakh, billion, million
AMOUNT_PATTERNS = [
    # ₹500 Cr / Rs 500 crore
    (r'(?:₹|rs\.?\s*|inr\s*)(\d+(?:\.\d+)?)\s*(?:cr(?:ore)?s?)', 'INR_CR'),
    # $500 million / USD 500 mn
    (r'(?:\$|usd\s*)(\d+(?:\.\d+)?)\s*(?:mn|million)', 'USD_MN'),
    # $1 billion
    (r'(?:\$|usd\s*)(\d+(?:\.\d+)?)\s*(?:bn|billion)', 'USD_BN'),
    # ₹500 million
    (r'(?:₹|rs\.?\s*|inr\s*)(\d+(?:\.\d+)?)\s*(?:mn|million)', 'INR_MN'),
    # 500 crore (no currency symbol)
    (r'(\d+(?:\.\d+)?)\s*(?:cr(?:ore)?s?)\b', 'INR_CR_BARE'),
    # raised ₹500 lakh
    (r'(?:₹|rs\.?\s*)(\d+(?:\.\d+)?)\s*(?:lakh|lac)', 'INR_LAKH'),
]

def extract_funding(text: str) -> dict:
    """Extract funding round and amount from text. Returns normalized amount in ₹Cr."""
    text_lower = text.lower()
    result = {"funding_round": None, "funding_amount": None, "funding_amount_cr": None}

    # Round type
    for pattern, label in ROUND_PATTERNS:
        if re.search(pattern, text_lower):
            result["funding_round"] = label
            break

    # Amount
    for pattern, unit in AMOUNT_PATTERNS:
        m = re.search(pattern, text_lower)
        if m:
            val = float(m.group(1))
            if unit == 'INR_CR' or unit == 'INR_CR_BARE':
                result["funding_amount"] = f"₹{val}Cr"
                result["funding_amount_cr"] = val
            elif unit == 'USD_MN':
                cr = val * 8.3  # approx USD→INR at 83
                result["funding_amount"] = f"${val}Mn (~₹{cr:.0f}Cr)"
                result["funding_amount_cr"] = cr
            elif unit == 'USD_BN':
                cr = val * 8300
                result["funding_amount"] = f"${val}Bn (~₹{cr:.0f}Cr)"
                result["funding_amount_cr"] = cr
            elif unit == 'INR_MN':
                cr = val / 10
                result["funding_amount"] = f"₹{val}Mn (~₹{cr:.1f}Cr)"
                result["funding_amount_cr"] = cr
            elif unit == 'INR_LAKH':
                cr = val / 100
                result["funding_amount"] = f"₹{val}L (~₹{cr:.2f}Cr)"
                result["funding_amount_cr"] = cr
            break

    return result


# ── HEADCOUNT EXTRACTION ──────────────────────────────────────────────────────

HEADCOUNT_PATTERNS = [
    r'hiring\s+(\d[\d,]+)\s+(?:employees?|people|staff|professionals?)',
    r'(\d[\d,]+)\s+(?:new\s+)?(?:jobs?|hires?|employees?|headcount)',
    r'expand(?:ing)?\s+(?:team|workforce|headcount)\s+(?:by\s+)?(\d[\d,]+)',
    r'(\d[\d,]+)\s+(?:new\s+)?positions?',
    r'add(?:ing)?\s+(\d[\d,]+)\s+(?:employees?|people|jobs?)',
    r'workforce\s+of\s+(\d[\d,]+)',
    r'(\d[\d,]+)\s+seats?\s+(?:in|at|across)',
    r'team\s+of\s+(\d[\d,]+)',
]

def extract_headcount(text: str) -> Optional[int]:
    """Extract headcount number from text."""
    text_lower = text.lower()
    for pattern in HEADCOUNT_PATTERNS:
        m = re.search(pattern, text_lower)
        if m:
            try:
                return int(m.group(1).replace(',', ''))
            except ValueError:
                pass
    return None


# ── SQFT EXTRACTION ───────────────────────────────────────────────────────────

SQFT_PATTERNS = [
    r'(\d[\d,]+)\s*(?:sq\.?\s*ft|sqft|square\s+feet)',
    r'(\d+(?:\.\d+)?)\s*(?:lakh|lac)\s*(?:sq\.?\s*ft|sqft|square\s+feet)',
    r'(\d+(?:\.\d+)?)\s*(?:mn|million)\s*(?:sq\.?\s*ft|sqft)',
    r'(\d[\d,]+)\s*(?:sf)\b',
]

def extract_sqft(text: str) -> Optional[int]:
    """Extract sqft figure from text, normalized to sqft."""
    text_lower = text.lower()
    for i, pattern in enumerate(SQFT_PATTERNS):
        m = re.search(pattern, text_lower)
        if m:
            try:
                val = float(m.group(1).replace(',', ''))
                if i == 1:  # lakh sqft
                    return int(val * 100000)
                elif i == 2:  # million sqft
                    return int(val * 1000000)
                return int(val)
            except ValueError:
                pass
    return None


# ── TIMELINE / URGENCY ────────────────────────────────────────────────────────

URGENCY_PATTERNS = [
    (r'\bq[1-4]\s*20[2-9]\d\b|\bq[1-4]\s*\'?[2-9]\d\b', 'HIGH'),
    (r'\bby\s+(?:end\s+of\s+)?(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\b', 'HIGH'),
    (r'\bimmediately\b|\bimminent\b|\burgent\b|\basap\b', 'CRITICAL'),
    (r'\bwithin\s+\d+\s+(?:days?|weeks?|months?)\b', 'HIGH'),
    (r'\bthis\s+(?:quarter|year|month)\b', 'HIGH'),
    (r'\bnext\s+(?:quarter|month)\b', 'HIGH'),
    (r'\b202[5-9]\b', 'MEDIUM'),
]

def extract_urgency_hint(text: str) -> Optional[str]:
    """Extract urgency level from timeline mentions in text."""
    text_lower = text.lower()
    for pattern, urgency in URGENCY_PATTERNS:
        if re.search(pattern, text_lower):
            return urgency
    return None


# ── MASTER ENRICHMENT ─────────────────────────────────────────────────────────

def enrich_signal(signal: dict, article: dict) -> dict:
    """
    Enrich a signal dict with extracted funding, headcount, sqft, urgency.
    Modifies signal in-place and returns it.
    """
    text = (article.get("title", "") + " " + article.get("text", "")).lower()
    sig_type = signal.get("signal_type", "")

    # Funding enrichment
    if sig_type in ("FUNDING", "EXPAND", "OFFICE"):
        funding = extract_funding(text)
        if funding["funding_round"]:
            signal["funding_round"] = funding["funding_round"]
        if funding["funding_amount"]:
            signal["funding_amount"] = funding["funding_amount"]
            # Boost confidence for large rounds
            cr = funding.get("funding_amount_cr", 0) or 0
            if cr >= 500:
                signal["confidence"] = min(signal.get("confidence", 70) + 10, 95)
                signal["urgency"] = "HIGH"
            elif cr >= 100:
                signal["confidence"] = min(signal.get("confidence", 70) + 5, 90)

    # Hiring enrichment
    if sig_type in ("HIRING", "EXPAND"):
        hc = extract_headcount(text)
        if hc:
            signal["headcount"] = hc
            # Boost confidence for large headcounts
            if hc >= 1000:
                signal["confidence"] = min(signal.get("confidence", 70) + 15, 95)
                signal["urgency"] = "HIGH"
            elif hc >= 500:
                signal["confidence"] = min(signal.get("confidence", 70) + 10, 90)
            elif hc >= 100:
                signal["confidence"] = min(signal.get("confidence", 70) + 5, 85)

    # Sqft enrichment
    sqft = extract_sqft(text)
    if sqft:
        signal["sqft"] = sqft
        if sqft >= 100000:
            signal["confidence"] = min(signal.get("confidence", 70) + 10, 95)
            signal["urgency"] = "HIGH"

    # Timeline urgency
    urgency_hint = extract_urgency_hint(text)
    if urgency_hint and signal.get("urgency", "MEDIUM") == "MEDIUM":
        signal["urgency"] = urgency_hint

    # Pass through published_at from article
    if article.get("published_at"):
        signal["published_at"] = article["published_at"]

    return signal

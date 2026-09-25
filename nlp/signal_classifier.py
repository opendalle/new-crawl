"""
Signal Classifier v2
====================
Fixes the core problem: old classifier matched ANY single keyword,
letting oil markets, Bollywood, banking through.

New logic:
- Requires COMBO of signal verb + signal noun (not just one keyword)
- Maps signals to proper types (not just first keyword hit)
- Extracts meaningful summary sentences, not raw text slice
- Scores confidence based on signal strength, not just count
"""

import re

# ── SIGNAL COMBOS (verb + noun required for match) ────────────────────────────

# Each entry: (verb_patterns, noun_patterns, signal_type, base_score)
SIGNAL_COMBOS = [
    # LEASE — strongest signal, specific transaction
    (
        ["leased", "signed lease", "lease signed", "took up", "taken up",
         "rented", "rental agreement", "mou signed", "loi signed",
         "letter of intent", "signed a deal", "inked a deal"],
        ["office", "office space", "sq ft", "sqft", "square feet", "floor",
         "campus", "facility", "workspace", "co-working", "coworking",
         "commercial space", "premises", "tower", "block", "wing"],
        "LEASE", 80
    ),
    # OFFICE — new office opening/setup
    (
        ["opened", "opening", "inaugurated", "launched", "set up", "setting up",
         "new office", "new campus", "new hq", "new headquarters",
         "new facility", "new centre", "new center", "commissioned"],
        ["office", "campus", "headquarters", "hq", "facility", "centre",
         "center", "tech park", "it park", "workspace", "co-working"],
        "OFFICE", 75
    ),
    # RELOCATE — company moving
    (
        ["relocated", "relocating", "relocation", "moved to", "moving to",
         "shifted to", "shifting to", "new address", "new location",
         "changed office", "new premises"],
        ["office", "campus", "hq", "headquarters", "facility", "premises",
         "workspace", "location"],
        "RELOCATE", 70
    ),
    # EXPAND — growth signal
    (
        ["expanding", "expansion", "expand", "scaling up", "additional space",
         "more space", "extra space", "doubling", "tripling",
         "new wing", "annex", "additional floor"],
        ["office", "campus", "facility", "space", "sq ft", "sqft",
         "square feet", "seats", "workstation", "premises"],
        "EXPAND", 65
    ),
    # HIRING — proxy demand signal (only with location + scale)
    (
        ["hiring", "recruiting", "recruitment", "headcount", "workforce",
         "employees", "team size", "adding", "onboarding"],
        ["bengaluru", "mumbai", "hyderabad", "pune", "delhi", "ncr",
         "gurugram", "noida", "chennai", "india"],  # must name city
        "HIRING", 40
    ),
    # FUNDING — proxy (company funded = may expand)
    (
        ["raised", "funding", "series a", "series b", "series c", "series d",
         "pre-series", "seed round", "investment", "backed by", "crore raised",
         "mn raised", "million raised", "unicorn"],
        ["startup", "company", "firm", "platform", "ventures",
         "technologies", "solutions", "india"],
        "FUNDING", 35
    ),
]

# ── NOISE BLOCKLIST ───────────────────────────────────────────────────────────
# If ANY of these dominate the text, reject immediately

HARD_NOISE = [
    # Entertainment
    r'\bsong\b', r'\balbum\b', r'\bfilm\b', r'\bmovie\b', r'\bactor\b',
    r'\bactress\b', r'\bbollywood\b', r'\blyric', r'\bviral video\b',
    r'\bcelebrity\b', r'\brappers?\b', r'\bsingers?\b',
    # Commodities / Oil
    r'\bcrude oil\b', r'\bpetroleum\b', r'\bopec\b', r'\bbrent\b',
    r'\boil price', r'\bnatural gas\b', r'\blpg crunch\b',
    r'\bstrait of hormuz\b', r'\boil market',
    # Politics
    r'\belection\b', r'\bvoting\b', r'\blok sabha\b', r'\brajya sabha\b',
    r'\bpolitical party\b', r'\bbjp\b', r'\bcongress party\b',
    # Financial markets (not corporate)
    r'\bsensex\b', r'\bnifty\b', r'\bshare price\b', r'\bstock market\b',
    r'\brepo rate\b', r'\bmonetary policy\b', r'\bcredit card\b',
    r'\binterest rate\b',
    # Sports
    r'\bcricket\b', r'\bipl\b', r'\bfifa\b', r'\bolympics\b',
    r'\btournament\b', r'\bsports team\b',
]

INDIA_LOCATIONS = [
    "mumbai", "navi mumbai", "thane", "pune", "delhi", "ncr", "gurugram",
    "gurgaon", "noida", "bengaluru", "bangalore", "hyderabad", "chennai",
    "kolkata", "ahmedabad", "surat", "jaipur", "lucknow", "india", "indian",
    "bkc", "lower parel", "andheri", "powai", "whitefield", "hsr layout",
    "koramangala", "cyberabad", "hitec city", "gachibowli", "bandra",
    "worli", "nariman point", "vikhroli", "goregaon", "malad", "belapur",
    "airoli", "bhiwandi", "electronic city", "sarjapur", "bellandur",
    "cybercity", "dlf", "unitech", "sec 62", "sec 44", "sec 135",
]


def _has_noise(text: str) -> bool:
    """Returns True if hard noise patterns dominate the text."""
    noise_hits = sum(1 for pat in HARD_NOISE if re.search(pat, text, re.IGNORECASE))
    return noise_hits >= 2


def _check_sqft(text: str) -> bool:
    """Check if specific sq ft measurement mentioned — very strong CRE signal."""
    return bool(re.search(
        r'\d[\d,]*\s*(?:sq\.?\s*ft|sqft|square\s*feet|lakh\s*sq|crore\s*sq)',
        text, re.IGNORECASE
    ))


def classify_signal(article: dict) -> dict | None:
    title = (article.get("title") or "").lower()
    summary = (article.get("summary") or "").lower()
    text_input = article.get("text", "")
    text = (title + " " + summary + " " + text_input[:2000]).lower()

    # ── Hard noise check ──────────────────────────────────────────────────────
    if _has_noise(text):
        return None

    # ── Sq ft shortcut — always a CRE signal ─────────────────────────────────
    sqft_hit = _check_sqft(text)
    location_hit = any(loc in text for loc in INDIA_LOCATIONS)

    # ── Try each signal combo ─────────────────────────────────────────────────
    best_match = None
    best_score = 0

    for verbs, nouns, sig_type, base_score in SIGNAL_COMBOS:
        verb_hits = [v for v in verbs if v in text]
        noun_hits = [n for n in nouns if n in text]

        if not verb_hits or not noun_hits:
            continue

        score = base_score
        score += len(verb_hits) * 5
        score += len(noun_hits) * 5
        if sqft_hit:
            score += 20
        if location_hit:
            score += 15

        score = min(score, 100)

        if score > best_score:
            best_score = score
            best_match = {
                "signal_type": sig_type,
                "confidence_score": score,
                "matched_phrases": verb_hits[:3] + noun_hits[:3],
                "location_hit": location_hit,
                "sqft_mentioned": sqft_hit,
            }

    # ── Fallback: sq ft alone is sufficient ──────────────────────────────────
    if not best_match and sqft_hit and location_hit:
        best_match = {
            "signal_type": "OFFICE",
            "confidence_score": 60,
            "matched_phrases": ["sq ft", "india location"],
            "location_hit": True,
            "sqft_mentioned": True,
        }

    return best_match


def extract_summary(text: str, matched_phrases: list) -> str:
    """
    Extract the most relevant 2-3 sentences from text.
    Prioritises sentences containing matched phrases or CRE keywords.
    """
    # Clean HTML artifacts
    clean = re.sub(r'<[^>]+>', ' ', text)
    clean = re.sub(r'\s+', ' ', clean).strip()

    # Split into sentences
    sentences = re.split(r'(?<=[.!?])\s+', clean)

    cre_priority = [
        "sq ft", "sqft", "square feet", "office", "campus", "lease",
        "facility", "expansion", "relocat", "headquarter", "hq",
        "workspace", "co-working", "coworking",
    ]

    # Score each sentence
    scored = []
    for sent in sentences:
        if len(sent) < 20:
            continue
        s = sent.lower()
        score = sum(1 for kw in cre_priority if kw in s)
        score += sum(2 for ph in matched_phrases if ph.lower() in s)
        scored.append((score, sent))

    # Sort by score, take top 3
    scored.sort(key=lambda x: x[0], reverse=True)
    top = [s for _, s in scored[:3] if _ > 0]

    if top:
        return " ".join(top)[:500]

    # Fallback: first 300 chars of clean text
    return clean[:300]

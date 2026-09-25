"""
CRE Intent Intelligence Layer v3 — Global
==========================================
Converts indirect signals into CRE leads.
Now handles global signals with India-impact detection.
"""

import re

FUNDING_ROUNDS = {
    "seed":         {"urgency": "LOW",    "confidence": 35, "reason": "Seed funding — first proper office needed in 6-12 months"},
    "pre-series a": {"urgency": "MEDIUM", "confidence": 45, "reason": "Pre-Series A — co-working likely insufficient soon"},
    "series a":     {"urgency": "MEDIUM", "confidence": 55, "reason": "Series A — 20-50 person team, dedicated office needed now"},
    "series b":     {"urgency": "HIGH",   "confidence": 70, "reason": "Series B — aggressive hiring, space requirement imminent"},
    "series c":     {"urgency": "HIGH",   "confidence": 75, "reason": "Series C — multi-city expansion, multiple office requirements"},
    "series d":     {"urgency": "HIGH",   "confidence": 80, "reason": "Series D+ — large campus or HQ upgrade in pipeline"},
    "pre-ipo":      {"urgency": "HIGH",   "confidence": 85, "reason": "Pre-IPO — prestigious address needed before listing"},
    "ipo":          {"urgency": "HIGH",   "confidence": 85, "reason": "IPO/listing — HQ upgrade and compliance space required"},
}

FUNDING_THRESHOLDS = [
    (500,  "HIGH",   85),
    (100,  "HIGH",   75),
    (50,   "HIGH",   65),
    (10,   "MEDIUM", 50),
]

INDIA_LOCATIONS = [
    "india", "bengaluru", "bangalore", "mumbai", "hyderabad", "pune",
    "delhi", "ncr", "gurugram", "gurgaon", "noida", "greater noida",
    "chennai", "kolkata", "ahmedabad", "surat", "jaipur", "lucknow",
    "chandigarh", "kochi", "coimbatore", "nagpur", "indore",
    "bkc", "lower parel", "andheri", "whitefield", "hitec city",
    "hinjewadi", "kharadi", "salt lake", "gift city", "aerocity",
    "pan-india", "pan india", "across india", "indian",
]

GLOBAL_LOCATIONS_MAP = {
    "USA":       ["new york", "manhattan", "san francisco", "silicon valley", "los angeles", "chicago", "seattle", "boston", "austin", "dallas", "miami"],
    "UK":        ["london", "canary wharf", "manchester", "birmingham", "edinburgh"],
    "Singapore": ["singapore", "raffles place", "marina bay"],
    "UAE":       ["dubai", "abu dhabi", "difc", "business bay", "sharjah"],
    "Japan":     ["tokyo", "osaka", "yokohama"],
    "Germany":   ["frankfurt", "berlin", "munich", "hamburg", "düsseldorf"],
    "Australia": ["sydney", "melbourne", "brisbane", "perth"],
    "HongKong":  ["hong kong", "central", "kowloon"],
    "China":     ["shanghai", "beijing", "shenzhen"],
    "France":    ["paris", "la defense"],
}

FOREIGN_ENTRY_SIGNALS = [
    "enters india", "entry into india", "entering india", "india entry",
    "launches in india", "launch in india", "india launch",
    "expands to india", "india expansion", "india operations",
    "india subsidiary", "india office", "india headquarters",
    "india presence", "forays into india", "india debut",
    "global capability centre", "gcc", "global delivery centre",
    "captive centre", "global in-house centre", "gic",
    "india unit", "india arm", "india entity",
    "sets up india", "setting up india",
]

HIGH_VALUE_FOREIGN = [
    "microsoft", "google", "amazon", "apple", "meta", "salesforce",
    "oracle", "sap", "adobe", "servicenow", "workday",
    "jpmorgan", "goldman", "morgan stanley", "blackrock", "kkr", "carlyle",
    "mckinsey", "bcg", "bain", "deloitte", "pwc", "ey", "kpmg", "accenture",
    "airbus", "boeing", "siemens", "bosch", "schneider", "abb",
    "walmart", "target", "ikea", "zara", "h&m",
    "nvidia", "intel", "amd", "qualcomm", "arm",
    "tesla", "bmw", "mercedes", "volkswagen", "toyota",
]

GROWTH_SIGNALS = {
    "unicorn":      {"confidence": 75, "reason": "Unicorn milestone — HQ upgrade to premium address almost certain", "urgency": "HIGH"},
    "ipo":          {"confidence": 80, "reason": "IPO filing — boardroom, compliance space, investor relations office needed", "urgency": "HIGH"},
    "acqui":        {"confidence": 65, "reason": "M&A activity — office consolidation or new combined HQ likely in 6-18 months", "urgency": "MEDIUM"},
    "merger":       {"confidence": 65, "reason": "Merger — combined entity will rationalize or upgrade office portfolio", "urgency": "MEDIUM"},
    "headcount":    {"confidence": 55, "reason": "Significant headcount growth — current space likely insufficient", "urgency": "MEDIUM"},
    "data center":  {"confidence": 70, "reason": "Data centre expansion — support office space also required", "urgency": "MEDIUM"},
    "data centre":  {"confidence": 70, "reason": "Data centre expansion — support office space also required", "urgency": "MEDIUM"},
    "manufacturing":{"confidence": 60, "reason": "Manufacturing facility — admin/office component needed", "urgency": "MEDIUM"},
    "warehouse":    {"confidence": 55, "reason": "Logistics/warehouse expansion — operations office required", "urgency": "MEDIUM"},
}


def parse_funding_amount_cr(text: str) -> float:
    text = text.lower()
    patterns = [
        (r'(?:rs\.?|inr|₹)\s*([\d,]+(?:\.\d+)?)\s*crore', 1.0),
        (r'(?:rs\.?|inr|₹)\s*([\d,]+(?:\.\d+)?)\s*cr\b', 1.0),
        (r'(?:rs\.?|inr|₹)\s*([\d,]+(?:\.\d+)?)\s*lakh', 0.01),
        (r'\$\s*([\d,]+(?:\.\d+)?)\s*million', 8.3),
        (r'\$\s*([\d,]+(?:\.\d+)?)\s*mn', 8.3),
        (r'([\d,]+(?:\.\d+)?)\s*million\s*(?:dollar|usd)', 8.3),
        (r'\$\s*([\d,]+(?:\.\d+)?)\s*billion', 8300.0),
        (r'([\d,]+(?:\.\d+)?)\s*crore', 1.0),
    ]
    for pattern, multiplier in patterns:
        m = re.search(pattern, text)
        if m:
            try:
                return float(m.group(1).replace(",", "")) * multiplier
            except ValueError:
                pass
    return 0.0


def detect_country(text: str) -> str:
    """Detect primary country of signal."""
    t = text.lower()
    # Check India first (our primary market)
    if any(loc in t for loc in INDIA_LOCATIONS):
        return "India"
    # Check global
    for country, locs in GLOBAL_LOCATIONS_MAP.items():
        if any(loc in t for loc in locs):
            return country
    return "India"


def detect_region(text: str, source_region: str = "India") -> str:
    """Detect region category for filtering."""
    t = text.lower()
    india_kw = ["india", "gcc", "global capability centre", "captive centre",
                "india entry", "india expansion", "india office", "india hq"]
    if any(kw in t for kw in india_kw):
        # Check if it's a global company expanding to India
        if any(co in t for co in HIGH_VALUE_FOREIGN):
            return "India_Impact"
        for country, locs in GLOBAL_LOCATIONS_MAP.items():
            if any(loc in t for loc in locs) and any(loc in t for loc in INDIA_LOCATIONS):
                return "India_Impact"
        return "India"
    if source_region and source_region not in ["India", "India_Impact"]:
        return source_region
    return "India"


def analyze_funding_intent(title: str, text: str, location: str) -> dict | None:
    combined = (title + " " + text).lower()

    funding_triggers = ["raised", "raises", "funding", "series", "investment",
                        "backed", "seed round", "pre-series", "ipo", "listing"]
    if not any(t in combined for t in funding_triggers):
        return None

    # Must mention India OR be a notable global funding
    india_hit  = any(loc in combined for loc in INDIA_LOCATIONS)
    global_hit = any(loc in combined for locs in GLOBAL_LOCATIONS_MAP.values() for loc in locs)
    if not india_hit and not global_hit:
        return None

    round_info = {"urgency": "MEDIUM", "confidence": 40,
                  "reason": "Company received funding — will expand team and require office space"}
    for round_name, info in FUNDING_ROUNDS.items():
        if round_name in combined:
            round_info = info
            break

    amount_cr = parse_funding_amount_cr(combined)
    if amount_cr > 0:
        for threshold, urgency, conf in FUNDING_THRESHOLDS:
            if amount_cr >= threshold:
                round_info["urgency"]     = urgency
                round_info["confidence"]  = max(round_info["confidence"], conf)
                round_info["reason"]     += f" (₹{amount_cr:.0f}Cr raised)"
                break

    return {
        "signal_type":      "FUNDING",
        "confidence_score": round_info["confidence"],
        "why_cre":          round_info["reason"],
        "urgency":          round_info["urgency"],
        "country":          detect_country(combined),
        "suggested_action": "Contact within 2 weeks — present co-working for immediate need, long-term lease for 12-18 months",
    }


def analyze_foreign_entry_intent(title: str, text: str) -> dict | None:
    combined = (title + " " + text).lower()

    if not any(sig in combined for sig in FOREIGN_ENTRY_SIGNALS):
        return None

    is_high_value = any(co in combined for co in HIGH_VALUE_FOREIGN)
    is_gcc = any(gcc in combined for gcc in [
        "gcc", "global capability centre", "global delivery centre",
        "captive centre", "global in-house centre", "gic"
    ])

    if is_gcc:
        return {
            "signal_type":      "EXPAND",
            "confidence_score": 92,
            "why_cre":          "GCC/Captive Centre setup — typically 500-5000 seats, Grade A office in metro required immediately",
            "urgency":          "HIGH",
            "country":          detect_country(combined),
            "suggested_action": "Priority outreach — GCC setups need 50,000-500,000 sqft. Present pre-committed space options in BKC/Whitefield/HITEC.",
        }
    if is_high_value:
        return {
            "signal_type":      "EXPAND",
            "confidence_score": 82,
            "why_cre":          "Major global brand entering India — flagship India HQ in Grade A building required",
            "urgency":          "HIGH",
            "country":          detect_country(combined),
            "suggested_action": "Priority outreach — present premium Grade A options in BKC/Lower Parel/Whitefield/HITEC City",
        }
    return {
        "signal_type":      "EXPAND",
        "confidence_score": 65,
        "why_cre":          "Foreign company entering India — first office requirement guaranteed",
        "urgency":          "MEDIUM",
        "country":          detect_country(combined),
        "suggested_action": "Contact within 1 week — present managed office as first step, then long-term lease",
    }


def analyze_growth_intent(title: str, text: str) -> dict | None:
    combined = (title + " " + text).lower()

    # Check India OR global locations
    india_hit  = any(loc in combined for loc in INDIA_LOCATIONS)
    global_hit = any(loc in combined for locs in GLOBAL_LOCATIONS_MAP.values() for loc in locs)
    if not india_hit and not global_hit:
        return None

    for keyword, info in GROWTH_SIGNALS.items():
        if keyword in combined:
            return {
                "signal_type":      "EXPAND",
                "confidence_score": info["confidence"],
                "why_cre":          info["reason"],
                "urgency":          info["urgency"],
                "country":          detect_country(combined),
            }
    return None


def analyze_cre_intent(title: str, text: str, location: str = "India") -> dict | None:
    candidates = []

    intent = analyze_foreign_entry_intent(title, text)
    if intent:
        candidates.append(intent)

    intent = analyze_funding_intent(title, text, location)
    if intent:
        candidates.append(intent)

    intent = analyze_growth_intent(title, text)
    if intent:
        candidates.append(intent)

    if not candidates:
        return None
    return max(candidates, key=lambda x: x["confidence_score"])

"""
CRE Relevance Filter v3 — Global
=================================
Handles India (primary) + global CRE signals.
India signals: full location + keyword check
Global signals: looser filter, relies more on intent layer
"""

import re

SPACE_VERBS = [
    "leased", "leasing", "lease", "signed lease", "signed a lease",
    "rented", "renting", "took up", "taken up",
    "moved to", "moving to", "relocated", "relocating", "relocation",
    "shifted", "shifting",
    "opened", "opening", "launched", "launch", "launches",
    "set up", "sets up", "setting up",
    "unveiled", "unveils", "unveil",
    "inaugurated", "commissioned",
    "acquired", "acquiring", "purchased", "buying",
    "expanding", "expansion", "expand", "scaled up", "scaling up",
    "new office", "new facility", "new campus", "new hq", "new headquarters",
    "additional space", "more space", "extra space",
    "enters india", "entering india", "entry into india",
    "sq ft", "sqft", "sq. ft", "square feet", "square foot",
    "lakh sq", "crore sq",
    "to open", "will open", "plans to open", "to set up", "to launch",
    "breaks ground", "groundbreaking", "foundation stone",
    "operationalise", "operationalize",
    "pre-leased", "pre-lease", "pre leased",
    "signed mou", "mou signed", "loi signed",
    "inked deal", "signed deal", "closed deal",
]

SPACE_NOUNS = [
    "office", "office space", "office park", "office building", "office tower",
    "workspace", "co-working", "coworking", "co-location", "colocation",
    "flex space", "managed office", "serviced office",
    "campus", "headquarters", "hq", "corporate office", "global hq",
    "facility", "centre", "center", "development centre", "delivery centre",
    "global capability centre", "gcc", "captive centre", "global delivery centre",
    "global in-house centre", "gic",
    "tech park", "it park", "sez", "special economic zone",
    "commercial space", "commercial property", "commercial real estate",
    "floor", "tower", "block", "wing",
    "seat", "seats", "workstation", "workstations",
    "sqft", "sq ft", "square feet", "square foot",
    "data centre", "data center", "hyperscale",
    "warehouse", "logistics park", "logistics hub",
    "distribution centre", "fulfilment centre", "fulfillment center",
    "r&d centre", "innovation hub", "technology centre",
    "industrial park", "manufacturing facility", "plant",
]

GLOBAL_LOCATIONS = [
    # India
    "bengaluru", "bangalore", "mumbai", "delhi", "ncr", "gurugram", "gurgaon",
    "noida", "hyderabad", "pune", "chennai", "kolkata", "ahmedabad",
    "navi mumbai", "thane", "bkc", "worli", "lower parel", "andheri",
    "whitefield", "electronic city", "koramangala", "bandra",
    "cyberabad", "hitec city", "gachibowli", "hinjewadi", "kharadi",
    "salt lake", "rajarhat", "gift city", "aerocity",
    "india", "pan-india", "pan india", "across india",
    # USA
    "new york", "manhattan", "san francisco", "silicon valley", "los angeles",
    "chicago", "boston", "seattle", "austin", "miami", "dallas",
    # UK
    "london", "canary wharf", "city of london", "manchester", "birmingham",
    # Singapore
    "singapore", "raffles place", "marina bay",
    # UAE
    "dubai", "abu dhabi", "difc", "business bay",
    # Japan
    "tokyo", "osaka", "yokohama",
    # Germany
    "frankfurt", "berlin", "munich", "hamburg",
    # Australia
    "sydney", "melbourne", "brisbane",
    # Other
    "hong kong", "shanghai", "beijing", "paris", "amsterdam",
    "toronto", "vancouver", "riyadh", "doha",
]

NOISE_TOPICS = {
    "entertainment": [
        "song", "album", "movie", "film", "actor", "actress", "bollywood",
        "music", "singer", "rapper", "lyric", "viral video", "celebrity",
        "instagram", "reel", "controversy",
    ],
    "commodities": [
        "crude oil", "oil prices", "petroleum", "natural gas", "brent",
        "opec", "fuel prices", "lpg crunch", "gold price", "silver price",
    ],
    "politics": [
        "election", "parliament", "lok sabha", "rajya sabha",
        "bjp", "congress party", "political party", "vote",
        "chief minister", "prime minister",
    ],
    "banking_retail": [
        "credit card", "emi", "rbi policy", "repo rate", "monetary policy",
        "stock market", "nifty", "sensex", "share price",
    ],
    "sports": [
        "cricket", "ipl", "fifa", "olympics", "tournament", "sports team",
    ],
}


def _score_text(text: str, terms: list) -> int:
    t = text.lower()
    return sum(1 for term in terms if term in t)


def is_cre_relevant(title: str, text: str) -> tuple[bool, float, str]:
    combined = (title + " " + text).lower()

    verb_score = _score_text(combined, SPACE_VERBS)
    noun_score = _score_text(combined, SPACE_NOUNS)
    cre_score  = verb_score + noun_score

    if cre_score == 0:
        strong_intents = [
            "gcc", "global capability centre", "global delivery centre",
            "captive centre", "co-location campus", "global in-house centre",
            "india entry", "enters india", "india headquarters",
            "data centre india", "data center india",
            "hyperscale", "pre-leased", "pre-lease",
        ]
        if any(phrase in combined for phrase in strong_intents):
            cre_score = 3
        else:
            return False, 0.0, "no_cre_terms"

    sqft_match = re.search(
        r'\d[\d,]*\s*(?:sq\.?\s*ft|sqft|square\s*feet|lakh\s*sq|million\s*sq)',
        combined
    )
    if sqft_match:
        cre_score += 5

    # Noise check
    total_noise    = 0
    dominant_noise = None
    for topic, terms in NOISE_TOPICS.items():
        n = _score_text(combined, terms)
        if n > total_noise:
            total_noise    = n
            dominant_noise = topic
    if total_noise > 0 and cre_score < total_noise:
        return False, 0.0, f"noise_dominant:{dominant_noise}"

    # Location check — global now, not just India
    location_hit = any(loc in combined for loc in GLOBAL_LOCATIONS)
    if not location_hit and cre_score < 3:
        return False, 0.1, "no_india_location"

    base_confidence = min(cre_score / 8.0, 1.0)
    if verb_score > 0 and noun_score > 0:
        base_confidence = min(base_confidence + 0.2, 1.0)
    if sqft_match:
        base_confidence = min(base_confidence + 0.3, 1.0)
    if location_hit:
        base_confidence = min(base_confidence + 0.1, 1.0)

    return True, round(base_confidence, 2), "cre_relevant"


def get_signal_type(title: str, text: str) -> str:
    combined = (title + " " + text).lower()

    if re.search(r'\d[\d,]*\s*(?:sq\.?\s*ft|sqft|square\s*feet)', combined):
        if any(w in combined for w in ["leased", "signed", "took up", "rented", "pre-leased"]):
            return "LEASE"
        return "OFFICE"

    if any(w in combined for w in ["pre-leased", "pre-lease", "mou signed", "loi signed"]):
        return "LEASE"
    if any(w in combined for w in ["new office", "new campus", "inaugurated", "set up office"]):
        return "OFFICE"
    if any(w in combined for w in ["leased", "lease", "rented", "rental agreement"]):
        return "LEASE"
    if any(w in combined for w in ["relocat", "shifted", "moving to", "moved to"]):
        return "RELOCATE"
    if any(w in combined for w in ["warehouse", "logistics", "distribution centre", "fulfilment"]):
        return "WAREHOUSE"
    if any(w in combined for w in ["data centre", "data center", "hyperscale"]):
        return "DATACENTER"
    if any(w in combined for w in ["expand", "expansion", "scaling", "additional space"]):
        return "EXPAND"
    if any(w in combined for w in ["raised", "funding", "series", "investment", "backed"]):
        return "FUNDING"
    if any(w in combined for w in ["hiring", "headcount", "employees", "recruit", "jobs"]):
        return "HIRING"

    return "OFFICE"

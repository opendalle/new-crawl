"""
Entity Extractor v2
====================
Upgrades from v1:
- Pan-India locations (not just Mumbai)
- Filters out false-positive "companies" (news outlets, govt bodies)
- Extracts sq ft figures as structured data
- Detects company + city pairs for better signal attribution
"""

import re

# spaCy is optional (it is ~50 MB with the model). If it isn't installed the
# extractor falls back to a headline pattern matcher — see title_company().
_NLP = None
_NLP_TRIED = False


def _get_nlp():
    global _NLP, _NLP_TRIED
    if not _NLP_TRIED:
        _NLP_TRIED = True
        try:
            import spacy
            _NLP = spacy.load("en_core_web_sm")
        except Exception as e:  # ImportError or model missing
            print(f"[Entities] spaCy unavailable ({type(e).__name__}) — using headline patterns only")
            _NLP = None
    return _NLP


# "Acme Corp leases 2 lakh sq ft…", "Acme to set up GCC…", "Acme's India arm opens…"
_HEADLINE_VERBS = (
    r"leases?|leased|signs?|signed|inks?|takes?|took|to|opens?|opened|plans?|acquires?|acquired|"
    r"buys?|bought|raises?|raised|expands?|expanded|sets up|launches?|launched|moves?|moved|"
    r"relocates?|relocated|bags?|secures?|secured|announces?|announced|files?|filed|hires?|"
    r"picks?|unveils?|unveiled|inaugurates?|inaugurated|enters?|entered|invests?|invested|"
    r"completes?|completed|sells?|sold|lays off|cuts|shuts|vacates?|gets|receives?|wins?|forays"
)
_TITLE_RX = re.compile(
    r"^(?:[A-Z][\w.&'\-]*\s+){0,1}?((?:[A-Z0-9][\w.&'\-]*)(?:\s+(?:[A-Z0-9][\w.&'\-]*|&|of|and|de)){0,5})"
    r"(?:'s?|’s?)?(?:\s+(?:India|Group|arm|unit))?\s+(?:" + _HEADLINE_VERBS + r")\b")
_BAD_LEADS = {"india", "indian", "govt", "government", "centre", "center", "state", "sebi", "rbi",
              "exclusive", "breaking", "update", "report", "reports", "watch", "explained", "why",
              "how", "what", "when", "where", "who", "top", "new", "big", "this", "these", "the",
              "global", "us", "uk", "mumbai", "bengaluru", "delhi", "hyderabad", "pune", "chennai"}


def title_company(title: str) -> str | None:
    """Company named at the start of a headline, or None. Conservative on purpose."""
    t = (title or "").strip().strip('"“”')
    t = re.sub(r"^(exclusive|breaking|update|watch)\s*[:\-–|]\s*", "", t, flags=re.I)
    m = _TITLE_RX.match(t)
    if not m:
        return None
    name = m.group(1).strip(" ,.-")
    name = re.sub(r"(\s+(India|arm|unit|Inc))+$", "", name).strip()
    if not name:
        return None
    if name.lower() in _BAD_LEADS or name.split()[0].lower() in _BAD_LEADS:
        return None
    if len(name) < 3 or len(name) > 60 or name.isdigit():
        return None
    return name

# ── LOCATION DATA ─────────────────────────────────────────────────────────────

ALL_INDIA_CRE_LOCATIONS = [
    # Mumbai metro
    "Mumbai", "Navi Mumbai", "BKC", "Bandra Kurla Complex",
    "Lower Parel", "Andheri", "Powai", "Thane", "Belapur",
    "Airoli", "Bhiwandi", "Worli", "Nariman Point", "Vikhroli",
    "Goregaon", "Malad", "Bandra", "Kurla", "Kanjurmarg",
    # Bengaluru
    "Bengaluru", "Bangalore", "Whitefield", "Electronic City",
    "Koramangala", "HSR Layout", "Sarjapur", "Bellandur",
    "Marathahalli", "Hebbal", "Manyata", "Devanahalli",
    # Hyderabad
    "Hyderabad", "Cyberabad", "HITEC City", "Gachibowli",
    "Madhapur", "Kondapur", "Financial District", "Nanakramguda",
    # Delhi NCR
    "Delhi", "NCR", "Gurugram", "Gurgaon", "Noida", "Greater Noida",
    "Faridabad", "Ghaziabad", "Aerocity", "Connaught Place",
    "Nehru Place", "Okhla", "Jasola", "Dwarka", "Rohini",
    "Cyber City", "DLF", "Unitech", "IFFCO Chowk",
    # Pune
    "Pune", "Hinjewadi", "Kharadi", "Viman Nagar", "Baner",
    "Wakad", "Pimpri", "Chinchwad", "Magarpatta", "Hadapsar",
    # Chennai
    "Chennai", "OMR", "Old Mahabalipuram Road", "Guindy",
    "Perungudi", "Taramani", "Sholinganallur", "Anna Salai",
    # Kolkata
    "Kolkata", "Salt Lake", "Rajarhat", "New Town", "Park Street",
    # Other tier-1
    "Ahmedabad", "GIFT City", "Surat", "Jaipur", "Lucknow",
    "Chandigarh", "Mohali", "Kochi", "Thiruvananthapuram",
    "Coimbatore", "Nagpur", "Indore",
    # Generic
    "India", "Pan-India", "Pan India",
]

LOCATION_SET = {loc.lower() for loc in ALL_INDIA_CRE_LOCATIONS}

# ── NOISE COMPANY FILTERS ─────────────────────────────────────────────────────
# spaCy often tags news outlets, govt bodies as ORG — filter them

NOISE_ORGS = {
    # News media
    "times of india", "economic times", "hindustan times", "the hindu",
    "business standard", "livemint", "mint", "ndtv", "cnbc", "bloomberg",
    "reuters", "pti", "ani", "inc42", "yourstory", "entrackr",
    "moneycontrol", "financial express", "business today",
    # Govt / regulatory
    "rbi", "sebi", "mca", "bse", "nse", "rera", "mcgm", "bbmp",
    "government of india", "ministry", "supreme court", "high court",
    "income tax", "gst council",
    # Generic words that spaCy falsely tags
    "india", "indian", "company", "startup", "firm", "group",
    "the company", "sources", "analysts", "experts",
}

# ── KNOWN CRE-ACTIVE COMPANIES ────────────────────────────────────────────────
# Boost confidence if these are detected

KNOWN_CRE_COMPANIES = {
    # IT/Tech (biggest space consumers)
    "infosys", "tcs", "wipro", "hcl", "tech mahindra", "ltimindtree",
    "cognizant", "accenture", "ibm", "capgemini", "mphasis",
    # GCCs
    "jpmorgan", "goldman sachs", "morgan stanley", "citi", "hsbc",
    "barclays", "deutsche bank", "ubs", "wells fargo",
    "american express", "mastercard", "visa",
    # E-commerce / consumer
    "amazon", "flipkart", "meesho", "zepto", "swiggy", "zomato",
    "bigbasket", "blinkit", "dunzo", "ola", "rapido", "porter",
    # Fintech
    "razorpay", "paytm", "phonepe", "cred", "bharatpe", "groww",
    "zerodha", "angelone", "upstox",
    # Flex space operators (always expanding)
    "wework", "indiqube", "awfis", "table space", "bhive",
    "cowrks", "smartworks", "91springboard", "innov8",
    # Others
    "byju", "unacademy", "upgrd", "cars24", "ola electric",
    "ather", "pure ev", "delhivery", "ecom express", "xpressbees",
}


def extract_entities(text: str) -> dict:
    """
    Extract companies, locations, and CRE-specific data from text.
    Returns structured dict with cleaned, filtered entities.
    """
    nlp = _get_nlp()
    doc = nlp(text[:50000]) if nlp else None

    # ── Companies ─────────────────────────────────────────────────────────────
    raw_companies = [ent.text.strip() for ent in doc.ents if ent.label_ == "ORG"] if doc else []

    companies = []
    known_hits = []
    for co in raw_companies:
        if len(co) < 3 or len(co) > 60:
            continue
        if co.lower() in NOISE_ORGS:
            continue
        # Filter out numbers-only or punctuation-heavy
        if re.match(r'^[\d\s\W]+$', co):
            continue
        if co not in companies:
            companies.append(co)
        if co.lower() in KNOWN_CRE_COMPANIES:
            known_hits.append(co)

    # ── Locations ─────────────────────────────────────────────────────────────
    raw_locations = [
        ent.text.strip() for ent in doc.ents
        if ent.label_ in ("GPE", "LOC", "FAC")
    ] if doc else []

    # Also check against our known CRE location list
    text_lower = text.lower()
    matched_locations = []
    for loc in ALL_INDIA_CRE_LOCATIONS:
        if loc.lower() in text_lower:
            matched_locations.append(loc)

    # Merge spaCy locations + our list, deduplicate
    all_locations = list({
        loc for loc in (raw_locations + matched_locations)
        if loc.lower() in LOCATION_SET or loc in matched_locations
    })

    # Sort by specificity (longer = more specific)
    all_locations.sort(key=len, reverse=True)

    # ── Sq Ft extraction ──────────────────────────────────────────────────────
    sqft_matches = re.findall(
        r'([\d,]+)\s*(?:sq\.?\s*ft|sqft|square\s*feet)',
        text, re.IGNORECASE
    )
    sqft_values = []
    for m in sqft_matches:
        try:
            val = int(m.replace(",", ""))
            if val > 100:  # Filter noise (e.g. "5 sq ft" bathroom)
                sqft_values.append(val)
        except ValueError:
            pass

    # ── Funding amount extraction ─────────────────────────────────────────────
    funding_matches = re.findall(
        r'(?:rs\.?|inr|₹|usd|\$)?\s*([\d,]+(?:\.\d+)?)\s*(?:crore|cr|lakh|mn|million|billion|bn)',
        text, re.IGNORECASE
    )

    return {
        "companies": companies[:5],           # Top 5 companies
        "locations": all_locations[:3],        # Top 3 locations (most specific first)
        "known_cre_companies": known_hits,     # Verified CRE-active companies
        "sqft_values": sqft_values[:3],        # Extracted sq ft numbers
        "funding_amounts": funding_matches[:2], # Extracted funding amounts
        "india_hit": bool(all_locations),      # True if any India location found
    }

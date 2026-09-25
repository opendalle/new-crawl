"""
Lead Scorer v3 — With Distress + Tier 1 Intel
"""

SIGNAL_WEIGHTS = {
    # Direct transaction — hottest
    "LEASE":        90,
    "RELOCATE":     75,
    "OFFICE":       65,
    # Growth signals
    "EXPAND":       60,
    "DATACENTER":   58,
    "WAREHOUSE":    52,
    # Distress — high urgency, time-sensitive
    "DISTRESS":     80,
    # Proxy demand
    "FUNDING":      45,
    "HIRING":       38,
    # Filing signals
    "FILING":       42,
    "DATA CENTRE":  58,
    # Legacy
    "OFFICE_EXPANSION":    60,
    "WAREHOUSE_DEMAND":    52,
    "DATACENTER_BUILD":    58,
    "LAND_ACQUISITION":    45,
    "LOGISTICS_EXPANSION": 52,
    "CAPITAL_DEPLOYMENT":  30,
    "NO_SIGNAL":            0,
}

URGENCY_MULTIPLIERS = {"CRITICAL": 1.7, "HIGH": 1.5, "MEDIUM": 1.0, "LOW": 0.7}

PRIORITY_THRESHOLDS = {"HIGH": 80, "MEDIUM": 35, "LOW": 0}


def _multi_signal_bonus(n: int) -> float:
    if n >= 5: return 1.4
    if n >= 3: return 1.25
    if n >= 2: return 1.1
    return 1.0


def _norm(raw: str) -> str:
    t = (raw or "NO_SIGNAL").upper().strip()
    aliases = {
        "OFFICE_EXPANSION":    "OFFICE",
        "WAREHOUSE_DEMAND":    "WAREHOUSE",
        "DATACENTER_BUILD":    "DATACENTER",
        "LOGISTICS_EXPANSION": "WAREHOUSE",
        "CAPITAL_DEPLOYMENT":  "FUNDING",
    }
    return aliases.get(t, t)


def compute_lead_score(signals: list) -> dict:
    if not signals:
        return {"score": 0, "signal_count": 0, "priority_level": "LOW"}

    total = 0.0
    top_signal = "NONE"
    top_w = 0
    has_distress = False

    for sig in signals:
        st   = _norm(sig.get("signal_type", "NO_SIGNAL"))
        base = SIGNAL_WEIGHTS.get(st, 0)
        urg  = (sig.get("urgency") or "MEDIUM").upper()
        raw_conf = sig.get("confidence", sig.get("confidence_score", 50))
        conf = min(max(float(raw_conf or 0), 0), 100) / 100.0
        mult = URGENCY_MULTIPLIERS.get(urg, 1.0)
        total += base * mult * conf
        if base > top_w:
            top_w = base
            top_signal = st
        if st == "DISTRESS":
            has_distress = True

    total *= _multi_signal_bonus(len(signals))
    # Distress bonus — time-sensitive, bump priority
    if has_distress:
        total *= 1.2
    total = round(total)

    priority = (
        "HIGH"   if total >= PRIORITY_THRESHOLDS["HIGH"]   else
        "MEDIUM" if total >= PRIORITY_THRESHOLDS["MEDIUM"] else
        "LOW"
    )

    return {
        "score":          total,
        "signal_count":   len(signals),
        "priority_level": priority,
    }

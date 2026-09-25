"""
Hiring intelligence from companies' own public job boards (the legal,
reliable replacement for scraping LinkedIn).

Supported public APIs (response shapes verified Sept 2026):
  greenhouse : https://boards-api.greenhouse.io/v1/boards/<board>/jobs
               jobs[].{id,title,absolute_url,location.name,first_published,updated_at}
  lever      : https://api.lever.co/v0/postings/<board>?mode=json
               [].{id,text,hostedUrl,createdAt,categories.{location,allLocations,team},country}
  ashby      : https://api.ashbyhq.com/posting-api/job-board/<board>
               jobs[].{id,title,jobUrl,location,secondaryLocations[].location,publishedAt,address}

How a "hiring surge" is computed (no guessing):
  every run stores a snapshot {total, count per location, facilities/RE roles};
  a surge = open roles in a focus location rose by ≥ min_delta AND ≥ min_ratio
  versus the previous snapshot. The first run only records a baseline.
  Facilities / workplace / real-estate roles are emitted as individual signals
  with the job title + location + URL as evidence.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from crawler.http_client import HttpClient
from crawler.items import make_item

OFFICE_ROLE_PATTERNS = [
    r"\bfacilit(y|ies)\b", r"\bworkplace\b", r"\breal estate\b", r"\bcorporate real estate\b",
    r"\bsite (lead|operations|manager)\b", r"\bcampus\b", r"\boffice manager\b",
    r"\bproperty manager\b", r"\bleasing\b", r"\bprojects? manager.*(fit[- ]?out|construction|interiors)\b",
    r"\bfit[- ]?out\b", r"\bdata cent(er|re) (construction|development|site)\b",
    r"\bcountry manager\b", r"\bgeneral manager,? india\b", r"\bhead of india\b", r"\bsite head\b",
]
_ROLE_RX = [re.compile(p, re.I) for p in OFFICE_ROLE_PATTERNS]


def _norm_loc(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


class ATSJobsCrawler:
    def __init__(self, boards: list[dict], focus_locations: list[str], store=None,
                 http: HttpClient | None = None, min_delta: int = 8, min_ratio: float = 1.3,
                 min_roles_for_surge: int = 10):
        self.boards = boards
        self.focus = [f.lower() for f in focus_locations]
        self.store = store
        self.http = http or HttpClient(extra_headers={"Accept": "application/json"})
        self.min_delta, self.min_ratio, self.min_roles = min_delta, min_ratio, min_roles_for_surge
        self.health = {"boards": 0, "boards_ok": 0, "jobs": 0}

    # ── fetchers → list of {title, url, locations[]} ────────────────────────
    def _greenhouse(self, board):
        data = self.http.get_json(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs")
        if not isinstance(data, dict):
            return None, ""
        jobs = [{"title": j.get("title", ""), "url": j.get("absolute_url", ""),
                 "locations": [_norm_loc((j.get("location") or {}).get("name", ""))],
                 "published": j.get("first_published") or j.get("updated_at")}
                for j in data.get("jobs", [])]
        return jobs, f"https://boards.greenhouse.io/{board}"

    def _lever(self, board):
        data = self.http.get_json(f"https://api.lever.co/v0/postings/{board}", params={"mode": "json"})
        if not isinstance(data, list):
            return None, ""
        jobs = []
        for j in data:
            cat = j.get("categories") or {}
            locs = cat.get("allLocations") or [cat.get("location", "")]
            ts = j.get("createdAt")
            jobs.append({"title": j.get("text", ""), "url": j.get("hostedUrl", ""),
                         "locations": [_norm_loc(x) for x in locs if x],
                         "published": datetime.fromtimestamp(ts / 1000, timezone.utc).isoformat()
                         if isinstance(ts, (int, float)) else None})
        return jobs, f"https://jobs.lever.co/{board}"

    def _ashby(self, board):
        data = self.http.get_json(f"https://api.ashbyhq.com/posting-api/job-board/{board}")
        if not isinstance(data, dict):
            return None, ""
        jobs = []
        for j in data.get("jobs", []):
            if j.get("isListed") is False:
                continue
            locs = [j.get("location", "")] + [s.get("location", "") for s in j.get("secondaryLocations") or []]
            addr = ((j.get("address") or {}).get("postalAddress") or {})
            if addr.get("addressLocality"):
                locs.append(addr["addressLocality"])
            jobs.append({"title": j.get("title", ""), "url": j.get("jobUrl", ""),
                         "locations": [_norm_loc(x) for x in locs if x], "published": j.get("publishedAt")})
        return jobs, f"https://jobs.ashbyhq.com/{board}"

    FETCHERS = {"greenhouse": "_greenhouse", "lever": "_lever", "ashby": "_ashby"}

    # ── main ────────────────────────────────────────────────────────────────
    def crawl(self) -> list[dict]:
        items = []
        for b in self.boards:
            if b.get("enabled") is False:
                continue
            ats, board, company = b["ats"].lower(), b["board"], b["company"]
            fn = self.FETCHERS.get(ats)
            if not fn:
                print(f"[ATS] unsupported ats '{ats}' for {company}")
                continue
            self.health["boards"] += 1
            jobs, board_url = getattr(self, fn)(board)
            if jobs is None:
                print(f"[ATS] {company} ({ats}/{board}): fetch failed")
                continue
            self.health["boards_ok"] += 1
            self.health["jobs"] += len(jobs)
            items += self._analyse(company, ats, board, board_url, jobs, b.get("country", "Global"))
        return items

    def _count_focus(self, jobs):
        counts = {f: 0 for f in self.focus}
        rx = {f: re.compile(r"(?<![a-z])" + re.escape(f) + r"(?![a-z])") for f in self.focus}
        for j in jobs:
            blob = " | ".join(j["locations"]).lower()
            for f in self.focus:
                if rx[f].search(blob):   # word-boundary: 'india' ≠ 'indianapolis'
                    counts[f] += 1
        return counts

    def _analyse(self, company, ats, board, board_url, jobs, country):
        now = datetime.now(timezone.utc)
        counts = self._count_focus(jobs)
        role_jobs = [j for j in jobs if any(rx.search(j["title"]) for rx in _ROLE_RX)]
        snap = {"company_name": company, "ats": ats, "board": board, "total_jobs": len(jobs),
                "by_location": counts, "role_hits": {"count": len(role_jobs),
                                                     "titles": [j["title"] for j in role_jobs[:15]]},
                "board_url": board_url, "taken_at": now.isoformat()}
        prev = self.store.last_job_snapshot(ats, board) if self.store else None
        if self.store:
            self.store.save_job_snapshot(snap)
        print(f"[ATS] {company:<24} {len(jobs):>4} open roles | focus: "
              + ", ".join(f"{k}={v}" for k, v in counts.items() if v) + (" | baseline" if not prev else ""))

        out = []
        # (a) surges vs previous snapshot
        if prev:
            prev_counts = prev.get("by_location") or {}
            prev_date = str(prev.get("taken_at", ""))[:10]
            for loc, n in counts.items():
                p = int(prev_counts.get(loc, 0) or 0)
                if n >= self.min_roles and n - p >= self.min_delta and n >= p * self.min_ratio:
                    ev = (f"{ats.title()} job board '{board}' lists {n} open roles matching '{loc}' "
                          f"on {now.date()} (was {p} on {prev_date}).")
                    out.append(make_item(
                        kind="jobs", source=f"ATS_{ats.upper()}", url=board_url,
                        title=f"{company}: hiring surge in {loc.title()} ({p}→{n} open roles)",
                        text=ev, company=company, published_at=now.isoformat(), country=country,
                        signal_type_hint="HIRING", evidence_override=ev, location_hint=loc.title(),
                        headcount_hint=n, confidence_boost=10 if n - p >= 25 else 0,
                        source_key_override=f"surge|{ats}|{board}|{loc}|{now.date()}"))
                elif p == 0 and n >= 3:
                    ev = (f"{ats.title()} job board '{board}' lists {n} open roles matching '{loc}' "
                          f"on {now.date()}; the previous snapshot ({prev_date}) had none.")
                    out.append(make_item(
                        kind="jobs", source=f"ATS_{ats.upper()}", url=board_url,
                        title=f"{company}: first openings in {loc.title()} ({n} roles)",
                        text=ev, company=company, published_at=now.isoformat(), country=country,
                        signal_type_hint="EXPAND", evidence_override=ev, location_hint=loc.title(),
                        source_key_override=f"newloc|{ats}|{board}|{loc}"))
        # (b) facilities / workplace / real-estate roles — each is its own evidence
        for j in role_jobs:
            locs = ", ".join(j["locations"]) or "unspecified"
            ev = f"Open role on {company}'s {ats.title()} board: \"{j['title']}\" — location: {locs}."
            out.append(make_item(
                kind="jobs", source=f"ATS_{ats.upper()}", url=j["url"] or board_url,
                title=f"{company} hiring: {j['title']} ({locs})", text=ev, company=company,
                published_at=j.get("published") or now.isoformat(), country=country,
                signal_type_hint="HIRING", evidence_override=ev, location_hint=j["locations"][0] if j["locations"] else "",
                confidence_boost=15, why_cre_hint="Facilities / workplace / real-estate hire usually "
                                                  "accompanies a new or expanding site"))
        return out

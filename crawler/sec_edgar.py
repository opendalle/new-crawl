"""
SEC EDGAR — the fixed version.

1) Full-text search (efts.sec.gov/LATEST/search-index)
   Verified response shape (Sept 2026):
     hits.hits[]._id      = "<accession>:<filename>"
     hits.hits[]._source  = {ciks[], display_names[], file_date, form, root_forms[],
                             adsh, file_type ("8-K" | "EX-10.1" …), items[] (8-K items),
                             biz_locations[], biz_states[], sics[] …}
   v4 read `entity_name`/`accession_no`/`entity_id`, which do not exist.

   8-K item codes worth knowing for CRE:
     1.01 material definitive agreement (leases, purchase agreements, credit deals)
     2.01 completed acquisition/disposition of assets (property deals)
     2.03 new direct financial obligation      2.05 exit / disposal costs (office closures)
     2.06 material impairment                  8.01 other events
   EX-10.x exhibits are the actual signed agreements (lease text, sq ft, rent).

2) Form D (private placements; filed within 15 days of first sale).
   Daily index → primary_doc.xml → issuer, industry group, amounts, first-sale date.
   This is the public trace of a private round — the closest legal thing to a term sheet.

SEC fair-access rules: ≤10 requests/second and a User-Agent with a contact
e-mail (set SEC_USER_AGENT). HttpClient enforces ~6 req/s for sec.gov hosts.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from urllib.parse import quote_plus

from crawler.http_client import HttpClient, sec_user_agent
from crawler.items import make_item, dump_raw

ITEM_NAMES = {
    "1.01": "Entry into a Material Definitive Agreement",
    "1.02": "Termination of a Material Definitive Agreement",
    "2.01": "Completion of Acquisition or Disposition of Assets",
    "2.03": "Creation of a Direct Financial Obligation",
    "2.05": "Costs Associated with Exit or Disposal Activities",
    "2.06": "Material Impairments",
    "5.02": "Departure/Appointment of Officers",
    "7.01": "Regulation FD Disclosure",
    "8.01": "Other Events",
    "9.01": "Financial Statements and Exhibits",
}

DEFAULT_FTS_QUERIES = [
    {"q": '"lease agreement" "square feet"', "forms": "8-K"},
    {"q": '"office space" "square feet"', "forms": "8-K"},
    {"q": '"new headquarters" lease', "forms": "8-K"},
    {"q": '"data center" lease megawatts', "forms": "8-K"},
    {"q": '"purchase and sale agreement" property', "forms": "8-K"},
    {"q": '"lease termination" office', "forms": "8-K"},
    {"q": '"India" "global capability center"', "forms": "8-K,10-Q,10-K"},
]


def _clean_name(display: str) -> tuple[str, str]:
    """'SI-BONE, Inc. (SIBN) (CIK 0001459839)' → ('SI-BONE, Inc.', 'SIBN')."""
    ticker = ""
    m = re.search(r"\(([A-Z0-9.\-, ]{1,20})\)\s*\(CIK", display or "")
    if m:
        ticker = m.group(1).split(",")[0].strip()
    name = re.sub(r"\s*\((?:CIK [0-9]+|[A-Z0-9.\-, ]{1,20})\)", "", display or "").strip()
    return name, ticker


class SECFullTextSearch:
    URL = "https://efts.sec.gov/LATEST/search-index"

    def __init__(self, http: HttpClient | None = None, queries: list[dict] | None = None,
                 max_hits_per_query: int = 100):
        self.http = http or HttpClient(user_agent=sec_user_agent(),
                                       extra_headers={"Accept": "application/json"})
        self.queries = queries or DEFAULT_FTS_QUERIES
        self.max_hits = max_hits_per_query
        self.health = {"queries": 0, "hits": 0, "errors": 0}

    def crawl(self, days_back: int = 3) -> list[dict]:
        end = date.today()
        start = end - timedelta(days=days_back)
        items, seen = [], set()
        for qi, q in enumerate(self.queries):
            fetched = 0
            while fetched < self.max_hits:
                params = {"q": q["q"], "forms": q.get("forms", "8-K"), "dateRange": "custom",
                          "startdt": start.isoformat(), "enddt": end.isoformat()}
                if fetched:
                    params["from"] = fetched
                data = self.http.get_json(self.URL, params=params)
                self.health["queries"] += 1
                if not isinstance(data, dict):
                    self.health["errors"] += 1
                    break
                if qi == 0 and not fetched:
                    dump_raw("sec_fts", data)
                hits = (data.get("hits") or {}).get("hits") or []
                if not hits:
                    break
                for h in hits:
                    it = self._to_item(h, q["q"])
                    if it and it["url"] not in seen:
                        seen.add(it["url"])
                        items.append(it)
                fetched += len(hits)
                total = ((data.get("hits") or {}).get("total") or {}).get("value", 0)
                if fetched >= total:
                    break
            print(f"[SEC FTS] {q['q'][:50]:<50} → {fetched} hits")
        self.health["hits"] = len(items)
        return items

    def _to_item(self, hit: dict, query: str) -> dict | None:
        src = hit.get("_source") or {}
        _id = hit.get("_id", "")
        adsh = src.get("adsh") or _id.split(":")[0]
        filename = _id.split(":", 1)[1] if ":" in _id else ""
        ciks = src.get("ciks") or []
        if not (adsh and ciks):
            return None
        cik = str(int(ciks[0]))
        folder = f"https://www.sec.gov/Archives/edgar/data/{cik}/{adsh.replace('-', '')}"
        doc_url = f"{folder}/{filename}" if filename else ""
        index_url = f"{folder}/{adsh}-index.htm"
        name, ticker = _clean_name((src.get("display_names") or [""])[0])
        form = src.get("form") or (src.get("root_forms") or [""])[0]
        file_type = src.get("file_type") or form
        items = src.get("items") or []
        item_text = "; ".join(f"Item {i} {ITEM_NAMES.get(i, '')}".strip() for i in items)
        loc = (src.get("biz_locations") or [""])[0]
        title = f"{name} {form} ({file_type})" + (f" — {item_text}" if item_text else "")
        return make_item(
            kind="filing", source="SEC_EDGAR", url=doc_url or index_url, doc_url=doc_url,
            title=title, text=item_text, company=name,
            published_at=f"{src.get('file_date')}T00:00:00+00:00" if src.get("file_date") else None,
            country="USA", category=file_type,
            ids={"sec_cik": cik}, location_hint=loc,
            extra={"adsh": adsh, "form": form, "file_type": file_type, "items": items,
                   "ticker": ticker, "index_url": index_url, "query": query,
                   "search_url": "https://efts.sec.gov/LATEST/search-index?q=" + quote_plus(query)},
            exchange="SEC")


class SECFormD:
    """Private placements from the EDGAR daily form index."""
    INDEX = "https://www.sec.gov/Archives/edgar/daily-index/{y}/QTR{q}/form.{ymd}.idx"
    LINE = re.compile(r"^(D|D/A)\s+(.+?)\s{2,}(\d{1,10})\s+(\d{8}|\d{4}-\d{2}-\d{2})\s+(\S+)\s*$")

    REAL_ESTATE_GROUPS = {"reits and finance", "commercial", "construction", "residential",
                          "other real estate", "real estate"}

    def __init__(self, http: HttpClient | None = None, max_forms: int = 250,
                 min_amount_usd: float = 10_000_000):
        self.http = http or HttpClient(user_agent=sec_user_agent())
        self.max_forms = max_forms
        self.min_amount = min_amount_usd
        self.health = {"index_days": 0, "forms_seen": 0, "xml_fetched": 0, "kept": 0}

    def _index_lines(self, d: date) -> list[tuple]:
        url = self.INDEX.format(y=d.year, q=(d.month - 1) // 3 + 1, ymd=d.strftime("%Y%m%d"))
        res = self.http.get(url, quiet=True)
        if not res or res.status >= 400:
            return []
        self.health["index_days"] += 1
        out = []
        for line in res.text.splitlines():
            m = self.LINE.match(line)
            if m and m.group(1) == "D":
                out.append((m.group(2).strip(), m.group(3), m.group(5)))
        return out

    @staticmethod
    def _xml_values(xml_text: str) -> dict:
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            return {}

        def find(path_names):
            node = root
            for name in path_names:
                nxt = None
                for child in node.iter():
                    if child is node:
                        continue
                    if child.tag.split("}")[-1] == name:
                        nxt = child
                        break
                if nxt is None:
                    # schema variant: fall back to the leaf name anywhere in the doc
                    if path_names[-1] in ("value", "city"):
                        return ""   # too generic to search globally
                    for el in root.iter():
                        if el.tag.split("}")[-1] == path_names[-1] and (el.text or "").strip():
                            return el.text.strip()
                    return ""
                node = nxt
            return (node.text or "").strip()

        return {
            "entityName": find(["primaryIssuer", "entityName"]),
            "city": find(["primaryIssuer", "issuerAddress", "city"]),
            "state": find(["primaryIssuer", "issuerAddress", "stateOrCountryDescription"]),
            "industryGroupType": find(["offeringData", "industryGroup", "industryGroupType"]),
            "totalOfferingAmount": find(["offeringData", "offeringSalesAmounts", "totalOfferingAmount"]),
            "totalAmountSold": find(["offeringData", "offeringSalesAmounts", "totalAmountSold"]),
            "dateOfFirstSale": find(["offeringData", "typeOfFiling", "dateOfFirstSale", "value"]),
            "yetToOccur": find(["offeringData", "typeOfFiling", "dateOfFirstSale", "yetToOccur"]),
        }

    def crawl(self, days_back: int = 2) -> list[dict]:
        items = []
        forms = []
        for i in range(days_back, -1, -1):
            forms += self._index_lines(date.today() - timedelta(days=i))
        self.health["forms_seen"] = len(forms)
        print(f"[SEC Form D] {len(forms)} Form D filings in index; reading up to {self.max_forms}")
        dumped = False
        for name, cik, path in forms[: self.max_forms]:
            m = re.search(r"(\d{10}-\d{2}-\d{6})", path)
            if not m:
                continue
            adsh = m.group(1)
            folder = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{adsh.replace('-', '')}"
            xml_url = f"{folder}/primary_doc.xml"
            res = self.http.get(xml_url, quiet=True, max_bytes=2_000_000)
            if not res or res.status >= 400:
                continue
            self.health["xml_fetched"] += 1
            v = self._xml_values(res.text)
            if not dumped:
                dump_raw("sec_form_d_sample", {"url": xml_url, "parsed": v})
                dumped = True
            group = (v.get("industryGroupType") or "").lower()

            def num(x):
                try:
                    return float(str(x).replace(",", ""))
                except (TypeError, ValueError):
                    return 0.0

            sold, offered = num(v.get("totalAmountSold")), num(v.get("totalOfferingAmount"))
            is_re = group in self.REAL_ESTATE_GROUPS
            if not is_re and max(sold, offered) < self.min_amount:
                continue
            # evidence = verbatim field values from the filing XML
            evidence = "; ".join(f"{k}: {val}" for k, val in v.items() if val)
            items.append(make_item(
                kind="filing", source="SEC_FORM_D", url=f"{folder}/{adsh}-index.htm", doc_url=xml_url,
                title=f"{v.get('entityName') or name} — Form D private offering"
                      + (f" (${sold:,.0f} sold)" if sold else ""),
                text=evidence, company=v.get("entityName") or name, country="USA",
                category="FORM_D", ids={"sec_cik": str(int(cik))},
                location_hint=", ".join(x for x in (v.get("city"), v.get("state")) if x),
                extra={"adsh": adsh, "industry": v.get("industryGroupType"), "amount_sold": sold,
                       "amount_offered": offered, "first_sale": v.get("dateOfFirstSale"),
                       "structured_evidence": evidence},
                signal_type_hint="FUNDING", exchange="SEC"))
        self.health["kept"] = len(items)
        print(f"[SEC Form D] kept {len(items)} (real-estate issuers or ≥ ${self.min_amount:,.0f})")
        return items

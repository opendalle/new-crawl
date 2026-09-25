"""
Verify your Supabase setup before the first crawl.

    export SUPABASE_URL=https://esnugiumktntfmvxkvwa.supabase.co
    export SUPABASE_SERVICE_ROLE_KEY=...     # from Supabase → Settings → API
    python tools/check_setup.py

Checks: key roles are right, schema.sql has been run (all tables/views exist),
the anon key can READ but not WRITE, and the service key can write.
"""
import base64
import json
import os
import re
import sys

import requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def role(jwt):
    try:
        p = jwt.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(p + "=" * (-len(p) % 4))).get("role", "?")
    except Exception:
        return "not-a-jwt"


def main():
    url = (os.environ.get("SUPABASE_URL") or "").rstrip("/")
    svc = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_KEY") or ""
    cfg = open("nexus-config.js").read()
    anon = re.search(r'supabaseAnonKey:\s*"([^"]+)"', cfg).group(1)
    ok = True

    def check(cond, msg):
        nonlocal ok
        print(("  ✓ " if cond else "  ✗ ") + msg)
        ok &= bool(cond)

    print("Keys")
    check(url.startswith("https://"), f"SUPABASE_URL set ({url or 'missing'})")
    check(role(svc) == "service_role", f"service key role = {role(svc)}")
    check(role(anon) == "anon", f"nexus-config.js key role = {role(anon)} (must be anon)")
    if not (url and svc):
        sys.exit("\nSet SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY first.")

    H = lambda k: {"apikey": k, "Authorization": f"Bearer {k}"}
    print("Schema (run database/schema.sql in the SQL editor if these fail)")
    for t in ["companies", "signals", "lead_scores", "forward_events", "documents", "job_snapshots",
              "crawl_runs", "signal_feed", "event_calendar"]:
        r = requests.get(f"{url}/rest/v1/{t}?select=*&limit=1", headers=H(svc), timeout=20)
        check(r.status_code == 200, f"{t}: HTTP {r.status_code}")
    r = requests.get(f"{url}/rest/v1/signals?select=evidence,source_key&limit=1", headers=H(svc), timeout=20)
    check(r.status_code == 200, "signals has v5 columns (evidence, source_key)")

    print("Security")
    r = requests.get(f"{url}/rest/v1/signal_feed?select=*&limit=1", headers=H(anon), timeout=20)
    check(r.status_code == 200, f"anon can read signal_feed (HTTP {r.status_code})")
    r = requests.get(f"{url}/rest/v1/documents?select=doc_key&limit=1", headers=H(anon), timeout=20)
    check(r.status_code == 200 and r.json() == [] or r.status_code in (401, 403),
          "anon cannot read documents (private)")
    r = requests.post(f"{url}/rest/v1/companies", headers={**H(anon), "Content-Type": "application/json"},
                      json={"company_name": "rls-test", "normalized_name": "rls-test"}, timeout=20)
    check(r.status_code in (401, 403), f"anon cannot write (HTTP {r.status_code})")
    r = requests.post(f"{url}/rest/v1/companies?on_conflict=normalized_name",
                      headers={**H(svc), "Content-Type": "application/json",
                               "Prefer": "resolution=merge-duplicates,return=representation"},
                      json={"company_name": "setup-check", "normalized_name": "__setup_check__"}, timeout=20)
    check(r.status_code in (200, 201), f"service key can write (HTTP {r.status_code})")
    requests.delete(f"{url}/rest/v1/companies?normalized_name=eq.__setup_check__", headers=H(svc), timeout=20)

    print("\nAll good — run: python main.py" if ok else "\nFix the ✗ items above.")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()

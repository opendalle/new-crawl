"""
Export the local SQLite database to data/feed.json so the dashboard can be
viewed without Supabase:

    python main.py --db sqlite
    python tools/export_json.py
    python -m http.server 8000      # then open http://localhost:8000/?local=1
"""
import json
import os
import sqlite3
import sys
from datetime import date

path = os.environ.get("NEXUS_SQLITE_PATH", "data/nexus.db")
if not os.path.exists(path):
    sys.exit(f"{path} not found — run `python main.py --db sqlite` first")
con = sqlite3.connect(path)
con.row_factory = sqlite3.Row
q = lambda sql, *a: [dict(r) for r in con.execute(sql, a).fetchall()]
signals = q("""SELECT s.*, c.company_name, c.nse_symbol, c.bse_code, l.score AS lead_score, l.priority_level
               FROM signals s LEFT JOIN companies c USING(company_id)
               LEFT JOIN lead_scores l USING(company_id) ORDER BY s.created_at DESC LIMIT 5000""")
events = q("""SELECT e.*, c.company_name, c.nse_symbol, c.bse_code FROM forward_events e
              LEFT JOIN companies c USING(company_id) WHERE e.event_date IS NULL OR e.event_date >= ?
              ORDER BY e.event_date""", date.today().isoformat())
for e in events:
    e["cre_relevant"] = bool(e["cre_relevant"])
runs = q("SELECT * FROM crawl_runs ORDER BY finished_at DESC LIMIT 1")
for r in runs:
    r["report"] = json.loads(r["report"] or "{}")
os.makedirs("data", exist_ok=True)
with open("data/feed.json", "w", encoding="utf-8") as f:
    json.dump({"signals": signals, "events": events, "runs": runs}, f, ensure_ascii=False, default=str)
print(f"data/feed.json: {len(signals)} signals, {len(events)} upcoming events")

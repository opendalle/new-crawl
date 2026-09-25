"""
NEXUS PROP INTEL v5 — command line entry point.

Examples
  python main.py                                  # everything, last 1 day
  python main.py --sources bse,nse --days 3       # Indian exchanges only
  python main.py --sources sec_fts,sec_form_d     # US only
  python main.py --db sqlite                      # local database at data/nexus.db
  python main.py --dry-run --sources news         # no DB writes; see data/dry_run_signals.json
  python main.py --no-docs                        # skip PDF downloads (fast)

Sources: bse, nse, nse_board_meetings, bse_results, sec_fts, sec_form_d, ats, ir, news
"""
import argparse
import os
import sys

os.chdir(os.path.dirname(os.path.abspath(__file__)))   # config/ and data/ are repo-relative

from pipeline import ALL_SOURCES, Pipeline


def main(argv=None):
    ap = argparse.ArgumentParser(description="Nexus CRE intelligence crawler")
    ap.add_argument("--sources", default=",".join(ALL_SOURCES),
                    help=f"comma list (default all): {','.join(ALL_SOURCES)}")
    ap.add_argument("--days", type=int, default=1, help="days back for filings (default 1)")
    ap.add_argument("--db", choices=["supabase", "sqlite"], default=None,
                    help="storage backend (default: supabase if env vars set, else sqlite)")
    ap.add_argument("--no-docs", action="store_true", help="don't download/read filing documents")
    ap.add_argument("--no-llm", action="store_true", help="don't call Gemini even if a key is set")
    ap.add_argument("--max-docs", type=int, default=None, help="document download budget")
    ap.add_argument("--dry-run", action="store_true", help="crawl + extract, write nothing to the DB")
    args = ap.parse_args(argv)

    sources = [s.strip() for s in args.sources.split(",") if s.strip()]
    bad = [s for s in sources if s not in ALL_SOURCES]
    if bad:
        ap.error(f"unknown source(s): {bad}")
    report = Pipeline(sources=sources, days_back=args.days, db=args.db, read_docs=not args.no_docs,
                      use_llm=not args.no_llm, max_docs=args.max_docs, dry_run=args.dry_run).run()
    failed = [k for k, v in report["sources"].items() if not v.get("ok")]
    # exit non-zero only if EVERY requested source failed (so Actions shows red)
    return 1 if failed and len(failed) == len(report["sources"]) else 0


if __name__ == "__main__":
    sys.exit(main())

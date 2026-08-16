"""One-shot duplicate reconciliation against existing data.

The dedupe key is company + title, so a source that embeds the season in
the title ("Software Engineering Intern, Dynamo - Fall 2026") mints a
second record for a posting already tracked under its plain spelling. The
pipeline now reconciles these every cycle; this script runs the same pass
once over the rows that predate it.

Identity is two-tier because gate redirects flatten some sites' distinct
reqs onto one generic page: rows sharing a canonical_url merge on a
matching ATS job key regardless of title, or, when no row carries a key,
only when every title folds to the same token set. A canonical URL held
by 3+ rows that will not unify is printed as collapsed and left alone.

Backend comes from DATABASE_URL or SUPABASE_URL_POSTGRES (Postgres), or
--sqlite PATH for a local file. Prints every fold it performs, survivor
title <- duplicate title. --dry-run prints without writing. Safe to
re-run: a rejected duplicate is no longer live, so it cannot merge again.

    docker compose run --rm poller python scripts/reconcile_dupes.py --dry-run
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from intake.pipeline import reconcile_duplicates


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sqlite", help="reconcile a local SQLite db instead of Postgres")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the folds without writing them")
    args = ap.parse_args()

    if args.sqlite:
        src = Path(args.sqlite)
        if not src.exists():
            sys.exit(f"sqlite db not found: {src}")
        from intake.store import Store
        store = Store(src)
    else:
        url = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_URL_POSTGRES")
        if not url:
            sys.exit("set DATABASE_URL (or SUPABASE_URL_POSTGRES), or pass --sqlite")
        from intake.store_postgres import PostgresStore
        store = PostgresStore(url)

    def show(survivor, dup):
        print(f"merge {survivor.company}: {survivor.title!r} ({survivor.id}) "
              f"<- {dup.title!r} ({dup.id})")

    def show_collapse(group):
        print(f"collapsed {group[0].company}: {len(group)} rows share "
              f"{group[0].canonical_url}")

    merged, collapsed = reconcile_duplicates(
        store, on_merge=show, on_collapse=show_collapse, apply=not args.dry_run
    )
    print(f"{'would fold' if args.dry_run else 'folded'} {merged} duplicate(s), "
          f"skipped {collapsed} collapsed canonical(s)")


if __name__ == "__main__":
    main()

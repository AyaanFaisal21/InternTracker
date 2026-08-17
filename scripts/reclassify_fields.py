"""Recompute degree_levels and season for existing rows, offline.

The degree classifier once read page chrome (application-form school
dropdowns, error-shell related-job links, founder bios) and the season
parser once harvested copyright and founding years; rows gated before
the fixes still carry those labels. This script recomputes both fields
for every non-rejected row from stored data only: title + qualifications
excerpt through intake.degrees.classify_posting, and stored season +
title + qualifications through intake.dates.reconcile_season. No
network, no LLM.

Locations need no pass: countries and the remote flag derive at read
time in web.py (locations.countries_of / is_remote over the stored
location strings), so the location-parser fixes are live the moment the
code deploys.

Backend comes from DATABASE_URL or SUPABASE_URL_POSTGRES (Postgres), or
--sqlite PATH for a local file. --dry-run prints every difference as
"id | company | field: old -> new" plus a summary count without
writing; without it the changes apply through the store. Deterministic
over stored fields, so it is safe to re-run.

    docker compose run --rm poller python scripts/reclassify_fields.py --dry-run
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from intake.dates import reconcile_season
from intake.degrees import classify_posting
from intake.schema import Status


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--sqlite", help="reclassify a local SQLite db instead of Postgres")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the changes without writing them")
    args = ap.parse_args()

    # Windows consoles default to cp1252; company names are UTF-8.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

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

    rows = live = changed_rows = 0
    degree_changes = season_changes = 0
    for p in store.all_postings():
        rows += 1
        if p.status == Status.REJECTED:
            continue
        live += 1
        changes: list[tuple[str, object, object]] = []
        new_levels = classify_posting(p.title, "", p.qualifications, None)
        if new_levels != p.degree_levels:
            changes.append(("degree_levels", p.degree_levels, new_levels))
            p.degree_levels = new_levels
            degree_changes += 1
        new_season = reconcile_season(p.season, p.title, p.qualifications or "")
        if new_season != p.season:
            changes.append(("season", p.season, new_season))
            p.season = new_season
            season_changes += 1
        if not changes:
            continue
        changed_rows += 1
        for field, old, new in changes:
            print(f"{p.id} | {p.company} | {field}: {old!r} -> {new!r}")
        if not args.dry_run:
            store.update(p)

    verb = "would change" if args.dry_run else "changed"
    print(f"{rows} rows, {live} live, {verb} {changed_rows} row(s): "
          f"{degree_changes} degree_levels, {season_changes} season")


if __name__ == "__main__":
    main()

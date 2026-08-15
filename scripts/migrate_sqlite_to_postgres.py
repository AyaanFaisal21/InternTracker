"""One-shot migration: SQLite intake.db -> Postgres (DATABASE_URL).

Copies rows verbatim — no pydantic validation, no normalization — so the
migrated data is exactly what the SQLite store held. Safe to re-run:
postings upsert with ON CONFLICT DO NOTHING (rows already in Postgres win,
because the live poller may have advanced them); suggestions and visits
are skipped entirely when their target table is non-empty (their identity
ids regenerate, so a blind re-run would duplicate them).

Run inside the poller container, where the volume and env are mounted:

    docker compose run --rm poller python scripts/migrate_sqlite_to_postgres.py

The WAL is checkpointed before reading; recent writes live in intake.db-wal
and a plain read of intake.db alone would miss them.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

POSTING_COLS = (
    "id", "company", "title", "url", "canonical_url", "category", "audience",
    "degree_levels", "date_posted", "date_posted_text", "season",
    "qualifications", "locations", "sources", "first_seen", "status",
    "reject_reason", "verdict",
)
JSON_COLS = {"audience", "degree_levels", "locations", "sources", "verdict"}


def posting_params(row: sqlite3.Row) -> tuple:
    out = []
    for col in POSTING_COLS:
        v = row[col]
        if col in JSON_COLS:
            v = Jsonb(json.loads(v)) if v is not None else None
        out.append(v)
    return tuple(out)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sqlite", default=os.environ.get("INTAKE_DB", "intake.db"),
                    help="path to intake.db (default: $INTAKE_DB or ./intake.db)")
    args = ap.parse_args()

    url = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_URL_POSTGRES")
    if not url:
        sys.exit("set DATABASE_URL (or SUPABASE_URL_POSTGRES)")
    src = Path(args.sqlite)
    if not src.exists():
        sys.exit(f"sqlite db not found: {src}")

    lite = sqlite3.connect(str(src))
    lite.row_factory = sqlite3.Row
    lite.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    postings = lite.execute(f"SELECT {', '.join(POSTING_COLS)} FROM postings").fetchall()
    suggestions = lite.execute(
        "SELECT kind, value, company, keywords, status, result, created_at "
        "FROM suggestions ORDER BY id"
    ).fetchall()
    visits = lite.execute("SELECT page, ua, at FROM visits ORDER BY id").fetchall()
    print(f"sqlite: {len(postings)} postings, {len(suggestions)} suggestions, "
          f"{len(visits)} visits")

    with psycopg.connect(url, connect_timeout=15) as pg:
        cur = pg.execute("SELECT count(*) FROM postings")
        print(f"postgres before: {cur.fetchone()[0]} postings")

        pg.cursor().executemany(
            f"""INSERT INTO postings ({', '.join(POSTING_COLS)})
                VALUES ({', '.join(['%s'] * len(POSTING_COLS))})
                ON CONFLICT (id) DO NOTHING""",
            [posting_params(r) for r in postings],
        )

        for table, rows, cols in (
            ("suggestions", suggestions,
             ("kind", "value", "company", "keywords", "status", "result", "created_at")),
            ("visits", visits, ("page", "ua", "at")),
        ):
            n = pg.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            if n:
                print(f"{table}: target has {n} rows, skipping (already migrated)")
                continue
            if rows:
                pg.cursor().executemany(
                    f"INSERT INTO {table} ({', '.join(cols)}) "
                    f"VALUES ({', '.join(['%s'] * len(cols))})",
                    [tuple(r) for r in rows],
                )
        pg.commit()

        for t in ("postings", "suggestions", "visits"):
            n = pg.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
            print(f"postgres after: {t} = {n} rows")
    print("done")


if __name__ == "__main__":
    main()

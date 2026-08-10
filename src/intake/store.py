"""SQLite persistence. One table: postings, keyed by dedupe id.

The store is the pipeline's memory. A detection whose id already exists merges
sources/locations instead of creating a new record, so re-polls are idempotent.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .schema import Posting, RawDetection, Source, Status

SCHEMA = """
CREATE TABLE IF NOT EXISTS postings (
    id            TEXT PRIMARY KEY,
    company       TEXT NOT NULL,
    title         TEXT NOT NULL,
    url           TEXT NOT NULL,
    canonical_url TEXT,
    degree_levels TEXT NOT NULL DEFAULT '[]',  -- JSON array
    date_posted   TEXT,
    date_posted_text TEXT,
    season        TEXT,
    locations     TEXT NOT NULL,   -- JSON array
    sources       TEXT NOT NULL,   -- JSON array
    first_seen    TEXT NOT NULL,
    status        TEXT NOT NULL,
    reject_reason TEXT,
    verdict       TEXT             -- JSON, verifier output
);
"""


class Store:
    def __init__(self, path: Path | str):
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(postings)")}
        if "canonical_url" not in cols:
            self.conn.execute("ALTER TABLE postings ADD COLUMN canonical_url TEXT")
        if "degree_levels" not in cols:
            self.conn.execute(
                "ALTER TABLE postings ADD COLUMN degree_levels TEXT NOT NULL DEFAULT '[]'"
            )
        if "date_posted" not in cols:
            self.conn.execute("ALTER TABLE postings ADD COLUMN date_posted TEXT")
        if "date_posted_text" not in cols:
            self.conn.execute("ALTER TABLE postings ADD COLUMN date_posted_text TEXT")
        if "season" not in cols:
            self.conn.execute("ALTER TABLE postings ADD COLUMN season TEXT")
        self.conn.commit()

    def get(self, posting_id: str) -> Posting | None:
        row = self.conn.execute(
            "SELECT * FROM postings WHERE id = ?", (posting_id,)
        ).fetchone()
        return self._to_posting(row) if row else None

    def upsert_detection(self, det: RawDetection) -> tuple[Posting, bool]:
        """Insert a detection or merge it into the existing record.

        Returns (posting, is_new). is_new drives the pipeline: only new
        postings enter verification.
        """
        existing = self.get(det.dedupe_key())
        if existing is None:
            p = Posting.from_detection(det)
            self._write(p)
            return p, True
        changed = False
        if det.source not in existing.sources:
            existing.sources.append(det.source)
            changed = True
        if existing.date_posted is None and det.date_posted is not None:
            existing.date_posted = det.date_posted
            changed = True
        for loc in det.locations:
            if loc and loc not in existing.locations:
                existing.locations.append(loc)
                changed = True
        if changed:
            self._write(existing)
        return existing, False

    def update(self, p: Posting) -> None:
        self._write(p)

    def all_postings(self) -> list[Posting]:
        rows = self.conn.execute(
            "SELECT * FROM postings ORDER BY COALESCE(date_posted, first_seen) DESC"
        ).fetchall()
        return [self._to_posting(r) for r in rows]

    def by_status(self, status: Status) -> list[Posting]:
        rows = self.conn.execute(
            "SELECT * FROM postings WHERE status = ?", (status.value,)
        ).fetchall()
        return [self._to_posting(r) for r in rows]

    def _write(self, p: Posting) -> None:
        self.conn.execute(
            """INSERT INTO postings
               (id, company, title, url, canonical_url, degree_levels,
                date_posted, date_posted_text, season, locations, sources,
                first_seen, status, reject_reason, verdict)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                 canonical_url=excluded.canonical_url,
                 degree_levels=excluded.degree_levels,
                 date_posted=excluded.date_posted,
                 date_posted_text=excluded.date_posted_text,
                 season=excluded.season,
                 locations=excluded.locations, sources=excluded.sources,
                 status=excluded.status, reject_reason=excluded.reject_reason,
                 verdict=excluded.verdict""",
            (
                p.id,
                p.company,
                p.title,
                p.url,
                p.canonical_url,
                json.dumps(p.degree_levels),
                p.date_posted.isoformat() if p.date_posted else None,
                p.date_posted_text,
                p.season,
                json.dumps(p.locations),
                json.dumps([s.value for s in p.sources]),
                p.first_seen.isoformat(),
                p.status.value,
                p.reject_reason,
                p.verdict.model_dump_json() if p.verdict else None,
            ),
        )
        self.conn.commit()

    @staticmethod
    def _to_posting(row: sqlite3.Row) -> Posting:
        from .schema import Verdict

        return Posting(
            id=row["id"],
            company=row["company"],
            title=row["title"],
            url=row["url"],
            canonical_url=row["canonical_url"],
            degree_levels=json.loads(row["degree_levels"]),
            date_posted=row["date_posted"],
            date_posted_text=row["date_posted_text"],
            season=row["season"],
            locations=json.loads(row["locations"]),
            sources=[Source(s) for s in json.loads(row["sources"])],
            first_seen=row["first_seen"],
            status=Status(row["status"]),
            reject_reason=row["reject_reason"],
            verdict=Verdict.model_validate_json(row["verdict"]) if row["verdict"] else None,
        )

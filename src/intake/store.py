"""SQLite persistence. One table: postings, keyed by dedupe id.

The store is the pipeline's memory. A detection whose id already exists merges
sources/locations instead of creating a new record, so re-polls are idempotent.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from pathlib import Path

from .schema import Posting, RawDetection, Source, Status

_pg_store = None
_pg_lock = threading.Lock()


def open_store(db_path: Path | str):
    """Store factory. DATABASE_URL set -> Postgres; unset -> SQLite.

    SQLite returns a fresh connection each call — the cheap per-request
    pattern the threaded web handlers rely on. Postgres returns one shared
    process-wide store because network connections are too expensive to
    open per request; it locks internally.
    """
    url = os.environ.get("DATABASE_URL")
    if not url:
        return Store(db_path)
    global _pg_store
    with _pg_lock:
        if _pg_store is None or _pg_store.url != url:
            from .store_postgres import PostgresStore
            _pg_store = PostgresStore(url)
        return _pg_store

SCHEMA = """
CREATE TABLE IF NOT EXISTS postings (
    id            TEXT PRIMARY KEY,
    company       TEXT NOT NULL,
    title         TEXT NOT NULL,
    url           TEXT NOT NULL,
    canonical_url TEXT,
    category      TEXT NOT NULL DEFAULT 'internship',
    audience      TEXT NOT NULL DEFAULT '[]',  -- JSON array
    degree_levels TEXT NOT NULL DEFAULT '[]',  -- JSON array
    date_posted   TEXT,
    date_posted_text TEXT,
    season        TEXT,
    qualifications TEXT,
    locations     TEXT NOT NULL,   -- JSON array
    sources       TEXT NOT NULL,   -- JSON array
    first_seen    TEXT NOT NULL,
    status        TEXT NOT NULL,
    reject_reason TEXT,
    verdict       TEXT             -- JSON, verifier output
);

CREATE TABLE IF NOT EXISTS suggestions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    kind       TEXT NOT NULL,       -- 'url' | 'company'
    value      TEXT NOT NULL,       -- the url or company name
    company    TEXT,                -- optional display name for url kind
    keywords   TEXT,                -- optional comma separated
    status     TEXT NOT NULL DEFAULT 'new',  -- new | matched | no_match | error
    result     TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS visits (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    page TEXT NOT NULL,
    ua   TEXT,
    at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS subscriptions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    channel    TEXT NOT NULL,               -- 'push' | 'email'
    target     TEXT NOT NULL,               -- push endpoint JSON or email address
    filters    TEXT NOT NULL DEFAULT '{}',  -- JSON {"companies": [...]}; '{}' = all
    token      TEXT NOT NULL UNIQUE,        -- opaque unsubscribe secret (uuid4 hex)
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    active     INTEGER NOT NULL DEFAULT 1
);
"""


class Store:
    def __init__(self, path: Path | str):
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        # Poller and web run as separate processes against this file. WAL
        # lets readers proceed during writes; busy_timeout rides out the
        # occasional write collision instead of raising immediately.
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=5000")
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
        if "qualifications" not in cols:
            self.conn.execute("ALTER TABLE postings ADD COLUMN qualifications TEXT")
        if "category" not in cols:
            self.conn.execute(
                "ALTER TABLE postings ADD COLUMN category TEXT NOT NULL DEFAULT 'internship'"
            )
        if "audience" not in cols:
            self.conn.execute(
                "ALTER TABLE postings ADD COLUMN audience TEXT NOT NULL DEFAULT '[]'"
            )
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

    def add_suggestion(
        self, kind: str, value: str, company: str | None = None,
        keywords: str | None = None,
    ) -> int:
        cur = self.conn.execute(
            "INSERT INTO suggestions (kind, value, company, keywords) VALUES (?,?,?,?)",
            (kind, value, company, keywords),
        )
        self.conn.commit()
        return cur.lastrowid

    def pending_suggestions(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM suggestions WHERE status = 'new' ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]

    def resolve_suggestion(self, sid: int, status: str, result: str) -> None:
        self.conn.execute(
            "UPDATE suggestions SET status = ?, result = ? WHERE id = ?",
            (status, result[:500], sid),
        )
        self.conn.commit()

    def record_visit(self, page: str, ua: str | None) -> None:
        self.conn.execute("INSERT INTO visits (page, ua) VALUES (?, ?)", (page, ua))
        self.conn.commit()

    def visit_count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM visits").fetchone()[0]

    def recent_suggestions(self, limit: int = 25) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM suggestions ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def add_subscription(self, channel: str, target: str, filters: dict) -> tuple[int, str]:
        token = uuid.uuid4().hex
        cur = self.conn.execute(
            "INSERT INTO subscriptions (channel, target, filters, token) VALUES (?,?,?,?)",
            (channel, target, json.dumps(filters), token),
        )
        self.conn.commit()
        return cur.lastrowid, token

    def deactivate_by_token(self, token: str) -> bool:
        cur = self.conn.execute(
            "UPDATE subscriptions SET active = 0 WHERE token = ?", (token,)
        )
        self.conn.commit()
        return cur.rowcount > 0

    def active_subscriptions(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM subscriptions WHERE active = 1 ORDER BY id"
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["filters"] = json.loads(d["filters"])
            out.append(d)
        return out

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

    def duplicate_groups(self) -> list[list[Posting]]:
        """Live postings sharing a canonical_url, grouped, for reconciliation.

        Live means gated, verified or published: pending rows have no
        canonical_url yet, and rejected rows must stay down or every cycle
        would re-merge them.
        """
        rows = self.conn.execute(
            """SELECT * FROM postings
               WHERE status IN ('gated', 'verified', 'published')
                 AND canonical_url IN (
                     SELECT canonical_url FROM postings
                     WHERE canonical_url IS NOT NULL
                       AND status IN ('gated', 'verified', 'published')
                     GROUP BY canonical_url
                     HAVING COUNT(*) > 1)
               ORDER BY canonical_url, first_seen"""
        ).fetchall()
        groups: dict[str, list[Posting]] = {}
        for r in rows:
            groups.setdefault(r["canonical_url"], []).append(self._to_posting(r))
        return list(groups.values())

    def _write(self, p: Posting) -> None:
        self.conn.execute(
            """INSERT INTO postings
               (id, company, title, url, canonical_url, category, audience, degree_levels,
                date_posted, date_posted_text, season, qualifications,
                locations, sources, first_seen, status, reject_reason, verdict)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                 title=excluded.title,
                 canonical_url=excluded.canonical_url,
                 category=excluded.category,
                 audience=excluded.audience,
                 degree_levels=excluded.degree_levels,
                 date_posted=excluded.date_posted,
                 date_posted_text=excluded.date_posted_text,
                 season=excluded.season,
                 qualifications=excluded.qualifications,
                 locations=excluded.locations, sources=excluded.sources,
                 status=excluded.status, reject_reason=excluded.reject_reason,
                 verdict=excluded.verdict""",
            (
                p.id,
                p.company,
                p.title,
                p.url,
                p.canonical_url,
                p.category,
                json.dumps(p.audience),
                json.dumps(p.degree_levels),
                p.date_posted.isoformat() if p.date_posted else None,
                p.date_posted_text,
                p.season,
                p.qualifications,
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
            category=row["category"],
            audience=json.loads(row["audience"]),
            degree_levels=json.loads(row["degree_levels"]),
            date_posted=row["date_posted"],
            date_posted_text=row["date_posted_text"],
            season=row["season"],
            qualifications=row["qualifications"],
            locations=json.loads(row["locations"]),
            sources=[Source(s) for s in json.loads(row["sources"])],
            first_seen=row["first_seen"],
            status=Status(row["status"]),
            reject_reason=row["reject_reason"],
            verdict=Verdict.model_validate_json(row["verdict"]) if row["verdict"] else None,
        )

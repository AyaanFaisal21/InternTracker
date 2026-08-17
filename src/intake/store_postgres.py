"""Postgres persistence over the same interface as store.Store.

The schema lives in scripts/supabase_schema.sql and is applied out of band;
this module assumes the tables exist. One process holds one connection,
created lazily and shared across threads behind a lock — network
connections are too expensive for the per-request pattern the SQLite store
uses. Autocommit matches the SQLite store's statement-level semantics and
avoids idle-in-transaction sessions on the pooler. A dead connection is
reopened and the operation retried once; every write is a single idempotent
statement, so the retry cannot double-apply.
"""

from __future__ import annotations

import secrets
import threading
import uuid
from datetime import date, datetime, timedelta, timezone

from .schema import Posting, RawDetection, Source, Status, Verdict, suggestion_key


def utc_day() -> date:
    """Today in UTC, as a date for the `day` column. Mirrors store.utc_day,
    which returns the text SQLite stores."""
    return datetime.now(timezone.utc).date()

try:
    import psycopg
    from psycopg.rows import dict_row
    from psycopg.types.json import Jsonb
except ImportError as e:  # pragma: no cover
    raise ImportError(
        "the Postgres backend needs psycopg: pip install '.[postgres]'"
    ) from e


class PostgresStore:
    def __init__(self, url: str):
        self.url = url
        self._lock = threading.Lock()
        self._conn: psycopg.Connection | None = None

    def _connect(self) -> psycopg.Connection:
        self._conn = psycopg.connect(
            self.url, row_factory=dict_row, connect_timeout=15, autocommit=True
        )
        return self._conn

    def _run(self, fn):
        with self._lock:
            conn = self._conn or self._connect()
            try:
                return fn(conn)
            except (psycopg.OperationalError, psycopg.InterfaceError):
                try:
                    conn.close()
                except Exception:
                    pass
                return fn(self._connect())

    # -- postings ---------------------------------------------------------

    def get(self, posting_id: str) -> Posting | None:
        row = self._run(
            lambda c: c.execute(
                "SELECT * FROM postings WHERE id = %s", (posting_id,)
            ).fetchone()
        )
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
        rows = self._run(
            lambda c: c.execute(
                "SELECT * FROM postings "
                "ORDER BY COALESCE(date_posted, first_seen) DESC"
            ).fetchall()
        )
        return [self._to_posting(r) for r in rows]

    def by_status(self, status: Status) -> list[Posting]:
        rows = self._run(
            lambda c: c.execute(
                "SELECT * FROM postings WHERE status = %s", (status.value,)
            ).fetchall()
        )
        return [self._to_posting(r) for r in rows]

    def duplicate_groups(self) -> list[list[Posting]]:
        """Live postings sharing a canonical_url, grouped, for reconciliation.

        Same contract as the SQLite store: live means gated, verified or
        published, so rejected duplicates never re-merge.
        """
        rows = self._run(
            lambda c: c.execute(
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
        )
        groups: dict[str, list[Posting]] = {}
        for r in rows:
            groups.setdefault(r["canonical_url"], []).append(self._to_posting(r))
        return list(groups.values())

    def _write(self, p: Posting) -> None:
        self._run(
            lambda c: c.execute(
                """INSERT INTO postings
                   (id, company, title, url, canonical_url, category, audience,
                    degree_levels, date_posted, date_posted_text, season,
                    qualifications, locations, sources, first_seen, status,
                    reject_reason, verdict)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
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
                    Jsonb(p.audience),
                    Jsonb(p.degree_levels),
                    p.date_posted,
                    p.date_posted_text,
                    p.season,
                    p.qualifications,
                    Jsonb(p.locations),
                    Jsonb([s.value for s in p.sources]),
                    p.first_seen,
                    p.status.value,
                    p.reject_reason,
                    Jsonb(p.verdict.model_dump(mode="json")) if p.verdict else None,
                ),
            )
        )

    @staticmethod
    def _to_posting(row: dict) -> Posting:
        # jsonb comes back as Python lists/dicts and timestamptz as datetime,
        # so no decode step — the mirror image of the SQLite json.loads path.
        return Posting(
            id=row["id"],
            company=row["company"],
            title=row["title"],
            url=row["url"],
            canonical_url=row["canonical_url"],
            category=row["category"],
            audience=row["audience"],
            degree_levels=row["degree_levels"],
            date_posted=row["date_posted"],
            date_posted_text=row["date_posted_text"],
            season=row["season"],
            qualifications=row["qualifications"],
            locations=row["locations"],
            sources=[Source(s) for s in row["sources"]],
            first_seen=row["first_seen"],
            status=Status(row["status"]),
            reject_reason=row["reject_reason"],
            verdict=Verdict.model_validate(row["verdict"]) if row["verdict"] else None,
        )

    # -- suggestions ------------------------------------------------------

    def add_suggestion(
        self, kind: str, value: str, company: str | None = None,
        keywords: str | None = None,
    ) -> int:
        return self._run(
            lambda c: c.execute(
                "INSERT INTO suggestions (kind, value, company, keywords) "
                "VALUES (%s,%s,%s,%s) RETURNING id",
                (kind, value, company, keywords),
            ).fetchone()["id"]
        )

    def pending_suggestions(self) -> list[dict]:
        rows = self._run(
            lambda c: c.execute(
                "SELECT * FROM suggestions WHERE status = 'new' ORDER BY id"
            ).fetchall()
        )
        return [self._sugg_dict(r) for r in rows]

    def resolve_suggestion(self, sid: int, status: str, result: str) -> None:
        self._run(
            lambda c: c.execute(
                "UPDATE suggestions SET status = %s, result = %s WHERE id = %s",
                (status, result[:500], sid),
            )
        )

    def defer_suggestion(self, sid: int, result: str) -> None:
        """Note only, status untouched: the row stays 'new' so the next cycle
        retries it. See the SQLite store for why."""
        self._run(
            lambda c: c.execute(
                "UPDATE suggestions SET result = %s WHERE id = %s", (result[:500], sid)
            )
        )

    def duplicate_suggestion(self, kind: str, value: str, within_days: int = 30) -> dict | None:
        """Same contract as the SQLite store: an open or recently resolved
        suggestion with the same normalized identity, or None."""
        rows = self._run(
            lambda c: c.execute(
                """SELECT * FROM suggestions
                    WHERE kind = %s
                      AND (status = 'new'
                           OR created_at >= now() - make_interval(days => %s))
                    ORDER BY id DESC LIMIT 200""",
                (kind, int(within_days)),
            ).fetchall()
        )
        key = suggestion_key(kind, value)
        return next(
            (self._sugg_dict(r) for r in rows if suggestion_key(kind, r["value"]) == key),
            None,
        )

    def recent_suggestions(self, limit: int = 25) -> list[dict]:
        rows = self._run(
            lambda c: c.execute(
                "SELECT * FROM suggestions ORDER BY id DESC LIMIT %s", (limit,)
            ).fetchall()
        )
        return [self._sugg_dict(r) for r in rows]

    # -- resolver cache and budget ----------------------------------------

    def cached_resolution(self, key: str, max_age_days: int) -> dict | None:
        row = self._run(
            lambda c: c.execute(
                "SELECT resolution FROM company_resolutions "
                "WHERE key = %s AND resolved_at >= now() - make_interval(days => %s)",
                (key, int(max_age_days)),
            ).fetchone()
        )
        # jsonb decodes to a dict on the way out, the mirror of the SQLite
        # json.loads path.
        return row["resolution"] if row else None

    def cache_resolution(self, key: str, company: str, resolution: dict) -> None:
        self._run(
            lambda c: c.execute(
                """INSERT INTO company_resolutions (key, company, resolution, resolved_at)
                   VALUES (%s,%s,%s,now())
                   ON CONFLICT(key) DO UPDATE SET
                     company=excluded.company,
                     resolution=excluded.resolution,
                     resolved_at=excluded.resolved_at""",
                (key, company, Jsonb(resolution)),
            )
        )

    def resolver_calls_today(self) -> int:
        row = self._run(
            lambda c: c.execute(
                "SELECT calls FROM resolver_spend WHERE day = %s", (utc_day(),)
            ).fetchone()
        )
        return row["calls"] if row else 0

    def count_resolver_call(self) -> int:
        # The only non-idempotent write in this store, so _run's reconnect
        # retry can over-count by one. That direction is the safe one: an
        # over-count stops spending early, an under-count would not.
        return self._run(
            lambda c: c.execute(
                "INSERT INTO resolver_spend (day, calls) VALUES (%s, 1) "
                "ON CONFLICT(day) DO UPDATE SET calls = resolver_spend.calls + 1 "
                "RETURNING calls",
                (utc_day(),),
            ).fetchone()["calls"]
        )

    @staticmethod
    def _sugg_dict(row: dict) -> dict:
        # SQLite hands back created_at as TEXT; match it so the web layer's
        # json.dumps keeps working.
        d = dict(row)
        if d.get("created_at") is not None:
            d["created_at"] = d["created_at"].isoformat()
        return d

    # -- subscriptions ----------------------------------------------------

    def add_subscription(self, channel: str, target: str, filters: dict) -> tuple[int, str]:
        # token is unique, so the once-retry in _run cannot silently
        # double-insert; a replayed INSERT errors instead.
        token = uuid.uuid4().hex
        sid = self._run(
            lambda c: c.execute(
                "INSERT INTO subscriptions (channel, target, filters, token) "
                "VALUES (%s,%s,%s,%s) RETURNING id",
                (channel, target, Jsonb(filters), token),
            ).fetchone()["id"]
        )
        return sid, token

    def deactivate_by_token(self, token: str) -> bool:
        return self._run(
            lambda c: c.execute(
                "UPDATE subscriptions SET active = false WHERE token = %s",
                (token,),
            ).rowcount > 0
        )

    def active_subscriptions(self) -> list[dict]:
        rows = self._run(
            lambda c: c.execute(
                "SELECT * FROM subscriptions WHERE active ORDER BY id"
            ).fetchall()
        )
        return [self._sub_dict(r) for r in rows]

    @staticmethod
    def _sub_dict(row: dict) -> dict:
        # jsonb filters arrive already decoded; created_at gets the same
        # TEXT alignment _sugg_dict applies.
        d = dict(row)
        if d.get("created_at") is not None:
            d["created_at"] = d["created_at"].isoformat()
        return d

    # -- visits -----------------------------------------------------------

    def visit_salt(self) -> str:
        """Same contract as the SQLite store: one durable salt per UTC day,
        minted on first use, and minting one drops salts older than two days
        so those days' hashes stop being re-derivable."""
        day = utc_day()
        row = self._run(
            lambda c: c.execute(
                "SELECT salt FROM visit_salts WHERE day = %s", (day,)
            ).fetchone()
        )
        if row:
            return row["salt"]
        # DO UPDATE with the stored value is a no-op that still returns the
        # existing row; DO NOTHING would return nothing when a concurrent
        # request won the insert, and one day must resolve to one salt.
        salt = self._run(
            lambda c: c.execute(
                "INSERT INTO visit_salts (day, salt) VALUES (%s, %s) "
                "ON CONFLICT(day) DO UPDATE SET salt = visit_salts.salt "
                "RETURNING salt",
                (day, secrets.token_hex(32)),
            ).fetchone()["salt"]
        )
        self._run(
            lambda c: c.execute(
                "DELETE FROM visit_salts WHERE day < %s", (day - timedelta(days=2),)
            )
        )
        return salt

    def record_visit(self, page: str, visitor_hash: str, device: str) -> None:
        # ua is left out, not passed as None: the column holds history only.
        self._run(
            lambda c: c.execute(
                "INSERT INTO visits (page, visitor_hash, device) VALUES (%s,%s,%s)",
                (page, visitor_hash, device),
            )
        )

    def visit_count(self) -> int:
        return self._run(
            lambda c: c.execute("SELECT COUNT(*) AS n FROM visits").fetchone()["n"]
        )

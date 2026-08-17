"""SQLite persistence. One table: postings, keyed by dedupe id.

The store is the pipeline's memory. A detection whose id already exists merges
sources/locations instead of creating a new record, so re-polls are idempotent.
"""

from __future__ import annotations

import json
import os
import secrets
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .schema import Posting, RawDetection, Source, Status, suggestion_key


def utc_day() -> str:
    """Today in UTC. The resolver budget resets on this boundary, so it must
    not depend on the host's timezone."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")

_pg_store = None
_pg_lock = threading.Lock()

# Consent evidence, dropped from every subscription dict a store hands back.
# The columns exist so a CASL or GDPR challenge can be answered from SQL, and
# nothing in the fan-out, the sender or the API needs them, so they stop at
# this boundary and cannot reach a log line or a response body by accident.
CONSENT_COLUMNS = ("consent_ip", "verify_ip")


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
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    page         TEXT NOT NULL,
    ua           TEXT,   -- rows written before the hash; nothing writes it now
    visitor_hash TEXT,   -- sha256(day salt + ip + ua)[:16]; the ip is never stored
    device       TEXT,   -- 'mobile' | 'desktop' | 'bot'
    at           TEXT NOT NULL DEFAULT (datetime('now'))
);

-- One salt per UTC day, the input that makes visitor_hash unique per day and
-- unrelatable across days. Durable and not in-process because the web
-- container restarts on every deploy, and a fresh in-memory salt would split
-- one visitor into two identities mid-day. Salts older than two days are
-- deleted, after which those days' hashes are irreversible even by us.
CREATE TABLE IF NOT EXISTS visit_salts (
    day  TEXT PRIMARY KEY,          -- YYYY-MM-DD, UTC
    salt TEXT NOT NULL
);

-- Resolver output, keyed by the normalized company name (schema.norm_text).
-- A cache hit means no API call, so this table is the resolver's main spend
-- defense. Negative results are cached too: a name that resolves to nothing
-- must not be cheaper to re-trigger than one that resolves.
CREATE TABLE IF NOT EXISTS company_resolutions (
    key         TEXT PRIMARY KEY,   -- norm_text(company)
    company     TEXT NOT NULL,      -- as submitted, for display
    resolution  TEXT NOT NULL,      -- JSON, resolve.CompanyResolution
    resolved_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Durable per-UTC-day resolver call count. Durable and not in-process
-- because the poller restarts, and an in-memory counter would hand a fresh
-- budget to anyone who can make it crash.
CREATE TABLE IF NOT EXISTS resolver_spend (
    day   TEXT PRIMARY KEY,         -- YYYY-MM-DD, UTC
    calls INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS subscriptions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    channel      TEXT NOT NULL,               -- 'push' | 'email'
    target       TEXT NOT NULL,               -- push endpoint JSON or email address
    -- JSON: {"companies": [...], "company_keys": [...], "degrees": [...],
    -- "countries": [...]}. Every key is optional and every one present
    -- narrows; '{}' is the subscription to everything. See notify.build_filters.
    filters      TEXT NOT NULL DEFAULT '{}',
    token        TEXT NOT NULL UNIQUE,        -- opaque unsubscribe secret (uuid4 hex)
    verify_token TEXT,                        -- double opt-in secret; only ever inside the email
    verified     INTEGER NOT NULL DEFAULT 0,  -- email: the confirmation link was clicked
    -- Consent evidence, and the only address this project retains anywhere.
    -- Page views deliberately keep none (visits.visitor_hash is a
    -- daily-salted hash whose salt is deleted after two days), because
    -- counting visitors needs no identity. Proving an opt-in does: a CASL or
    -- GDPR challenge asks who asked for this mail, from where, and when they
    -- confirmed, and a hash answers none of it. Purpose-limited to that one
    -- answer: never logged, never in an API response, never joined to visits.
    consent_ip   TEXT,                        -- address the signup came from
    verified_at  TEXT,                        -- when the confirmation link was clicked
    verify_ip    TEXT,                        -- address that clicked it
    -- Set when SES accepts a confirmation, NULL when none was ever sent.
    -- That distinction is what the prune runs on: a signup made while the
    -- email channel is unconfigured waits here instead of expiring unmailed.
    confirmation_sent_at TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    active       INTEGER NOT NULL DEFAULT 1
);

-- The unique index on verify_token is created in _migrate, after the column
-- exists: this script also runs against files written before it did.

-- Durable per-UTC-day send counter, one row per subscriber per day. Durable
-- and not in-process for the same reason as resolver_spend: the poller
-- restarts on every deploy, and an in-memory counter would hand a fresh
-- allowance to anyone who can make it restart.
CREATE TABLE IF NOT EXISTS notify_sends (
    day             TEXT NOT NULL,           -- YYYY-MM-DD, UTC
    subscription_id INTEGER NOT NULL,
    sends           INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, subscription_id)
);
"""


class Store:
    def __init__(self, path: Path | str):
        self.cache_key = str(path)  # identifies the data a cache holds
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
        scols = {r[1] for r in self.conn.execute("PRAGMA table_info(subscriptions)")}
        if "verify_token" not in scols:
            self.conn.execute("ALTER TABLE subscriptions ADD COLUMN verify_token TEXT")
        if "verified" not in scols:
            self.conn.execute(
                "ALTER TABLE subscriptions ADD COLUMN verified INTEGER NOT NULL DEFAULT 0"
            )
        # Consent evidence and the confirmation stamp. Rows written before
        # these existed keep NULLs, which read correctly: no recorded address,
        # and no confirmation ever sent.
        for col in ("consent_ip", "verified_at", "verify_ip", "confirmation_sent_at"):
            if col not in scols:
                self.conn.execute(f"ALTER TABLE subscriptions ADD COLUMN {col} TEXT")
        # NULLs stay distinct under a SQLite unique index, so rows created
        # before double opt-in existed keep their empty verify_token.
        self.conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS subscriptions_verify_token_idx "
            "ON subscriptions (verify_token)"
        )
        vcols = {r[1] for r in self.conn.execute("PRAGMA table_info(visits)")}
        if "visitor_hash" not in vcols:
            self.conn.execute("ALTER TABLE visits ADD COLUMN visitor_hash TEXT")
        if "device" not in vcols:
            self.conn.execute("ALTER TABLE visits ADD COLUMN device TEXT")
        self.conn.commit()

    def get(self, posting_id: str) -> Posting | None:
        row = self.conn.execute(
            "SELECT * FROM postings WHERE id = ?", (posting_id,)
        ).fetchone()
        return self._to_posting(row) if row else None

    def upsert_detection(self, det: RawDetection) -> tuple[Posting | None, bool]:
        """Insert a detection or merge it into the existing record.

        Returns (posting, is_new); posting is None when the detection changed
        nothing, matching the Postgres backend, which cannot cheaply produce a
        row in that case. is_new drives the pipeline: only new postings enter
        verification.
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
        if not changed:
            return None, False
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

    def defer_suggestion(self, sid: int, result: str) -> None:
        """Write the note, keep the suggestion queued. Used when a spend cap
        stops work from happening yet: the visitor sees why, and the next
        cycle picks the row up again because status stays 'new'."""
        self.conn.execute(
            "UPDATE suggestions SET result = ? WHERE id = ?", (result[:500], sid)
        )
        self.conn.commit()

    def duplicate_suggestion(self, kind: str, value: str, within_days: int = 30) -> dict | None:
        """An open or recently resolved suggestion with the same identity.

        A repeat submission must never create a second unit of work: the
        submit endpoint is public and a company resolution costs money. The
        window scan is bounded and normalization happens in Python, so the
        key rule lives in one place (schema.suggestion_key).
        """
        rows = self.conn.execute(
            """SELECT * FROM suggestions
                WHERE kind = ?
                  AND (status = 'new' OR created_at >= datetime('now', ?))
                ORDER BY id DESC LIMIT 200""",
            (kind, f"-{int(within_days)} days"),
        ).fetchall()
        key = suggestion_key(kind, value)
        return next(
            (dict(r) for r in rows if suggestion_key(kind, r["value"]) == key), None
        )

    def cached_resolution(self, key: str, max_age_days: int) -> dict | None:
        row = self.conn.execute(
            "SELECT resolution FROM company_resolutions "
            "WHERE key = ? AND resolved_at >= datetime('now', ?)",
            (key, f"-{int(max_age_days)} days"),
        ).fetchone()
        return json.loads(row["resolution"]) if row else None

    def cache_resolution(self, key: str, company: str, resolution: dict) -> None:
        self.conn.execute(
            """INSERT INTO company_resolutions (key, company, resolution, resolved_at)
               VALUES (?,?,?,datetime('now'))
               ON CONFLICT(key) DO UPDATE SET
                 company=excluded.company,
                 resolution=excluded.resolution,
                 resolved_at=excluded.resolved_at""",
            (key, company, json.dumps(resolution)),
        )
        self.conn.commit()

    def resolver_calls_today(self) -> int:
        row = self.conn.execute(
            "SELECT calls FROM resolver_spend WHERE day = ?", (utc_day(),)
        ).fetchone()
        return row["calls"] if row else 0

    def count_resolver_call(self) -> int:
        self.conn.execute(
            "INSERT INTO resolver_spend (day, calls) VALUES (?, 1) "
            "ON CONFLICT(day) DO UPDATE SET calls = calls + 1",
            (utc_day(),),
        )
        self.conn.commit()
        return self.resolver_calls_today()

    def visit_salt(self) -> str:
        """Today's visitor-hash salt, minted on the day's first visit and
        reused until the UTC boundary. Creating one prunes salts older than
        two days: without the salt, that day's hashes cannot be re-derived
        from any IP, which is the point of storing a hash instead of one.
        """
        day = utc_day()
        row = self.conn.execute(
            "SELECT salt FROM visit_salts WHERE day = ?", (day,)
        ).fetchone()
        if row:
            return row["salt"]
        self.conn.execute(
            "INSERT INTO visit_salts (day, salt) VALUES (?, ?) "
            "ON CONFLICT(day) DO NOTHING",
            (day, secrets.token_hex(32)),
        )
        self.conn.execute(
            "DELETE FROM visit_salts WHERE day < date(?, '-2 days')", (day,)
        )
        self.conn.commit()
        # Re-read instead of trusting the value just generated: threaded
        # handlers race here, and one day must resolve to one salt or the
        # same visitor splits into two hashes.
        return self.conn.execute(
            "SELECT salt FROM visit_salts WHERE day = ?", (day,)
        ).fetchone()["salt"]

    def record_visit(self, page: str, visitor_hash: str, device: str) -> None:
        # ua is left out, not passed as None: the column holds history only.
        self.conn.execute(
            "INSERT INTO visits (page, visitor_hash, device) VALUES (?,?,?)",
            (page, visitor_hash, device),
        )
        self.conn.commit()

    def visit_count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM visits").fetchone()[0]

    def recent_suggestions(self, limit: int = 25) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM suggestions ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def add_subscription(
        self, channel: str, target: str, filters: dict, consent_ip: str | None = None,
    ) -> tuple[int, str, str]:
        """Create a subscription. Returns (id, token, verify_token).

        token is the unsubscribe secret the API hands back to the caller.
        verify_token is the double opt-in secret that only ever travels
        inside the confirmation email: an email row starts unverified and
        receives nothing until someone at that address clicks the link.

        consent_ip is where the request came from. It pairs with created_at
        as half the opt-in evidence; the confirmation click writes the other
        half (see verify_by_token).
        """
        token, verify_token = uuid.uuid4().hex, uuid.uuid4().hex
        cur = self.conn.execute(
            "INSERT INTO subscriptions "
            "(channel, target, filters, token, verify_token, consent_ip) "
            "VALUES (?,?,?,?,?,?)",
            (channel, target, json.dumps(filters), token, verify_token, consent_ip),
        )
        self.conn.commit()
        return cur.lastrowid, token, verify_token

    def deactivate_by_token(self, token: str) -> bool:
        cur = self.conn.execute(
            "UPDATE subscriptions SET active = 0 WHERE token = ?", (token,)
        )
        self.conn.commit()
        return cur.rowcount > 0

    def verify_by_token(self, verify_token: str, verify_ip: str | None = None) -> bool:
        """Flip the double opt-in flag. True when the token named a row.

        Idempotent: a second click on the same link still matches, so the
        confirmation page never looks broken to someone who double-tapped.
        The evidence columns take the first click and keep it, because the
        click that proves the opt-in is the one that flipped the flag.
        """
        cur = self.conn.execute(
            "UPDATE subscriptions SET verified = 1, "
            "verified_at = COALESCE(verified_at, datetime('now')), "
            "verify_ip = COALESCE(verify_ip, ?) WHERE verify_token = ?",
            (verify_ip, verify_token),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def mark_confirmation_sent(self, sub_id: int) -> None:
        """Stamp the row a confirmation was accepted for. The stamp starts
        the unconfirmed window, so a row that was never mailed never enters
        it. Keeps the first stamp: that is the mailing the window runs on."""
        self.conn.execute(
            "UPDATE subscriptions SET confirmation_sent_at = datetime('now') "
            "WHERE id = ? AND confirmation_sent_at IS NULL",
            (sub_id,),
        )
        self.conn.commit()

    def pending_confirmations(self, limit: int = 5) -> list[dict]:
        """Active email rows that were never mailed a confirmation.

        A null confirmation_sent_at is the ordinary state while the email
        channel is unconfigured: the subscription exists, nothing was sent,
        and nobody can be expected to click a link they never received.
        These wait here rather than expiring, and the poller mails them on
        the first cycle after sending works.
        """
        rows = self.conn.execute(
            "SELECT * FROM subscriptions WHERE active = 1 AND channel = 'email' "
            "AND verified = 0 AND confirmation_sent_at IS NULL "
            "ORDER BY id LIMIT ?",
            (int(limit),),
        ).fetchall()
        return [self._sub_dict(r) for r in rows]

    def prune_unverified(self, days: int = 7) -> int:
        """Delete email subscriptions that were mailed a confirmation and
        never clicked it. An address whose owner ignored the confirmation is
        not ours to keep, and the row is otherwise immortal because nothing
        else ever touches it.

        Scoped to rows that were actually mailed. A signup made while
        sending was unconfigured has no confirmation to ignore, so deleting
        it would discard a consent the visitor gave and we simply could not
        act on yet.
        """
        cur = self.conn.execute(
            "DELETE FROM subscriptions WHERE channel = 'email' AND verified = 0 "
            "AND confirmation_sent_at IS NOT NULL "
            "AND confirmation_sent_at < datetime('now', ?)",
            (f"-{int(days)} days",),
        )
        self.conn.commit()
        return cur.rowcount

    def notify_sends_today(self, sub_id: int) -> int:
        row = self.conn.execute(
            "SELECT sends FROM notify_sends WHERE day = ? AND subscription_id = ?",
            (utc_day(), sub_id),
        ).fetchone()
        return row["sends"] if row else 0

    def count_notify_send(self, sub_id: int) -> int:
        self.conn.execute(
            "INSERT INTO notify_sends (day, subscription_id, sends) VALUES (?,?,1) "
            "ON CONFLICT(day, subscription_id) DO UPDATE SET sends = sends + 1",
            (utc_day(), sub_id),
        )
        self.conn.commit()
        return self.notify_sends_today(sub_id)

    def active_subscriptions(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM subscriptions WHERE active = 1 ORDER BY id"
        ).fetchall()
        return [self._sub_dict(r) for r in rows]

    @staticmethod
    def _sub_dict(row: sqlite3.Row) -> dict:
        d = dict(row)
        d["filters"] = json.loads(d["filters"])
        # SQLite has no boolean type; match the Postgres store so the
        # fan-out's opt-in check reads the same on both backends.
        d["verified"] = bool(d["verified"])
        for col in CONSENT_COLUMNS:
            d.pop(col, None)
        return d

    def data_version(self) -> str | None:
        """Change token for read caches; see PostgresStore.data_version.

        None means "cannot tell, rebuild": SQLite reads are local and free,
        so there is nothing to protect against here, and the caller's TTL
        still bounds the work.
        """
        return None

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

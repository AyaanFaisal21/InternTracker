"""Session persistence and the durable spend counters.

One interface, one in-memory implementation. The Postgres implementation is
deliberately absent: adding tables to the live Supabase schema is a
migration, and the migration is deferred until the endpoints exist. The
table shape it has to satisfy is written out in docs/interview-agent.md, and
it is exactly this protocol.

The spend counters are separate from the session rows for the same reason
intake keeps `resolver_spend` separate from `suggestions`: a budget that can
be reset by deleting rows is not a budget.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol

from .schema import Session


def utc_day() -> str:
    """Today in UTC. The daily budget resets on this boundary, so it must not
    depend on the host's timezone. Copied from intake/store.py rather than
    imported, to keep the packages independent."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


class SessionStore(Protocol):
    """What the interview loop needs from storage.

    Sessions are values, not handles: `get` returns a snapshot and a mutation
    is only durable once `save` is called. That is how the Postgres backend
    will behave, so the in-memory one behaves that way too rather than
    letting local code accidentally depend on shared references.
    """

    def create(self, session: Session) -> None: ...

    def get(self, session_id: str) -> Session | None: ...

    def save(self, session: Session) -> None: ...

    def sessions_today(self, user_key: str) -> int: ...

    def tokens_today(self) -> int: ...

    def count_tokens(self, tokens: int) -> None: ...


class MemorySessionStore:
    """In-process store. Backs the tests and a local run.

    Nothing here survives a restart, which is fine for the two things it is
    for and disqualifying for anything else: the daily budget in particular
    only means something once it is in Postgres.
    """

    def __init__(self) -> None:
        self.sessions: dict[str, Session] = {}
        self.spend: dict[str, int] = {}

    def create(self, session: Session) -> None:
        self.sessions[session.id] = session.model_copy(deep=True)

    def get(self, session_id: str) -> Session | None:
        found = self.sessions.get(session_id)
        return found.model_copy(deep=True) if found is not None else None

    def save(self, session: Session) -> None:
        self.sessions[session.id] = session.model_copy(deep=True)

    def sessions_today(self, user_key: str) -> int:
        day = utc_day()
        return sum(
            1
            for s in self.sessions.values()
            if s.user_key == user_key and s.created_at.strftime("%Y-%m-%d") == day
        )

    def tokens_today(self) -> int:
        return self.spend.get(utc_day(), 0)

    def count_tokens(self, tokens: int) -> None:
        day = utc_day()
        self.spend[day] = self.spend.get(day, 0) + max(0, tokens)

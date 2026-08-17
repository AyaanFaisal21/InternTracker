"""Spend guard in front of the interview loop.

Same job as intake.resolve.GuardedResolver and the same shape: the thing that
calls the model stays a thin client, and every limit lives here where it can
be read in one screen.

Three layers, cheapest check first:

1. Per-user session cap for the UTC day. Friction, not security: the user key
   is a daily-salted hash of address and user agent, so it costs a determined
   person one browser profile to reset. It is here to stop the ordinary case,
   which is one bored person opening twenty sessions.
2. Durable daily token budget. This is the real ceiling, the one that holds
   when every other layer is bypassed. A session is admitted only when a
   whole normal session still fits in what is left, so sessions already open
   can finish and be scored instead of dying mid-interview.
3. Per-session token ceiling. Bounds one runaway conversation.

The ceiling is checked before a turn, not during one, so the turn that
crosses it completes. A ceiling bounds new work; it is not an exact stop.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .config import InterviewSettings
from .schema import Session, Speaker, Usage

log = logging.getLogger("interview")


@dataclass(frozen=True)
class Decision:
    """A budget answer. `reason` is student-facing text when refused, so it
    says what happens next and never names a token count."""

    allowed: bool
    reason: str = ""


ALLOW = Decision(True)


class BudgetGuard:
    def __init__(self, store, settings: InterviewSettings):
        self.store = store
        self.settings = settings

    def begin_session(self, user_key: str) -> Decision:
        s = self.settings
        if self.store.sessions_today(user_key) >= s.sessions_per_user_per_day:
            return Decision(
                False,
                f"You have used all {s.sessions_per_user_per_day} practice "
                "sessions for today. They reset at midnight UTC.",
            )
        left = s.daily_token_budget - self.store.tokens_today()
        if left < s.session_token_estimate:
            log.warning("INTERVIEW-BUDGET daily budget spent, refusing new sessions")
            return Decision(
                False,
                "Practice is closed for today. Sessions open again at "
                "midnight UTC.",
            )
        return ALLOW

    def before_turn(self, session: Session) -> Decision:
        s = self.settings
        if len(session.transcript.by(Speaker.CANDIDATE)) > s.max_turns:
            return Decision(False, "turn limit reached")
        if session.usage.total >= s.session_token_ceiling:
            log.warning("INTERVIEW-BUDGET session %s hit its token ceiling", session.id)
            return Decision(False, "session token ceiling reached")
        if self.store.tokens_today() >= s.daily_token_budget:
            log.warning("INTERVIEW-BUDGET daily budget spent mid-session")
            return Decision(False, "daily token budget spent")
        return ALLOW

    def record(self, session: Session, usage: Usage) -> None:
        """Bill one turn against the session and against the day.

        Called after the turn, because the counts only exist then. A turn
        that errored partway still bills for what it streamed, which is why
        the caller records whatever usage it has rather than skipping on
        failure.
        """
        session.add_usage(usage)
        if usage.total:
            self.store.count_tokens(usage.total)

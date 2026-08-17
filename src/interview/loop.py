"""The turn loop.

One class holds the whole flow: open a session, take a spoken turn, stream
the interviewer back, close, score. The HTTP handler that will sit in front
of this owns request parsing and nothing else, which is what keeps the
product testable without a server.

Turn based on purpose. The student speaks, the browser decides they stopped,
the transcript arrives here as one string, and the reply streams back. Full
duplex changes the transport and the endpointer and nothing in this file.
See docs/interview-agent.md.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .budget import BudgetGuard
from .config import InterviewSettings
from .interviewer import (
    FALLBACK_QUESTION,
    Interviewer,
    Reply,
    closing_line,
    opening_line,
    stream_words,
)
from .problems import get_problem
from .rubric import Report
from .schema import Session, SessionState, Speaker
from .scorer import Scorer
from .store import SessionStore

log = logging.getLogger("interview")


@dataclass(frozen=True)
class Started:
    """Outcome of an attempt to open a session. `refused` carries
    student-facing text and is empty when the session opened."""

    session: Session | None = None
    refused: str = ""


class InterviewLoop:
    def __init__(
        self,
        store: SessionStore,
        interviewer: Interviewer,
        scorer: Scorer,
        settings: InterviewSettings,
        guard: BudgetGuard | None = None,
    ):
        self.store = store
        self.interviewer = interviewer
        self.scorer = scorer
        self.settings = settings
        self.guard = guard or BudgetGuard(store, settings)

    def start(self, user_key: str, problem_id: str) -> Started:
        """Open a session and speak the opening line.

        The opening line is local text, so opening a session costs nothing.
        That matters because the page can be opened by anyone and most
        openings never become interviews.
        """
        problem = get_problem(problem_id)
        if problem is None:
            return Started(refused="That problem does not exist.")
        decision = self.guard.begin_session(user_key)
        if not decision.allowed:
            return Started(refused=decision.reason)

        session = Session.open(user_key, problem_id)
        session.begin(opening_line(problem))
        self.store.create(session)
        return Started(session=session)

    def turn(self, session: Session, said: str) -> Reply:
        """Record what the student said and stream the interviewer's answer.

        The caller must drain the returned Reply. Draining it is what appends
        the interviewer turn, bills the spend, and saves, so nothing is
        written for text the student never received.
        """
        problem = get_problem(session.problem_id)  # start() proved it exists
        session.say(Speaker.CANDIDATE, said, max_chars=self.settings.max_candidate_chars)
        # Saved before the model is called, not after. What the student said
        # is the expensive thing to lose: it cannot be recovered from a
        # retry, and a crash mid-generation should leave a record of what we
        # were about to be billed for.
        self.store.save(session)

        decision = self.guard.before_turn(session)
        if not decision.allowed:
            log.info("INTERVIEW closing session %s: %s", session.id, decision.reason)
            return self._closing_reply(session)

        reply = self.interviewer.reply(problem, session.transcript)
        return reply.then(lambda r: self._close_turn(session, r))

    def end(self, session: Session) -> Session:
        """Stop the interview. Idempotent for a session already ended."""
        if session.state is not SessionState.ENDED:
            session.end()
            self.store.save(session)
        return session

    def score(self, session: Session) -> Report | None:
        """Run the scoring pass over an ended session.

        None means the pass failed and the session stays at `ended` for a
        later retry. A session is only marked scored when a real report
        exists.
        """
        if session.state is not SessionState.ENDED:
            log.warning(
                "INTERVIEW refusing to score session %s in state %s",
                session.id, session.state.value,
            )
            return None
        problem = get_problem(session.problem_id)
        report = self.scorer.score(problem, session.transcript)
        if report is None:
            return None
        session.attach(report)
        self.store.save(session)
        return report

    def _close_turn(self, session: Session, reply: Reply) -> None:
        self.guard.record(session, reply.usage)
        text = reply.text.strip()
        if reply.refused or not text:
            # A refusal carries no content, so there is nothing to show. Stay
            # in role and hand the turn back rather than surfacing an error.
            log.warning("INTERVIEW empty turn on session %s, using the fallback", session.id)
            text = FALLBACK_QUESTION
        session.say(Speaker.INTERVIEWER, text)
        self.store.save(session)

    def _closing_reply(self, session: Session) -> Reply:
        """The last turn when a limit stopped the interview.

        The student sees an interview ending. The reason it ended is an
        operator's problem and is already in the log.
        """
        def finish(reply: Reply) -> None:
            session.say(Speaker.INTERVIEWER, reply.text)
            session.end()
            self.store.save(session)

        return Reply(stream_words(closing_line())).then(finish)

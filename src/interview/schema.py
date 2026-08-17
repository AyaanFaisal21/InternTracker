"""Data models for one interview session.

Transcript is the product. The audio is a convenience the student may keep,
the model call is an implementation detail, but the ordered list of who said
what is what gets scored, stored, and shown back. Everything else here hangs
off it.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field

from .rubric import Report


class Speaker(str, Enum):
    INTERVIEWER = "interviewer"
    CANDIDATE = "candidate"


class SessionState(str, Enum):
    CREATED = "created"          # opened, nothing said yet
    IN_PROGRESS = "in_progress"  # the interview is running
    ENDED = "ended"              # no more turns, not yet scored
    SCORED = "scored"            # a report is attached


# The only moves allowed. CREATED -> ENDED covers the student who opens the
# page and leaves; there is nothing to score, and the row still records that
# the session happened.
TRANSITIONS = frozenset({
    (SessionState.CREATED, SessionState.IN_PROGRESS),
    (SessionState.CREATED, SessionState.ENDED),
    (SessionState.IN_PROGRESS, SessionState.ENDED),
    (SessionState.ENDED, SessionState.SCORED),
})


class LifecycleError(RuntimeError):
    """An illegal session transition. Always a caller bug, never a model
    outcome, so this one raises rather than being contained."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Turn(BaseModel):
    speaker: Speaker
    text: str
    at: datetime


class Problem(BaseModel):
    """One algorithm problem plus what an interviewer would push on.

    `optimal` and `pressure_points` are interviewer-side only. They reach the
    model inside the system block, never the student, and the prompt forbids
    stating them.
    """

    id: str
    title: str
    difficulty: str                                       # easy | medium | hard
    statement: str
    constraints: list[str] = Field(default_factory=list)
    examples: list[str] = Field(default_factory=list)
    optimal: str | None = None                            # e.g. "O(n) time, O(n) space"
    pressure_points: list[str] = Field(default_factory=list)  # questions worth asking


class Usage(BaseModel):
    """Token counts as the API reports them.

    `total` sums all four, including cache reads, which bill at a tenth of
    the input rate. Metering them at face value makes the session ceiling
    stricter than the money it stands for. That is the safe direction for a
    ceiling and it keeps the number one an operator can reason about without
    a price table.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    @property
    def total(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_tokens
            + self.cache_write_tokens
        )

    def plus(self, other: "Usage") -> "Usage":
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
            cache_write_tokens=self.cache_write_tokens + other.cache_write_tokens,
        )


class Transcript(BaseModel):
    """The ordered turns of one session."""

    turns: list[Turn] = Field(default_factory=list)

    def __len__(self) -> int:
        return len(self.turns)

    def add(self, speaker: Speaker, text: str, max_chars: int = 0) -> Turn:
        """Append one turn. `max_chars` clamps a spoken turn: speech
        recognition can run away on a hot microphone, and an unbounded turn
        is an unbounded prompt."""
        body = " ".join((text or "").split())
        if max_chars and len(body) > max_chars:
            body = body[:max_chars].rstrip()
        turn = Turn(speaker=speaker, text=body, at=_now())
        self.turns.append(turn)
        return turn

    def by(self, speaker: Speaker) -> list[Turn]:
        return [t for t in self.turns if t.speaker is speaker]

    def as_messages(self) -> list[dict]:
        """The Anthropic `messages` array for the next interviewer turn.

        Leading interviewer turns are dropped: the API requires the first
        message to be `user`, and the opening line is fixed text that already
        travels in the cached system block. Everything after that maps one
        turn to one message, appended in order, which is what keeps the
        cached prefix byte-stable from turn to turn.
        """
        turns = list(self.turns)
        while turns and turns[0].speaker is Speaker.INTERVIEWER:
            turns.pop(0)
        return [
            {
                "role": "assistant" if t.speaker is Speaker.INTERVIEWER else "user",
                "content": t.text,
            }
            for t in turns
        ]

    def as_text(self, max_chars: int = 0) -> str:
        """Flat rendering for the scoring pass.

        Over the limit, turns are dropped from the middle rather than the
        end. The opening exchange carries the framing score and the closing
        exchanges carry the response-to-challenge score, so both ends must
        survive; the repetitive middle is what a long session has too much of.
        """
        lines = [f"{t.speaker.value}: {t.text}" for t in self.turns]
        body = "\n".join(lines)
        if not max_chars or len(body) <= max_chars:
            return body

        # The opening turn always survives, even when it alone is over the
        # limit: a transcript with no beginning cannot be scored for framing.
        head, tail = lines[:1], []
        used = len(head[0]) + 1
        for line in reversed(lines[1:]):
            if used + len(line) + 1 > max_chars:
                break
            tail.insert(0, line)
            used += len(line) + 1
        dropped = len(lines) - len(head) - len(tail)
        marker = f"[... {dropped} turns omitted ...]"
        return "\n".join([*head, marker, *tail])


class Session(BaseModel):
    """One interview, from the click that opens it to the report."""

    id: str
    user_key: str            # opaque, per-day; see docs/interview-agent.md
    problem_id: str
    state: SessionState = SessionState.CREATED
    created_at: datetime = Field(default_factory=_now)
    started_at: datetime | None = None
    ended_at: datetime | None = None
    scored_at: datetime | None = None
    transcript: Transcript = Field(default_factory=Transcript)
    usage: Usage = Field(default_factory=Usage)
    recording_key: str | None = None   # object key when the student saved audio
    report: Report | None = None

    @classmethod
    def open(cls, user_key: str, problem_id: str) -> "Session":
        return cls(id=secrets.token_hex(8), user_key=user_key, problem_id=problem_id)

    def _move(self, to: SessionState) -> None:
        if (self.state, to) not in TRANSITIONS:
            raise LifecycleError(f"cannot move a session from {self.state.value} to {to.value}")
        self.state = to

    def begin(self, opening: str) -> Turn:
        """Start the interview with the interviewer's opening line."""
        self._move(SessionState.IN_PROGRESS)
        self.started_at = _now()
        return self.transcript.add(Speaker.INTERVIEWER, opening)

    def say(self, speaker: Speaker, text: str, max_chars: int = 0) -> Turn:
        if self.state is not SessionState.IN_PROGRESS:
            raise LifecycleError(f"cannot speak into a {self.state.value} session")
        return self.transcript.add(speaker, text, max_chars=max_chars)

    def end(self) -> None:
        self._move(SessionState.ENDED)
        self.ended_at = _now()

    def attach(self, report: Report) -> None:
        self._move(SessionState.SCORED)
        self.report = report
        self.scored_at = _now()

    def add_usage(self, usage: Usage) -> None:
        self.usage = self.usage.plus(usage)

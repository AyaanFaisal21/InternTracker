"""Live simulated technical interview.

A student picks an algorithm problem, explains their solution out loud, and
an agent pushes back the way a human interviewer does. Afterwards a second
pass scores the explanation against a fixed rubric.

This package is deliberately separate from `intake`. The two products share
a node and a domain and nothing else: no imports cross the boundary, and the
few small helpers that would be shared (env parsing, the UTC day key) are
copied rather than imported so either package can move without the other.

Design notes and the deferred Postgres migration live in
docs/interview-agent.md.
"""

from .config import InterviewSettings, load_interview_settings
from .interviewer import (
    Interviewer,
    LogInterviewer,
    Reply,
    build_interviewer,
    closing_line,
    opening_line,
)
from .loop import InterviewLoop, Started
from .problems import PROBLEMS, get_problem
from .rubric import DIMENSIONS, DimensionScore, Report
from .schema import (
    LifecycleError,
    Problem,
    Session,
    SessionState,
    Speaker,
    Transcript,
    Turn,
    Usage,
)
from .scorer import Scorer, StubScorer, build_scorer
from .store import MemorySessionStore, SessionStore

__all__ = [
    "DIMENSIONS",
    "DimensionScore",
    "InterviewLoop",
    "InterviewSettings",
    "Interviewer",
    "LifecycleError",
    "LogInterviewer",
    "MemorySessionStore",
    "PROBLEMS",
    "Problem",
    "Reply",
    "Report",
    "Scorer",
    "Session",
    "SessionState",
    "SessionStore",
    "Speaker",
    "Started",
    "StubScorer",
    "Transcript",
    "Turn",
    "Usage",
    "build_interviewer",
    "build_scorer",
    "closing_line",
    "get_problem",
    "load_interview_settings",
    "opening_line",
]

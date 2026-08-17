"""Runtime configuration for the interview product.

Every number here is either a latency knob or a spend knob, and both are the
first things an operator reaches for when a session feels slow or a bill
surprises them, so all of them read from the environment.

The env helper is copied from intake/config.py rather than imported. The
duplication is four lines and it keeps this package free of any dependency
on the intake package.
"""

from __future__ import annotations

import os

from pydantic import BaseModel


def _env_int(name: str, default: int) -> int:
    """Env override for a numeric knob. A malformed value falls back to the
    default rather than failing open."""
    try:
        return max(0, int(os.environ[name]))
    except (KeyError, ValueError):
        return default


def _env_str(name: str, default: str) -> str:
    """Env override for a string knob. Blank is the same as unset, so a stray
    `INTERVIEW_EFFORT=` line cannot send an empty effort to the API."""
    return (os.environ.get(name) or "").strip() or default


# Spend ceilings. See docs/interview-agent.md for how these were sized: a
# 20 minute session bills roughly 57k tokens, so the per-session ceiling is
# about four times a normal session and the daily budget admits about
# sixteen.
DAILY_TOKEN_BUDGET = 1_000_000        # all sessions, per UTC day
SESSION_TOKEN_CEILING = 250_000       # hard stop for one runaway session
SESSION_TOKEN_ESTIMATE = 60_000       # what a normal session costs; the
# admission reserve, so a session is only opened when a whole normal session
# still fits inside the remaining daily budget
SESSIONS_PER_USER_PER_DAY = 3


class InterviewSettings(BaseModel):
    """One process's view of the interview product."""

    # Interviewer turn. Latency dominates: a student waiting ten seconds for
    # a question stops treating this as an interview. Effort stays low and
    # max_tokens stays small, because the model owes one question, not an
    # essay. max_tokens caps thinking plus reply, and thinking is on by
    # default on this model.
    interviewer_model: str = "claude-opus-5"
    interviewer_effort: str = "low"          # low | medium
    interviewer_max_tokens: int = 1024

    # Post-session scoring. One call, nobody is waiting on it, and the output
    # is the product, so this pass gets real depth.
    scorer_model: str = "claude-opus-5"
    scorer_effort: str = "high"              # high | xhigh
    scorer_max_tokens: int = 8192

    # Conversation shape.
    max_turns: int = 40                      # candidate turns before the close
    max_candidate_chars: int = 4000          # one spoken turn, clamped
    max_transcript_chars: int = 40_000       # what the scorer reads

    # Spend.
    session_token_ceiling: int = SESSION_TOKEN_CEILING
    session_token_estimate: int = SESSION_TOKEN_ESTIMATE
    daily_token_budget: int = DAILY_TOKEN_BUDGET
    sessions_per_user_per_day: int = SESSIONS_PER_USER_PER_DAY


def load_interview_settings() -> InterviewSettings:
    """Read the environment. Called per process, not per import, so a
    container that gets a new ceiling on redeploy needs no code change."""
    return InterviewSettings(
        interviewer_effort=_env_str("INTERVIEW_EFFORT", "low"),
        scorer_effort=_env_str("INTERVIEW_SCORER_EFFORT", "high"),
        max_turns=_env_int("INTERVIEW_MAX_TURNS", 40),
        session_token_ceiling=_env_int(
            "INTERVIEW_SESSION_TOKENS", SESSION_TOKEN_CEILING
        ),
        session_token_estimate=_env_int(
            "INTERVIEW_SESSION_ESTIMATE", SESSION_TOKEN_ESTIMATE
        ),
        daily_token_budget=_env_int("INTERVIEW_DAILY_TOKENS", DAILY_TOKEN_BUDGET),
        sessions_per_user_per_day=_env_int(
            "INTERVIEW_SESSIONS_PER_USER", SESSIONS_PER_USER_PER_DAY
        ),
    )

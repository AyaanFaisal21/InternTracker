"""The turn loop end to end on the stub interviewer.

Everything real except the model: a real session, a real transcript, real
budget guards, a real store. No network, no API key, and neither
ClaudeInterviewer nor ClaudeScorer is ever called.
"""

import pytest

from interview.config import InterviewSettings, load_interview_settings
from interview.interviewer import (
    LogInterviewer,
    Reply,
    build_interviewer,
    closing_line,
    opening_line,
    stream_words,
)
from interview.loop import InterviewLoop
from interview.problems import PROBLEMS, get_problem
from interview.rubric import DIMENSIONS
from interview.schema import SessionState, Speaker
from interview.scorer import StubScorer, build_scorer
from interview.store import MemorySessionStore


def settings(**over) -> InterviewSettings:
    return InterviewSettings(**over)


def build(**over):
    store = MemorySessionStore()
    loop = InterviewLoop(store, LogInterviewer(), StubScorer(), settings(**over))
    return store, loop


def running(loop, store, user="user-a", problem="two-sum"):
    started = loop.start(user, problem)
    assert started.session is not None, started.refused
    return started.session


# -- opening --------------------------------------------------------------


def test_starting_opens_a_session_and_speaks_first():
    store, loop = build()
    session = running(loop, store)

    assert session.state is SessionState.IN_PROGRESS
    assert len(session.transcript) == 1
    opening = session.transcript.turns[0]
    assert opening.speaker is Speaker.INTERVIEWER
    assert opening.text == opening_line(get_problem("two-sum"))
    assert "Two Sum" in opening.text
    # Persisted before the student says anything.
    assert store.get(session.id).state is SessionState.IN_PROGRESS


def test_an_unknown_problem_is_refused_without_a_session():
    store, loop = build()
    started = loop.start("user-a", "traveling-salesman")
    assert started.session is None
    assert "does not exist" in started.refused
    assert store.sessions == {}


def test_a_refused_session_never_reaches_the_store():
    store, loop = build(sessions_per_user_per_day=1)
    running(loop, store)
    started = loop.start("user-a", "two-sum")
    assert started.session is None and "practice session" in started.refused
    assert len(store.sessions) == 1


# -- turns ----------------------------------------------------------------


def test_a_turn_streams_and_records_both_sides():
    store, loop = build()
    session = running(loop, store)

    reply = loop.turn(session, "  I would use   a hash map  ")
    # The candidate turn lands immediately; the interviewer turn does not
    # exist until the reply is drained.
    assert len(session.transcript) == 2
    assert session.transcript.turns[1].text == "I would use a hash map"

    chunks = list(reply)
    assert len(chunks) > 1                       # it really streamed
    assert "".join(chunks) == reply.text
    assert reply.done is True and reply.refused is False

    assert len(session.transcript) == 3
    last = session.transcript.turns[2]
    assert last.speaker is Speaker.INTERVIEWER
    assert last.text == reply.text
    assert store.get(session.id).transcript.turns[2].text == reply.text


def test_an_undrained_reply_keeps_the_student_turn_and_nothing_else():
    """A student who closes the tab mid-answer keeps what they said and does
    not get a transcript turn they never received."""
    store, loop = build()
    session = running(loop, store)

    loop.turn(session, "I would sort it")
    stored = store.get(session.id)
    assert len(stored.transcript) == 2
    assert stored.transcript.turns[1].speaker is Speaker.CANDIDATE
    assert stored.usage.total == 0


def test_the_stub_walks_the_problem_pressure_points():
    store, loop = build()
    session = running(loop, store)

    asked = [loop.turn(session, f"answer {i}").drain() for i in range(3)]
    assert len(set(asked)) == 3
    assert set(asked).issubset(set(get_problem("two-sum").pressure_points))


def test_messages_stay_a_valid_conversation_after_several_turns():
    store, loop = build()
    session = running(loop, store)
    for i in range(3):
        loop.turn(session, f"answer {i}").drain()

    messages = session.transcript.as_messages()
    assert messages[0]["role"] == "user"
    assert messages[-1]["role"] == "assistant"
    assert [m["role"] for m in messages] == ["user", "assistant"] * 3


def test_hitting_a_limit_closes_the_interview_in_role():
    store, loop = build(max_turns=1)
    session = running(loop, store)

    loop.turn(session, "first answer").drain()
    assert session.state is SessionState.IN_PROGRESS

    final = loop.turn(session, "second answer")
    text = final.drain()
    # The student sees an interview ending, not a token ceiling.
    assert text == closing_line()
    assert "token" not in text.lower() and "budget" not in text.lower()
    assert session.state is SessionState.ENDED
    assert store.get(session.id).state is SessionState.ENDED


# -- ending and scoring ---------------------------------------------------


def test_ending_then_scoring_reaches_scored_and_persists_the_report():
    store, loop = build()
    session = running(loop, store)
    loop.turn(session, "I would use a hash map").drain()

    loop.end(session)
    assert session.state is SessionState.ENDED

    report = loop.score(session)
    assert report is not None
    assert {s.dimension for s in report.scores} == set(DIMENSIONS)
    assert session.state is SessionState.SCORED

    stored = store.get(session.id)
    assert stored.state is SessionState.SCORED
    assert stored.report is not None
    assert stored.report.total == report.total


def test_ending_twice_is_a_no_op():
    store, loop = build()
    session = running(loop, store)
    loop.end(session)
    first = session.ended_at
    loop.end(session)
    assert session.ended_at == first


def test_a_live_session_is_not_scored():
    store, loop = build()
    session = running(loop, store)
    assert loop.score(session) is None
    assert session.state is SessionState.IN_PROGRESS


class BrokenScorer:
    """Stands in for a refusal, a payload that failed validation, or a
    transport error. All three leave the session ready for a retry."""

    def score(self, problem, transcript):
        return None


def test_a_failed_scoring_pass_leaves_the_session_ended_for_retry():
    store = MemorySessionStore()
    loop = InterviewLoop(store, LogInterviewer(), BrokenScorer(), settings())
    session = running(loop, store)
    loop.end(session)

    assert loop.score(session) is None
    assert session.state is SessionState.ENDED
    assert store.get(session.id).report is None

    # Same session, working scorer, no lost work.
    InterviewLoop(store, LogInterviewer(), StubScorer(), settings()).score(session)
    assert session.state is SessionState.SCORED


# -- streaming seam -------------------------------------------------------


def test_stream_words_reassembles_exactly():
    for text in ("one", "a b c", "That is our time. Thanks.", ""):
        assert "".join(stream_words(text)) == text


def test_a_reply_runs_its_callbacks_once_after_the_last_chunk():
    seen = []
    reply = Reply(stream_words("a b c")).then(lambda r: seen.append(r.text))
    for chunk in reply:
        assert seen == []          # nothing fires mid-stream
        assert chunk
    assert seen == ["a b c"]


# -- wiring ---------------------------------------------------------------


def test_the_problem_set_is_well_formed():
    for problem_id, problem in PROBLEMS.items():
        assert problem.id == problem_id
        assert problem.difficulty in ("easy", "medium", "hard")
        assert problem.statement and problem.examples and problem.constraints
        assert len(problem.pressure_points) >= 3
        assert problem.optimal
    assert get_problem("nope") is None


def test_without_a_credential_the_stubs_are_the_default(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("INTERVIEW_AGENT", raising=False)
    assert isinstance(build_interviewer(settings()), LogInterviewer)
    assert isinstance(build_scorer(settings()), StubScorer)


def test_the_off_switch_beats_a_credential(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("INTERVIEW_AGENT", "off")
    assert isinstance(build_interviewer(settings()), LogInterviewer)
    assert isinstance(build_scorer(settings()), StubScorer)


def test_a_credential_selects_the_live_agents(monkeypatch):
    """Construction only. The client is built on first use, so nothing here
    needs a real key and no request is ever made."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.delenv("INTERVIEW_AGENT", raising=False)
    assert type(build_interviewer(settings())).__name__ == "ClaudeInterviewer"
    assert type(build_scorer(settings())).__name__ == "ClaudeScorer"


def test_settings_default_to_the_documented_model_and_efforts():
    s = settings()
    assert s.interviewer_model == "claude-opus-5" and s.scorer_model == "claude-opus-5"
    # Latency dominates a turn; the report is the product.
    assert s.interviewer_effort == "low" and s.scorer_effort == "high"


@pytest.mark.parametrize(
    "name,field,value,expected",
    [
        ("INTERVIEW_EFFORT", "interviewer_effort", "medium", "medium"),
        ("INTERVIEW_DAILY_TOKENS", "daily_token_budget", "250000", 250_000),
        ("INTERVIEW_SESSIONS_PER_USER", "sessions_per_user_per_day", "9", 9),
        ("INTERVIEW_MAX_TURNS", "max_turns", "notanumber", 40),
        ("INTERVIEW_EFFORT", "interviewer_effort", "   ", "low"),
    ],
)
def test_spend_knobs_read_the_environment(monkeypatch, name, field, value, expected):
    monkeypatch.setenv(name, value)
    assert getattr(load_interview_settings(), field) == expected

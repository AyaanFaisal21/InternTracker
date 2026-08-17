"""Session lifecycle and transcript assembly.

No network, no API key, no model. Everything here is pure data, which is the
point: the transcript is the product, so its shape is worth pinning before
any of it reaches a request body.
"""

from datetime import datetime, timedelta, timezone

import pytest

from interview.rubric import DIMENSIONS, DimensionScore, Report
from interview.schema import (
    LifecycleError,
    Session,
    SessionState,
    Speaker,
    Transcript,
    Usage,
)


def report() -> Report:
    return Report(
        scores=[
            DimensionScore(dimension=d, level=3, evidence="said it", improvement="do it")
            for d in DIMENSIONS
        ],
        summary="fine",
        next_steps=["practice"],
    )


# -- lifecycle ------------------------------------------------------------


def test_a_new_session_is_created_and_silent():
    s = Session.open("user-a", "two-sum")
    assert s.state is SessionState.CREATED
    assert len(s.transcript) == 0
    assert s.started_at is None and s.ended_at is None and s.report is None
    assert len(s.id) == 16


def test_begin_starts_the_interview_and_records_the_opening():
    s = Session.open("user-a", "two-sum")
    s.begin("Today's problem is Two Sum.")
    assert s.state is SessionState.IN_PROGRESS
    assert s.started_at is not None
    assert [t.speaker for t in s.transcript.turns] == [Speaker.INTERVIEWER]


def test_the_whole_happy_path_walks_created_to_scored():
    s = Session.open("user-a", "two-sum")
    s.begin("open")
    s.say(Speaker.CANDIDATE, "I would sort it")
    s.say(Speaker.INTERVIEWER, "why sort")
    s.end()
    assert s.state is SessionState.ENDED and s.ended_at is not None
    s.attach(report())
    assert s.state is SessionState.SCORED
    assert s.report is not None and s.scored_at is not None


def test_an_abandoned_session_can_end_without_starting():
    s = Session.open("user-a", "two-sum")
    s.end()
    assert s.state is SessionState.ENDED
    assert len(s.transcript) == 0


@pytest.mark.parametrize(
    "moves",
    [
        ["begin", "begin"],          # already running
        ["begin", "attach"],         # scoring a live session
        ["attach"],                  # scoring a session that never ran
        ["begin", "end", "end"],     # ending twice
        ["begin", "end", "attach", "attach"],
    ],
)
def test_illegal_transitions_raise(moves):
    s = Session.open("user-a", "two-sum")
    with pytest.raises(LifecycleError):
        for move in moves:
            if move == "begin":
                s.begin("open")
            elif move == "end":
                s.end()
            else:
                s.attach(report())


@pytest.mark.parametrize("state", ["created", "ended"])
def test_nobody_speaks_into_a_session_that_is_not_running(state):
    s = Session.open("user-a", "two-sum")
    if state == "ended":
        s.end()
    with pytest.raises(LifecycleError):
        s.say(Speaker.CANDIDATE, "hello")


def test_usage_accumulates_across_turns():
    s = Session.open("user-a", "two-sum")
    s.add_usage(Usage(input_tokens=100, output_tokens=20))
    s.add_usage(Usage(input_tokens=150, output_tokens=30, cache_read_tokens=900))
    assert s.usage.input_tokens == 250 and s.usage.output_tokens == 50
    assert s.usage.cache_read_tokens == 900
    # Cache reads bill at a tenth but count 1:1 against the ceiling.
    assert s.usage.total == 1200


# -- transcript -----------------------------------------------------------


def test_turns_carry_speaker_text_and_time():
    t = Transcript()
    before = datetime.now(timezone.utc) - timedelta(seconds=1)
    turn = t.add(Speaker.CANDIDATE, "I would use a hash map")
    assert turn.speaker is Speaker.CANDIDATE
    assert turn.text == "I would use a hash map"
    assert turn.at > before


def test_speech_is_flattened_and_clamped():
    t = Transcript()
    turn = t.add(Speaker.CANDIDATE, "  so   like\n\nO of n  ", max_chars=0)
    assert turn.text == "so like O of n"
    # A hot microphone produces an unbounded turn, which is an unbounded prompt.
    long = t.add(Speaker.CANDIDATE, "word " * 500, max_chars=40)
    assert len(long.text) <= 40


def test_messages_drop_the_leading_interviewer_turn():
    """The API rejects an assistant first message, and the opening line
    already travels in the cached system block."""
    t = Transcript()
    t.add(Speaker.INTERVIEWER, "Today's problem is Two Sum.")
    t.add(Speaker.CANDIDATE, "I would use a hash map")
    t.add(Speaker.INTERVIEWER, "which property do you need")
    t.add(Speaker.CANDIDATE, "constant lookup")

    messages = t.as_messages()
    assert [m["role"] for m in messages] == ["user", "assistant", "user"]
    assert messages[0]["content"] == "I would use a hash map"
    assert messages[-1]["content"] == "constant lookup"


def test_messages_of_an_unstarted_transcript_are_empty():
    t = Transcript()
    t.add(Speaker.INTERVIEWER, "Today's problem is Two Sum.")
    assert t.as_messages() == []


def test_messages_grow_by_appending_only():
    """Prompt caching is a prefix match, so every earlier message must be
    byte identical from one turn to the next."""
    t = Transcript()
    t.add(Speaker.INTERVIEWER, "open")
    t.add(Speaker.CANDIDATE, "first")
    first = t.as_messages()
    t.add(Speaker.INTERVIEWER, "why")
    t.add(Speaker.CANDIDATE, "second")
    grown = t.as_messages()
    assert grown[: len(first)] == first
    assert len(grown) == len(first) + 2


def test_by_speaker_splits_the_turns():
    t = Transcript()
    t.add(Speaker.INTERVIEWER, "a")
    t.add(Speaker.CANDIDATE, "b")
    t.add(Speaker.CANDIDATE, "c")
    assert len(t.by(Speaker.CANDIDATE)) == 2
    assert len(t.by(Speaker.INTERVIEWER)) == 1
    assert len(t) == 3


def test_scorer_text_names_the_speakers():
    t = Transcript()
    t.add(Speaker.INTERVIEWER, "why sort")
    t.add(Speaker.CANDIDATE, "to group overlaps")
    assert t.as_text() == "interviewer: why sort\ncandidate: to group overlaps"


def test_a_long_transcript_loses_its_middle_not_its_ends():
    t = Transcript()
    t.add(Speaker.INTERVIEWER, "opening line")
    for i in range(40):
        t.add(Speaker.CANDIDATE, f"filler turn number {i}")
    t.add(Speaker.CANDIDATE, "final answer")

    text = t.as_text(max_chars=300)
    assert len(text) <= 300 + len("[... 99 turns omitted ...]")
    assert text.startswith("interviewer: opening line")   # framing survives
    assert text.endswith("candidate: final answer")       # the challenge does too
    assert "turns omitted" in text

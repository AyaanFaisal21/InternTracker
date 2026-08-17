"""Spend guards: per-user session cap, durable daily budget, session ceiling.

The guards run against a real store, because their whole point is that they
outlive one request. The store here is in memory; the Postgres one has to
answer the same six questions, which is what makes this suite the contract
for that migration.
"""

from datetime import timedelta

from interview.budget import BudgetGuard
from interview.config import InterviewSettings
from interview.schema import Session, Speaker, Usage
from interview.store import MemorySessionStore, utc_day


def settings(**over) -> InterviewSettings:
    return InterviewSettings(**over)


def setup(**over):
    store = MemorySessionStore()
    return store, BudgetGuard(store, settings(**over))


def opened(store: MemorySessionStore, user_key: str, problem="two-sum") -> Session:
    session = Session.open(user_key, problem)
    store.create(session)
    return session


# -- admission ------------------------------------------------------------


def test_a_fresh_day_admits_a_session():
    _, guard = setup()
    assert guard.begin_session("user-a").allowed is True


def test_a_user_gets_a_fixed_number_of_sessions_per_day():
    store, guard = setup(sessions_per_user_per_day=3)
    for _ in range(3):
        assert guard.begin_session("user-a").allowed is True
        opened(store, "user-a")

    refused = guard.begin_session("user-a")
    assert refused.allowed is False
    assert "3 practice" in refused.reason and "midnight UTC" in refused.reason
    # The cap is per user, not global.
    assert guard.begin_session("user-b").allowed is True


def test_yesterdays_sessions_do_not_hold_today_back():
    store, guard = setup(sessions_per_user_per_day=1)
    session = opened(store, "user-a")
    assert guard.begin_session("user-a").allowed is False

    store.sessions[session.id].created_at -= timedelta(days=1)
    assert store.sessions_today("user-a") == 0
    assert guard.begin_session("user-a").allowed is True


def test_a_session_is_admitted_only_when_a_whole_session_still_fits():
    """The reserve is what lets sessions already open finish and be scored
    instead of dying mid-interview when the day runs out."""
    store, guard = setup(daily_token_budget=100_000, session_token_estimate=60_000)
    assert guard.begin_session("user-a").allowed is True

    store.count_tokens(50_000)  # 50k left, less than one session
    refused = guard.begin_session("user-a")
    assert refused.allowed is False
    assert "closed for today" in refused.reason


# -- mid-session ----------------------------------------------------------


def test_the_session_ceiling_stops_the_next_turn():
    store, guard = setup(session_token_ceiling=10_000)
    session = opened(store, "user-a")

    session.add_usage(Usage(input_tokens=9_000, output_tokens=500))
    assert guard.before_turn(session).allowed is True

    # The turn that crosses the ceiling completes; the next one does not.
    session.add_usage(Usage(input_tokens=600))
    refused = guard.before_turn(session)
    assert refused.allowed is False and "ceiling" in refused.reason


def test_the_turn_limit_stops_the_next_turn():
    store, guard = setup(max_turns=2)
    session = opened(store, "user-a")
    session.begin("open")

    for _ in range(2):
        session.say(Speaker.CANDIDATE, "still talking")
        assert guard.before_turn(session).allowed is True

    session.say(Speaker.CANDIDATE, "one more")
    refused = guard.before_turn(session)
    assert refused.allowed is False and "turn limit" in refused.reason


def test_a_spent_daily_budget_stops_a_running_session():
    store, guard = setup(daily_token_budget=1_000)
    session = opened(store, "user-a")
    assert guard.before_turn(session).allowed is True

    store.count_tokens(1_000)
    assert guard.before_turn(session).allowed is False


# -- billing --------------------------------------------------------------


def test_recording_a_turn_bills_the_session_and_the_day():
    store, guard = setup()
    session = opened(store, "user-a")

    guard.record(session, Usage(input_tokens=900, output_tokens=200))
    guard.record(session, Usage(input_tokens=150, output_tokens=180, cache_read_tokens=900))

    assert session.usage.total == 900 + 200 + 150 + 180 + 900
    assert store.tokens_today() == session.usage.total


def test_a_turn_that_produced_nothing_does_not_touch_the_day():
    store, guard = setup()
    session = opened(store, "user-a")
    guard.record(session, Usage())
    assert store.tokens_today() == 0
    assert session.usage.total == 0


def test_the_day_counter_is_keyed_on_the_utc_boundary():
    store, _ = setup()
    store.count_tokens(500)
    assert store.tokens_today() == 500
    assert store.spend == {utc_day(): 500}

    del store.spend[utc_day()]  # the boundary rolled
    assert store.tokens_today() == 0


def test_the_store_hands_back_snapshots_not_handles():
    """A mutation is only durable once save is called, which is how the
    Postgres backend will behave."""
    store = MemorySessionStore()
    session = Session.open("user-a", "two-sum")
    store.create(session)

    session.begin("open")
    assert store.get(session.id).state.value == "created"

    store.save(session)
    assert store.get(session.id).state.value == "in_progress"
    assert store.get(session.id) is not store.get(session.id)

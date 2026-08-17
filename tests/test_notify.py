"""Notification backbone: subscription store roundtrip, company, degree and
country matching, the double opt-in gate, and the batched publish-time
fan-out. Senders are capture callables; nothing leaves the process.

The matching tables are the load-bearing part. A false positive mails
someone about a job they never asked for, and a false negative leaves them
waiting on silence, so both directions are asserted by name. The three
"absence matches" rules get their own cases: unstated degree, unparsed
country and remote all have to pass a filter rather than fail it, and each
is the difference between a working feed and a silent one.
"""

from unittest.mock import patch

import pytest

from intake.config import Settings, Watchlist
from intake.notify import (
    build_filters,
    company_key,
    notify_new_postings,
)
from intake.pipeline import Pipeline
from intake.schema import Posting, RawDetection, Source, Status, Verdict
from intake.store import Store
from intake.verify.rules import GateResult


def make_posting(company="Stripe", title="Software Engineer Intern",
                 degrees=(), locations=(), verdict_degrees=None):
    p = Posting.from_detection(RawDetection(
        source=Source.GREENHOUSE, company=company, title=title,
        url="https://x.co/1", locations=list(locations),
    ))
    p.degree_levels = list(degrees)
    if verdict_degrees is not None:
        p.verdict = Verdict(
            is_swe_internship=True, is_open=True, is_legitimate=True,
            degree_levels=list(verdict_degrees), confidence="high", reasons=[],
        )
    return p


def sub(sid, companies=None, degrees=None, countries=None,
        channel="email", verified=True):
    return {"id": sid, "channel": channel, "target": f"s{sid}@x.edu",
            "verified": verified, "token": f"tok{sid}",
            "filters": build_filters(companies or [], degrees, countries)}


def delivered(filters_sub, posting) -> bool:
    """Did this subscription receive this posting? Asserted through the real
    fan-out rather than _matches, so the tables cover delivery and not just
    the predicate."""
    return notify_new_postings([filters_sub], [posting], lambda s, ps: None) == 1


# -- store roundtrip ------------------------------------------------------

def test_subscription_store_roundtrip(tmp_path):
    store = Store(tmp_path / "t.db")
    sid1, token1, verify1 = store.add_subscription(
        "email", "a@b.edu", build_filters(["NVIDIA"]))
    sid2, token2, verify2 = store.add_subscription(
        "push", '{"endpoint": "https://p.example/x"}', {})
    assert sid1 != sid2 and token1 != token2 and verify1 != verify2
    assert len(token1) == 32 and len(verify1) == 32
    assert token1 != verify1  # the unsubscribe secret is not the verify secret

    subs = store.active_subscriptions()
    assert [s["id"] for s in subs] == [sid1, sid2]
    # Verbatim spelling for display, keys for delivery, decoded not text.
    assert subs[0]["filters"] == {"companies": ["NVIDIA"], "company_keys": ["nvidia"]}
    assert subs[1]["filters"] == {}
    assert subs[0]["channel"] == "email" and subs[1]["target"].startswith("{")
    assert subs[0]["verified"] is False  # nothing is sent until it is True

    assert store.verify_by_token(verify1) is True
    assert store.active_subscriptions()[0]["verified"] is True
    # Idempotent: a double-tapped link must not look broken.
    assert store.verify_by_token(verify1) is True
    assert store.verify_by_token("not-a-token") is False

    assert store.deactivate_by_token(token1) is True
    assert [s["id"] for s in store.active_subscriptions()] == [sid2]
    assert store.deactivate_by_token("not-a-token") is False


def test_only_mailed_and_unconfirmed_email_subscriptions_expire(tmp_path):
    store = Store(tmp_path / "p.db")
    stale, _, _ = store.add_subscription("email", "stale@x.edu", {})
    fresh, _, _ = store.add_subscription("email", "fresh@x.edu", {})
    confirmed, _, verify = store.add_subscription("email", "ok@x.edu", {})
    pushed, _, _ = store.add_subscription("push", '{"endpoint": "x"}', {})
    waiting, _, _ = store.add_subscription("email", "waiting@x.edu", {})
    store.verify_by_token(verify)
    # Everything but `waiting` was mailed a confirmation; `waiting` is a
    # signup taken while the email channel was still dark.
    for sid in (stale, fresh, confirmed):
        store.mark_confirmation_sent(sid)
    store.conn.execute(
        "UPDATE subscriptions SET created_at = datetime('now', '-8 days') "
        "WHERE id IN (?,?,?)", (stale, confirmed, waiting))
    store.conn.execute(
        "UPDATE subscriptions SET confirmation_sent_at = datetime('now', '-8 days') "
        "WHERE id IN (?,?)", (stale, confirmed))
    store.conn.commit()

    # Only the address that was asked and never answered goes. A confirmed
    # row is wanted, push has no confirmation step to fail, and the row
    # nobody could confirm was never sent anything to confirm.
    assert store.prune_unverified() == 1
    assert sorted(s["id"] for s in store.active_subscriptions()) == [
        fresh, confirmed, pushed, waiting]
    assert store.prune_unverified() == 0


def test_a_signup_taken_before_launch_waits_instead_of_expiring(tmp_path):
    store = Store(tmp_path / "wait.db")
    waiting, _, _ = store.add_subscription("email", "early@x.edu", {})
    store.conn.execute(
        "UPDATE subscriptions SET created_at = datetime('now', '-90 days') "
        "WHERE id = ?", (waiting,))
    store.conn.commit()

    # Nothing was sent to this address, so there is nothing it failed to
    # confirm. The consent stands until it is confirmed or withdrawn, however
    # long the launch takes.
    assert store.prune_unverified() == 0
    assert [s["id"] for s in store.active_subscriptions()] == [waiting]
    assert [s["id"] for s in store.pending_confirmations()] == [waiting]
    # It is still unconfirmed, so it receives no alerts in the meantime.
    assert notify_new_postings(store.active_subscriptions(), [make_posting()],
                               lambda s, ps: None) == 0

    # The moment it is mailed, the ordinary window starts, counted from that
    # mailing rather than from a signup nobody answered.
    store.mark_confirmation_sent(waiting)
    assert store.prune_unverified() == 0
    store.conn.execute(
        "UPDATE subscriptions SET confirmation_sent_at = datetime('now', '-8 days') "
        "WHERE id = ?", (waiting,))
    store.conn.commit()
    assert store.prune_unverified() == 1


# -- consent evidence -----------------------------------------------------

def test_consent_evidence_records_the_request_and_the_confirmation(tmp_path):
    store = Store(tmp_path / "consent.db")
    sid, _, verify = store.add_subscription(
        "email", "a@b.edu", build_filters(["NVIDIA"]), "198.51.100.23")

    def row():
        return store.conn.execute(
            "SELECT * FROM subscriptions WHERE id = ?", (sid,)).fetchone()

    # Half the evidence at signup: when the request arrived and from where.
    assert row()["consent_ip"] == "198.51.100.23" and row()["created_at"]
    assert row()["verified_at"] is None and row()["verify_ip"] is None

    # The other half at the click, which is what proves the opt-in was
    # double rather than merely claimed.
    assert store.verify_by_token(verify, "203.0.113.7") is True
    confirmed_at = row()["verified_at"]
    assert confirmed_at and row()["verify_ip"] == "203.0.113.7"

    # A later click on the same link still answers True, and the evidence
    # stays the evidence: the click that confirmed, not the last one.
    assert store.verify_by_token(verify, "203.0.113.99") is True
    assert row()["verify_ip"] == "203.0.113.7"
    assert row()["verified_at"] == confirmed_at


def test_consent_addresses_stay_in_the_table(tmp_path):
    store = Store(tmp_path / "leak.db")
    sid, _, verify = store.add_subscription("email", "a@b.edu", {}, "198.51.100.23")
    store.verify_by_token(verify, "203.0.113.7")

    # The columns answer a legal question from SQL and nothing else, so no
    # dict this store hands out carries them: nothing downstream can log or
    # serialize an address it was never given.
    for d in (*store.active_subscriptions(), *store.pending_confirmations()):
        assert "consent_ip" not in d and "verify_ip" not in d
    assert store.active_subscriptions()[0]["id"] == sid
    stored = store.conn.execute(
        "SELECT consent_ip, verify_ip FROM subscriptions").fetchone()
    assert tuple(stored) == ("198.51.100.23", "203.0.113.7")


# -- immediate suppression ------------------------------------------------

def test_unsubscribing_lands_before_the_next_fan_out(tmp_path):
    db = tmp_path / "off.db"
    store = Store(db)
    stay, _, v1 = store.add_subscription("email", "stay@x.edu", {})
    off, token, v2 = store.add_subscription("email", "off@x.edu", {})
    store.verify_by_token(v1)
    store.verify_by_token(v2)
    sends = []

    def fan_out(from_store):
        return notify_new_postings(from_store.active_subscriptions(),
                                   [make_posting()],
                                   lambda s, ps: sends.append(s["id"]))

    assert fan_out(store) == 2

    # No queue and no deferred write: the call that answers True has already
    # committed, which a second connection to the same file proves.
    assert store.deactivate_by_token(token) is True
    assert [s["id"] for s in Store(db).active_subscriptions()] == [stay]
    assert fan_out(Store(db)) == 1
    assert sends == [stay, off, stay]


def test_daily_send_counter_is_per_subscriber_and_durable(tmp_path):
    db = tmp_path / "c.db"
    store = Store(db)
    sid, _, _ = store.add_subscription("email", "a@b.edu", {})
    assert store.notify_sends_today(sid) == 0
    assert store.count_notify_send(sid) == 1
    assert store.count_notify_send(sid) == 2
    assert store.notify_sends_today(sid + 1) == 0
    # Durable: the poller restarts on every deploy, and an in-process count
    # would hand a fresh allowance to anyone who can make it restart.
    assert Store(db).notify_sends_today(sid) == 2


# -- matching -------------------------------------------------------------

# (what the subscriber picked, the posting's company, should it match)
MATCH_CASES = [
    ("NVIDIA", "NVIDIA", True),
    ("nvidia.", "NVIDIA", True),
    ("NVIDIA Corp", "NVIDIA", True),                     # suffix stripped
    ("NVIDIA", "NVIDIA Corporation", True),              # and in reverse
    ("Palantir Technologies, Inc.", "Palantir", True),   # two suffixes
    ("Stripe Inc", "Stripe, Inc.", True),
    ("Facebook", "Meta", True),                          # alias
    ("Meta", "Facebook", True),
    ("Alphabet", "Google", True),
    ("Twitter", "X", True),
    # The conservative half. "goldman" not matching "Goldman Sachs" is the
    # designed behavior, not a gap: partial matching is what would put
    # Apple Bank postings in an Apple subscriber's inbox. Autocomplete off
    # /api/companies is how a subscriber lands on the full name instead.
    ("goldman", "Goldman Sachs", False),
    ("apple", "Apple Bank", False),
    ("Apple Bank", "Apple", False),
    ("Googel", "Google", False),                         # typo, not a rename
    ("Meta", "Meta Bank", False),
]


@pytest.mark.parametrize("picked,posted,expected", MATCH_CASES)
def test_company_matching_table(picked, posted, expected):
    sends = []
    n = notify_new_postings([sub(1, [picked])], [make_posting(posted)],
                            lambda s, ps: sends.append(s["id"]))
    assert (n == 1) is expected and (sends == [1]) is expected
    assert (company_key(picked) == company_key(posted)) is expected


def test_empty_filters_match_every_company():
    sends = []
    n = notify_new_postings([sub(1), sub(2)], [make_posting("Anthropic")],
                            lambda s, ps: sends.append(s["id"]))
    assert n == 2 and sends == [1, 2]


def test_build_filters_keeps_the_wording_and_the_key():
    assert build_filters([]) == {}
    assert build_filters(["NVIDIA Corp", "stripe."]) == {
        "companies": ["NVIDIA Corp", "stripe."],
        "company_keys": ["nvidia", "stripe"],
    }


def test_build_filters_writes_only_the_dimensions_that_narrow():
    # Nothing picked stays the '{}' the store has always understood, and an
    # empty dimension is left out rather than stored as an empty list, so
    # every key present in a stored row means a constraint.
    assert build_filters([], [], []) == {}
    assert build_filters([], ["BS"]) == {"degrees": ["BS"]}
    assert build_filters([], None, ["Canada"]) == {"countries": ["Canada"]}
    assert build_filters(["NVIDIA"], ["BS"], ["United States", "Canada"]) == {
        "companies": ["NVIDIA"],
        "company_keys": ["nvidia"],
        "degrees": ["BS"],
        "countries": ["United States", "Canada"],
    }


def test_rows_written_before_company_keys_existed_still_match():
    sends = []
    legacy = {"id": 9, "channel": "push", "target": "x", "verified": False,
              "token": "t9", "filters": {"companies": ["NVIDIA Corp"]}}
    n = notify_new_postings([legacy], [make_posting("NVIDIA")],
                            lambda s, ps: sends.append(s["id"]))
    assert n == 1 and sends == [9]


# -- degrees --------------------------------------------------------------

# (degrees the subscriber picked, the posting's degree levels, should it match)
DEGREE_CASES = [
    ([], ["PhD"], True),                 # nothing picked constrains nothing
    ([], [], True),
    (["BS"], ["BS"], True),
    (["BS"], ["BS", "MS"], True),        # OR within the dimension
    (["MS", "PhD"], ["PhD"], True),
    (["BS", "MS", "PhD"], ["MS"], True),
    (["BS"], ["MS"], False),
    (["BS"], ["MS", "PhD"], False),      # the undergraduate's whole problem
    (["MS", "PhD"], ["BS"], False),
    (["PhD"], ["BS", "MS"], False),
    # The load-bearing case. A posting states no level when the classifier
    # could not support one, which it deliberately prefers over guessing, so
    # reading the empty list as "matches nothing" would hide most of the
    # board from every subscriber who picked a degree at all.
    (["BS"], [], True),
    (["PhD"], [], True),
    (["BS", "MS", "PhD"], [], True),
]


@pytest.mark.parametrize("picked,stated,expected", DEGREE_CASES)
def test_degree_matching_table(picked, stated, expected):
    assert delivered(sub(1, degrees=picked), make_posting(degrees=stated)) is expected


def test_the_verifier_read_of_the_degree_beats_the_title_heuristic():
    # The rule gate guesses from the title; the verifier read the page. The
    # board renders the verdict, so the alerts have to filter on it or the
    # mail and the page disagree about the same posting.
    graduate = make_posting(degrees=["BS"], verdict_degrees=["PhD"])
    assert delivered(sub(1, degrees=["BS"]), graduate) is False
    assert delivered(sub(1, degrees=["PhD"]), graduate) is True

    # An empty verdict is not an answer, so the heuristic still stands.
    heuristic = make_posting(degrees=["BS"], verdict_degrees=[])
    assert delivered(sub(1, degrees=["BS"]), heuristic) is True
    assert delivered(sub(1, degrees=["PhD"]), heuristic) is False


# -- countries ------------------------------------------------------------

# (countries the subscriber picked, the posting's location labels, expected)
COUNTRY_CASES = [
    ([], ["Bengaluru"], True),                        # nothing picked
    (["United States"], ["New York, NY"], True),
    (["United States"], ["US, CA, Santa Clara"], True),
    (["United States", "Canada"], ["Toronto, ON"], True),   # OR within
    (["United States", "Canada"], ["Seattle"], True),
    (["India"], ["Bengaluru"], True),
    (["United States"], ["Bengaluru"], False),
    (["Canada"], ["US, CA, Santa Clara"], False),
    (["United States", "Canada"], ["London"], False),
    # Absence of evidence must not hide a posting, so both unknowns pass.
    # "Atlantis" parses to no country at all: an unread label is not proof
    # the job is somewhere else.
    (["United States"], ["Atlantis"], True),
    (["United States"], [], True),                    # no labels at all
    # Remote is workable from anywhere, so it clears every region even when
    # the label also names a country we did not pick.
    (["United States"], ["Remote - India"], True),
    (["Canada"], ["Remote"], True),
    (["Germany"], ["US Remote"], True),
]


@pytest.mark.parametrize("picked,labels,expected", COUNTRY_CASES)
def test_country_matching_table(picked, labels, expected):
    assert delivered(sub(1, countries=picked),
                     make_posting(locations=labels)) is expected


# -- the dimensions together ----------------------------------------------

def test_dimensions_are_anded_together():
    wanted = sub(1, companies=["NVIDIA"], degrees=["BS"],
                 countries=["United States"])
    hit = make_posting("NVIDIA", degrees=["BS"], locations=["Santa Clara, CA"])
    assert delivered(wanted, hit) is True

    # Each dimension alone is enough to refuse, so no one of them can be
    # carrying the others.
    for miss in (
        make_posting("Stripe", degrees=["BS"], locations=["Santa Clara, CA"]),
        make_posting("NVIDIA", degrees=["PhD"], locations=["Santa Clara, CA"]),
        make_posting("NVIDIA", degrees=["BS"], locations=["Bengaluru"]),
    ):
        assert delivered(wanted, miss) is False

    # And the three absences still pass while the rest of the filter holds.
    assert delivered(wanted, make_posting("NVIDIA", degrees=[],
                                          locations=["Atlantis"])) is True


def test_the_undergraduate_who_follows_a_big_employer():
    # The case the filters exist for: a PhD-only research role abroad at a
    # company the subscriber does follow, and the undergraduate posting at
    # the same company that they actually want.
    student = sub(1, companies=["NVIDIA"], degrees=["BS"],
                  countries=["United States", "Canada"])
    research = make_posting("NVIDIA", "Research Scientist Intern",
                            degrees=["PhD"], locations=["Tel Aviv"])
    intern = make_posting("NVIDIA", "Software Engineer Intern",
                          degrees=["BS"], locations=["Toronto, ON"])
    calls = []
    n = notify_new_postings([student], [research, intern],
                            lambda s, ps: calls.append([p.title for p in ps]))
    assert n == 1 and calls == [["Software Engineer Intern"]]


def test_rows_written_before_degrees_and_countries_existed_are_unconstrained():
    # A legacy row carries neither key, and the fan-out must read that as
    # the same "every degree, every region" it meant when it was written.
    legacy = {"id": 9, "channel": "email", "target": "x@y.edu",
              "verified": True, "token": "t9",
              "filters": {"companies": ["NVIDIA"], "company_keys": ["nvidia"]}}
    for posting in (
        make_posting("NVIDIA", degrees=["PhD"], locations=["Tel Aviv"]),
        make_posting("NVIDIA", degrees=["BS"], locations=["Santa Clara, CA"]),
        make_posting("NVIDIA"),
    ):
        assert delivered(legacy, posting) is True
    assert delivered(legacy, make_posting("Stripe")) is False


# -- opt-in gate ----------------------------------------------------------

def test_unverified_email_gets_nothing_and_push_is_unaffected():
    sends = []
    subs = [sub(1, verified=False), sub(2, verified=True),
            sub(3, channel="push", verified=False)]
    n = notify_new_postings(subs, [make_posting()],
                            lambda s, ps: sends.append(s["id"]))
    # 1 never confirmed. 3 is a push endpoint the browser minted only after
    # the visitor granted permission, so its consent is already proven.
    assert n == 2 and sends == [2, 3]


# -- batching and the daily cap -------------------------------------------

def test_one_send_per_subscriber_carries_every_matching_posting():
    calls = []
    postings = [make_posting("Stripe"), make_posting("NVIDIA"),
                make_posting("Stripe", "ML Intern")]
    subs = [sub(1), sub(2, ["Stripe"]), sub(3, ["Datadog"])]
    n = notify_new_postings(subs, postings,
                            lambda s, ps: calls.append((s["id"], len(ps))))
    # One call each, never one per posting; the subscriber with no matches
    # is not mailed an empty message.
    assert n == 2 and calls == [(1, 3), (2, 2)]


def test_daily_cap_holds_across_cycles(tmp_path):
    store = Store(tmp_path / "cap.db")
    sid, _, verify = store.add_subscription("email", "a@b.edu", {})
    store.verify_by_token(verify)
    subs = store.active_subscriptions()
    calls = []

    for _ in range(5):
        notify_new_postings(subs, [make_posting()],
                            lambda s, ps: calls.append(s["id"]), store, 3)
    assert len(calls) == 3
    assert store.notify_sends_today(sid) == 3
    # Uncapped is the default the matching tests use, so the cap can only
    # ever be a deliberate setting.
    notify_new_postings(subs, [make_posting()], lambda s, ps: calls.append(s["id"]))
    assert len(calls) == 4


# -- pipeline fan-out -----------------------------------------------------

class FakeDetector:
    name = "fake"

    def __init__(self, detections):
        self.detections = detections

    def poll(self):
        return self.detections


class FakeVerifier:
    def verify(self, p):
        return Verdict(
            is_swe_internship=True, is_open=True, is_legitimate=True,
            confidence="high", reasons=[],
        )


DET = RawDetection(
    source=Source.GREENHOUSE, company="Stripe",
    title="Software Engineer Intern", url="https://stripe.com/jobs/1",
)
DET2 = RawDetection(
    source=Source.GREENHOUSE, company="Stripe",
    title="ML Engineer Intern", url="https://stripe.com/jobs/2",
)
DET3 = RawDetection(
    source=Source.GREENHOUSE, company="NVIDIA",
    title="Software Engineer Intern", url="https://nvidia.com/jobs/3",
)


def make_pipeline(tmp_path, store, sender, detections=(DET,)):
    return Pipeline(
        settings=Settings(watchlist=Watchlist(), db_path=tmp_path / "t.db"),
        store=store,
        detectors=[FakeDetector(list(detections))],
        verifier=FakeVerifier(),
        publisher=lambda p: None,
        sender=sender,
    )


def confirmed(store, target, filters):
    sid, _, verify = store.add_subscription("email", target, filters)
    store.verify_by_token(verify)
    return sid


@patch("intake.pipeline.run_rules",
       return_value=GateResult(canonical_url="https://stripe.com/jobs/1", degree_levels=["BS"]))
def test_publish_fans_out_once_per_matching_subscription(_rules, tmp_path):
    store = Store(tmp_path / "t.db")
    confirmed(store, "all@x.edu", {})
    confirmed(store, "stripe@x.edu", build_filters(["stripe!"]))
    confirmed(store, "nv@x.edu", build_filters(["NVIDIA"]))
    store.add_subscription("email", "unconfirmed@x.edu", {})  # never clicked
    sends = []
    pipeline = make_pipeline(tmp_path, store, lambda s, ps: sends.append(s["target"]))

    report = pipeline.run_cycle()
    assert report.published == 1 and report.errors == []
    assert sorted(sends) == ["all@x.edu", "stripe@x.edu"]

    # Already published: a second cycle must not resend.
    pipeline.run_cycle()
    assert len(sends) == 2


# One canonical URL per posting: a shared one would make the reconcile pass
# fold all three into a single record before publish.
@patch("intake.pipeline.run_rules",
       side_effect=lambda p, http: GateResult(canonical_url=p.url, degree_levels=["BS"]))
def test_a_burst_of_postings_is_one_message_per_subscriber(_rules, tmp_path):
    store = Store(tmp_path / "b.db")
    confirmed(store, "all@x.edu", {})
    confirmed(store, "stripe@x.edu", build_filters(["Stripe Inc"]))
    calls = []
    pipeline = make_pipeline(
        tmp_path, store, lambda s, ps: calls.append((s["target"], len(ps))),
        detections=(DET, DET2, DET3),
    )

    report = pipeline.run_cycle()
    assert report.published == 3
    # Three postings, two subscribers, two messages. Per-posting fan-out
    # would have been five.
    assert sorted(calls) == [("all@x.edu", 3), ("stripe@x.edu", 2)]


@patch("intake.pipeline.run_rules",
       return_value=GateResult(canonical_url="https://stripe.com/jobs/1", degree_levels=["BS"]))
def test_sender_error_does_not_kill_the_cycle(_rules, tmp_path):
    store = Store(tmp_path / "t.db")
    confirmed(store, "all@x.edu", {})

    def boom(s, ps):
        raise RuntimeError("channel down")

    pipeline = make_pipeline(tmp_path, store, boom)
    report = pipeline.run_cycle()
    assert report.published == 1
    assert any(e.startswith("notify") for e in report.errors)
    assert pipeline.store.get(DET.dedupe_key()).status == Status.PUBLISHED


@patch("intake.pipeline.run_rules",
       return_value=GateResult(canonical_url="https://stripe.com/jobs/1", degree_levels=["BS"]))
def test_the_cycle_prunes_unconfirmed_subscriptions(_rules, tmp_path):
    store = Store(tmp_path / "pr.db")
    stale, _, _ = store.add_subscription("email", "stale@x.edu", {})
    waiting, _, _ = store.add_subscription("email", "waiting@x.edu", {})
    store.mark_confirmation_sent(stale)
    store.conn.execute(
        "UPDATE subscriptions SET created_at = datetime('now', '-8 days'), "
        "confirmation_sent_at = datetime('now', '-8 days') WHERE id = ?",
        (stale,))
    store.conn.execute(
        "UPDATE subscriptions SET created_at = datetime('now', '-8 days') "
        "WHERE id = ?", (waiting,))
    store.conn.commit()
    pipeline = make_pipeline(tmp_path, store, lambda s, ps: None)

    # verify=False is how production runs, so the prune has to happen on
    # that path too. The row that was mailed and ignored goes; the one that
    # was never mailed is a signup waiting on launch and stays, with its
    # confirmation still owed.
    pipeline.run_cycle(verify=False)
    assert [s["id"] for s in store.active_subscriptions()] == [waiting]
    assert [s["id"] for s in store.pending_confirmations()] == [waiting]

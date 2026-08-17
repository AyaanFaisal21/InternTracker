"""Web server tests: real HTTP server on an ephemeral port, real store.
Covers the dashboard smoke path, the company list the subscribe form
autocompletes against, the subscription endpoints including the double
opt-in and the degree and country filters they accept, the consent evidence
they record, the email-clickable links, the visit beacon's privacy
contract, and the suggestion endpoint's spend defenses (validation and
duplicate collapsing).

No mail can leave: every test that reaches the subscribe endpoint replaces
web.email_sender with a recorder, so the result does not depend on whether
the optional boto3 extra happens to be installed.

Skips when the environment forbids loopback connections (some sandboxes do).
"""

import logging
import socket
import threading
import time
from http.server import ThreadingHTTPServer
from unittest.mock import patch

import httpx
import pytest


def loopback_blocked() -> bool:
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    probe.listen(1)
    try:
        socket.create_connection(probe.getsockname(), timeout=1).close()
        return False
    except OSError:
        return True
    finally:
        probe.close()

from intake.notify import notify_new_postings
from intake.schema import Posting, RawDetection, Source, Status
from intake.store import Store
from intake.web import (
    COMPANIES_LIMIT,
    CONFIRM_LIMIT,
    SUBSCRIBE_LIMIT,
    SUGGEST_LIMIT,
    UNSUBSCRIBE_LIMIT,
    VERIFY_LIMIT,
    VISIT_LIMIT,
    make_handler,
    reset_companies_cache,
    visitor_hash,
)

MAC = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")


class Mailbox:
    """Stands in for senders.EmailSender at the subscribe endpoint."""

    def __init__(self, ok=True):
        self.ok = ok
        self.sent: list[dict] = []

    def send_confirmation(self, sub: dict) -> bool:
        self.sent.append(sub)
        return self.ok


def serve(db):
    """Start the handler on an ephemeral port and wait for the listener."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(db))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    for _ in range(20):
        try:
            httpx.get(f"{base}/")
            break
        except httpx.ConnectError:
            time.sleep(0.05)
    else:
        raise AssertionError("server never came up")
    return server, base


@pytest.mark.skipif(loopback_blocked(), reason="loopback connections blocked")
def test_dashboard_serves_html_and_json(tmp_path):
    db = tmp_path / "t.db"
    store = Store(db)
    store.upsert_detection(
        RawDetection(
            source=Source.GREENHOUSE, company="Stripe",
            title="Software Engineer Intern", url="https://stripe.com/jobs/1",
            locations=["New York, NY"],
        )
    )
    store.upsert_detection(
        RawDetection(
            source=Source.GREENHOUSE, company="Oracle",
            title="Software Developer Intern", url="https://oracle.com/jobs/2",
            locations=["Remote - US"],
        )
    )

    server, base = serve(db)
    try:
        html = httpx.get(f"{base}/")
        assert html.status_code == 200 and "Shortlist" in html.text

        board = httpx.get(f"{base}/listings")
        assert board.status_code == 200 and "Want to contribute" in board.text

        visit = httpx.post(f"{base}/api/visit", json={"page": "landing"})
        assert visit.status_code == 200
        assert Store(db).visit_count() == 1

        api = httpx.get(f"{base}/api/postings")
        assert api.status_code == 200
        postings = api.json()
        assert len(postings) == 2
        by = {p["company"]: p for p in postings}
        assert by["Stripe"]["status"] == "pending"
        # Derived location semantics: countries plus the remote boolean.
        assert by["Stripe"]["countries"] == ["United States"]
        assert by["Stripe"]["remote"] is False
        assert by["Oracle"]["countries"] == ["United States"]
        assert by["Oracle"]["remote"] is True

        assert httpx.get(f"{base}/nope").status_code == 404
    finally:
        server.shutdown()


@pytest.mark.skipif(loopback_blocked(), reason="loopback connections blocked")
def test_visit_beacon_stores_a_hash_and_never_the_address(tmp_path):
    db = tmp_path / "vh.db"
    Store(db)
    VISIT_LIMIT.reset()  # the limiter is process-wide state shared by tests
    ip = "198.51.100.23"  # the real client; every request arrives from Caddy
    server, base = serve(db)
    try:
        r = httpx.post(f"{base}/api/visit", json={"page": "landing"},
                       headers={"X-Forwarded-For": ip, "User-Agent": MAC})
        assert r.status_code == 200 and r.json() == {}

        store = Store(db)
        row = store.conn.execute("SELECT * FROM visits").fetchone()
        assert row["page"] == "landing" and row["device"] == "desktop"
        assert row["ua"] is None
        # Reproducible while today's salt lives, which is what makes the
        # counts real, and irreversible once the salt is pruned.
        assert row["visitor_hash"] == visitor_hash(store.visit_salt(), ip, MAC)

        # The address reaches no column of any table, not merely no column
        # of visits.
        tables = [t[0] for t in store.conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")]
        for table in tables:
            for stored in store.conn.execute(f"SELECT * FROM {table}"):
                assert ip not in " ".join(str(v) for v in tuple(stored))

        # A phone and a crawler behind the same address, told apart.
        for ua in ("Mozilla/5.0 (Linux; Android 14; Pixel 8) Mobile Safari/537.36",
                   "Mozilla/5.0 (compatible; Googlebot/2.1)"):
            assert httpx.post(f"{base}/api/visit", json={"page": "listings"},
                              headers={"X-Forwarded-For": ip,
                                       "User-Agent": ua}).status_code == 200
        devices = [v["device"] for v in
                   store.conn.execute("SELECT device FROM visits ORDER BY id")]
        assert devices == ["desktop", "mobile", "bot"]
    finally:
        VISIT_LIMIT.reset()
        server.shutdown()


@pytest.mark.skipif(loopback_blocked(), reason="loopback connections blocked")
def test_visit_beacon_honors_its_rate_limit(tmp_path):
    db = tmp_path / "vr.db"
    Store(db)
    VISIT_LIMIT.reset()
    server, base = serve(db)
    beacon = {"page": "landing"}
    try:
        # One client, so the burst reuses a connection the way a browser
        # does rather than minting 32 sockets.
        with httpx.Client(headers={"User-Agent": MAC}) as client:
            # 30 per minute, keyed on the forwarded address rather than on
            # Caddy's, so one abusive client cannot spend everyone's window.
            for _ in range(30):
                ok = client.post(f"{base}/api/visit", json=beacon,
                                 headers={"X-Forwarded-For": "198.51.100.9"})
                assert ok.status_code == 200
            over = client.post(f"{base}/api/visit", json=beacon,
                               headers={"X-Forwarded-For": "198.51.100.9"})
            assert over.status_code == 429
            other = client.post(f"{base}/api/visit", json=beacon,
                                headers={"X-Forwarded-For": "198.51.100.10"})
            assert other.status_code == 200
        assert Store(db).visit_count() == 31
    finally:
        VISIT_LIMIT.reset()
        server.shutdown()


@pytest.mark.skipif(loopback_blocked(), reason="loopback connections blocked")
def test_subscribe_and_unsubscribe_endpoints(tmp_path):
    db = tmp_path / "s.db"
    Store(db)  # create the schema before the first request
    SUBSCRIBE_LIMIT.reset()
    CONFIRM_LIMIT.reset()
    reset_companies_cache()
    mailbox = Mailbox()

    server, base = serve(db)
    try:
        # Four invalid shapes, each 400. SUBSCRIBE_LIMIT allows 5/min, so
        # these plus the one valid request below spend the window exactly.
        bad = [
            {"channel": "sms", "target": "5551234"},
            {"channel": "email", "target": ""},
            {"channel": "email", "target": "a@b.edu",
             "filters": {"companies": ["x" * 81]}},
            {"channel": "email", "target": "a@b.edu",
             "filters": {"companies": [f"c{i}" for i in range(21)]}},
        ]
        for payload in bad:
            assert httpx.post(f"{base}/api/subscribe", json=payload).status_code == 400

        with patch("intake.web.email_sender", return_value=mailbox):
            r = httpx.post(f"{base}/api/subscribe", json={
                "channel": "email", "target": "af@scarletmail.rutgers.edu",
                "filters": {"companies": ["NVIDIA"]},
            })
        assert r.status_code == 200
        created = r.json()
        assert created["id"] >= 1 and len(created["token"]) == 32
        assert created["pending_verification"] is True
        assert created["verification_sent"] is True
        assert created["matches"] == {"NVIDIA": 0}   # nothing detected yet
        subs = Store(db).active_subscriptions()
        assert subs[0]["channel"] == "email"
        # What the subscriber typed, plus the key the fan-out compares on.
        assert subs[0]["filters"] == {"companies": ["NVIDIA"],
                                      "company_keys": ["nvidia"]}
        assert subs[0]["verified"] is False   # nothing is sent until confirmed
        # Exactly one confirmation, carrying the verify secret and never the
        # unsubscribe secret in its place.
        assert len(mailbox.sent) == 1
        assert mailbox.sent[0]["target"] == "af@scarletmail.rutgers.edu"
        assert mailbox.sent[0]["verify_token"] != created["token"]

        # Sixth request in the window hits the limiter.
        assert httpx.post(f"{base}/api/subscribe", json={
            "channel": "email", "target": "late@x.edu",
        }).status_code == 429

        # Unsubscribe: empty token 400, real token true, unknown token false.
        assert httpx.post(f"{base}/api/unsubscribe", json={}).status_code == 400
        ok = httpx.post(f"{base}/api/unsubscribe", json={"token": created["token"]})
        assert ok.status_code == 200 and ok.json() == {"ok": True}
        assert Store(db).active_subscriptions() == []
        miss = httpx.post(f"{base}/api/unsubscribe", json={"token": "0" * 32})
        assert miss.status_code == 200 and miss.json() == {"ok": False}
    finally:
        SUBSCRIBE_LIMIT.reset()
        CONFIRM_LIMIT.reset()
        server.shutdown()


@pytest.mark.skipif(loopback_blocked(), reason="loopback connections blocked")
def test_subscribe_refuses_addresses_that_cannot_receive_mail(tmp_path):
    db = tmp_path / "e.db"
    Store(db)
    SUBSCRIBE_LIMIT.reset()
    server, base = serve(db)
    try:
        # Anyone can type anything here, including a header-injection
        # attempt, so the shape is judged before a row exists.
        bad = ["nobody", "a@b", "a b@example.com",
               "a@b.edu\r\nBcc: victim@example.com", "x" * 250 + "@example.com"]
        for target in bad:
            r = httpx.post(f"{base}/api/subscribe",
                           json={"channel": "email", "target": target})
            assert r.status_code == 400, target
        assert Store(db).active_subscriptions() == []
    finally:
        SUBSCRIBE_LIMIT.reset()
        server.shutdown()


@pytest.mark.skipif(loopback_blocked(), reason="loopback connections blocked")
def test_subscribe_stores_degree_and_country_filters(tmp_path):
    db = tmp_path / "df.db"
    Store(db)
    SUBSCRIBE_LIMIT.reset()
    CONFIRM_LIMIT.reset()
    reset_companies_cache()
    mailbox = Mailbox()

    # (what the form sent, what the row should hold). Both dimensions are
    # optional, and every combination of set and unset has to land as the
    # filter it describes rather than as a filter that matches nothing.
    cases = [
        ({}, {}),
        # Explicitly empty is the same "no constraint" as absent, so a form
        # that always sends the keys cannot subscribe someone to silence.
        ({"companies": [], "degrees": [], "countries": []}, {}),
        ({"degrees": ["BS"]}, {"degrees": ["BS"]}),
        ({"countries": ["United States", "Canada"]},
         {"countries": ["United States", "Canada"]}),
        ({"companies": ["NVIDIA"], "degrees": ["PhD", "MS"],
          "countries": ["Canada"]},
         {"companies": ["NVIDIA"], "company_keys": ["nvidia"],
          "degrees": ["MS", "PhD"], "countries": ["Canada"]}),
        # Control characters become spaces, runs collapse and repeats fold,
        # so what is stored compares equal to the names countries_of derives.
        ({"countries": ["  United\tStates ", "United States"]},
         {"countries": ["United States"]}),
    ]

    server, base = serve(db)
    try:
        with patch("intake.web.email_sender", return_value=mailbox):
            for i, (sent, _) in enumerate(cases):
                r = httpx.post(
                    f"{base}/api/subscribe",
                    json={"channel": "email", "target": f"s{i}@x.edu",
                          "filters": sent},
                    # One client per request: the per-IP window allows five.
                    headers={"X-Forwarded-For": f"198.51.100.{i}"},
                )
                assert r.status_code == 200, sent
                # The response still answers the company question and only
                # that one: degrees and countries are picked from fixed
                # lists and cannot be a name we do not carry.
                assert set(r.json()["matches"]) == set(sent.get("companies") or [])
        assert [s["filters"] for s in Store(db).active_subscriptions()] == [
            expected for _, expected in cases]
    finally:
        SUBSCRIBE_LIMIT.reset()
        CONFIRM_LIMIT.reset()
        server.shutdown()


@pytest.mark.skipif(loopback_blocked(), reason="loopback connections blocked")
def test_subscribe_refuses_filters_it_could_never_honor(tmp_path):
    db = tmp_path / "bf.db"
    Store(db)
    SUBSCRIBE_LIMIT.reset()

    # Every one of these would store a filter that matches nothing, which
    # reaches the subscriber as a feed that never fires rather than as an
    # error they could fix. Refused at the door instead.
    bad = [
        {"degrees": ["BA"]},                      # not a level postings carry
        {"degrees": ["bs"]},                      # nor is a different casing
        {"degrees": ["BS", "MS", "PhD", "BS"]},   # longer than the whole set
        {"degrees": "BS"},                        # a bare string is not a list
        {"degrees": [1]},
        # Unhashable, so the type check has to come before the membership
        # test or this is a 500 instead of a 400.
        {"degrees": [{"level": "BS"}]},
        {"countries": ["x" * 61]},
        {"countries": [f"C{i}" for i in range(11)]},
        {"countries": [""]},
        {"countries": ["   "]},                   # empty once padding goes
        {"countries": [""]},           # empty once controls go
        {"countries": [None]},
        {"countries": "United States"},
        "not-a-dict",
    ]

    server, base = serve(db)
    try:
        for i, filters in enumerate(bad):
            r = httpx.post(
                f"{base}/api/subscribe",
                json={"channel": "email", "target": "a@b.edu", "filters": filters},
                headers={"X-Forwarded-For": f"203.0.113.{i}"},
            )
            assert r.status_code == 400, filters
        assert Store(db).active_subscriptions() == []
    finally:
        SUBSCRIBE_LIMIT.reset()
        server.shutdown()


@pytest.mark.skipif(loopback_blocked(), reason="loopback connections blocked")
def test_one_address_cannot_be_mailed_confirmations_repeatedly(tmp_path):
    db = tmp_path / "flood.db"
    Store(db)
    SUBSCRIBE_LIMIT.reset()
    CONFIRM_LIMIT.reset()
    mailbox = Mailbox()
    server, base = serve(db)
    try:
        results = []
        with patch("intake.web.email_sender", return_value=mailbox):
            for i in range(3):
                # Three different clients, one victim address: the per-IP
                # window never fires, so the per-recipient one has to.
                results.append(httpx.post(
                    f"{base}/api/subscribe",
                    json={"channel": "email", "target": "victim@example.edu"},
                    headers={"X-Forwarded-For": f"198.51.100.{i}"},
                ).json())
        assert [r["verification_sent"] for r in results] == [True, True, False]
        assert len(mailbox.sent) == 2
        # The rows still exist and expire on their own; only the mail stopped.
        assert len(Store(db).active_subscriptions()) == 3
    finally:
        SUBSCRIBE_LIMIT.reset()
        CONFIRM_LIMIT.reset()
        server.shutdown()


@pytest.mark.skipif(loopback_blocked(), reason="loopback connections blocked")
def test_companies_endpoint_and_subscribe_match_counts(tmp_path):
    db = tmp_path / "co.db"
    store = Store(db)
    SUBSCRIBE_LIMIT.reset()
    COMPANIES_LIMIT.reset()
    CONFIRM_LIMIT.reset()
    reset_companies_cache()
    for company, title in [("NVIDIA", "SWE Intern"), ("NVIDIA", "ML Intern"),
                           ("NVIDIA Corp", "Systems Intern"),
                           ("Stripe", "SWE Intern"), ("Ghost Co", "Fake Intern")]:
        store.upsert_detection(RawDetection(
            source=Source.GREENHOUSE, company=company, title=title,
            url=f"https://x.co/{company}/{title}",
        ))
    dead = next(p for p in store.by_status(Status.PENDING) if p.company == "Ghost Co")
    dead.status = Status.REJECTED
    store.update(dead)

    server, base = serve(db)
    try:
        r = httpx.get(f"{base}/api/companies")
        assert r.status_code == 200
        # One row per employer whatever the detectors spelled it, richest
        # first, and a rejected-only company is not offered at all.
        assert r.json() == [{"name": "NVIDIA", "postings": 3},
                            {"name": "Stripe", "postings": 1}]

        # The same fold decides the subscribe response, so a name picked out
        # of the list above always reports a non-zero count, and a company
        # we do not track yet reports zero instead of silently matching.
        with patch("intake.web.email_sender", return_value=Mailbox()):
            created = httpx.post(f"{base}/api/subscribe", json={
                "channel": "email", "target": "a@b.edu",
                "filters": {"companies": ["nvidia corp.", "Datadog"]},
            }).json()
        assert created["matches"] == {"nvidia corp.": 3, "Datadog": 0}
        assert Store(db).active_subscriptions()[0]["filters"]["company_keys"] == [
            "datadog", "nvidia"]
    finally:
        SUBSCRIBE_LIMIT.reset()
        COMPANIES_LIMIT.reset()
        CONFIRM_LIMIT.reset()
        reset_companies_cache()
        server.shutdown()


@pytest.mark.skipif(loopback_blocked(), reason="loopback connections blocked")
def test_verify_and_unsubscribe_work_as_plain_link_clicks(tmp_path):
    db = tmp_path / "lnk.db"
    store = Store(db)
    VERIFY_LIMIT.reset()
    UNSUBSCRIBE_LIMIT.reset()
    sid, token, verify_token = store.add_subscription("email", "a@b.edu", {})

    server, base = serve(db)
    try:
        # A mail client can send a link click and nothing else: no JSON body,
        # no header, so both tokens travel in the query string and both
        # answers are HTML.
        page = httpx.get(f"{base}/api/verify?token={verify_token}")
        assert page.status_code == 200
        assert page.headers["content-type"].startswith("text/html")
        assert "Alerts confirmed" in page.text and verify_token not in page.text
        assert Store(db).active_subscriptions()[0]["verified"] is True

        # Idempotent: a second click still confirms rather than erroring.
        assert httpx.get(f"{base}/api/verify?token={verify_token}").status_code == 200
        bad = httpx.get(f"{base}/api/verify?token={'0' * 32}")
        assert bad.status_code == 404 and "not recognized" in bad.text
        assert httpx.get(f"{base}/api/verify").status_code == 404

        off = httpx.get(f"{base}/api/unsubscribe?token={token}")
        assert off.status_code == 200 and "Unsubscribed" in off.text
        assert Store(db).active_subscriptions() == []
        # No confirmation step and no second thought: unsubscribing has to
        # be free and simple, and repeating it changes nothing.
        again = httpx.get(f"{base}/api/unsubscribe?token={token}")
        assert again.status_code == 200 and "Unsubscribed" in again.text
        assert Store(db).active_subscriptions() == []
        assert httpx.get(f"{base}/api/unsubscribe?token=nope").status_code == 404
    finally:
        VERIFY_LIMIT.reset()
        UNSUBSCRIBE_LIMIT.reset()
        server.shutdown()


@pytest.mark.skipif(loopback_blocked(), reason="loopback connections blocked")
def test_one_click_unsubscribe_post_carries_its_token_in_the_query(tmp_path):
    db = tmp_path / "occ.db"
    store = Store(db)
    UNSUBSCRIBE_LIMIT.reset()
    _, token, _ = store.add_subscription("email", "a@b.edu", {})

    server, base = serve(db)
    try:
        # RFC 8058: the mail client POSTs the List-Unsubscribe URL with a
        # form body. The body is not JSON and is never read, so the endpoint
        # must not 400 on it.
        r = httpx.post(f"{base}/api/unsubscribe?token={token}",
                       content=b"List-Unsubscribe=One-Click",
                       headers={"Content-Type": "application/x-www-form-urlencoded"})
        assert r.status_code == 200 and r.json() == {"ok": True}
        assert Store(db).active_subscriptions() == []
    finally:
        UNSUBSCRIBE_LIMIT.reset()
        server.shutdown()


@pytest.mark.skipif(loopback_blocked(), reason="loopback connections blocked")
def test_consent_evidence_is_recorded_and_never_served(tmp_path, caplog):
    db = tmp_path / "consent.db"
    Store(db)
    for limiter in (SUBSCRIBE_LIMIT, CONFIRM_LIMIT, VERIFY_LIMIT, COMPANIES_LIMIT):
        limiter.reset()
    reset_companies_cache()
    # Both requests arrive through Caddy, so the forwarded address is the
    # visitor and the socket address is the proxy.
    signup_ip, click_ip = "198.51.100.23", "203.0.113.7"
    mailbox = Mailbox()

    server, base = serve(db)
    try:
        with caplog.at_level(logging.INFO, logger="intake"):
            with patch("intake.web.email_sender", return_value=mailbox):
                created = httpx.post(
                    f"{base}/api/subscribe",
                    json={"channel": "email", "target": "af@scarletmail.rutgers.edu",
                          "filters": {"companies": ["NVIDIA"]}},
                    headers={"X-Forwarded-For": signup_ip},
                )
            assert created.status_code == 200

            row = Store(db).conn.execute("SELECT * FROM subscriptions").fetchone()
            # Half the opt-in evidence: when the request came in, from where,
            # and that a confirmation actually went out.
            assert row["consent_ip"] == signup_ip and row["created_at"]
            assert row["confirmation_sent_at"] and row["verified_at"] is None

            page = httpx.get(
                f"{base}/api/verify?token={mailbox.sent[0]['verify_token']}",
                headers={"X-Forwarded-For": click_ip},
            )
            assert page.status_code == 200 and "Alerts confirmed" in page.text
            row = Store(db).conn.execute("SELECT * FROM subscriptions").fetchone()
            # The other half: someone at that address clicked, and when.
            assert row["verify_ip"] == click_ip and row["verified_at"]

            served = [created.text, page.text]
            for path in ("/api/postings", "/api/companies", "/api/suggestions"):
                served.append(httpx.get(f"{base}{path}").text)

        # The narrow exception to this project's no-IP rule buys exactly one
        # thing, an answer to "who asked for this mail". It buys no reach:
        # neither address is in a response body or a log line.
        for body in served:
            assert signup_ip not in body and click_ip not in body
        assert signup_ip not in caplog.text and click_ip not in caplog.text
    finally:
        for limiter in (SUBSCRIBE_LIMIT, CONFIRM_LIMIT, VERIFY_LIMIT, COMPANIES_LIMIT):
            limiter.reset()
        reset_companies_cache()
        server.shutdown()


@pytest.mark.skipif(loopback_blocked(), reason="loopback connections blocked")
@pytest.mark.parametrize("sender,sent", [(None, False), (Mailbox(ok=False), False)])
def test_a_signup_nobody_could_mail_waits_instead_of_expiring(sender, sent, tmp_path):
    """No SES sender at all, and an SES that refused. Either way no
    confirmation reached the visitor, so the row is a promise we still owe
    rather than an invitation they ignored."""
    db = tmp_path / "dark.db"
    Store(db)
    SUBSCRIBE_LIMIT.reset()
    CONFIRM_LIMIT.reset()

    server, base = serve(db)
    try:
        with patch("intake.web.email_sender", return_value=sender):
            r = httpx.post(f"{base}/api/subscribe",
                           json={"channel": "email", "target": "early@x.edu"})
        assert r.status_code == 200
        # The contract the frontend keys on to say "alerts are coming soon".
        assert r.json()["verification_sent"] is sent
        assert r.json()["pending_verification"] is True

        store = Store(db)
        row = store.conn.execute("SELECT * FROM subscriptions").fetchone()
        assert row["confirmation_sent_at"] is None
        # Unmailed rows are outside the prune at any window, so the signup
        # survives until sending works and the poller mails it.
        assert store.prune_unverified(0) == 0
        assert [s["id"] for s in store.pending_confirmations()] == [row["id"]]
    finally:
        SUBSCRIBE_LIMIT.reset()
        CONFIRM_LIMIT.reset()
        server.shutdown()


@pytest.mark.skipif(loopback_blocked(), reason="loopback connections blocked")
def test_unsubscribing_suppresses_before_the_next_fan_out(tmp_path):
    db = tmp_path / "sup.db"
    store = Store(db)
    UNSUBSCRIBE_LIMIT.reset()
    stay, _, stay_verify = store.add_subscription("email", "stay@x.edu", {})
    off, token, off_verify = store.add_subscription("email", "off@x.edu", {})
    store.verify_by_token(stay_verify)
    store.verify_by_token(off_verify)
    posting = Posting.from_detection(RawDetection(
        source=Source.GREENHOUSE, company="Stripe",
        title="Software Engineer Intern", url="https://stripe.com/jobs/1",
    ))

    def fan_out():
        sends = []
        notify_new_postings(Store(db).active_subscriptions(), [posting],
                            lambda s, ps: sends.append(s["id"]))
        return sends

    server, base = serve(db)
    try:
        assert fan_out() == [stay, off]
        r = httpx.post(f"{base}/api/unsubscribe", json={"token": token})
        assert r.status_code == 200 and r.json() == {"ok": True}
        # The row is inactive by the time the response arrives: no queue, no
        # batch, nothing left to drain before the next cycle honors it.
        assert fan_out() == [stay]

        # The emailed one-click path lands the same way.
        again = httpx.get(f"{base}/api/unsubscribe?token={token}")
        assert again.status_code == 200
        assert fan_out() == [stay]
    finally:
        UNSUBSCRIBE_LIMIT.reset()
        server.shutdown()


@pytest.mark.skipif(loopback_blocked(), reason="loopback connections blocked")
def test_suggest_rejects_implausible_company_values(tmp_path):
    db = tmp_path / "v.db"
    Store(db)
    SUGGEST_LIMIT.reset()  # the limiter is process-wide state shared by tests
    server, base = serve(db)
    try:
        # A company name reaches a paid LLM call, so garbage is refused at the
        # door rather than queued. 5 requests: exactly the per-minute window.
        bad = [
            {"kind": "company", "value": "x" * 81},
            {"kind": "company", "value": "12345"},
            {"kind": "company", "value": "https://linkedin.com/jobs"},
            {"kind": "company", "value": "   "},
            {"kind": "sms", "value": "Goldman Sachs"},
        ]
        for payload in bad:
            assert httpx.post(f"{base}/api/suggest", json=payload).status_code == 400
        assert Store(db).recent_suggestions() == []

        SUGGEST_LIMIT.reset()
        ok = httpx.post(f"{base}/api/suggest", json={
            "kind": "company", "value": "Goldman\r\n Sachs", "keywords": "k" * 300,
        })
        assert ok.status_code == 200 and ok.json()["id"] >= 1
        row = Store(db).recent_suggestions()[0]
        assert "\r" not in row["value"] and len(row["keywords"]) == 120
    finally:
        server.shutdown()


@pytest.mark.skipif(loopback_blocked(), reason="loopback connections blocked")
def test_suggest_collapses_duplicate_submissions(tmp_path):
    db = tmp_path / "d.db"
    Store(db)
    SUGGEST_LIMIT.reset()
    server, base = serve(db)
    try:
        first = httpx.post(f"{base}/api/suggest",
                           json={"kind": "company", "value": "Goldman Sachs"})
        assert first.status_code == 200 and "duplicate" not in first.json()

        # Same company, different spelling: one unit of work, same success
        # shape, so a flood of resubmissions cannot multiply resolver spend.
        for value in ("goldman sachs", "  GOLDMAN SACHS. "):
            again = httpx.post(f"{base}/api/suggest",
                               json={"kind": "company", "value": value})
            assert again.status_code == 200
            assert again.json() == {"id": first.json()["id"], "duplicate": True}

        dupe_url = httpx.post(f"{base}/api/suggest", json={
            "kind": "url", "value": "https://higher.gs.com/roles/1",
        })
        assert dupe_url.status_code == 200 and "duplicate" not in dupe_url.json()
        assert len(Store(db).recent_suggestions()) == 2
    finally:
        SUGGEST_LIMIT.reset()
        server.shutdown()


def test_snapshot_skips_reread_when_version_unchanged(monkeypatch):
    """The cache must re-read only when the data actually changed.

    This is the behaviour that keeps a hosted Postgres bill flat: the board
    refetches on a timer from every open tab, and without this each refetch
    was a full table read.
    """
    from intake import web

    class FakeStore:
        cache_key = "fake"

        def __init__(self):
            self.reads = 0
            self.version = "v1"

        def data_version(self):
            return self.version

        def all_postings(self):
            self.reads += 1
            return [f"rows-{self.reads}"]

    monkeypatch.setattr(web, "_snapshot", None)
    monkeypatch.setattr(web, "SNAPSHOT_TTL_S", 0.0)  # force the version check
    store = FakeStore()

    first = web.postings_snapshot(store)
    assert store.reads == 1

    again = web.postings_snapshot(store)
    assert store.reads == 1          # unchanged version: no second read
    assert again is first            # and the same rows are handed back

    store.version = "v2"
    changed = web.postings_snapshot(store)
    assert store.reads == 2          # a real change does re-read
    assert changed != first

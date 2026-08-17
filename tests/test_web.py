"""Web server tests: real HTTP server on an ephemeral port, real store.
Covers the dashboard smoke path, the subscription endpoints, and the
suggestion endpoint's spend defenses (validation and duplicate collapsing).

Skips when the environment forbids loopback connections (some sandboxes do).
"""

import socket
import threading
import time
from http.server import ThreadingHTTPServer

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

from intake.schema import RawDetection, Source
from intake.store import Store
from intake.web import SUGGEST_LIMIT, make_handler


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
def test_subscribe_and_unsubscribe_endpoints(tmp_path):
    db = tmp_path / "s.db"
    Store(db)  # create the schema before the first request

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

        r = httpx.post(f"{base}/api/subscribe", json={
            "channel": "email", "target": "af@scarletmail.rutgers.edu",
            "filters": {"companies": ["NVIDIA"]},
        })
        assert r.status_code == 200
        created = r.json()
        assert created["id"] >= 1 and len(created["token"]) == 32
        subs = Store(db).active_subscriptions()
        assert subs[0]["channel"] == "email"
        assert subs[0]["filters"] == {"companies": ["NVIDIA"]}

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

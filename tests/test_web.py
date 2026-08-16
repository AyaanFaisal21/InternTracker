"""Web server tests: real HTTP server on an ephemeral port, real store.
Covers the dashboard smoke path and the subscription endpoints.

Skips when the environment forbids loopback connections (some sandboxes do).
"""

import socket
import threading
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
from intake.web import make_handler


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

    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(db))
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{port}"
        html = None
        for _ in range(20):  # wait for the listener thread
            try:
                html = httpx.get(f"{base}/")
                break
            except httpx.ConnectError:
                import time
                time.sleep(0.05)
        assert html is not None, "server never came up"
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

    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(db))
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{port}"
        up = None
        for _ in range(20):  # wait for the listener thread
            try:
                up = httpx.get(f"{base}/")
                break
            except httpx.ConnectError:
                import time
                time.sleep(0.05)
        assert up is not None, "server never came up"

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

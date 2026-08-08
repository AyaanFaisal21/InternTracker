"""Dashboard smoke test: real HTTP server on an ephemeral port, real store.

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
        assert html.status_code == 200 and "RUemployed intake" in html.text

        api = httpx.get(f"{base}/api/postings")
        assert api.status_code == 200
        postings = api.json()
        assert len(postings) == 1
        assert postings[0]["company"] == "Stripe"
        assert postings[0]["status"] == "pending"

        assert httpx.get(f"{base}/nope").status_code == 404
    finally:
        server.shutdown()

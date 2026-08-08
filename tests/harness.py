"""Test harness utilities.

fixture_client(): an httpx.Client whose transport replays fixture JSON
instead of touching the network. Route by URL substring. Unmatched URLs
return 404 so an unexpected request fails the test instead of escaping.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str):
    return json.loads((FIXTURES / name).read_text())


def fixture_client(routes: dict[str, object]) -> httpx.Client:
    """routes: url-substring -> JSON-serializable payload."""

    def handler(request: httpx.Request) -> httpx.Response:
        for needle, payload in routes.items():
            if needle in str(request.url):
                if isinstance(payload, str):
                    return httpx.Response(200, text=payload)
                return httpx.Response(200, json=payload)
        return httpx.Response(404, text="no fixture route")

    return httpx.Client(transport=httpx.MockTransport(handler))

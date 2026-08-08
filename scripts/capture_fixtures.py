"""Refresh test fixtures from live ATS APIs.

Run when a detector test fails against real data: capture, diff the fixture,
fix the parser. Trims payloads to the first few entries to keep fixtures
reviewable.

Usage: python scripts/capture_fixtures.py
"""

import json
from pathlib import Path

import httpx

OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "live"
SOURCES = {
    "greenhouse_stripe.json": "https://boards-api.greenhouse.io/v1/boards/stripe/jobs",
    "lever_palantir.json": "https://api.lever.co/v0/postings/palantir?mode=json",
    "ashby_ramp.json": "https://api.ashbyhq.com/posting-api/job-board/ramp",
}


def trim(payload, n=5):
    if isinstance(payload, list):
        return payload[:n]
    if isinstance(payload, dict) and isinstance(payload.get("jobs"), list):
        return {**payload, "jobs": payload["jobs"][:n]}
    return payload


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    client = httpx.Client(timeout=30.0, follow_redirects=True)
    for name, url in SOURCES.items():
        resp = client.get(url)
        resp.raise_for_status()
        (OUT / name).write_text(json.dumps(trim(resp.json()), indent=2))
        print(f"captured {name} ({resp.status_code})")


if __name__ == "__main__":
    main()

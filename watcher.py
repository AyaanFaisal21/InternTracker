"""Poll job boards for new internship postings and push a phone notification.

    python3 watcher.py --dry-run     # print instead of notify
    python3 watcher.py               # notify via NTFY_TOPIC / DISCORD_WEBHOOK_URL

Designed to run headless on a GitHub Actions cron. State (which postings have
been seen) lives in state/seen.json and is committed back by the workflow, so
the whole system is a repo: no server, no database, and an audit trail of when
every posting was first detected.

First run against an empty state SEEDS silently: every current posting is
recorded and none are notified, because "here are 200 jobs that already
existed" is noise, not signal. Only postings that appear after the seed fire.

Sources are adapters over the JSON APIs the big applicant-tracking systems
expose. Workday is implemented for NVIDIA; greenhouse/lever/ashby adapters are
included so adding a company is a config edit, not code.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys
import time
from dataclasses import dataclass

import requests
import yaml

ROOT = pathlib.Path(__file__).resolve().parent
STATE_FILE = ROOT / "state" / "seen.json"
CONFIG_FILE = ROOT / "config.yaml"

UA = {"User-Agent": "internwatch/1.0 (personal job-alert tool)"}


@dataclass
class Job:
    company: str
    id: str
    title: str
    url: str
    location: str
    posted: str


# --------------------------------------------------------------------- sources

def fetch_workday(c: dict) -> list[Job]:
    """Workday's unofficial JSON search. Paginated, 20 per page.

    The search is fuzzy (a query for "intern" also returns adjacent roles), so
    the title regex in config does the real filtering, not the search term.
    """
    base = f"https://{c['host']}/wday/cxs/{c['tenant']}/{c['site']}"
    jobs, offset = [], 0
    ids_seen: set[str] = set()
    total = None  # taken from the first response
    while offset < c.get("max_results", 1200):
        r = requests.post(
            f"{base}/jobs",
            json={
                "appliedFacets": {},
                "limit": 20,
                "offset": offset,
                "searchText": c.get("search", "intern"),
            },
            headers={**UA, "Content-Type": "application/json"},
            timeout=30,
        )
        r.raise_for_status()
        body = r.json()
        if total is None:
            total = int(body.get("total", 0))
        page = body.get("jobPostings", [])
        if not page:
            break
        for p in page:
            bullet = p.get("bulletFields") or []
            jid = bullet[0] if bullet else p.get("externalPath", "")
            # Relevance-ranked pagination is not stable: rows shift between
            # pages mid-scroll, so the same posting can appear on several
            # pages (and occasionally one slips between them — the next poll
            # self-heals that). Dedupe here or one job counts as fifteen.
            if not jid or str(jid) in ids_seen:
                continue
            ids_seen.add(str(jid))
            jobs.append(Job(
                company=c["name"],
                id=str(jid),
                title=p.get("title", ""),
                url=f"https://{c['host']}/en-US/{c['site']}{p.get('externalPath', '')}",
                location=p.get("locationsText", ""),
                posted=p.get("postedOn", ""),
            ))
        offset += 20
        if total is not None and offset >= total + 40:
            break
        time.sleep(0.2)  # politeness between pages
    return jobs


def fetch_greenhouse(c: dict) -> list[Job]:
    r = requests.get(
        f"https://boards-api.greenhouse.io/v1/boards/{c['board']}/jobs",
        headers=UA, timeout=30,
    )
    r.raise_for_status()
    return [
        Job(c["name"], str(j["id"]), j.get("title", ""), j.get("absolute_url", ""),
            (j.get("location") or {}).get("name", ""), j.get("updated_at", ""))
        for j in r.json().get("jobs", [])
    ]


def fetch_lever(c: dict) -> list[Job]:
    r = requests.get(
        f"https://api.lever.co/v0/postings/{c['org']}?mode=json",
        headers=UA, timeout=30,
    )
    r.raise_for_status()
    return [
        Job(c["name"], j.get("id", ""), j.get("text", ""), j.get("hostedUrl", ""),
            (j.get("categories") or {}).get("location", ""), "")
        for j in r.json()
    ]


def fetch_ashby(c: dict) -> list[Job]:
    r = requests.get(
        f"https://api.ashbyhq.com/posting-api/job-board/{c['org']}",
        headers=UA, timeout=30,
    )
    r.raise_for_status()
    return [
        Job(c["name"], j.get("id", ""), j.get("title", ""), j.get("jobUrl", ""),
            j.get("location", ""), "")
        for j in r.json().get("jobs", [])
    ]


FETCHERS = {
    "workday": fetch_workday,
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "ashby": fetch_ashby,
}


# --------------------------------------------------------------------- filters

def wanted(job: Job, c: dict) -> bool:
    include = c.get("title_include", r"(?i)\bintern(ship)?\b|co-?op")
    exclude = c.get("title_exclude", "")
    if not re.search(include, job.title):
        return False
    if exclude and re.search(exclude, job.title):
        return False
    return True


# ---------------------------------------------------------------------- notify

def notify_ntfy(topic: str, jobs: list[Job]) -> None:
    for j in jobs:
        requests.post(
            f"https://ntfy.sh/{topic}",
            data=f"{j.title}\n{j.location}  ({j.posted})".encode(),
            headers={
                **UA,
                "Title": f"New {j.company} internship",
                "Click": j.url,
                "Priority": "high",
                "Tags": "rotating_light",
            },
            timeout=30,
        )


def notify_discord(webhook: str, jobs: list[Job]) -> None:
    lines = [f"**{j.company}** — [{j.title}]({j.url})\n{j.location}  ({j.posted})"
             for j in jobs]
    requests.post(webhook, json={"content": "\n\n".join(lines)[:1900]}, timeout=30)


# ----------------------------------------------------------------------- state

def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=1, sort_keys=True))


# ------------------------------------------------------------------------ main

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="print, don't notify")
    args = ap.parse_args()

    config = yaml.safe_load(CONFIG_FILE.read_text())
    state = load_state()
    new_jobs: list[Job] = []

    for c in config["companies"]:
        if not c.get("enabled", True):
            continue
        try:
            jobs = FETCHERS[c["type"]](c)
        except Exception as exc:
            # One board failing must not block the others; the workflow's own
            # failure email covers persistent breakage.
            print(f"[warn] {c['name']}: {type(exc).__name__}: {exc}", file=sys.stderr)
            continue

        matched = [j for j in jobs if wanted(j, c)]
        seen: dict = state.setdefault(c["name"], {})
        seeding = not seen

        fresh = [j for j in matched if j.id not in seen]
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        for j in fresh:
            seen[j.id] = {"title": j.title, "first_seen": now}

        label = "seeded" if seeding else "new"
        print(f"{c['name']}: {len(jobs)} fetched, {len(matched)} match filters, "
              f"{len(fresh)} {label}")
        if not seeding:
            new_jobs.extend(fresh)

    save_state(state)

    if not new_jobs:
        return 0

    for j in new_jobs:
        print(f"  NEW  {j.company}  {j.title}  {j.location}  {j.url}")

    if args.dry_run:
        return 0

    # Cap a pathological burst (board re-index churning IDs) so a bug cannot
    # page the phone fifty times.
    burst = new_jobs[:12]
    topic = os.environ.get("NTFY_TOPIC")
    webhook = os.environ.get("DISCORD_WEBHOOK_URL")
    if topic:
        notify_ntfy(topic, burst)
    if webhook:
        notify_discord(webhook, burst)
    if not topic and not webhook:
        print("[warn] no NTFY_TOPIC or DISCORD_WEBHOOK_URL set; found new jobs "
              "but had nowhere to send them", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

# InternTracker

Watches job boards for new internship postings and pushes a phone notification
the moment one appears. Runs on a GitHub Actions cron, so it works with the
laptop closed: no server, no database — state is a JSON file committed back to
this repo, which doubles as an audit trail of when each posting was first seen.

Currently watching: **NVIDIA** (Workday), PhD-labelled roles excluded.

## How it works

- `watcher.py` polls each board's JSON API (Workday implemented; greenhouse,
  lever and ashby adapters included), filters titles by regex, and diffs
  against `state/seen.json`.
- First run against an empty state seeds silently — recording 200 postings
  that already existed is noise, not signal. Only postings that appear after
  the seed notify.
- Notifications go to [ntfy.sh](https://ntfy.sh) (instant push, no account)
  and optionally a Discord webhook. Both are repo secrets.

## Run locally

    pip install -r requirements.txt
    python3 watcher.py --dry-run

## Add a company

Edit `config.yaml`. Greenhouse/lever/ashby boards need one line each; Workday
needs host + tenant + site (readable off any posting URL).

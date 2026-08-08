# RUemployed — Intake Engine

Detects SWE internship postings from multiple sources. Merges them into one
pipeline. Verifies each posting before it can publish. Nothing publishes
without a stored verdict.

## Why multiple sources

Community list repos (SimplifyJobs et al.) miss postings and add latency.
This engine polls ATS APIs directly for watched companies. The list repos
remain as a wide-coverage backstop.

| Detector | Method | Latency | Coverage |
|---|---|---|---|
| Greenhouse | public board JSON API | seconds after publish | watchlist only |
| Lever | public postings JSON API | seconds after publish | watchlist only |
| Ashby | public job-board JSON API | seconds after publish | watchlist only |
| GitHub lists | listings.json diff | minutes–hours | wide |

## Pipeline

```
detectors ──> dedupe/merge ──> rule gate ──> verifier agent ──> publish
 (poll)        (store)         (cheap,        (Claude,           (hook)
                               final          structured
                               reject)        verdict)
```

- **Dedupe**: key = normalized company + title. The same job found by two
  detectors becomes one record with merged sources. Re-polls are idempotent.
- **Rule gate**: deterministic checks. Dead URL, disqualifying terms,
  non-SWE role. Rejects cheaply so the agent only sees ambiguous cases.
- **Verifier agent**: fetches the posting page and asks Claude for a
  structured verdict (`is_swe_internship`, `is_open`, `is_legitimate`,
  `season`, `confidence`, `reasons`). The schema is API-enforced. A closed
  or ghost posting that still returns HTTP 200 fails here, not in rules.
- **Publish**: `default_publisher` logs. Replace it with the web-app API
  call when the frontend exists.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
export ANTHROPIC_API_KEY=sk-ant-...
```

## Run

```bash
intake run              # one cycle
intake loop -i 120      # continuous, 120 s interval
pytest                  # tests (no network, no API key needed)
```

## Configure

Edit `config/watchlist.yaml`. Add board tokens/slugs for companies you care
about. State lives in `intake.db` (SQLite).

## Known limits (v1)

- Dedupe collapses identical titles at one company across locations.
- Workday boards are not covered. Their API is per-tenant and hostile;
  add a detector when a watched company requires it.
- Verifier cost scales with new postings, not poll frequency — dedupe
  ensures each posting is verified once.

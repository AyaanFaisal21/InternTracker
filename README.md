# Shortlist

Multi-source intake engine and public job board for software engineering
(SWE) internships. In production at https://short-list.app. Formerly
RUemployed.

Detectors poll company hiring systems directly. A pipeline merges duplicate
sightings, rejects junk with deterministic rules, and asks a Claude agent
for a structured verdict before any posting can reach published status.
A React board serves the result. The feed is built for Rutgers CS students
first. Nothing in it is Rutgers-only.

## Why multiple sources

Community list repos (SimplifyJobs et al.) miss postings and add latency.
Shortlist polls applicant tracking system (ATS) APIs directly for watched
companies and keeps the list repos as a wide backstop. Companies without a
public API get custom adapters. Companies that gate their APIs behind
browser tokens get a real browser.

| Detector | Method | Source latency | Coverage |
|---|---|---|---|
| Greenhouse | public board JSON API | seconds after publish | watchlist |
| Lever | public postings JSON API | seconds after publish | watchlist |
| Ashby | public job-board JSON API | seconds after publish | watchlist |
| Workday | per-tenant CXS JSON API, one adapter for every tenant | seconds after publish | watchlist |
| Google | server-rendered careers HTML | seconds after publish | Google |
| Microsoft | public careers search JSON API | seconds after publish | Microsoft |
| Apple | Playwright browser capture | seconds after publish | Apple |
| Suggestions | visitor-submitted URLs and company probes | next cycle | anything |
| GitHub lists | listings.json diff | minutes to hours | wide |
| Curated lists | listings.json and README-table parse | minutes to hours | wide |

Workday looks per-company but is not. Every tenant serves the same JSON
endpoint on its own subdomain, so one adapter plus four config lines covers
NVIDIA, Schweitzer, or any other Workday employer.

Apple gates its search API behind tokens, and its server-rendered pages
ignore query parameters. The detector launches headless Chromium
(Playwright), types the query into the site's own search box, and captures
the JSON response the site sends its own frontend. The site mints its own
tokens. The engine never reverses them.

Visitors contribute from the board. A submitted URL is ingested directly.
A submitted company name is probed against the Greenhouse, Lever, and Ashby
APIs under likely slug variants. A hit emits detections and reports the
board name so it can join the watchlist. Every submission still passes the
rule gate and the verifier path.

Curated lists carry more than internships: fellowships, scholarships,
research programs, and recruiting events, tagged by category and audience
(underclassmen, diversity). Aggregator links are resolved to the employer's
own page before anything is shown.

Coverage scales with `config/watchlist.yaml`. There is no global ATS index,
so board tokens must be enumerated. The marginal cost of one more company
is one HTTP request per cycle.

## Pipeline

```
detectors --> dedupe/merge --> rule gate --> verifier agent --> publish
  (poll)       (store)         (cheap,       (Claude,           (status
                               final         structured          change
                               reject)       verdict)            + hook)
```

Statuses: `pending -> gated -> verified -> published`, with `rejected`
possible at the gate or the verdict.

- **Dedupe.** Key = SHA-1 of normalized company + title. The same job found
  by two detectors becomes one record with merged sources and locations.
  Re-polls are idempotent. Only new records enter verification, so verifier
  cost scales with new postings, not poll frequency.
- **Rule gate.** Deterministic checks and enrichment. Titles with scam
  markers (unpaid, brand ambassador, commission-only) are rejected.
  Non-tech titles without any tech signal are rejected. The gate resolves
  each URL to the employer's canonical page: it follows redirects, unwraps
  social redirectors (l.instagram.com et al.), and strips utm/ref tracking
  while keeping ATS-functional parameters. A 403 or 429 is treated as a bot
  wall, not a dead posting. The gate also classifies degree levels
  (BS/MS/PhD) from title and page text, extracts a qualifications excerpt,
  parses the season, promotes event pages to the event category, and tags
  audiences. A rule rejection is final. A pass is not approval. It only
  forwards the posting.
- **Verifier agent.** Fetches the posting page, strips it to text, and asks
  Claude for a verdict against the detection metadata: `is_swe_internship`,
  `is_open`, `is_legitimate`, `season`, `date_posted`, `degree_levels`,
  `confidence`, `reasons`. The schema is enforced by the API (structured
  outputs), so there is no free-text parsing. A closed or ghost posting
  that still returns HTTP 200 fails here, not at the gate. Verdict fields
  override the gate's heuristics. A verifier error leaves the posting gated
  for retry next cycle. Verification is currently switched off in
  production (`--no-verify`) pending a review policy, so postings park at
  `gated`.
- **Publish.** A status change plus a hook (currently a log line). The
  board reads the store directly and shows every posting with its live
  status, so a row is visible from first detection. `published` marks rows
  that hold an approved verdict. Nothing reaches `published` without one.
  The Supabase public REST surface exposes published rows only (see
  Storage).

## Storage

One store interface, two backends. `DATABASE_URL` picks the backend at
runtime: unset means SQLite, set means Postgres.

**SQLite** is the local default (`intake.db`). The poller and the web
server run as separate processes against one file, so the store enables
write-ahead logging (WAL) and a busy timeout. Web handlers open a cheap
connection per request. Schema changes apply as additive column migrations
on open.

**Postgres** runs production on Supabase. The backend (psycopg 3) holds one
connection per process behind a lock, uses autocommit, and reopens plus
retries once on a dead connection. Every write is a single idempotent
upsert, so the retry cannot double-apply. jsonb and timestamptz replace
SQLite's text encodings.

The Postgres schema lives in `scripts/supabase_schema.sql` (idempotent,
applied out of band): a status check constraint, indexes on status and
freshness, and an `updated_at` trigger. Row-level security is enabled on
every table with one policy: anonymous and authenticated roles may read
postings with status `published`, and nothing else. Supabase serves every
public table over its REST API with a key that ships in any frontend, so
this policy is the fence that keeps unverified rows private. Pipeline
writes are unaffected because the owning role bypasses row-level security.

`scripts/migrate_sqlite_to_postgres.py` moved the production data:
checkpoint the WAL, copy rows verbatim, `ON CONFLICT DO NOTHING` for
postings, skip tables that already hold rows. Safe to re-run.

## Web API

The API server is Python stdlib `http.server`, threaded, no framework, no
third-party web dependencies.

| Route | Method | Purpose |
|---|---|---|
| `/api/postings` | GET | all postings, newest first, plus derived `countries` and `role` fields |
| `/api/suggestions` | GET | recent suggestions with status |
| `/api/suggest` | POST | queue a posting URL or company name |
| `/api/visit` | POST | page-view beacon |

Suggest and visit are rate limited per client IP with an in-process sliding
window (5 and 30 per minute), reading `X-Forwarded-For` behind the proxy.
Locally the server binds both loopback families, because some systems
resolve localhost to ::1. In the container it binds 0.0.0.0. The server
also renders a legacy HTML dashboard at `/` and `/listings` for
dependency-free local inspection. Production routes those paths to the
React build instead.

## Frontend

React 19 with TypeScript, built by Vite. `react-router-dom` is the only
other runtime dependency. Two routes:

- `/` renders an org-profile landing: live posting and company counts,
  pinned collections, preset views per season and degree with 21-day
  activity sparklines, and role topic chips.
- `/listings` renders the board: sidebar filters (status, type, audience,
  degree, role, posted recency, country, season), text search, spotlight
  cards, the contribute form, and rows that expand to qualifications,
  locations, and verifier output.

All backend I/O lives in `frontend/src/api.ts` with typed payloads and
relative URLs. Swapping backends means replacing that one file. The board
refreshes every 30 seconds. Monotonic request ids guard the refresh, so a
slow stale response can never overwrite newer data.

Dev server: `npm run dev` on http://localhost:5173, proxying `/api` to
`VITE_API_TARGET` (default http://localhost:8642). `npm run build`
type-checks (`tsc -b`) and bundles to `dist/`.

## Production

Everything runs on one EC2 node under Docker Compose. Data lives in
Supabase.

| Service | Base | Job |
|---|---|---|
| caddy | caddy:2-alpine | TLS, static frontend, `/api` reverse proxy |
| web | python:3.13-slim | the stdlib API server on port 8642 |
| poller | python:3.13-slim + Chromium | detection loop, one cycle every 120 s |
| frontend | node:22-slim build stage | builds the React bundle into a shared volume, then exits |

Caddy terminates TLS with an automatic Let's Encrypt certificate for
`short-list.app`. One origin serves everything: `/api/*` proxies to the web
container, every other path serves the React build, and unknown paths fall
back to `index.html` so client-side routes resolve on direct load. No
CORS, no subdomains. The `.app` TLD is HSTS-preloaded. Browsers refuse
plain HTTP on it, so the site exists only over TLS.

**CI/CD.** Every push and pull request runs the Python test suite and the
frontend type-check plus build. A push to main that passes both deploys
over ssh. The deploy key on the server is a forced-command key: whatever
command the client requests, the server runs `scripts/deploy.sh` (git pull
`--ff-only`, compose build, compose up) and nothing else. The workflow pins
the server's host key, so there is no trust-on-first-use. A concurrency
group serializes deploys.

**Backups.** A nightly workflow dumps the Supabase database with
`pg_dump`, encrypts the dump with GPG (AES-256, symmetric), and stores it
as a GitHub Actions artifact for 90 days. The workflow also runs on manual
dispatch. Restore commands are documented in
`.github/workflows/backup.yml`.

## Local development

Python 3.11+.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

Optional extras:

```bash
pip install -e ".[postgres]"    # Postgres backend (psycopg)
pip install -e ".[browser]" && playwright install chromium    # Apple tier
export ANTHROPIC_API_KEY=sk-ant-...    # verifier only
```

Run:

```bash
intake run --no-verify   # one cycle, detect + gate only, no API key needed
intake run               # one cycle with verification
intake loop -i 120       # continuous, 120 s interval
intake serve -p 8642     # API server
pytest                   # 68 tests, no network, ~2 s
```

Frontend:

```bash
cd frontend && npm install && npm run dev
```

Full stack, as deployed:

```bash
docker compose up -d --build
```

## Configuration

`config/watchlist.yaml` declares what to poll: Greenhouse board tokens,
Lever slugs, Ashby board names, Workday tenants (`company`, `host`,
`tenant`, `site`), the custom and browser adapter keys, and the list URLs.
Add a board and the next cycle covers that company.

| Variable | Effect |
|---|---|
| `DATABASE_URL` | set: Postgres backend. unset: SQLite. |
| `INTAKE_DB` | SQLite path. Default `intake.db`. |
| `INTAKE_WATCHLIST` | watchlist path. Default `config/watchlist.yaml`. |
| `ANTHROPIC_API_KEY` | verifier credential. Needed only when verification is on. |
| `INTAKE_BIND` | web server bind address. Default: both loopbacks. |
| `WEB_PORT` | compose: host port for the web container. Default 8642. |
| `DOMAIN` | compose: hostname for automatic HTTPS. Default `:80` for bare-IP access. |
| `SUPABASE_URL_POSTGRES` | compose: fills `DATABASE_URL` in web and poller. |

The verifier model is set by `verifier_model` in `src/intake/config.py`.

## Testing

68 tests run in about two seconds and never touch the network. A fixture
harness routes detector HTTP through `httpx.MockTransport`: URL substrings
map to canned payloads, and any unmatched request returns 404 so nothing
escapes. Fixtures are trimmed captures of live ATS responses.
`scripts/capture_fixtures.py` refreshes them when a real API drifts. The
web test boots a real HTTP server on an ephemeral loopback port against a
real store, and skips itself where a sandbox blocks loopback. CI runs the
suite on every push and pull request.

## Known limits

- Dedupe collapses identical titles at one company across locations. The
  fix path is salting identity with the ATS requisition id, which
  detectors already store in the payload.
- Verification is off in production pending a review policy. Postings park
  at `gated`, and the board labels each row with its status.
- Detector prefiltering is title-based (intern and tech-signal regexes).
  A role titled without a recognizable keyword is not picked up. Curated
  lists skip the prefilter, so they can still surface such roles.
- Rate limiting is in-process and per-container. It is sized for abuse
  resistance, not precision.
- One node, one region, by design. Expected load is small, and the ladder
  climbs only as needed.

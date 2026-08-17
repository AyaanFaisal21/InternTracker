-- Shortlist schema for Supabase Postgres.
-- Paste into Supabase Dashboard -> SQL Editor -> New query -> Run.
-- Mirrors the SQLite schema in src/intake/store.py with native types.
-- Safe to re-run (idempotent).

create table if not exists postings (
    id               text primary key,  -- sha1[:16] of company|title (schema.py dedupe_key)
    company          text not null,
    title            text not null,
    url              text not null,     -- as detected
    canonical_url    text,              -- resolved employer page; publish this, never url
    category         text not null default 'internship',
    audience         jsonb not null default '[]',
    degree_levels    jsonb not null default '[]',
    date_posted      timestamptz,
    date_posted_text text,
    season           text,
    qualifications   text,
    locations        jsonb not null default '[]',
    sources          jsonb not null default '[]',
    first_seen       timestamptz not null,
    status           text not null default 'pending'
                     check (status in ('pending','gated','verified','rejected','published')),
    reject_reason    text,
    verdict          jsonb,
    updated_at       timestamptz not null default now()
);

-- Query patterns: pipeline selects by_status; dashboard orders by freshness.
create index if not exists postings_status_idx on postings (status);
create index if not exists postings_fresh_idx
    on postings ((coalesce(date_posted, first_seen)) desc);

create table if not exists suggestions (
    id         bigint generated always as identity primary key,
    kind       text not null check (kind in ('url','company')),
    value      text not null,
    company    text,
    keywords   text,
    status     text not null default 'new'
               check (status in ('new','matched','no_match','error')),
    result     text,
    created_at timestamptz not null default now()
);

create table if not exists visits (
    id   bigint generated always as identity primary key,
    page text not null,
    ua   text,
    at   timestamptz not null default now()
);

-- Keep postings.updated_at honest without depending on the moddatetime
-- extension (which Supabase installs into a nonstandard schema).
create or replace function set_updated_at() returns trigger
language plpgsql as $$
begin
    new.updated_at = now();
    return new;
end $$;

create or replace trigger postings_updated_at
    before update on postings
    for each row execute function set_updated_at();

-- Supabase serves every public-schema table over its REST API using the
-- anon key that ships inside any frontend. RLS on + one narrow policy
-- means that API exposes only published postings; pipeline writes are
-- unaffected because the postgres role owns the tables and bypasses RLS.
alter table postings   enable row level security;
alter table suggestions enable row level security;
alter table visits     enable row level security;

drop policy if exists "public reads published postings" on postings;
create policy "public reads published postings"
    on postings for select
    to anon, authenticated
    using (status = 'published');

-- Later, if the frontend should write suggestions straight to Supabase
-- (skipping the EC2 endpoint), open inserts narrowly:
-- create policy "public files suggestions"
--     on suggestions for insert
--     to anon, authenticated
--     with check (kind in ('url','company'));

-- Notification subscriptions. The delivery channel (web push vs email) is
-- undecided: target holds the push endpoint JSON or the email address,
-- filters narrows fan-out to named companies ('{}' = every company), and
-- token is the opaque unsubscribe secret the API hands the subscriber.
create table if not exists subscriptions (
    id         bigint generated always as identity primary key,
    channel    text not null check (channel in ('push','email')),
    target     text not null,
    filters    jsonb not null default '{}',
    token      text not null unique,
    created_at timestamptz not null default now(),
    active     boolean not null default true
);

-- RLS on with no policies: targets and tokens must never reach the anon
-- REST surface. Every subscription read and write flows through our API
-- server, whose owning role bypasses RLS.
alter table subscriptions enable row level security;

-- Company resolver (src/intake/resolve.py). /api/suggest is public, so every
-- resolution is an anonymous visitor spending Anthropic credit. These two
-- tables are the spend defense: the cache turns a flood of repeated
-- submissions into one paid call, and the per-day counter is the hard
-- ceiling. key is norm_text(company), so casing and punctuation collapse.
create table if not exists company_resolutions (
    key         text primary key,
    company     text not null,          -- as submitted, for display
    resolution  jsonb not null,         -- resolve.CompanyResolution
    resolved_at timestamptz not null default now()
);

-- Negative results are cached too, so a name that resolves to nothing is not
-- cheaper to re-trigger than one that resolves.
create index if not exists company_resolutions_fresh_idx
    on company_resolutions (resolved_at desc);

create table if not exists resolver_spend (
    day   date primary key,             -- UTC day the calls were made
    calls integer not null default 0
);

-- RLS on with no policies, same reasoning as subscriptions: resolver output
-- and spend counts are internal. Reads and writes flow through the pipeline
-- and the API server, whose owning role bypasses RLS.
alter table company_resolutions enable row level security;
alter table resolver_spend      enable row level security;

-- Unique visitors without IP retention (src/intake/web.py visitor_hash).
-- visitor_hash is sha256(that day's salt + ip + user agent) cut to 16 hex
-- chars: repeatable within one UTC day, unrelatable across days, and derived
-- from an address that is never written anywhere. device replaces the raw
-- user agent, which is a fingerprint on its own, with a coarse class. ua
-- keeps the rows written before this change; nothing writes it now.
alter table visits add column if not exists visitor_hash text;
alter table visits add column if not exists device text
    check (device in ('mobile','desktop','bot'));

-- Query pattern: every rollup in scripts/metrics.sql windows on at.
create index if not exists visits_fresh_idx on visits (at desc);

-- The salt behind visitor_hash, one row per UTC day. Rows older than two
-- days are deleted as each new salt is minted, and a deleted salt makes that
-- day's hashes irreversible even to us, which is the whole point of the
-- design. Durable rather than in-process because the web container restarts
-- on every deploy.
create table if not exists visit_salts (
    day  date primary key,              -- UTC day the salt covers
    salt text not null
);

-- RLS on with no policies, and this table is the strictest case of it: a
-- leaked live salt would let anyone re-derive today's hashes by guessing
-- addresses. visits already runs RLS with no policies, so the hashes
-- themselves stay off the anon REST surface as well.
alter table visit_salts enable row level security;

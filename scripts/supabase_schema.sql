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

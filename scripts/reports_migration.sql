-- Reports: bug reports and fix suggestions from the site.
-- Paste into Supabase Dashboard -> SQL Editor -> New query -> Run.
-- Safe to re-run. Mirrors the SQLite schema in src/intake/store.py.
--
-- Deliberately anonymous: `reporter` is the same daily-salted hash the visit
-- counter uses, so submissions can be capped per day while nothing can be
-- walked back to a person once that day's salt is deleted.
create table if not exists reports (
    id         bigint generated always as identity primary key,
    kind       text not null check (kind in ('issue', 'fix')),
    body       text not null,
    context    text,                        -- page or posting they were on
    reporter   text,                        -- daily salted hash, not an identity
    status     text not null default 'new'
               check (status in ('new', 'triaged', 'actioned', 'dismissed')),
    summary    text,                        -- filled by the AI review pass
    created_at timestamptz not null default now()
);

-- The two query patterns: the daily cap counts one reporter's rows today,
-- and the review pass reads the unhandled queue oldest first.
create index if not exists reports_reporter_day on reports (reporter, created_at desc);
create index if not exists reports_new on reports (id) where status = 'new';

-- Row level security on with no policy: Supabase serves every public table
-- over its REST API with a key that ships in the frontend bundle, so a table
-- with no policy is the only one anonymous clients cannot read. Reports are
-- not public. The pipeline role bypasses RLS, so our writes are unaffected.
alter table reports enable row level security;

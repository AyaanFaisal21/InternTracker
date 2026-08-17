-- Shortlist visitor metrics for Supabase Postgres.
-- Paste one block into Supabase Dashboard -> SQL Editor -> New query -> Run.
-- Read-only: nothing here writes, so every block is safe to re-run.
--
-- visits.visitor_hash identifies a visitor for one UTC day and no longer.
-- The salt behind it rotates at midnight UTC and is deleted two days later,
-- so one person on two days is two unrelated hashes. Never count distinct
-- hashes over a window wider than a day and call the answer people; across
-- days the count is visitor-days, which is the honest traffic number anyway.
--
-- Buckets say `at time zone 'UTC'` so they line up with the salt's day
-- whatever the SQL editor's session timezone happens to be.
--
-- device <> 'bot' drops self-declared crawlers. It also drops every row
-- written before this instrumentation, because those rows carry no device.

-- Unique visitors per day, last 30 days. The headline number.
select date_trunc('day', visits.at at time zone 'UTC')::date as day_utc,
       count(distinct visitor_hash) as visitors,
       count(*)                     as views
from visits
where at >= now() - interval '30 days'
  and device <> 'bot'
group by 1
order by 1 desc;

-- Unique visitors per hour, last 7 days. One salt covers a whole day, so
-- distinct hashes inside any window shorter than a day are distinct people.
select date_trunc('hour', visits.at at time zone 'UTC') as hour_utc,
       count(distinct visitor_hash) as visitors,
       count(*)                     as views
from visits
where at >= now() - interval '7 days'
  and device <> 'bot'
group by 1
order by 1 desc;

-- Page views per day, split by page, last 30 days. The Python board sent
-- 'listings:<repo>'; split_part folds those into one 'listings' row.
select date_trunc('day', visits.at at time zone 'UTC')::date as day_utc,
       split_part(page, ':', 1)     as page,
       count(*)                     as views,
       count(distinct visitor_hash) as visitors
from visits
where at >= now() - interval '30 days'
  and device <> 'bot'
group by 1, 2
order by 1 desc, 3 desc;

-- Busiest hours of the day, last 30 days: when students actually show up.
-- Eastern, not UTC, because that is the clock the audience lives on. Each
-- visitor counts once per day, so a daily 2pm reader adds 30 visitor-days.
select extract(hour from visits.at at time zone 'America/New_York')::int as hour_et,
       count(distinct visitor_hash) as visitor_days,
       count(*)                     as views
from visits
where at >= now() - interval '30 days'
  and device <> 'bot'
group by 1
order by 2 desc;

-- Bot share, last 30 days. The filter above is only worth trusting while
-- this stays plausible: a collapse means crawlers found a new user agent.
select date_trunc('day', visits.at at time zone 'UTC')::date as day_utc,
       count(*) filter (where device = 'bot')  as bot_views,
       count(*) filter (where device <> 'bot') as human_views
from visits
where at >= now() - interval '30 days'
group by 1
order by 1 desc;

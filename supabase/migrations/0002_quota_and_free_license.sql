-- Phantomline: free-tier auto-issue + render quota counter.
--
-- Run in Supabase SQL Editor after 0001_user_projects.sql is already applied.
-- Idempotent — safe to re-run.
--
-- What this migration does:
--   1. Adds a `monthly_render_limit` column to the existing `licenses` table
--      so per-tier quotas are stored on the license itself (5 for free,
--      huge number for paid tiers).
--   2. Creates a `usage_meter` table that tracks renders-per-month per user.
--      Server reads this on every render submission to enforce the quota.
--   3. Creates a Postgres trigger that auto-issues a free-tier license row
--      whenever a new user signs up via Supabase Auth. This is the "default
--      license" for new accounts — they get 5 renders/month, no payment
--      required, instantly on first sign-in.
--
-- After running:
--   - Every existing auth.users row that doesn't already have a license
--     should be backfilled with one (we run that backfill at the bottom).
--   - usage_meter is empty by default; it fills as users render.
-- ============================================================================

-- 1. Add quota columns to licenses if not present.
do $$
begin
    if not exists (
        select 1 from information_schema.columns
        where table_schema = 'public' and table_name = 'licenses'
        and column_name = 'monthly_render_limit'
    ) then
        alter table public.licenses
            add column monthly_render_limit integer not null default 999999;
    end if;
end$$;

-- 2. usage_meter table.
create table if not exists public.usage_meter (
    user_id         uuid not null references auth.users(id) on delete cascade,
    month           text not null,            -- 'YYYY-MM' (server-rendered)
    renders_count   integer not null default 0,
    updated_at      timestamptz not null default now(),
    primary key (user_id, month)
);

create index if not exists usage_meter_user_id_idx on public.usage_meter(user_id);

alter table public.usage_meter enable row level security;

drop policy if exists "usage_meter_select_own" on public.usage_meter;
create policy "usage_meter_select_own" on public.usage_meter
    for select using (auth.uid() = user_id);
-- No insert/update policy — only the server (service_role) writes here.

-- 3. Auto-issue a free-tier license on every new signup.
--    Runs as SECURITY DEFINER so the trigger can write into licenses
--    without needing the new user's JWT (which doesn't exist yet
--    at signup time).
create or replace function public.issue_free_license_on_signup()
returns trigger language plpgsql security definer set search_path = public as $$
declare
    user_email  text;
begin
    -- Pull email from the new auth.users row.
    user_email := lower(coalesce(new.email, ''));
    if user_email = '' then
        return new;  -- no email = no license, but don't block signup
    end if;

    -- Skip if this email already has any license (Stripe purchase before
    -- signup, manual backfill, etc.). One license per email keeps the
    -- account view clean.
    if exists (select 1 from public.licenses where lower(email) = user_email) then
        return new;
    end if;

    insert into public.licenses (
        email,
        tier,
        lifetime,
        is_founding,
        monthly_render_limit,
        issued_at,
        created_at,
        license_id,
        key
    ) values (
        user_email,
        'free',
        false,
        false,
        5,                       -- 5 renders/month on the free plan
        extract(epoch from now())::bigint,
        now(),
        'free-' || encode(gen_random_bytes(8), 'hex'),
        ''                       -- no HMAC key for free tier; offline desktop validate skips
    );

    return new;
end;
$$;

drop trigger if exists issue_free_license_trigger on auth.users;
create trigger issue_free_license_trigger
    after insert on auth.users
    for each row execute function public.issue_free_license_on_signup();

-- 4. Backfill: any existing user without a license gets one now.
--    Scoped to users created in the last 90 days so we don't issue
--    licenses to ancient test accounts.
insert into public.licenses (
    email, tier, lifetime, is_founding, monthly_render_limit,
    issued_at, created_at, license_id, key
)
select
    lower(u.email),
    'free',
    false,
    false,
    5,
    extract(epoch from now())::bigint,
    now(),
    'free-' || encode(gen_random_bytes(8), 'hex'),
    ''
from auth.users u
where u.email is not null
  and u.email != ''
  and u.created_at > now() - interval '90 days'
  and not exists (
      select 1 from public.licenses l where lower(l.email) = lower(u.email)
  );

-- 5. Backfill monthly_render_limit on existing paid licenses so
--    the quota check returns "unlimited" for them.
update public.licenses
set monthly_render_limit = 999999
where tier in ('pro', 'studio') or lifetime = true;

-- ============================================================================
-- Verify with:
--   select tier, count(*) from public.licenses group by tier;
--   select count(*) from public.usage_meter;
-- ============================================================================

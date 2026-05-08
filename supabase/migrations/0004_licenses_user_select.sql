-- Phantomline: let users SELECT their own licenses by email via a JWT-scoped
-- policy, so the new desktop /api/license/sync route can fetch a buyer's
-- licenses without the server holding the service_role key.
--
-- Run in Supabase SQL Editor after 0003_user_youtube_tokens.sql is applied.
-- Idempotent — safe to re-run.
--
-- Why
-- ---
-- Before this migration, /api/account/licenses on phantomline.xyz works only
-- because the hosted server has SUPABASE_SERVICE_ROLE_KEY in env, which
-- bypasses RLS. Local desktop installs deliberately don't ship the service
-- role key (it's a foot-gun to leave on user laptops). So a desktop install
-- has the user's JWT but no way to read their own licenses — RLS denies it.
--
-- This policy fixes that: an authenticated user can SELECT rows where the
-- license email matches the email in their JWT. Their own rows, nothing else.
--
-- Existing service_role calls keep working — service_role bypasses RLS, so
-- the hosted /api/account/licenses endpoint is unaffected.
-- ============================================================================

-- 1. Make sure RLS is on. (0001_user_projects.sql probably already enabled it
--    for licenses; this is a no-op if so.)
alter table public.licenses enable row level security;

-- 2. Drop any prior version of the policy so this migration is rerunnable.
drop policy if exists "licenses_select_own_by_email" on public.licenses;

-- 3. Allow any authenticated user to read rows whose email matches the
--    `email` claim on their JWT. lower() on both sides because Stripe and
--    Google can disagree on casing.
create policy "licenses_select_own_by_email" on public.licenses
    for select
    to authenticated
    using (
        lower(email) = lower(coalesce(auth.jwt() ->> 'email', ''))
    );

-- ============================================================================
-- Verify (sign in as a real user in the SQL editor's "Authenticated" role,
-- swap the email below for a known buyer):
--   set role authenticated;
--   set request.jwt.claim.email = 'buyer@example.com';
--   select id, tier, email from public.licenses;
-- Should return only that buyer's rows. Reset with: reset role; reset all;
-- ============================================================================

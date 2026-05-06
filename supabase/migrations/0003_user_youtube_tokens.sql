-- Phantomline: per-user YouTube OAuth tokens (PRIORITY 5).
--
-- Run this in the Supabase SQL Editor (Project → SQL Editor → New query).
-- Idempotent: safe to re-run.
--
-- Why this exists:
--   The legacy /api/youtube/connect flow used a SINGLE shared
--   output/publishing/youtube_connection.json file on the Render
--   container's local disk. That meant every signed-in user on the
--   hosted site shared one YouTube channel — whoever connected last
--   was the only one who could publish. On Render's free tier the
--   file also evaporated on every container restart.
--
--   This migration moves YouTube OAuth state to a per-user table
--   with RLS. Each user gets their own access_token + refresh_token,
--   and the publish endpoints read the row for the signed-in
--   auth.uid() instead of a shared file.
--
-- After running this SQL:
--   1. Confirm the row count is 0 (no migrations of legacy state needed
--      — Kyle was the only user with a token in the shared file).
--   2. Update Google Cloud Console for the OAuth client to add the
--      YouTube scopes (see /docs/single-oauth-checklist.md). The
--      same OAuth client used for sign-in handles YouTube too.
--   3. Update Supabase Auth → Providers → Google → Scopes (additional)
--      to include https://www.googleapis.com/auth/youtube.upload
--      and https://www.googleapis.com/auth/youtube.readonly. These are
--      requested incrementally — users only see the YouTube-scopes
--      consent screen when they click "Connect YouTube" in the studio.

-- =============================================================================
-- 1. user_youtube_tokens table
-- =============================================================================

create table if not exists public.user_youtube_tokens (
    user_id           uuid primary key references auth.users(id) on delete cascade,
    access_token      text not null,
    refresh_token     text,
    expires_at        timestamptz,           -- when the access_token stops working
    scope             text,                  -- granted scopes from Google
    channel_id        text,                  -- cached UC... id once first call resolves it
    channel_title     text,                  -- cached display name for the UI
    -- Last-used metadata for diagnostics.
    last_refreshed_at timestamptz,
    last_used_at      timestamptz,
    created_at        timestamptz not null default now(),
    updated_at        timestamptz not null default now()
);

-- Auto-bump updated_at on update.
create or replace function public.user_youtube_tokens_set_updated_at()
returns trigger language plpgsql as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

drop trigger if exists user_youtube_tokens_updated_at on public.user_youtube_tokens;
create trigger user_youtube_tokens_updated_at
    before update on public.user_youtube_tokens
    for each row execute function public.user_youtube_tokens_set_updated_at();

-- =============================================================================
-- 2. Row-level security
-- =============================================================================

alter table public.user_youtube_tokens enable row level security;

-- A user can read their own row, and only their own row.
drop policy if exists "youtube_tokens_select_own" on public.user_youtube_tokens;
create policy "youtube_tokens_select_own"
    on public.user_youtube_tokens
    for select
    using (auth.uid() = user_id);

-- A user can insert their own row (called once at first connect).
drop policy if exists "youtube_tokens_insert_own" on public.user_youtube_tokens;
create policy "youtube_tokens_insert_own"
    on public.user_youtube_tokens
    for insert
    with check (auth.uid() = user_id);

-- A user can update their own row (refresh token rotation, channel cache).
drop policy if exists "youtube_tokens_update_own" on public.user_youtube_tokens;
create policy "youtube_tokens_update_own"
    on public.user_youtube_tokens
    for update
    using (auth.uid() = user_id)
    with check (auth.uid() = user_id);

-- A user can delete their own row (the "Disconnect YouTube" button).
drop policy if exists "youtube_tokens_delete_own" on public.user_youtube_tokens;
create policy "youtube_tokens_delete_own"
    on public.user_youtube_tokens
    for delete
    using (auth.uid() = user_id);

-- service_role bypasses RLS automatically — no separate policies needed
-- for the auto-issue-license edge function or any cross-user admin path.

-- =============================================================================
-- 3. Helper view for the studio UI: just the row's connect/disconnect state
-- =============================================================================
-- Doesn't expose the access_token to clients (that's still in the base
-- table). Just enough for the Publish UI to know "is YouTube connected?
-- Which channel?" without sending the bearer over the wire.

create or replace view public.user_youtube_status as
    select
        user_id,
        channel_id,
        channel_title,
        scope,
        expires_at,
        (case
            when access_token is null then 'disconnected'
            when expires_at is not null and expires_at < now() then 'expired'
            else 'connected'
        end) as status,
        created_at,
        updated_at
    from public.user_youtube_tokens;

-- The view inherits RLS from the underlying table — users only see
-- their own row through the view too.

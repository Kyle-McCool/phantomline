-- Phantomline: user-scoped projects + storage migration.
--
-- Run this in the Supabase SQL Editor (Project → SQL Editor → New query).
-- Idempotent: safe to re-run if a step already exists.
--
-- After running this SQL:
--   1. Go to Storage → Create a new private bucket named "project-files"
--   2. Go to Storage → Create a new PUBLIC bucket named "avatars"
--   3. The SQL below already creates the RLS policies for both buckets;
--      you don't need to add anything in the bucket settings UI.
--
-- Why this exists:
--   The previous web app stored every visitor's projects in a single
--   shared JSON file on the server's local disk. Two problems:
--     (a) Anyone signed in saw everyone else's library.
--     (b) Render's container disk is ephemeral, so any restart wiped
--         everything.
--   This migration moves projects + files into Supabase, scoped by
--   auth.uid() with RLS so users only see their own work, and persisted
--   across container restarts and devices.

-- =============================================================================
-- 1. projects table
-- =============================================================================

create table if not exists public.projects (
    id              text primary key,        -- UUID hex (12 chars), client-generated to match local store format
    user_id         uuid not null references auth.users(id) on delete cascade,
    kind            text not null,           -- 'video', 'narration', 'short_script', 'video_plan', 'bundle', etc.
    title           text not null default 'Untitled',
    status          text not null default 'pending',
    params          jsonb not null default '{}'::jsonb,
    files           jsonb not null default '{}'::jsonb,  -- { role -> storage path }
    members         jsonb,                   -- only set on bundle kind
    error           text,
    duration_seconds  numeric,
    word_count      integer,
    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now()
);

create index if not exists projects_user_id_created_at_idx
    on public.projects(user_id, created_at desc);
create index if not exists projects_user_id_kind_idx
    on public.projects(user_id, kind);

-- Auto-bump updated_at on any row update.
create or replace function public.projects_set_updated_at()
returns trigger language plpgsql as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

drop trigger if exists projects_updated_at on public.projects;
create trigger projects_updated_at
    before update on public.projects
    for each row execute function public.projects_set_updated_at();

-- =============================================================================
-- 2. RLS: users only see / write their own rows.
-- =============================================================================

alter table public.projects enable row level security;

-- Drop existing policies if re-running so create-policy doesn't 42710.
drop policy if exists "projects_select_own" on public.projects;
drop policy if exists "projects_insert_own" on public.projects;
drop policy if exists "projects_update_own" on public.projects;
drop policy if exists "projects_delete_own" on public.projects;

create policy "projects_select_own" on public.projects
    for select using (auth.uid() = user_id);

create policy "projects_insert_own" on public.projects
    for insert with check (auth.uid() = user_id);

create policy "projects_update_own" on public.projects
    for update using (auth.uid() = user_id) with check (auth.uid() = user_id);

create policy "projects_delete_own" on public.projects
    for delete using (auth.uid() = user_id);

-- The Phantomline server uses the service_role key for some flows (file
-- attachments stream through the Flask backend before landing in Storage).
-- service_role bypasses RLS automatically so no extra policy needed.

-- =============================================================================
-- 3. user_profiles table (separate from auth.users so we can store our own
--    fields without touching the auth schema).
-- =============================================================================

create table if not exists public.user_profiles (
    user_id         uuid primary key references auth.users(id) on delete cascade,
    display_name    text,
    avatar_url      text,                    -- public URL into the avatars bucket
    avatar_path     text,                    -- storage path so we can delete on replace
    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now()
);

drop trigger if exists user_profiles_updated_at on public.user_profiles;
create trigger user_profiles_updated_at
    before update on public.user_profiles
    for each row execute function public.projects_set_updated_at();

alter table public.user_profiles enable row level security;

drop policy if exists "user_profiles_select_own" on public.user_profiles;
drop policy if exists "user_profiles_upsert_own" on public.user_profiles;
drop policy if exists "user_profiles_update_own" on public.user_profiles;

-- Anyone can SELECT a profile (avatars need to be readable for the studio
-- top-right widget when a viewer is logged in as themselves; we can tighten
-- to auth.uid() = user_id later if we add a "private profile" toggle).
create policy "user_profiles_select_own" on public.user_profiles
    for select using (auth.uid() = user_id);

create policy "user_profiles_upsert_own" on public.user_profiles
    for insert with check (auth.uid() = user_id);

create policy "user_profiles_update_own" on public.user_profiles
    for update using (auth.uid() = user_id) with check (auth.uid() = user_id);

-- =============================================================================
-- 4. Storage RLS for the project-files bucket
--    (CREATE THE BUCKET MANUALLY FIRST: Storage → New bucket → "project-files",
--     mark it PRIVATE / not public.)
-- =============================================================================

-- Folder layout: project-files/<user_id>/<project_id>/<role>.<ext>
-- The first path segment must equal the user's auth.uid() — that's the
-- enforcement check in the policies below.

drop policy if exists "project_files_select_own" on storage.objects;
drop policy if exists "project_files_insert_own" on storage.objects;
drop policy if exists "project_files_update_own" on storage.objects;
drop policy if exists "project_files_delete_own" on storage.objects;

create policy "project_files_select_own" on storage.objects
    for select using (
        bucket_id = 'project-files'
        and (storage.foldername(name))[1] = auth.uid()::text
    );

create policy "project_files_insert_own" on storage.objects
    for insert with check (
        bucket_id = 'project-files'
        and (storage.foldername(name))[1] = auth.uid()::text
    );

create policy "project_files_update_own" on storage.objects
    for update using (
        bucket_id = 'project-files'
        and (storage.foldername(name))[1] = auth.uid()::text
    );

create policy "project_files_delete_own" on storage.objects
    for delete using (
        bucket_id = 'project-files'
        and (storage.foldername(name))[1] = auth.uid()::text
    );

-- =============================================================================
-- 5. Storage RLS for the avatars bucket
--    (CREATE THE BUCKET MANUALLY FIRST: Storage → New bucket → "avatars",
--     mark it PUBLIC so avatar URLs work without signed URLs.)
-- =============================================================================

-- Folder layout: avatars/<user_id>/avatar.<ext>
-- Public read; only the owner can upload/replace/delete their own avatar.

drop policy if exists "avatars_public_read" on storage.objects;
drop policy if exists "avatars_upload_own" on storage.objects;
drop policy if exists "avatars_update_own" on storage.objects;
drop policy if exists "avatars_delete_own" on storage.objects;

create policy "avatars_public_read" on storage.objects
    for select using (bucket_id = 'avatars');

create policy "avatars_upload_own" on storage.objects
    for insert with check (
        bucket_id = 'avatars'
        and (storage.foldername(name))[1] = auth.uid()::text
    );

create policy "avatars_update_own" on storage.objects
    for update using (
        bucket_id = 'avatars'
        and (storage.foldername(name))[1] = auth.uid()::text
    );

create policy "avatars_delete_own" on storage.objects
    for delete using (
        bucket_id = 'avatars'
        and (storage.foldername(name))[1] = auth.uid()::text
    );

-- =============================================================================
-- Done. Verify with these queries:
--   select count(*) from public.projects;       -- should be 0
--   select count(*) from public.user_profiles;  -- should be 0
--   select bucket_id, name from storage.objects where bucket_id in ('project-files', 'avatars') limit 5;
-- =============================================================================

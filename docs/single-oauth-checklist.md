# Single-OAuth checklist (PRIORITY 5)

Status: SQL migration shipped (`supabase/migrations/0003_user_youtube_tokens.sql`).
Code endpoints + UI changes are next, but require the dashboard config below
to be complete first. This file is the runbook.

## Goal

One Google OAuth client, used for BOTH:
- Phantomline sign-in (via Supabase Auth)
- Per-user YouTube publishing (incremental scope grant)

Users never bring their own client_id/secret. The publish flow upgrades
the existing Google sign-in grant with `youtube.upload` + `youtube.readonly`
scopes the first time they click "Connect YouTube."

## Why it matters

- Removes the "shared single channel" problem: legacy
  `output/publishing/youtube_connection.json` had one connection serving
  every signed-in user on hosted. Each user now gets their own row in
  `user_youtube_tokens` (RLS-scoped to their auth.uid()).
- Removes the "bring your own credentials" friction step from the
  Publish flow.
- Lets Render's container disk reset without losing connections.

---

## Step 1 — Run the SQL migration

Supabase dashboard → Project (`vdzydhrgazqeyaalguuy`) → SQL Editor →
New Query → paste the contents of
`supabase/migrations/0003_user_youtube_tokens.sql` → Run.

Idempotent. Safe to re-run.

Verify:
- Table Editor → `public.user_youtube_tokens` exists, 0 rows
- Authentication → Policies → 4 policies on `user_youtube_tokens`
  (select / insert / update / delete, all `auth.uid() = user_id`)

---

## Step 2 — Google Cloud Console

Project: `My First Project` (the one that already hosts the Phantomline
OAuth 2.0 Client ID `1064227581288-45vhambtc77vrg0bsf90f05d6vunam4v.apps.googleusercontent.com`).

### 2a. Enable the YouTube Data API v3

[https://console.cloud.google.com/apis/library/youtube.googleapis.com](https://console.cloud.google.com/apis/library/youtube.googleapis.com)
→ Click **Enable** if not already enabled.

### 2b. Add YouTube scopes to the OAuth consent screen

[https://console.cloud.google.com/auth/scopes](https://console.cloud.google.com/auth/scopes)
→ Click **Add or remove scopes** → search for and check:

| Scope | Purpose |
|-------|---------|
| `https://www.googleapis.com/auth/youtube.upload` | Upload videos to the user's channel |
| `https://www.googleapis.com/auth/youtube.readonly` | Read channel + video metadata |
| `openid` | Already added by sign-in flow |
| `.../auth/userinfo.email` | Already added by sign-in flow |
| `.../auth/userinfo.profile` | Already added by sign-in flow |

→ **Update**.

These count as **sensitive scopes** in Google's terms. Two implications:

1. The first time any user clicks Connect YouTube, Google shows a yellow
   "unverified app" warning. Click through is allowed up to ~100 users
   ("Test users" or "External" published-but-unverified mode).
2. To remove the warning, submit for OAuth verification at
   [https://support.google.com/cloud/answer/13463073](https://support.google.com/cloud/answer/13463073).
   Verification takes 4-6 weeks and requires a privacy policy URL,
   homepage URL, and a video showing the scopes in use. Defer until
   the first 50 users sign up.

### 2c. (No redirect URI changes needed)

The same callback already in use for sign-in
(`https://vdzydhrgazqeyaalguuy.supabase.co/auth/v1/callback`) handles
the YouTube grant too — Supabase's incremental authorization layers
the new scopes onto the same callback.

---

## Step 3 — Supabase Auth → Providers → Google

[https://supabase.com/dashboard/project/vdzydhrgazqeyaalguuy/auth/providers](https://supabase.com/dashboard/project/vdzydhrgazqeyaalguuy/auth/providers)
→ Google (already enabled) → confirm:

- **Client IDs** has the same `1064227581288-...apps.googleusercontent.com`
- **Client Secret** present
- **Skip nonce checks**: leave OFF
- **Allow users without an email**: leave OFF

Supabase's `signInWithOAuth({ provider: 'google', options: { scopes: '...' } })`
will request the YouTube scopes when called from the Publish > Connect
YouTube button. Supabase forwards the scopes to Google and captures
the resulting `provider_token` + `provider_refresh_token` in the
session payload — the studio JS reads those and POSTs them to
`/api/youtube/store-token` (endpoint coming in the next commit).

---

## Step 4 — wait for the next commit

After all of the above is done, the next commit will:

- Add `/api/youtube/store-token` (POST) — receives `provider_token`,
  `provider_refresh_token`, `expires_at` from the studio JS post-grant.
  Validates the user JWT, upserts into `user_youtube_tokens`. RLS
  enforces ownership.
- Add `/api/youtube/disconnect` (DELETE) — drops the user's row.
- Update Publish > Compose UI: "Connect YouTube" button now calls
  `supabase.auth.signInWithOAuth({ scopes: 'youtube.upload youtube.readonly' })`
  with `prompt: 'consent'` so the second-grant dialog is forced.
- Update `youtube_publish.py` to read tokens from `user_youtube_tokens`
  (per signed-in user) instead of the shared
  `output/publishing/youtube_connection.json` on hosted. Local installs
  keep using the file fallback so the "single channel for me" workflow
  still works without sign-in.

---

## Verification (after Step 4 lands)

- [ ] Sign out, then sign in fresh with the same Google account
- [ ] Click Publish > Connect YouTube — Google shows a "Phantomline
      wants to upload videos to your YouTube channel" prompt
- [ ] Approve. Confirm a row appears in `user_youtube_tokens` with
      your auth.uid() and a non-empty access_token + refresh_token
- [ ] Render a test video (8-second, default settings) and try the
      Publish button. The video should land on YOUR channel, not the
      legacy shared one.
- [ ] Open the studio in an incognito window with a DIFFERENT Google
      account. Click Connect YouTube. Confirm a SECOND row appears in
      `user_youtube_tokens`. Both rows coexist; each user uses their own.
- [ ] Click Disconnect YouTube. Confirm the row is deleted (not just
      hidden). The button reverts to "Connect YouTube."

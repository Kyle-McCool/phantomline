# Phantomline Release Notes

Sorted newest first. The version in `/VERSION` is what the desktop install
shows itself as. Bump it on every push to `main` that we want users to see
as an update notification.

Versioning: semver (`MAJOR.MINOR.PATCH`).
- **MAJOR**: schema break, license format change, anything requiring user data migration.
- **MINOR**: new features, new pages, new endpoints. Backward-compatible.
- **PATCH**: bug fixes, copy edits, polish.

## 1.0.0 — 2026-05-08

First versioned release. Marks the point where desktop installs can
auto-detect updates from the hosted version-check endpoint.

Bundled with this release:

- **Auto-update infrastructure** — `/api/system/version` reports installed version,
  `/api/system/update-check` compares against `phantomline.xyz`. The studio shows a
  banner when an update is available. The double-click launcher scripts also check
  on every start and offer to apply the update before launching the server.
- **License sync via Google sign-in** — paying customers no longer need to paste
  HMAC keys. Sign in once on `/account` (local or hosted) and the desktop install
  picks up the buyer's tier from Supabase via JWT-scoped RLS.
- **Detect-and-coach UX** for hosted `/account` — if the hosted page detects a
  Phantomline server is running locally, the activation button works directly;
  otherwise it shows OS-specific launcher instructions.
- **Three double-click launchers** — `start-phantomline.bat` (Windows),
  `start-phantomline.command` (macOS), `start-phantomline.sh` (Linux). Detect
  the venv, start the server, open the browser. Source-zip preserves executable
  bits on macOS / Linux launchers.
- **15 new SEO pages** — 5 niche pillars (ASMR/sleep, true crime, motivational,
  history, science explainer), 3 persona pages (solopreneurs, course creators,
  content marketers), 1 listicle (best-faceless-youtube-tools), 1 blog index +
  5 launch articles. Full schema markup (BreadcrumbList, Article, HowTo, FAQPage,
  ItemList, BlogPosting). Internal linking flows in both directions between old
  and new pillars.
- **Stripe → Supabase pipeline restored** — the `issue-license` edge function had
  never received the env vars it needed. Now configured and verified end-to-end.
  All paying customers from this point forward auto-issue without manual intervention.
- **Schema migrations 0002 + 0003 applied** — `monthly_render_limit` column,
  `usage_meter` table, `user_youtube_tokens` table, free-tier signup trigger,
  widened `licenses_tier_check` to allow `'free'`.

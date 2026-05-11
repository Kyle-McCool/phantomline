# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

Phantomline is a local-first AI faceless video studio. One Flask codebase serves three surfaces: a hosted PWA on Render (`phantomline.xyz`), a full desktop install, and an Android APK (Capacitor wrapper). Heavy AI inference runs either server-side (Ollama + Kokoro + MusicGen + ffmpeg) on the desktop install, or entirely in-browser (WebLLM + Web Speech + Web Audio + ffmpeg.wasm) on the hosted/PWA path. The server is never an AI inference bottleneck — it's a thin web tier on hosted, a full pipeline on desktop.

### LLM backend tiers

Script generation runs through one of three backends, picked in priority order:

1. **Cloud BYO-key** (Anthropic Claude or OpenAI GPT). Frontline quality, ~$0.005 per short script, no install friction. User pastes their own API key into Settings → AI engine → Cloud, or sets `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` in their `.env`. Browser calls the provider directly — nothing routes through our server.
2. **Ollama** on `localhost:11434`. Desktop default. Best for privacy, fully offline, no token cost. Requires `ollama pull llama3.1`.
3. **text.pollinations.ai**. Free public fallback for hosted deploys that have neither a cloud key nor Ollama.

The browser engine adapter lives in `static/engines.js` (`CloudKeyEngine`, `WebLLMEngine`, `ServerEngine`). The server-side dispatcher lives in `story_generator.py::generate()`. Both pick the highest-priority available backend; failures fall through to the next tier.

## Running locally

```bash
# Slim web tier (no desktop AI)
venv\Scripts\activate
pip install -r requirements.txt
python server.py
# → http://localhost:5000

# Full desktop mode (adds Ollama, Kokoro TTS, MusicGen)
pip install -r requirements-desktop.txt
# also: ollama pull llama3.1
python server.py
```

The `.env` file (never committed) must contain at minimum `GHOSTLINE_LICENSE_SECRET`. See `.env.example` for all variables.

## Smoke tests (no formal test suite yet)

1. `python server.py` boots without import errors
2. `http://localhost:5000` loads the studio
3. `http://localhost:5000/landing` loads the marketing page
4. `/api/system/health` returns `{"ok": true}`

## Architecture

### Two deploy modes, one codebase

Heavy ML imports (`tts`, `music`, `video_assembler`) are wrapped in `try/except` in `server.py`. When they fail to import (hosted deploy), routes that need them return 503. The same `server.py` boots in both modes.

### Import graph (kept acyclic)

```
core.py          ← no project imports; paths, ProjectStore singleton, JSON helpers, LLM helpers
server.py        ← core, project modules, route blueprints
routes/*.py      ← core, flask, individual project modules
```

`core.py` is the shared state module. Anything needed by two or more blueprints lives there. Domain-specific state stays in the owning route module.

### Route blueprints (registered in server.py)

- `billing.py` — license keys, tier gating, Stripe checkout URLs, quota (free: 5 renders/mo)
- `system.py` — health checks, model listing
- `launch.py` — first-run readiness probes + test render
- `insights.py` — channel analytics (Pro tier)
- `research.py` — YouTube keyword research (Pro tier)
- `optimize.py` — per-video repackaging (Pro tier)
- `bundles.py` — project import/export

New blueprints must be registered in `server.py`:
```python
from routes.your_module import your_bp
app.register_blueprint(your_bp)
```

### Front-end

- **`static/ghostline.js`** (~5000 lines) — the studio app. Not bundled; served as-is.
- **`static/engines.js`** — browser-side AI adapters (WebLLM, Web Speech, ffmpeg.wasm, Pexels, bundled music). Engine choice persists in `localStorage` under `ghostline.engine.v1`.
- **`static/ghostline.css`** (~3000 lines) — studio styles.
- **`static/landing.css` / `static/landing.js`** — marketing page.
- **`templates/index.html`** — studio app shell (served at `/` on desktop, `/app` on hosted).
- **`templates/landing.html`** — marketing site (served at `/landing`, or `/` on hosted).

Static assets are cache-busted via `?v=<mtime>` (the `versioned` Jinja filter in `server.py`). If the browser serves stale assets after a change, bump `CACHE_VERSION` in `static/sw.js`.

### License system

Offline-verifiable HMAC-SHA256 keys. Format: `GHL1.<base64-payload>.<base64-signature>`. Validation in `license.py::validate()`. Tier gating via `routes.billing::enforce_tier()`. The `GHL1.` prefix and `GHOSTLINE_*` env var names are intentional legacy — do not rename.

### Security

`server.py` enforces same-origin writes (POST/PUT/PATCH/DELETE must come from `localhost:5000`), sets CSP headers, and blocks iframe embedding. CDN allowlist includes jsdelivr (WebLLM) and Hugging Face (model weights).

## Naming convention (critical)

The project was rebranded from Ghostline to Phantomline. Internal names were intentionally preserved:

- **User-facing text** → "Phantomline"
- **File paths, env vars, JS globals, CSS filenames, localStorage keys, license prefix** → keep as `ghostline.*` / `GHOSTLINE_*` / `GhostlineEngines` / `GHL1.`

## Deployment

- **Production**: Render reads `render.yaml` from `main`. Every push to `main` auto-deploys. Logs at `dashboard.render.com`.
- **Domain**: `phantomline.xyz` (A record → `216.24.57.1`, CNAME `www` → `phantomline.onrender.com`).
- **Mobile**: Capacitor wraps the live site. `npx cap sync android && npx cap open android`.

## Branching & multi-agent workflow

`main` is production (every push auto-deploys to Render). Multiple Claude Code sessions work this codebase in parallel — Kyle on backend / SEO / license infra, Cesar on landing-page UI. To keep merges seamless:

### Hard rules

1. **Never force-push `main`.** Force-pushing rewrites shared history. Even when the content is identical, the SHA changes break everyone else's local. If you need to fix a mistake on main, push a NEW commit (revert, fix-up, etc.) — never `--force` or `--force-with-lease` to main. (Branch protection should also block this at the GitHub level.)

2. **Never commit directly to `main`.** Always work on a branch named `<owner>/<short-purpose>`:
   - `kyle/license-sync` (backend)
   - `kyle/seo-pillars` (content)
   - `cesar/landing-redesign` (UI)
   - `cesar/studio-refresh` (UI on the studio page, only after Kyle clears it per the working rules)

3. **Pull before you start.** First action of any session: `git fetch origin && git status` to see what landed since your last push. Rebase or merge `origin/main` into your branch before starting work.

4. **Always merge to main from a branch, with `--no-ff`.** This preserves the branch context in history and makes reverts surgical.

### The standard ship-a-feature flow

```bash
# 1. Start clean
git fetch origin
git checkout main
git pull --ff-only origin main

# 2. Branch
git checkout -b cesar/landing-hero-redesign

# 3. Work, commit small + often, push
git add ...
git commit -m "Hero: ..."
git push -u origin cesar/landing-hero-redesign

# 4. When ready to ship — merge cleanly
git fetch origin
git checkout main
git pull --ff-only origin main
git merge --no-ff cesar/landing-hero-redesign -m "Merge hero redesign"
git push origin main
```

If `git pull --ff-only origin main` errors because main moved while you were working, **don't force**. Instead:
```bash
git checkout cesar/landing-hero-redesign
git rebase origin/main      # replay your work onto fresh main
# resolve any conflicts on the branch (not on main)
git checkout main
git merge --ff-only cesar/landing-hero-redesign  # now fast-forward
git push origin main
```

### Conflict resolution philosophy

- **Resolve on the branch, never on main.** A messy main = broken site.
- **`git checkout --theirs <file>` when the conflict is spurious** (someone force-pushed and SHAs got rewritten — content is actually identical).
- **Read both versions first** when conflicts overlap real changes from both sides. Don't `--theirs` blindly if the other person added something legitimate.

### Coordinating across two Claude sessions

Both Kyle's and Cesar's Claude sessions work this repo. To avoid collision:
- Stay in your lane: Kyle = backend/SEO/license/scheduler/research; Cesar = landing/studio UI/CSS/JS for those two pages.
- Brand docs (`docs/brand-brief.md`, `docs/style-guide.md`) live local-only — gitignored. Don't try to commit them.
- If you find yourself touching the OTHER person's files, stop and check in via Slack first.

### What to do if you discover a force-push happened

```bash
git fetch origin
git status        # if your local branch is ahead by mysterious commits, that's a sign
# back up your work
git branch backup-pre-force-pull main
# preserve any local-only files (gitignored ones are safe; tracked-but-removed need rescue)
# reset to origin's version
git reset --hard origin/main
# re-apply your work
git merge backup-pre-force-pull   # OR cherry-pick the commits you need
```

## Working rules (from Kyle)

- **No new product features** without explicit discussion with Kyle. Current work is UI/visual only.
- **Landing page first.** Work in `templates/landing.html`, `static/landing.css`, and `static/landing.js`. Do not modify the studio page (`templates/index.html`, `static/ghostline.js`, `static/ghostline.css`) until local and browser AI modes are connected and working for test users.
- **Logo colors are the canonical brand palette.** Use them as the foundation for all design decisions. Beyond those anchor colors, creative freedom is encouraged — research competing tools and modern design trends to inform choices.

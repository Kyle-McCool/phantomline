# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

Phantomline is a local-first AI faceless video studio. One Flask codebase serves three surfaces: a hosted PWA on Render (`phantomline.xyz`), a full desktop install, and an Android APK (Capacitor wrapper). Heavy AI inference runs either server-side (Ollama + Kokoro + MusicGen + ffmpeg) on the desktop install, or entirely in-browser (WebLLM + Web Speech + Web Audio + ffmpeg.wasm) on the hosted/PWA path. The server is never an AI inference bottleneck — it's a thin web tier on hosted, a full pipeline on desktop.

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

## Branching

`main` is production (auto-deploys on push). Always branch first. Kyle holds production secrets (ask via Signal / 1Password, not Slack or email).

## Working rules (from Kyle)

- **No new product features** without explicit discussion with Kyle. Current work is UI/visual only.
- **Landing page first.** Work in `templates/landing.html`, `static/landing.css`, and `static/landing.js`. Do not modify the studio page (`templates/index.html`, `static/ghostline.js`, `static/ghostline.css`) until local and browser AI modes are connected and working for test users.
- **Logo colors are the canonical brand palette.** Use them as the foundation for all design decisions. Beyond those anchor colors, creative freedom is encouraged — research competing tools and modern design trends to inform choices.

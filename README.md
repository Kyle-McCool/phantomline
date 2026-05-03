# Phantomline

> Local-first AI faceless video studio. Desktop + PWA + Android APK from one Flask codebase.
> No paid APIs. Founding lifetime $79.

**Live:** https://phantomline.xyz
**Repo:** https://github.com/daculturedswine/phantomline

Phantomline turns ideas into scripts, narration, captions, music, B-roll, SEO packages, and scheduled YouTube posts. Heavy AI inference runs on the user's machine (Ollama + Kokoro + MusicGen) on desktop, or in the browser (WebLLM + Web Speech + Web Audio + ffmpeg.wasm) on the hosted PWA. Either way, no SaaS bill on the AI side.

---

## Two deploy modes (read this first)

Phantomline runs the **same Flask app** in two modes depending on where it's hosted:

### Hosted mode — `https://phantomline.xyz` (Render)
- Slim deploy: Flask + numpy + Pillow + requests only (`requirements.txt`).
- The server is a thin web tier: serves the landing page, license issuance, billing webhooks, and cheap API helpers (YouTube research, channel insights).
- **All AI inference happens in the user's browser** via WebLLM (Llama 3.2 1B in WebGPU), Web Speech API for TTS, Web Audio for music, and ffmpeg.wasm for MP4 assembly.
- The bundled music pack ships in `static/library/music/` for users without WebGPU.
- Auto-deploys on every push to `main` via `render.yaml`.

### Desktop mode — `python server.py` (local install)
- Full deploy: also installs `requirements-desktop.txt` (kokoro, transformers, soundfile, lameenc).
- Runs server-side AI: Ollama (LLM), Kokoro (TTS), MusicGen via HuggingFace transformers (music), ffmpeg subprocess (video).
- Optional: Forge / AUTOMATIC1111 at `http://127.0.0.1:7861` for AI-generated scenes.
- This is the "power user" mode where everything is local, including the model weights.

The same `server.py` boots in either mode. Heavy ML imports (`tts`, `music`, `video_assembler`) are wrapped in `try/except` so the hosted deploy doesn't crash without them.

---

## Quick start (local dev, full desktop mode)

```bash
git clone https://github.com/daculturedswine/phantomline.git
cd phantomline

# Python venv
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # Mac/Linux

# Install web tier + desktop AI tier
pip install -r requirements.txt
pip install -r requirements-desktop.txt

# Pull at least one Ollama model
# (install Ollama first from https://ollama.com)
ollama pull llama3.1

# Set up local env
cp .env.example .env             # then edit values
# ^ if .env.example doesn't exist, create .env with:
#   GHOSTLINE_LICENSE_SECRET=<openssl rand -hex 32>
#   YOUTUBE_API_KEY_2=<optional, for YouTube research routes>

# Run
python server.py
# → http://localhost:5000
```

If you only want the slim web tier (no Ollama / Kokoro / MusicGen):
```bash
pip install -r requirements.txt
python server.py
```
The server boots, the landing page works, but generation routes will return 503 until you install desktop deps.

---

## Architecture at a glance

```
phantomline/
├── server.py                    # Flask app entry. Imports route blueprints,
│                                  registers them, and handles startup.
│
├── core.py                      # Shared module-level state — paths, project store
│                                  singleton, JSON helpers, YouTube connection cache.
│                                  Routes import from here.
│
├── routes/                      # Flask blueprints, one per concern
│   ├── billing.py               # License keys, tier gating, Stripe checkout URLs,
│   │                              quota enforcement (free tier: 5 renders/mo)
│   ├── system.py                # Health checks, model listing
│   ├── launch.py                # First-run readiness probes + test render
│   ├── insights.py              # Channel analytics ingest (Pro tier)
│   ├── research.py              # YouTube keyword research (Pro tier)
│   ├── optimize.py              # Per-video repackaging (Pro tier)
│   └── bundles.py               # Project import/export
│
├── story_generator.py           # Long-form Ollama story engine (CLI + library)
├── tts.py                       # Kokoro TTS (lazy-loaded; desktop only)
├── music.py                     # MusicGen + crossfade loop + narration mixdown (desktop)
├── video_assembler.py           # ffmpeg subprocess MP4 builder (desktop)
├── projects.py                  # Persistent project store with atomic writes
├── youtube_publish.py           # YouTube Data API v3 client + OAuth dance
├── youtube_research.py          # vidIQ-style autocomplete + ranking signals
├── channel_insights.py          # Analytics CSV → ranked keyword profile
├── license.py                   # Offline HMAC-SHA256 license validation
│
├── static/
│   ├── phantomline.css          # Studio app styles
│   ├── landing.css              # Marketing page styles
│   ├── phantomline.js           # Studio app JS (~5000 lines)
│   ├── engines.js               # Browser-side AI adapters (WebLLM, Web Speech,
│   │                              ffmpeg.wasm, Pexels, bundled music library)
│   ├── manifest.json            # PWA manifest
│   ├── sw.js                    # Service worker (cache-first /static, network-first HTML)
│   ├── phantomline-logo.svg     # Brand icon (vector, no text)
│   └── library/music/           # 8 royalty-free ambient tracks (~11 MB total)
│
├── templates/
│   ├── index.html               # Studio app shell
│   └── landing.html             # Marketing site at /landing
│
├── supabase/
│   └── functions/
│       └── issue-license/       # Deno Edge Function: Stripe webhook → email license
│
├── render.yaml                  # Render Blueprint declarative deploy config
├── capacitor.config.json        # Android APK wrapper config
├── requirements.txt             # Web tier deps (slim)
├── requirements-desktop.txt     # Desktop AI tier deps (heavy)
└── output/                      # User-generated content. Gitignored.
    ├── projects/                # One folder per saved project
    ├── projects.json            # Project index (atomic writes)
    └── publishing/              # YouTube OAuth tokens cache
```

### Naming convention note

The project was originally called **Ghostline**, then rebranded to **Phantomline**. Filenames, env var names, JS globals, localStorage keys, and the license key prefix (`GHL1.`) were intentionally preserved during the rebrand to avoid breaking existing installs and license keys. **All user-facing strings say Phantomline.** When editing, follow the same rule:

- ✅ User-visible text → "Phantomline"
- ❌ File paths, env vars, JS globals, license prefix → leave as `ghostline.*` / `GHOSTLINE_*` / `GhostlineEngines` / `GHL1.`

---

## Environment variables

Set these in `.env` for local dev, or in Render's Environment tab for production.

| Variable | Required? | Purpose |
|---|---|---|
| `GHOSTLINE_LICENSE_SECRET` | yes | HMAC secret for license signing/verification. Generate with `openssl rand -hex 32`. Must match whatever issuer (Supabase Edge Function) issues keys. |
| `YOUTUBE_API_KEY_2` | for research | YouTube Data API v3 key. Each key is 10k units/day; add `YOUTUBE_API_KEY_3`, `_4`, etc. to scale. |
| `YOUTUBE_CLIENT_ID` | for publish | OAuth client ID for in-app YouTube publishing. |
| `YOUTUBE_CLIENT_SECRET` | for publish | OAuth client secret. |
| `GHOSTLINE_TELEMETRY_URL` | optional | If set, error events POST here. Local JSONL in `output/telemetry/` works regardless. |
| `GHOSTLINE_LICENSE_SECRETS_LEGACY` | rotation | Comma-separated old secrets, accepted during rotation windows. |

**Never commit `.env`.** It's in `.gitignore`.

---

## License system

Offline-verifiable HMAC keys. Format:

```
GHL1.<base64-payload>.<base64-signature>
```

Payload is JSON: `{tier: "pro", email: "...", iat: <unix>, exp: <unix>, lifetime: bool}`. Signature is HMAC-SHA256 of the payload with `GHOSTLINE_LICENSE_SECRET`.

Validation lives in `license.py::validate(key)`. Tier gating uses `routes.billing::enforce_tier(required)` which returns `(ok, error)`. Quota enforcement uses `consume_quota()` (free tier: 5 renders/month).

To rotate the secret, set the new value in `GHOSTLINE_LICENSE_SECRET` and add the old value to `GHOSTLINE_LICENSE_SECRETS_LEGACY` (comma-separated) until you reissue all customer keys.

---

## Dev workflow

### Branching

`main` is the production branch. Render's `autoDeploy: true` ships every push to `main`. Don't push WIP commits to main — branch first.

```bash
git checkout -b feature/whatever
# work, commit, push
git push -u origin feature/whatever
gh pr create --title "..." --body "..."
```

When the PR is approved and CI's clean (no CI yet — TODO), merge to main and Render auto-deploys.

### Running tests

There aren't formal tests yet. Smoke-test by:

1. `python server.py` boots without import errors
2. http://localhost:5000 loads the studio
3. http://localhost:5000/landing loads the marketing page
4. `/api/system/health` returns `{"ok": true}`

If any of those break, your change broke something.

### Touching the front-end

The studio app's CSS is `static/phantomline.css` (~3000 lines) and JS is `static/phantomline.js` (~5000 lines). Both are intentionally not bundled — they're served as-is and cache-busted via `?v=<mtime>` (the `versioned` Jinja filter in `server.py`).

If you add a new static asset and the browser doesn't pick it up, bump `CACHE_VERSION` in `static/sw.js` to invalidate the service worker cache.

### Touching the back-end

Each route blueprint owns its concern. Keep handlers thin — push logic into the module-level functions (`story_generator.py`, `youtube_research.py`, etc.) so they're testable without Flask.

When you add a new blueprint, register it in `server.py`:

```python
from routes.your_module import your_bp
app.register_blueprint(your_bp)
```

---

## Deploying

### Production (already set up)

Render reads `render.yaml` from `main` and provisions. Push to `main` → auto-deploy. Logs at https://dashboard.render.com/.

### Custom domain

Already wired: `phantomline.xyz` and `www.phantomline.xyz` → Render's free tier instance via:
- A record `@` → `216.24.57.1`
- CNAME `www` → `phantomline.onrender.com`

SSL is auto-provisioned via Let's Encrypt.

### Mobile (Capacitor wrap)

```bash
npm i
npx cap sync android
npx cap open android   # opens Android Studio
```

The wrapper points at `https://phantomline.xyz` so the APK is just a chrome around the live site. See `MOBILE_BUILD.md` for the keystore + signing dance.

---

## Troubleshooting

**`pip install -r requirements-desktop.txt` fails on Windows**
PyTorch wheels for some Python versions are flaky. Try Python 3.11 if you're on 3.13.

**The hosted server (Render) returns 503 for `/api/render`**
Expected. Hosted is web-tier-only; rendering happens browser-side via `engines.js`. Use the desktop install if you want server-side rendering.

**`ollama offline` badge on local install**
Open the Ollama desktop app, or run `ollama serve` in another terminal.

**Service worker is serving stale assets after a deploy**
Bump `CACHE_VERSION` in `static/sw.js`. The `activate` handler clears any cache whose key doesn't match the new version.

**Render deploy fails with "Exited with status 1"**
Check the Logs tab. 99% of the time it's a missing dep or import error — wrap the offending import in `try/except` if it's an optional desktop module.

---

## License

Proprietary. Contact kyle@makko.ai for commercial licensing.

The repo is private — collaborators have explicit access via GitHub. Do not redistribute.

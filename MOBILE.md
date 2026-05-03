# Phantomline mobile roadmap

This doc captures the contract that mobile builds (PWA, then APK) target.
Update it whenever the API or workflow changes.

## Scope decisions

Mobile Phantomline is a **lighter sibling** of desktop, not a port. These
decisions are deliberate; bring them up before changing.

### IN scope for mobile (PWA + APK)

- Idea generation (script + hook + structure)
- Title generation
- Short-form script generation (30s–10min)
- Narration via system TTS or Piper TTS
- Caption generation (Whisper.cpp on-device)
- Source-video assembly (user uploads footage, we narrate over it)
- Stock B-roll library (bundled, royalty-free)
- Project bundles + Library
- Publish draft (composes title/description/tags, no upload from mobile v1)
- Settings + walkthrough + feedback widget

### OUT of scope for mobile v1

- **Long-form story generation** (10K+ words) — too slow on a 1B/3B model
- **MusicGen** — generation is too slow, use bundled music library instead
- **FLUX / Forge image gen** — multi-GB models, won't fit
- **YouTube OAuth upload** — defer to mobile v2 or use desktop for upload
- **Optimize Library** (channel video repackaging) — desktop-only power feature
- **Channel insights CSV ingest** — desktop-only

## Mobile inference stack

| Pipeline step | Mobile model |
|---|---|
| Script + ideas | **Gemini Nano** (Android 14+ via AICore, free, on-device) OR **MLC-LLM** running Llama 3.2 1B (~2GB) |
| Narration | **Piper TTS** (~30MB voices) bundled, OR system TTS (Web Speech API on PWA, Android `TextToSpeech`) |
| Captions | **Whisper.cpp** small.en (466MB) for word-timed captions |
| Music | Bundled royalty-free library (200-500MB) — NO generation |
| Visuals | User-uploaded video preferred. Stock B-roll bundle (300-500MB). No on-device image gen v1. |
| Render | **WebCodecs** (PWA) or **ffmpeg-kit-android** (APK) |

## Distribution paths

### Path A — PWA (ship first)

- Lives at the same domain as desktop
- Detects mobile vs desktop and serves the right bundle
- WebGPU-based inference via WebLLM (Chrome on Android)
- No Play Store, instant updates
- Works on Android Chrome and iOS Safari 17+
- Effort: ~2-3 weeks to fork the JS workflow into mobile-friendly engine adapters

### Path B — Native APK (ship after PWA validates demand)

- Kotlin + Jetpack Compose OR Flutter
- MLC-LLM Android SDK for inference
- Piper TTS bundled
- Whisper.cpp via JNI
- ffmpeg-kit-android for render
- AAB upload to Play Console; first-run downloads ~500MB of model files
- Effort: 4-8 weeks initial build

## API contract for mobile

The PWA reuses the existing JSON API. The APK will use the same endpoints
once we expose a configurable `GHOSTLINE_API_BASE` so it can target either
the user's home Phantomline (over Tailscale) OR the cloud version (future).

### Endpoints mobile MUST be able to call

These are the endpoints mobile clients depend on. Breaking any of these
breaks mobile. Versioning bumps go in `Accept: application/vnd.phantomline.v2+json`
header, not URL paths.

| Endpoint | Method | Used by mobile for |
|---|---|---|
| `/api/launch/readiness` | GET | First-run setup check |
| `/api/models` | GET | List available local LLM models |
| `/api/voices` | GET | List Kokoro/Piper voices |
| `/api/start_short` | POST | Start short-script generation job |
| `/api/status/<job_id>` | GET | Poll script job |
| `/api/script/<job_id>` | GET | Fetch finished script |
| `/api/tts/start` | POST | Start narration job |
| `/api/tts/status/<job_id>` | GET | Poll narration |
| `/api/upload/source-video` | POST | Upload phone-recorded footage |
| `/api/video/draft/start` | POST | Start render |
| `/api/video/draft/status/<job_id>` | GET | Poll render |
| `/api/projects` | GET | List artifacts |
| `/api/projects/<id>/file/<role>` | GET | Stream/download artifact files |
| `/api/bundles` | GET / POST | List + create session bundles |
| `/api/publish/description` | POST | Generate publish draft (title, desc, tags) |
| `/api/settings` | GET / POST | User defaults |
| `/api/feedback` | POST | In-app feedback |
| `/api/telemetry/event` | POST | Anonymous error/usage events |

### Endpoints mobile DOES NOT need

- All `/api/research/*` (mobile can call them but they're optional)
- All `/api/optimize/*` (desktop-only feature)
- All `/api/youtube/*` and `/api/publish/posts*` (desktop owns YouTube OAuth in v1)
- `/api/insights/*` (desktop-only analytics ingest)
- `/api/music/*` and `/api/mix/*` (mobile uses bundled library, no generation)
- `/api/start` (long-form, too slow on phone)

## What's already mobile-ready

- ✅ **PWA manifest** at `/static/manifest.json` (installable, themed, with shortcuts)
- ✅ **Service worker** at `/sw.js` (offline shell + static caching, scope `/`)
- ✅ **Mobile-responsive CSS** in `static/phantomline.css` (≤960px breakpoint + ≤540px breakpoint, touch targets, safe-area-insets)
- ✅ **Install prompt UI** ("Install as app" button in Settings)
- ✅ **Theme color, Apple status bar, OG meta tags**
- ✅ **iOS zoom-on-focus prevention** (16px input font on mobile)
- ✅ **Whisper-ready** (caption flow already exists server-side; just swap the engine)
- ✅ **Project bundles** — perfect mobile data model already (one Library card per video session)

## What's NOT yet mobile-ready (next-turn work)

- ❌ **WebLLM integration** for browser-side inference. Currently calls `sg.generate` which hits Ollama on the desktop. Mobile PWA needs an adapter that runs Llama 3.2 in WebGPU.
- ❌ **Engine adapters** in JS — `makeVideoWorkflow` is hardcoded to call server endpoints. Refactor to `makeVideoWorkflow({ engine: ServerEngine | WebLLMEngine })`.
- ❌ **Bundled music + B-roll library** — assets to source/license and ship in `/static/library/`.
- ❌ **Mobile share-target manifest** so other apps can share text/video into Phantomline.
- ❌ **APK build pipeline** — separate codebase decision (Kotlin vs Flutter).
- ❌ **Cloud-or-local toggle** for the APK if we ever do option C (companion app).

## Recommended next-turn order

1. **WebLLM adapter** — make the script-gen pipeline pluggable. ~1 day.
2. **Bundled music library** — license a starter pack, ship in `/static/library/music/`. ~half day.
3. **Stock B-roll library** — same pattern. ~half day.
4. **Engine swap UI** — Settings: "Run AI on this device" toggle (hidden on desktop). ~half day.
5. **Mobile-only Make Video flow** — hide MusicGen + FLUX UI when on mobile/PWA install. ~half day.
6. **Test on actual phones** — Pixel + Galaxy + iPhone. ~1 day.
7. **Ship PWA at v1**. Decide on APK based on adoption.

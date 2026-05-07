# Phantomline Brand Brief

The "what we're building, why, for whom" doc that grounds every future
design decision. Reference this from prompts and PRs so subsequent
redesigns don't drift.

## What we're building

Phantomline is a local-first AI video studio. It generates faceless
short-form videos (Shorts/Reels/TikTok) entirely on the user's own
machine — Ollama for scripts and ideas, Kokoro for narration, MusicGen
for music, optional Forge for scene visuals, ffmpeg for the final
render. No paid APIs, no data leaving the laptop, no monthly subs piling
up. The web app at phantomline.xyz is the same product running against
the user's local engines (or against in-browser fallbacks for the demo
mode).

## Primary target audience

| Persona | Description | Why they pick Phantomline |
|---|---|---|
| **Local-first creator** | Indie YouTube/TikTok creator, comfortable installing tools, hates SaaS subscriptions piling up. | One install, no subs. Owns the pipeline end to end. |
| **Privacy-leaning hobbyist** | Cares that drafts and scripts don't leak to a third party. Generative-AI-curious but wary of cloud. | Everything runs locally; cloud sync is opt-in. |
| **Power user / batch producer** | Runs many channels or experiments with formats. Needs throughput + control. | No per-render fees, can render dozens overnight. |

## Goals (in priority order)

1. **Ship videos**. The whole product earns its keep when a user gets
   a renderable MP4 in their hands. Every flow exists to get to that
   moment with the fewest typed words possible.
2. **Build trust quickly**. First-run users have to believe the local
   pipeline really works (not a demo). Readiness checks, install banner,
   and the "Local" status pill all serve this.
3. **Reward returning users**. Day-two users should feel the studio
   recognize them — saved tabs, saved brand, channel intelligence,
   recent work. The hero / first-run hints disappear after they've
   been used.
4. **Publish gracefully**. After render, there's a clear handoff into
   YouTube upload + scheduling so the video doesn't die in the
   "I'll post it later" folder.

## What we are NOT

- We are **not** a cloud-rendered video tool. Anyone wanting "give me
  100 videos this week with no machine" is not the target.
- We are **not** an AI avatar / talking-head tool. Faceless is the
  whole point.
- We are **not** trying to be Adobe-grade for editors. The advanced
  controls expander is opt-in; the default is "ship the short."

## Brand direction

Aligned with `phantomline-brand-bible-markdown.md` v1.0 (May 2026).

- **Cool dark + cyan + Geist**. Vercel-flavored industrial-precision.
  References Linear, Vercel, Stripe docs. Tight spacing, monochrome
  cool-gray foundation, one accent color, no pills, no gradients on
  chrome, no shadows for elevation.
- **Color**: cyan-500 `#22E7F5` (logo color) is the only accent. Cool
  gray scale `#0B0F11` → `#E6EAEC` for surfaces and text. Never pure
  white for text.
- **Typography**: Geist Sans + Geist Mono (self-hosted from
  vercel.com/font ideally; Google Fonts CDN as a temporary fallback).
  Body weight is **450** (variable axis), not 400.
- **Voice**: lowercase, direct, technical-honest. No "10x your
  productivity!" copy. Real claims with real numbers ("2-min render
  on M2 Air", not "lightning fast"). Show the install commands; don't
  hide behind a "Get Started" button.

## Requirements

- Fully responsive — desktop + mobile interstitial gates studio on phones
- Accessible — WCAG 2.1 AA, skip-to-main, prefers-reduced-motion
- Privacy-respecting — no third-party analytics on landing without consent
- Performance — first paint <2.5s, no Lighthouse perf below 80

## Sections (current)

1. Landing — `templates/landing.html` (industrial-editorial redesign 2026-05)
2. Studio — `templates/index.html` (the app shell)
3. Account — `templates/account.html` (sign-in + billing)
4. Install — `templates/install.html` (download + setup)
5. Pillar pages — `templates/pillar_*.html` (SEO long-form articles)
6. Alternative + alternatives hub — competitor comparison pages
7. Pricing / About / Privacy / Terms

## Cross-references

- Style guide: `docs/style-guide.md`
- OAuth checklist: `docs/single-oauth-checklist.md`
- Tab simplification history: `docs/sprint1-tab-simplification.md`

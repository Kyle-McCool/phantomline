# 2026 YouTube thumbnail research — source of truth for `thumbnail_generator.py`

This document captures the research findings that drove the preset
design in `thumbnail_generator.py`. When that module changes, check
that the change is consistent with these findings — or if the platform
has shifted, refresh this doc first.

Original research date: 2026-05-03 (deep-research run, Sonnet 4.5).

## Platform changes (load-bearing for code)

- **Recommended upload size 2026:** 3840×2160, uploaded "as large as
  possible," minimum width 640 px. Mobile cap 2 MB; desktop 50 MB.
  → We generate at 1920×1080 (SDXL-friendly) and let the caller upscale
  before upload if max-spec is needed.
- **Vertical videos:** 16:9 custom thumbnails are auto-replaced with a
  4:5 thumbnail on mobile Home/Explore/Subs. → 4:5 is now a first-class
  aspect in `THUMBNAIL_SIZES`.
- **Native A/B testing:** YouTube's tool tests up to 3 title/thumbnail
  combos and chooses the winner by **watch-time share**, not CTR. If
  any variant is under 720p, all variants get downscaled to 480p.
  → Generator never outputs below 720p.
- **TV is now the primary YouTube device** in the U.S. by watch time.
  → Composition rule: small-render-first AND TV-safe. One focal subject,
  one contrast edge, one optional text payload.

## Cross-niche pattern data

Source: 1of10 study, 300k high-performing 2025 videos, 62.6B views.
Correlational, vendor-published, not peer-reviewed. Treat as directional.

- **Thumbnails with text averaged ~19% fewer views.** Best-performing
  text setups: no text, OR <10 chars covering <7% of image area.
  → `text_default = False` for everything except listicle. Listicle is
  the documented exception because the count IS the hook.
- **Cyan-dominant thumbnails averaged ~36% more views than baseline.**
  → Cyan is the default accent for faceless_story and horror_cosmic.
  Other niches use their niche-appropriate accent (red for listicle,
  warning red for documentary, hot orange for tutorial).
- **Dark thumbnails underperformed.** → Even horror_cosmic gets one
  motivated light source (monitor glow, lunar rim, lighthouse beam).
- **Faces are NOT a universal win.** → Subject placement varies per
  niche; faces only when relevant (case-subject's face for documentary,
  not the creator's; supporting reaction face for tutorial).

## Per-niche findings (encoded in THUMBNAIL_PRESETS)

### faceless_story
References: MrBallen, Lazy Masquerade.
- Palette: muted cinematic darks + one loud accent (blood red or icy cyan).
- Subject: narrator/witness on left third; evidence object on right third.
- Text: 0 by default; if used, 1-3 words top-left.
- Avoid: crime-board collage, six-photos arrangement, glowing evidence,
  HDR posterized look.
- AI prompt: documentary still, forensic flash, CCTV softness, analog
  grain, real focal length, muted cinematic grading.

### horror_cosmic
References: Lighthouse Horror, The Dark Somnium, Mr. Creeps.
- Palette: near-black + cold accent (cyan, lab green, lunar rim).
- Subject: anomaly centered, tiny human/object lower-third for scale.
- Text: 0-4 words max.
- Avoid: center-framed Midjourney monster portrait, purple smoke, fake
  volumetric fog.
- AI prompt: analog horror still, motivated light source, single
  anomaly, environmental scale, weathering, large negative space.

### mystery_documentary
References: Nick Crowley, Coffeehouse Crime.
- Palette: charcoal, yellowed paper, muted steel, warning red.
- Subject: artifact (mugshot/VHS/map/poster) center or right; case
  subject's face NOT creator's face.
- Text: 0-3 words; label not explanation.
- Avoid: fake detective board, fingerprints, blood splatter decoration.
- AI prompt: xerox texture, halftone print, aged paper, camera flash,
  timestamp overlay aesthetic, era-accurate props.

### tutorial_explainer
References: Fireship.
- Palette: dark slate + ONE hot accent.
- Subject: product/UI fragment center or center-right; supporting face
  on opposite side only when conflict needs reaction.
- Text: 2-4 words max, extra-bold sans, upper-left or left-center.
  This is the niche where text still survives.
- Avoid: full desktop screenshots, three windows, code panes, fake
  glossy 3D dashboards.
- AI prompt: real screenshots / product renders for the base layer; AI
  for mood/background only. Avoid AI-generated UI text.

### listicle (text-as-hero exception)
References: Top 5 Unknowns, Scary Mysteries, Top5s.
- Palette: red + white + black + gold accent (the only niche where the
  old loud-clickbait palette still works).
- Subject: text IS the hero. Giant numeral + headline center-left;
  hero crop on right edge.
- Text: 2-6 words, ONE numeral cluster, distressed display font, huge.
- Avoid: ten arrows, ten circles, scrapbook collage.
- AI prompt: one textured background or one hero object; manual
  typography for the count.

## What I deliberately did NOT encode

- **Brand consistency vs per-video** — research had no controlled data,
  only soft observations. Defer until we have our own A/B telemetry.
- **A/B testing tool integration** — research recommends YouTube native
  (concurrent, watch-time-optimized). Building our own tester is
  out-of-scope; we'll hand off the generated thumbnail and let the user
  feed it into YouTube's native test.
- **AI-down-ranking penalty** — research found NO official YouTube
  source confirming AI thumbnails are down-ranked. Audience trust is
  the real risk. We avoid the visual tells (glossy skin, fake fog,
  Midjourney monster portrait) via negative prompts instead of dodging
  AI generation entirely.

## When to refresh this doc

- YouTube changes thumbnail spec (size, A/B tool behavior).
- A new vendor study materially contradicts the 1of10 findings.
- We collect our own A/B data from paying users that supersedes vendor
  studies.
- A niche shifts visibly (e.g., listicles abandon text; documentary
  niches start winning with bright palettes).

The original deep-research output that informed this doc is preserved
in the project's session transcript dated 2026-05-03.

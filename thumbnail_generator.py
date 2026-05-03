"""Phantomline thumbnail generator.

YouTube CTR is title × thumbnail. The product generates everything else;
this module closes the gap.

Two paths:

1. **Forge SDXL** when available (desktop install with Forge running on
   localhost:7861, or any A1111-compatible endpoint). Generates a
   cinematic background image tuned for thumbnail use cases.

2. **PIL-only fallback** when Forge isn't reachable (the hosted Render
   slim deploy, or a desktop user who hasn't set up Forge yet).

Both paths end in `compose_thumbnail()` which lays the title text on top.
The composition layer changes per preset.

Design grounded in 2026 research (see docs/thumbnail-research-2026.md):

- 1of10 study (300k high-performing 2025 videos): thumbnails with text
  averaged ~19% fewer views; cyan-dominant thumbnails averaged +36%;
  dark thumbnails underperformed; faces are not a universal win.
  Correlational vendor data, treated as directional. Net product
  decision: default `text_overlay=False` for non-listicle presets.

- YouTube's native A/B tool optimizes for watch-time share, not CTR.
  Variants under 720p get the entire test downscaled to 480p, so we
  never output below 720p. Recommended upload size in 2026 is 3840×2160
  uploaded as-large-as-possible — we generate at 1920×1080 (Forge's
  reliable SDXL ceiling at standard checkpoints) and the caller can
  upscale before upload if they want max-spec output.

- "AI penalty" is not platform-policy; it's audience trust. Avoid the
  Midjourney-monster-portrait look — every preset's negative prompt
  includes the visual tells (glossy skin, floating embers, fake
  volumetric fog) called out in the research.
"""

from __future__ import annotations

import base64
import math
import os
import random
import re
from io import BytesIO
from pathlib import Path

import requests
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont


# ---------------------------------------------------------------------------
# Bundled fonts. These ship in static/fonts/ (committed to git) so the
# generator gets consistent typography on every machine — including the
# hosted Render slim deploy where OS-default Impact may not exist.
# ---------------------------------------------------------------------------
_FONTS_DIR = Path(__file__).resolve().parent / "static" / "fonts"

# Per-niche font choice. Picked so each preset has a recognizable voice:
#  - Anton: tall condensed sans, the modern "explainer big text" face
#  - BebasNeue: classic editorial poster, works for story/horror titles
#  - Bangers: comic-book display, the listicle hero face
#  - MontserratBlack: heavy round sans, used for tutorial captions
#  - Inter: subtitle / eyebrow / metadata
_FONT_FILES = {
    "anton": _FONTS_DIR / "Anton-Regular.ttf",
    "bebas": _FONTS_DIR / "BebasNeue-Regular.ttf",
    "bangers": _FONTS_DIR / "Bangers-Regular.ttf",
    "montserrat_black": _FONTS_DIR / "Montserrat-Black.ttf",
    "inter": _FONTS_DIR / "Inter-Bold.ttf",
}


# YouTube 2026 spec: 16:9 thumbnails uploaded at 3840×2160 ideal, with a
# minimum width of 640 px. We generate at 1920×1080 (SDXL-friendly,
# safely above the 720p A/B testing floor) and let the caller upscale
# to 4K before upload if needed. 9:16 (Shorts feed) and 4:5 (mobile
# Home/Explore replacement for vertical videos) are also supported.
THUMBNAIL_SIZES = {
    "16:9": (1920, 1080),
    "9:16": (1080, 1920),
    "4:5": (1080, 1350),
    "1:1": (1080, 1080),
}

# Bottom-right safe-zone — YouTube overlays the video duration here at
# render time. Anything in this corner gets clobbered. Pixels are
# percentages of canvas height/width.
SAFE_ZONE_BOTTOM_RIGHT = (0.18, 0.10)  # 18% width, 10% height kept clear

# Phantomline brand fallbacks for when no preset matches.
BRAND_BG = (9, 11, 12)            # #090b0c — site theme-color
BRAND_ACCENT = (52, 211, 219)     # phantomline cyan
TEXT_PRIMARY = (255, 255, 255)
TEXT_MUTED = (180, 195, 200)


# ---------------------------------------------------------------------------
# Per-niche presets. Wired from the 2026 deep-research findings.
#
# Each preset describes:
#   palette:              5-color hex sequence (most→least dominant)
#   accent:               hot accent color (RGB tuple) used for the
#                         underline / eyebrow / number block
#   bg:                   default canvas color when no AI image is
#                         generated (PIL fallback path)
#   subject_placement:    English description for SDXL prompt
#   composition_inject:   atmosphere + texture cues for SDXL prompt
#   negative_inject:      AI-tells to keep out of the image
#   text_default:         whether text-overlay is on by default
#                         (False for everything except listicle, per the
#                         1of10 -19% text-thumbnail finding)
#   text_max_words:       hard ceiling so a generator caller can't
#                         accidentally request 12-word essay overlays
#   text_position:        anchor for the optional overlay
# ---------------------------------------------------------------------------

THUMBNAIL_PRESETS = {
    "faceless_story": {
        "label": "Faceless story / Reddit narration (MrBallen-style)",
        "palette": [
            (19, 25, 35), (157, 30, 37), (231, 228, 221),
            (91, 201, 214), (52, 65, 75),
        ],
        "accent": (91, 201, 214),
        "secondary": (157, 30, 37),
        "bg": (19, 25, 35),
        "fallback_pattern": "rim_light",   # PIL fallback layout
        "title_font": "bebas",
        "subtitle_font": "inter",
        "subject_placement": (
            "witness silhouette, narrator stand-in, or reaction cutout on "
            "the left third; evidence object, location, or single anomaly "
            "on the right third"
        ),
        "composition_inject": (
            "documentary still, forensic flash photography, CCTV softness, "
            "one evidence object, analog grain, slight chromatic aberration, "
            "imperfect symmetry, scuffed surfaces, real focal length, "
            "muted cinematic grading"
        ),
        "negative_inject": (
            "crime-board collage, multiple photos arranged, fake newspaper "
            "scraps, glowing evidence, aggressive HDR, glossy skin, "
            "floating embers, fake volumetric fog, posterized true crime"
        ),
        "text_default": False,
        "text_max_words": 5,
        "text_position": "lower-left",
    },
    "horror_cosmic": {
        "label": "Horror / cosmic horror narration",
        "palette": [
            (7, 9, 13), (16, 34, 53), (49, 215, 255),
            (216, 212, 198), (97, 42, 66),
        ],
        "accent": (49, 215, 255),
        "secondary": (216, 212, 198),
        "bg": (7, 9, 13),
        "fallback_pattern": "spotlight",
        "title_font": "bebas",
        "subtitle_font": "inter",
        "subject_placement": (
            "anomaly centered with massive scale; tiny human or human-scale "
            "object placed lower-third for size reference"
        ),
        "composition_inject": (
            "analog horror still, motivated light source like monitor glow "
            "or lunar rim light or lab green or lighthouse beam, single "
            "anomaly, environmental scale, film grain, sensor noise, "
            "weathering, imperfect focus, real-world prop wear, large "
            "negative space, documentary evidence aesthetic"
        ),
        "negative_inject": (
            "beautiful monster portrait, center-framed creature against "
            "purple smoke, Midjourney horror wallpaper, glossy skin, "
            "fake volumetric fog, floating embers, generic SDXL fog"
        ),
        "text_default": False,
        "text_max_words": 5,
        "text_position": "lower-center",
    },
    "mystery_documentary": {
        "label": "Mystery documentary / true-crime adjacent",
        "palette": [
            (20, 21, 24), (199, 185, 160), (178, 30, 35),
            (236, 231, 220), (104, 122, 138),
        ],
        "accent": (178, 30, 35),
        "secondary": (199, 185, 160),
        "bg": (20, 21, 24),
        "fallback_pattern": "dossier",
        "title_font": "bebas",
        "subtitle_font": "inter",
        "subject_placement": (
            "single artifact (mugshot, VHS still, map, poster, house, or "
            "symbol) center or right; if a face is needed it is the case "
            "subject's face, never the creator's"
        ),
        "composition_inject": (
            "xerox texture, halftone print, aged paper, camera flash, "
            "timestamp overlay aesthetic, era-accurate props, mild "
            "chromatic bleeding, partial occlusion, dossier photography, "
            "evidence-room lighting"
        ),
        "negative_inject": (
            "fake detective board, fingerprint overlays, blood splatter "
            "decoration, six photos arranged, generic SDXL collage, "
            "glossy professional photography, AI-perfect symmetry"
        ),
        "text_default": False,
        "text_max_words": 4,
        "text_position": "lower-left",
    },
    "tutorial_explainer": {
        "label": "Tutorial / how-to / explainer (Fireship-style)",
        "palette": [
            (16, 19, 24), (255, 107, 61), (237, 237, 237),
            (86, 196, 255), (35, 42, 54),
        ],
        "accent": (255, 107, 61),
        "secondary": (86, 196, 255),
        "bg": (16, 19, 24),
        "fallback_pattern": "diagonal_block",
        "title_font": "anton",
        "subtitle_font": "montserrat_black",
        "subject_placement": (
            "one product fragment, UI crop, or rendered prop center or "
            "center-right; supporting human face or icon on the opposite "
            "side ONLY if the conflict requires a reaction"
        ),
        "composition_inject": (
            "clean studio lighting, single product shot, flat-but-deep "
            "background gradient, real screenshot composited cleanly, "
            "high color contrast on a dark slate base, before-and-after "
            "transformation reads left to right"
        ),
        "negative_inject": (
            "full desktop screenshots, three windows, code panes with "
            "unreadable text, fake glossy 3D dashboards, generic SDXL "
            "interface mockups, tab clutter, sticker bombing"
        ),
        "text_default": True,
        "text_max_words": 5,
        "text_position": "left-center",
    },
    "listicle": {
        "label": "Listicle / Top N (text-as-hero exception)",
        "palette": [
            (12, 12, 14), (198, 31, 38), (244, 241, 235),
            (226, 185, 59), (44, 90, 99),
        ],
        "accent": (198, 31, 38),
        "secondary": (226, 185, 59),
        "bg": (12, 12, 14),
        "fallback_pattern": "poster",
        "title_font": "bangers",
        "subtitle_font": "anton",
        "subject_placement": (
            "giant count or topic block on the center-left or full center; "
            "one hero concept image cropped on the right edge"
        ),
        "composition_inject": (
            "one textured background or one hero object only, distressed "
            "paper or scratched film texture, single high-contrast subject, "
            "negative space for typography overlay, magazine-poster aesthetic"
        ),
        "negative_inject": (
            "ten arrows, ten little circles, ten cutouts, scrapbook layout, "
            "AI-generated pseudo-letters, muddy text edges, generic SDXL "
            "infographic, cluttered composition"
        ),
        "text_default": True,
        "text_max_words": 6,
        "text_position": "center",
    },
}


def detect_preset(title: str, genre: str = "", recipe: str = "") -> str:
    """Best-effort preset auto-detection from title/genre/recipe strings.

    Returns one of the THUMBNAIL_PRESETS keys, defaulting to faceless_story
    (the broadest catch-all for narration content). Caller can override
    by passing `style=<preset_key>` directly to the API."""
    blob = f"{title} {genre} {recipe}".lower()

    # Listicle detection wins first because the count is the strongest
    # signal — "10 disturbing X" should not get classified as story even
    # if "disturbing" matches horror keywords.
    if re.search(r"\btop\s*\d+\b|\b\d{1,3}\s+(disturbing|creepy|terrifying|unexplained|mysterious|haunting|scariest|weirdest|strangest|dark)\b", blob):
        return "listicle"
    if "listicle" in blob or re.match(r"^\d{1,3}\s", blob):
        return "listicle"

    # Genre/recipe explicit overrides — if the upstream form said
    # "cosmic horror" or "documentary", that's a more reliable signal
    # than title-pattern guessing, so honor it before heuristics.
    explicit = f"{genre} {recipe}".lower()
    if "horror" in explicit or "cosmic" in explicit or "creepypasta" in explicit:
        return "horror_cosmic"
    if "documentary" in explicit or "true crime" in explicit or "investigation" in explicit:
        return "mystery_documentary"
    if "tutorial" in explicit or "explainer" in explicit or "how-to" in explicit:
        return "tutorial_explainer"
    if "listicle" in explicit:
        return "listicle"

    # Tutorial detection — but only on instructional-tense markers.
    # "Explained" / "explainer" alone is too noisy (MrBallen titles end
    # with "Explained" and they are not tutorials), so we require a
    # stronger signal: "how to", "how-to", "guide", etc.
    if "tutorial" in blob or "how to" in blob or "how-to" in blob or " guide" in blob or "step by step" in blob or "setup" in blob or "set up" in blob:
        return "tutorial_explainer"

    # Faceless-story signals — "found footage" / "I work in" / "I
    # checked" patterns are unambiguously story narration.
    if "found footage" in blob or "i work" in blob or "found out" in blob or "creepy story" in blob or "reddit" in blob or "scary story" in blob:
        return "faceless_story"

    if "horror" in blob or "cosmic" in blob or "eldritch" in blob or "backrooms" in blob or "analog horror" in blob or "creature" in blob or "monster" in blob or "creepypasta" in blob or "lighthouse" in blob or "abyss" in blob:
        return "horror_cosmic"

    # Use "myster" stem so both "mystery" and "mysteries" match. Same
    # for "documentar"/"investigat".
    if "documentar" in blob or "true crime" in blob or "case file" in blob or "investigat" in blob or "unsolved" in blob or "myster" in blob:
        return "mystery_documentary"

    return "faceless_story"


def get_preset(name: str | None) -> dict:
    """Resolve a preset name to its dict. Falls back to faceless_story."""
    if not name or name == "auto":
        return THUMBNAIL_PRESETS["faceless_story"]
    return THUMBNAIL_PRESETS.get(name, THUMBNAIL_PRESETS["faceless_story"])


# ---------------------------------------------------------------------------
# Forge prompt construction
# ---------------------------------------------------------------------------


def forge_thumbnail_prompt(title: str, preset_name: str = "faceless_story",
                           subject_hint: str = "") -> tuple[str, str]:
    """Build (positive_prompt, negative_prompt) tuned to a preset.

    The positive prompt composes: [title concept] + [subject placement
    rule] + [composition_inject from preset] + [universal thumbnail
    quality cues]. The negative prompt prepends preset-specific AI-tells
    to avoid (per 2026 research) on top of universal quality killers.
    """
    preset = get_preset(preset_name)
    parts = [title.strip()]
    if subject_hint.strip():
        parts.insert(0, subject_hint.strip())
    parts.extend([
        preset["subject_placement"],
        preset["composition_inject"],
        # Universal quality cues — apply to every preset.
        "cinematic YouTube thumbnail composition",
        "readable at 120px feed-scroll size",
        "TV-safe contrast ratios",
        "negative space for title overlay",
        "no text, no captions, no logo, no watermark, no UI",
        "8k detail, professional color grading",
    ])
    positive = ", ".join(parts)

    negative_parts = [
        preset["negative_inject"],
        # Universal AI-tells to avoid (from 2026 research).
        "blurry, low quality, watermark, text, caption, logo, distorted, "
        "bad anatomy, ugly, deformed, generic stock art, "
        "obvious AI generation, glossy skin, perfect symmetry, "
        "AI fingers, plastic skin, posterized HDR",
    ]
    negative = ", ".join(negative_parts)

    return positive, negative


# ---------------------------------------------------------------------------
# Forge backend
# ---------------------------------------------------------------------------


def _forge_url() -> str:
    return (
        os.getenv("GHOSTLINE_FORGE_URL")
        or os.getenv("BINDERY_FORGE_URL")
        or "http://127.0.0.1:7861"
    ).rstrip("/")


def _forge_checkpoint() -> str:
    return (
        os.getenv("GHOSTLINE_FORGE_CHECKPOINT")
        or os.getenv("BINDERY_FORGE_CHECKPOINT")
        or ""
    ).strip()


def forge_available(timeout: float = 1.5) -> bool:
    """Liveness check against Forge sd-webui API."""
    try:
        res = requests.get(f"{_forge_url()}/sdapi/v1/sd-models", timeout=timeout)
        return res.status_code == 200
    except requests.RequestException:
        return False


def generate_forge_background(title: str, *, aspect: str = "16:9",
                              preset_name: str = "faceless_story",
                              subject_hint: str = "",
                              steps: int = 22) -> bytes:
    """Hit Forge's txt2img with a preset-tuned prompt. Returns raw PNG bytes.

    Generation resolution is bin-aligned for SDXL (1024-base) and then
    upscaled to thumbnail size with LANCZOS. Going straight to 1920×1080
    in SDXL triggers framing artifacts at non-trained ratios."""
    width, height = THUMBNAIL_SIZES.get(aspect, THUMBNAIL_SIZES["16:9"])

    if aspect == "16:9":
        gen_w, gen_h = 1280, 720
    elif aspect == "9:16":
        gen_w, gen_h = 720, 1280
    elif aspect == "4:5":
        gen_w, gen_h = 832, 1040
    else:
        gen_w, gen_h = 1024, 1024

    positive, negative = forge_thumbnail_prompt(title, preset_name=preset_name,
                                                subject_hint=subject_hint)

    payload = {
        "prompt": positive,
        "negative_prompt": negative,
        "steps": int(max(8, min(40, steps))),
        "width": gen_w,
        "height": gen_h,
        "cfg_scale": 1.0,
        "distilled_cfg_scale": 3.5,
        "sampler_name": "Euler",
        "sampler_index": "Euler",
        "scheduler": "Simple",
        "batch_size": 1,
        "n_iter": 1,
        "seed": -1,
        "do_not_save_samples": True,
        "do_not_save_grid": True,
    }
    cp = _forge_checkpoint()
    if cp:
        payload["override_settings"] = {"sd_model_checkpoint": cp}
        payload["override_settings_restore_afterwards"] = True

    res = requests.post(f"{_forge_url()}/sdapi/v1/txt2img", json=payload, timeout=600)
    if res.status_code >= 400:
        raise RuntimeError(f"Forge txt2img failed: HTTP {res.status_code} — {res.text[:200]}")
    try:
        data = res.json()
    except ValueError as exc:
        raise RuntimeError(f"Forge returned non-JSON: {res.text[:200]}") from exc
    images = data.get("images") or []
    if not images:
        raise RuntimeError(f"Forge returned no images: {data.get('error') or data!r}")
    raw = base64.b64decode(images[0])
    with Image.open(BytesIO(raw)) as img:
        img = img.convert("RGB").resize((width, height), Image.LANCZOS)
        out = BytesIO()
        img.save(out, "PNG", optimize=True)
        return out.getvalue()


# ---------------------------------------------------------------------------
# Pollinations.ai — FREE FLUX endpoint. No API key. No auth. The community
# runs FLUX-schnell behind a public URL pattern:
#
#   https://image.pollinations.ai/prompt/{url_encoded_prompt}?width=...&height=...&model=flux&seed=...&nologo=true
#
# Trade-off: it's a free service, so reliability isn't guaranteed —
# response times vary (3-15s typical, 30s+ during peak), and outages
# happen. This is why we still chain to fal.ai (paid, reliable) → PIL
# (always works). For Phantomline at low-medium volume, Pollinations is
# the right primary backend.
# ---------------------------------------------------------------------------

_POLLINATIONS_BASE = "https://image.pollinations.ai/prompt/"


def pollinations_available() -> bool:
    """Pollinations is always available unless explicitly disabled."""
    return os.getenv("PHANTOMLINE_DISABLE_POLLINATIONS", "").lower() not in ("1", "true", "yes")


def generate_pollinations_background(title: str, *, aspect: str = "16:9",
                                     preset_name: str = "faceless_story",
                                     subject_hint: str = "",
                                     seed: int | None = None,
                                     timeout: float = 45.0) -> bytes:
    """Hit Pollinations.ai's free FLUX endpoint. Returns PNG bytes resized
    to the canonical thumbnail size. Raises RuntimeError on transport/HTTP
    failure so the caller can fall back to fal.ai or PIL.

    The prompt encoding is URL-safe but we keep it under ~2KB to avoid
    truncation by Pollinations' route handling. Seeding lets us reproduce
    a specific generation in the batch endpoint."""
    from urllib.parse import quote

    width, height = THUMBNAIL_SIZES.get(aspect, THUMBNAIL_SIZES["16:9"])
    positive, _negative = forge_thumbnail_prompt(
        title, preset_name=preset_name, subject_hint=subject_hint
    )
    # Pollinations doesn't accept negative prompts via URL params, so we
    # inline an "avoid X" suffix instead. FLUX models respond to negative
    # *guidance* even when delivered as positive text saying "no X".
    full_prompt = positive[:1500] + ", no text, no captions, no logos, no watermarks"

    encoded = quote(full_prompt, safe="")
    params = {
        "width": str(min(width, 1920)),  # Pollinations caps at 1920×1920
        "height": str(min(height, 1920)),
        "model": "flux",
        "nologo": "true",
        "enhance": "false",   # don't auto-rewrite our prompt
        "safe": "true",
    }
    if seed is not None:
        params["seed"] = str(int(seed))

    url = _POLLINATIONS_BASE + encoded + "?" + "&".join(f"{k}={v}" for k, v in params.items())

    try:
        res = requests.get(url, timeout=timeout, stream=True)
    except requests.RequestException as exc:
        raise RuntimeError(f"Pollinations request failed: {exc}") from exc

    if res.status_code != 200:
        raise RuntimeError(f"Pollinations returned HTTP {res.status_code}: {res.text[:200]}")

    # Cap download size to prevent abuse — 25 MB.
    chunks = []
    total = 0
    for chunk in res.iter_content(chunk_size=64 * 1024):
        chunks.append(chunk)
        total += len(chunk)
        if total > 25 * 1024 * 1024:
            raise RuntimeError("Pollinations response exceeded 25 MB cap.")
    raw = b"".join(chunks)

    try:
        with Image.open(BytesIO(raw)) as img:
            img = img.convert("RGB").resize((width, height), Image.LANCZOS)
            out = BytesIO()
            img.save(out, "PNG", optimize=True)
            return out.getvalue()
    except Exception as exc:
        raise RuntimeError(f"Pollinations returned non-image: {exc}") from exc


# ---------------------------------------------------------------------------
# fal.ai (FLUX schnell) — hosted SDXL/FLUX path
#
# fal.ai charges ~$0.005 per FLUX-schnell call as of 2026-04. The API
# returns 1024-bin images quickly (1-3s) and the quality beats vanilla
# SDXL by a wide margin, so this is the right hosted-deploy provider.
# Wire the FAL_KEY env var in Render dashboard; absence falls through
# to the PIL poster fallback.
# ---------------------------------------------------------------------------

_FALAI_ENDPOINT = "https://fal.run/fal-ai/flux/schnell"


def falai_available() -> bool:
    return bool((os.getenv("FAL_KEY") or os.getenv("FAL_API_KEY") or "").strip())


def generate_falai_background(title: str, *, aspect: str = "16:9",
                              preset_name: str = "faceless_story",
                              subject_hint: str = "",
                              seed: int | None = None) -> bytes:
    """Call fal.ai's FLUX schnell endpoint. Returns PNG bytes resized to
    the thumbnail's canonical width/height. Raises RuntimeError on any
    non-200 response so the caller can chain to a fallback."""
    key = (os.getenv("FAL_KEY") or os.getenv("FAL_API_KEY") or "").strip()
    if not key:
        raise RuntimeError("FAL_KEY not set; cannot use fal.ai path.")

    width, height = THUMBNAIL_SIZES.get(aspect, THUMBNAIL_SIZES["16:9"])
    positive, _negative = forge_thumbnail_prompt(
        title, preset_name=preset_name, subject_hint=subject_hint
    )

    # FLUX accepts named sizes or explicit dimensions. We bin to the
    # closest supported preset; fal upscales to our final size after.
    fal_size = {
        "16:9": "landscape_16_9",
        "9:16": "portrait_16_9",
        "4:5": "portrait_4_3",
        "1:1": "square_hd",
    }.get(aspect, "landscape_16_9")

    payload = {
        "prompt": positive,
        "image_size": fal_size,
        "num_inference_steps": 4,  # schnell is tuned for 1-4 steps
        "num_images": 1,
        "enable_safety_checker": True,
    }
    if seed is not None:
        payload["seed"] = int(seed)

    res = requests.post(
        _FALAI_ENDPOINT,
        json=payload,
        headers={"Authorization": f"Key {key}"},
        timeout=60,
    )
    if res.status_code >= 400:
        raise RuntimeError(f"fal.ai FLUX failed: HTTP {res.status_code} — {res.text[:200]}")

    try:
        data = res.json()
    except ValueError as exc:
        raise RuntimeError(f"fal.ai returned non-JSON: {res.text[:200]}") from exc

    images = data.get("images") or []
    if not images:
        raise RuntimeError(f"fal.ai returned no images: {data!r}")

    img_url = images[0].get("url")
    if not img_url:
        raise RuntimeError(f"fal.ai response missing url: {images[0]!r}")

    img_res = requests.get(img_url, timeout=30)
    if img_res.status_code != 200:
        raise RuntimeError(f"fal.ai image download failed: HTTP {img_res.status_code}")

    with Image.open(BytesIO(img_res.content)) as img:
        img = img.convert("RGB").resize((width, height), Image.LANCZOS)
        out = BytesIO()
        img.save(out, "PNG", optimize=True)
        return out.getvalue()


# ---------------------------------------------------------------------------
# PIL-only fallback poster compositions
#
# When neither Forge nor fal.ai is available, we still ship a thumbnail
# that doesn't look broken. Rather than punt to "diagonal lines on dark"
# we render real poster-grade compositions per preset:
#
#   rim_light       — vignetted dark with a single cyan rim glow on the
#                     right edge, evoking moonlight on a subject we can't
#                     actually generate
#   spotlight       — circular light cone descending from upper center,
#                     suggesting a single cosmic-horror anomaly without
#                     having to render it
#   dossier         — yellowed-paper texture with red stamp accent and
#                     coffee-ring stains, evidence-room aesthetic
#   diagonal_block  — dark slate split by a hot-orange angled wedge,
#                     the modern Fireship/explainer look
#   poster          — high-contrast magazine layout with a colored title
#                     band and accent bar, listicle territory
# ---------------------------------------------------------------------------


def _add_grain(img: Image.Image, amount: float = 0.03) -> Image.Image:
    """Adds film-grain noise. amount is 0-1; 0.03 is subtle, 0.08 heavy."""
    if amount <= 0:
        return img
    width, height = img.size
    # Build a low-res noise plate and upscale — smoother than per-pixel rand.
    noise_w, noise_h = max(1, width // 4), max(1, height // 4)
    noise = Image.new("L", (noise_w, noise_h))
    nd = noise.load()
    for y in range(noise_h):
        for x in range(noise_w):
            nd[x, y] = random.randint(0, 255)
    noise = noise.resize((width, height), Image.BILINEAR).filter(ImageFilter.GaussianBlur(0.6))
    grain = Image.new("RGB", (width, height), (128, 128, 128))
    grain = Image.blend(img, grain, amount)
    # Use the noise plate as a per-pixel mix mask so we don't flatten the image.
    return Image.composite(grain, img, noise.point(lambda v: int(v * amount * 255 / 128)))


def _radial_gradient(size: tuple[int, int], inner: tuple[int, int, int],
                     outer: tuple[int, int, int], center: tuple[float, float] = (0.5, 0.5),
                     radius_factor: float = 0.85) -> Image.Image:
    """Cheap radial gradient via per-row blending. Used for spotlights and
    vignettes. center is a (x_frac, y_frac) tuple in [0, 1]."""
    width, height = size
    cx, cy = int(width * center[0]), int(height * center[1])
    max_r = math.hypot(width, height) * radius_factor
    img = Image.new("RGB", size, outer)
    px = img.load()
    for y in range(height):
        for x in range(width):
            d = math.hypot(x - cx, y - cy) / max_r
            t = max(0.0, min(1.0, d))
            px[x, y] = (
                int(inner[0] + (outer[0] - inner[0]) * t),
                int(inner[1] + (outer[1] - inner[1]) * t),
                int(inner[2] + (outer[2] - inner[2]) * t),
            )
    return img


def _linear_gradient(size: tuple[int, int], top: tuple[int, int, int],
                     bottom: tuple[int, int, int], horizontal: bool = False) -> Image.Image:
    """Vertical (or horizontal) linear gradient."""
    width, height = size
    img = Image.new("RGB", size, top)
    draw = ImageDraw.Draw(img)
    span = width if horizontal else height
    for i in range(span):
        t = i / max(1, span - 1)
        c = (
            int(top[0] + (bottom[0] - top[0]) * t),
            int(top[1] + (bottom[1] - top[1]) * t),
            int(top[2] + (bottom[2] - top[2]) * t),
        )
        if horizontal:
            draw.line([(i, 0), (i, height)], fill=c)
        else:
            draw.line([(0, i), (width, i)], fill=c)
    return img


def _pattern_rim_light(width: int, height: int, preset: dict) -> Image.Image:
    """Vignetted dark canvas with a subtle cyan rim glow on the right
    edge. Suggests a backlit subject we couldn't actually generate."""
    bg = preset["bg"]
    accent = preset["accent"]
    rim = tuple(int(c * 0.45) for c in accent)
    img = _radial_gradient(
        (width, height),
        inner=rim,
        outer=bg,
        center=(0.92, 0.45),
        radius_factor=0.55,
    )
    # Darken everything else with a vignette
    overlay = _radial_gradient(
        (width, height), inner=(0, 0, 0), outer=bg, center=(0.5, 0.5), radius_factor=1.1
    )
    img = Image.blend(img, overlay, 0.45)
    return _add_grain(img, 0.04)


def _pattern_spotlight(width: int, height: int, preset: dict) -> Image.Image:
    """Cone of cold light descending from upper center over near-black —
    the cosmic-horror 'something is illuminated up there' suggestion."""
    bg = preset["bg"]
    accent = preset["accent"]
    cone_color = tuple(int(c * 0.55) for c in accent)
    img = _radial_gradient(
        (width, height),
        inner=cone_color,
        outer=bg,
        center=(0.5, 0.18),
        radius_factor=0.65,
    )
    return _add_grain(img, 0.05)


def _pattern_dossier(width: int, height: int, preset: dict) -> Image.Image:
    """Aged-paper background with a red stamp wedge and faint coffee
    rings. Evidence-room aesthetic for documentary niche."""
    paper = preset.get("secondary") or (199, 185, 160)
    accent = preset["accent"]
    bg = preset["bg"]
    img = _linear_gradient(
        (width, height),
        top=tuple(min(255, c + 18) for c in paper),
        bottom=tuple(max(0, c - 12) for c in paper),
    )
    draw = ImageDraw.Draw(img)
    # Red stamp wedge top-right
    stamp_pts = [
        (int(width * 0.62), int(height * 0.05)),
        (int(width * 0.98), int(height * 0.05)),
        (int(width * 0.98), int(height * 0.34)),
    ]
    draw.polygon(stamp_pts, fill=accent)
    # Coffee-ring stains
    for cx_f, cy_f, r_f in [(0.18, 0.78, 0.10), (0.72, 0.62, 0.07)]:
        cx, cy = int(width * cx_f), int(height * cy_f)
        r = int(min(width, height) * r_f)
        ring = Image.new("RGBA", (r * 2, r * 2), (0, 0, 0, 0))
        rd = ImageDraw.Draw(ring)
        rd.ellipse((0, 0, r * 2, r * 2), outline=(70, 40, 20, 80), width=4)
        rd.ellipse((6, 6, r * 2 - 6, r * 2 - 6), outline=(70, 40, 20, 50), width=2)
        ring = ring.filter(ImageFilter.GaussianBlur(2))
        img.paste(ring, (cx - r, cy - r), ring)
    # Subtle vignette
    vignette = _radial_gradient(
        (width, height), inner=(0, 0, 0), outer=bg, center=(0.5, 0.5), radius_factor=1.3
    )
    img = Image.blend(img, vignette, 0.25)
    return _add_grain(img, 0.06)


def _pattern_diagonal_block(width: int, height: int, preset: dict) -> Image.Image:
    """Dark slate split by a hot-accent angled wedge filling the right
    third. Modern explainer/Fireship look."""
    bg = preset["bg"]
    accent = preset["accent"]
    secondary = preset.get("secondary") or accent
    img = _linear_gradient(
        (width, height),
        top=bg,
        bottom=tuple(max(0, c - 8) for c in bg),
    )
    draw = ImageDraw.Draw(img)
    # Diagonal accent wedge — right two-fifths, angled
    wedge_pts = [
        (int(width * 0.55), 0),
        (width, 0),
        (width, height),
        (int(width * 0.42), height),
    ]
    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    od.polygon(wedge_pts, fill=accent + (160,))
    img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
    # Secondary accent bar near the wedge edge
    draw = ImageDraw.Draw(img)
    bar_x = int(width * 0.40)
    draw.rectangle((bar_x, 0, bar_x + 8, height), fill=secondary)
    return img


def _pattern_poster(width: int, height: int, preset: dict) -> Image.Image:
    """High-contrast magazine-poster layout for listicles. Solid black
    base, red title band on the left, gold accent stripe."""
    bg = preset["bg"]
    accent = preset["accent"]
    secondary = preset.get("secondary") or accent
    img = Image.new("RGB", (width, height), bg)
    draw = ImageDraw.Draw(img)
    # Big red angled band across center-left
    band_pts = [
        (0, int(height * 0.10)),
        (int(width * 0.62), int(height * 0.05)),
        (int(width * 0.58), int(height * 0.72)),
        (0, int(height * 0.78)),
    ]
    draw.polygon(band_pts, fill=accent)
    # Gold accent bar bottom
    draw.rectangle((0, int(height * 0.84), width, int(height * 0.88)), fill=secondary)
    # Subtle texture
    return _add_grain(img, 0.05)


_FALLBACK_PATTERNS = {
    "rim_light": _pattern_rim_light,
    "spotlight": _pattern_spotlight,
    "dossier": _pattern_dossier,
    "diagonal_block": _pattern_diagonal_block,
    "poster": _pattern_poster,
}


def generate_pil_background(title: str, *, aspect: str = "16:9",
                            preset_name: str = "faceless_story") -> bytes:
    """Render a poster-grade fallback for the preset.

    Per-preset `fallback_pattern` selects which composition. None of
    these claim to be photographs — they're explicitly stylized so a
    user knows they're getting the "no AI image generator available"
    fallback, but they look like a real designed thumbnail rather than
    a placeholder."""
    preset = get_preset(preset_name)
    width, height = THUMBNAIL_SIZES.get(aspect, THUMBNAIL_SIZES["16:9"])
    pattern_name = preset.get("fallback_pattern", "rim_light")
    pattern_fn = _FALLBACK_PATTERNS.get(pattern_name, _pattern_rim_light)
    img = pattern_fn(width, height, preset)
    out = BytesIO()
    img.save(out, "PNG", optimize=True)
    return out.getvalue()


# ---------------------------------------------------------------------------
# Title overlay composition
# ---------------------------------------------------------------------------


def _font(size: int, family: str = "bebas") -> ImageFont.FreeTypeFont:
    """Load a bundled font by family key. Falls through to OS fonts and
    finally PIL default if the bundled .ttf is missing (would only happen
    if static/fonts/ wasn't deployed)."""
    path = _FONT_FILES.get(family)
    if path and path.exists():
        try:
            return ImageFont.truetype(str(path), size)
        except OSError:
            pass
    # OS fallback — keep the old behavior so unbundled environments
    # don't crash the generator.
    os_candidates = {
        "anton": ["impact.ttf", "Impact.ttf", "arialbd.ttf"],
        "bebas": ["impact.ttf", "Impact.ttf", "arialbd.ttf"],
        "bangers": ["impact.ttf", "arialbd.ttf"],
        "montserrat_black": ["arialbd.ttf", "Arial Bold.ttf", "DejaVuSans-Bold.ttf"],
        "inter": ["arialbd.ttf", "Arial Bold.ttf", "DejaVuSans-Bold.ttf"],
    }.get(family, ["arialbd.ttf", "DejaVuSans-Bold.ttf"])
    for name in os_candidates:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _draw_distressed_text(draw: ImageDraw.ImageDraw,
                          xy: tuple[int, int],
                          text: str,
                          font: ImageFont.FreeTypeFont,
                          base_color: tuple[int, int, int] = (240, 235, 225),
                          stroke_width: int = 0,
                          shadow: bool = True,
                          target: Image.Image | None = None) -> None:
    """Render text with a distressed concrete/grunge fill — the EREBUS-
    poster look. Implementation: render the text into a transparent
    layer in solid color, generate a noise mask, multiply it against
    the text alpha so random pixels become semi-transparent. Result:
    text that looks weathered, not painted-on.

    Pass `target` to composite back into a parent RGB canvas; without
    it the function operates on the same image the draw is bound to."""
    if target is None:
        return  # need a target for compositing
    x, y = xy
    bbox = draw.textbbox((0, 0), text, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    pad = stroke_width * 2 + 24

    # Solid-color text on a transparent layer
    layer = Image.new("RGBA", (text_w + pad * 2, text_h + pad * 2), (0, 0, 0, 0))
    ldraw = ImageDraw.Draw(layer)
    if shadow:
        ldraw.text(
            (pad + 4, pad + 4), text, font=font,
            fill=(0, 0, 0, 200),
            stroke_width=stroke_width + 1 if stroke_width else 0,
            stroke_fill=(0, 0, 0, 200),
        )
    ldraw.text(
        (pad, pad), text, font=font, fill=base_color + (255,),
        stroke_width=stroke_width,
        stroke_fill=(0, 0, 0, 255) if stroke_width else (0, 0, 0, 0),
    )

    # Noise mask: pixels at random alpha cuts. Multiply against the
    # layer's alpha so we punch random holes in the text.
    layer_w, layer_h = layer.size
    noise_w, noise_h = max(1, layer_w // 3), max(1, layer_h // 3)
    noise_plate = Image.new("L", (noise_w, noise_h))
    np_pixels = noise_plate.load()
    for ny in range(noise_h):
        for nx in range(noise_w):
            v = random.randint(0, 255)
            # Bias toward keeping most pixels (avoids unreadable text)
            np_pixels[nx, ny] = v if v > 70 else 0
    noise_plate = noise_plate.resize((layer_w, layer_h), Image.BILINEAR)
    noise_plate = noise_plate.filter(ImageFilter.GaussianBlur(0.8))
    # Combine the noise with the layer alpha
    r, g, b, a = layer.split()
    a = ImageChops.multiply(a, noise_plate)
    distressed = Image.merge("RGBA", (r, g, b, a))
    target.paste(distressed, (x - pad, y - pad), distressed)


def _draw_text_with_stroke(draw: ImageDraw.ImageDraw,
                           xy: tuple[int, int],
                           text: str,
                           font: ImageFont.FreeTypeFont,
                           fill: tuple[int, int, int],
                           stroke_width: int = 6,
                           stroke_fill: tuple[int, int, int] = (0, 0, 0),
                           shadow: bool = True) -> None:
    """Render text with a thick black outline + drop shadow — the YouTube
    thumbnail typography signature. The outline keeps text readable over
    any background; the shadow adds depth so it doesn't look pasted on.

    Pillow's ImageDraw.text supports a `stroke_width` parameter that
    handles the outline natively, which is dramatically faster than
    manually drawing 8-direction copies."""
    x, y = xy
    if shadow:
        # Soft drop shadow — black, offset down-right, slightly larger
        # stroke so it reads as a glow not a copy.
        shadow_offset = max(2, stroke_width // 2)
        draw.text(
            (x + shadow_offset, y + shadow_offset),
            text,
            font=font,
            fill=(0, 0, 0),
            stroke_width=stroke_width + 1,
            stroke_fill=(0, 0, 0),
        )
    draw.text(
        (x, y),
        text,
        font=font,
        fill=fill,
        stroke_width=stroke_width,
        stroke_fill=stroke_fill,
    )


def _wrap_text(text: str, font: ImageFont.FreeTypeFont, max_width: int,
               draw: ImageDraw.ImageDraw) -> list[str]:
    words = text.strip().split()
    if not words:
        return []
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        candidate = current + " " + word
        bbox = draw.textbbox((0, 0), candidate, font=font)
        if bbox[2] - bbox[0] <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def _fit_text(text: str, max_width: int, max_height: int,
              draw: ImageDraw.ImageDraw,
              max_size: int, min_size: int,
              max_lines: int = 4,
              family: str = "bebas") -> tuple[ImageFont.FreeTypeFont, list[str], int]:
    for size in range(max_size, min_size - 1, -4):
        font = _font(size, family=family)
        lines = _wrap_text(text, font, max_width, draw)
        line_h = int(size * 1.05)
        total_h = line_h * len(lines)
        if total_h <= max_height and len(lines) <= max_lines:
            return font, lines, line_h
    font = _font(min_size, family=family)
    return font, _wrap_text(text, font, max_width, draw), int(min_size * 1.05)


def _shorten_for_overlay(title: str, max_words: int) -> str:
    """Truncate to the preset's max-word budget. No trailing ellipsis —
    "5…" reads as a wrong number, not a truncation. A clean cut at a
    word boundary lets the typography auto-fit handle the rest, and the
    title in the YouTube card / description carries the full payload
    anyway."""
    words = title.strip().split()
    if len(words) <= max_words:
        return title.strip()
    # Trim trailing connectors that produce awkward stops ("set up ollama in").
    truncated = words[:max_words]
    while truncated and truncated[-1].lower() in {
        "a", "an", "the", "of", "in", "on", "at", "to", "for", "and", "or", "but"
    }:
        truncated.pop()
    return " ".join(truncated) if truncated else " ".join(words[:max_words])


def _draw_pill_badge(target: Image.Image, anchor: tuple[int, int],
                     text: str, font: ImageFont.FreeTypeFont,
                     fill_color: tuple[int, int, int],
                     text_color: tuple[int, int, int] = (255, 255, 255),
                     padding: tuple[int, int] = (24, 12),
                     anchor_corner: str = "top-left",
                     icon: str = "") -> tuple[int, int]:
    """Draw a rounded-rect pill badge with text and optional icon glyph.
    Returns the bottom-right corner of the badge so callers can stack
    multiple badges. anchor_corner says which side the (x, y) refers to.

    Used for genre badges ("BASED ON TRUE EVENTS"), brand marks
    ("PHANTOMLINE"), and feature tags ("ORIGINAL STORY")."""
    layer = Image.new("RGBA", target.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)

    # Measure
    label = (icon + " " + text).strip() if icon else text
    bbox = draw.textbbox((0, 0), label, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    pad_x, pad_y = padding
    pill_w = text_w + pad_x * 2
    pill_h = text_h + pad_y * 2

    # Resolve anchor
    ax, ay = anchor
    if anchor_corner == "top-right":
        x0 = ax - pill_w
        y0 = ay
    elif anchor_corner == "bottom-left":
        x0 = ax
        y0 = ay - pill_h
    elif anchor_corner == "bottom-right":
        x0 = ax - pill_w
        y0 = ay - pill_h
    else:  # top-left
        x0 = ax
        y0 = ay
    x1 = x0 + pill_w
    y1 = y0 + pill_h

    # Pill body
    radius = pill_h // 2
    draw.rounded_rectangle((x0, y0, x1, y1), radius=radius, fill=fill_color + (240,))
    # Text
    text_x = x0 + pad_x - bbox[0]
    text_y = y0 + pad_y - bbox[1]
    draw.text((text_x, text_y), label, font=font, fill=text_color + (255,))

    target.alpha_composite(layer) if target.mode == "RGBA" else (
        target.paste(Image.alpha_composite(target.convert("RGBA"), layer).convert("RGB"))
    )
    # When target is RGB we need to bake-back manually
    if target.mode != "RGBA":
        baked = Image.alpha_composite(target.convert("RGBA"), layer).convert("RGB")
        target.paste(baked)
    return x1, y1


def _fetch_subject_image(url: str, timeout: float = 15.0) -> bytes | None:
    """Download a user-supplied background image. Returns PNG bytes or
    None on failure. Used as a manual alternative to AI generation —
    user pastes a Pexels/Unsplash URL and we composite text over it."""
    if not url or not url.strip():
        return None
    if not url.lower().startswith(("https://", "http://")):
        return None
    try:
        res = requests.get(url, timeout=timeout, stream=True)
        if res.status_code != 200:
            return None
        ctype = (res.headers.get("Content-Type") or "").lower()
        if not ctype.startswith("image/"):
            return None
        # Cap download size to prevent abuse — 10 MB max.
        chunks = []
        total = 0
        for chunk in res.iter_content(chunk_size=64 * 1024):
            chunks.append(chunk)
            total += len(chunk)
            if total > 10 * 1024 * 1024:
                return None
        raw = b"".join(chunks)
        with Image.open(BytesIO(raw)) as img:
            img = img.convert("RGB")
            out = BytesIO()
            img.save(out, "PNG", optimize=True)
            return out.getvalue()
    except Exception:
        return None


def compose_thumbnail(title: str, background_png: bytes, *,
                      aspect: str = "16:9",
                      preset_name: str = "faceless_story",
                      subtitle: str = "",
                      tagline: str = "",
                      category_badge: str = "",
                      brand_badge: str = "",
                      feature_tags: list[str] | None = None,
                      text_overlay: bool | None = None,
                      bg_is_ai_image: bool = False) -> bytes:
    """Lay text on a background per the preset's rules.

    Composition picker:
    - When `bg_is_ai_image=True` AND preset suppresses text → minimal
      mode (AI image is the hero, just a small eyebrow + accent).
    - Otherwise → poster mode (text IS the hero, giant stroked
      typography that survives small renders).

    The "always go poster when there's no AI image" rule is a deliberate
    upgrade from the previous behavior: a textless thumbnail with no
    real subject behind it is just a dark rectangle. The 2026 research
    -19% text penalty applies when there's an actual photograph to
    compete with — without one, text has to do the work.
    """
    preset = get_preset(preset_name)
    width, height = THUMBNAIL_SIZES.get(aspect, THUMBNAIL_SIZES["16:9"])

    bg = Image.open(BytesIO(background_png)).convert("RGB").resize(
        (width, height), Image.LANCZOS
    )

    use_text = preset["text_default"] if text_overlay is None else bool(text_overlay)
    feature_tags = list(feature_tags or [])

    # Decision: if we have a real AI image AND the preset is text-light
    # by default, use minimal-text mode. Otherwise poster mode.
    if bg_is_ai_image and not use_text and not subtitle.strip() and not tagline.strip() and not category_badge.strip() and not brand_badge.strip() and not feature_tags:
        out = BytesIO()
        bg.save(out, "PNG", optimize=True)
        return out.getvalue()

    if bg_is_ai_image and not use_text:
        return _compose_minimal_text(
            bg, title, preset,
            subtitle=subtitle, tagline=tagline,
            category_badge=category_badge, brand_badge=brand_badge,
            feature_tags=feature_tags,
        )

    return _compose_poster(
        bg, title, preset,
        subtitle=subtitle, tagline=tagline,
        category_badge=category_badge, brand_badge=brand_badge,
        feature_tags=feature_tags,
    )


def _compose_poster(bg: Image.Image, title: str, preset: dict, *,
                    subtitle: str,
                    tagline: str = "",
                    category_badge: str = "",
                    brand_badge: str = "",
                    feature_tags: list[str] | None = None) -> bytes:
    """Poster-grade composition. Text is the hero: large stroked
    typography, accent eyebrow, optional numeral cluster pulled from the
    title. Used as the default when there's no AI image background, and
    for listicle preset always."""
    width, height = bg.size
    draw = ImageDraw.Draw(bg)

    title_font_family = preset.get("title_font", "bebas")
    sub_font_family = preset.get("subtitle_font", "inter")

    # Pull a leading numeral so listicles read as "20" + "MYSTERIOUS EVENTS"
    # not "20 MYSTERIOUS EVENTS" packed into one block. Numeric hooks are
    # the listicle signature; presets with no numeral fall through to a
    # single big headline.
    numeral_match = re.match(r"^\s*(\d{1,3})\b", title.strip())
    numeral = ""
    rest = title.strip()
    if numeral_match:
        numeral = numeral_match.group(1)
        rest = title[numeral_match.end():].strip()

    margin_x = int(width * 0.05)
    margin_y = int(height * 0.06)
    text_box_w = int(width * 0.88)

    cur_y = margin_y

    # Eyebrow subtitle in accent color.
    if subtitle.strip():
        sub_text = subtitle.strip().upper()
        sub_size = int(height * 0.045)
        sub_font = _font(sub_size, family=sub_font_family)
        _draw_text_with_stroke(
            draw, (margin_x, cur_y), sub_text,
            sub_font, fill=preset["accent"],
            stroke_width=4, shadow=False,
        )
        sub_bbox = draw.textbbox((0, 0), sub_text, font=sub_font)
        cur_y += (sub_bbox[3] - sub_bbox[1]) + int(height * 0.025)

    # Giant numeral if listicle-style.
    if numeral:
        num_font, _, _ = _fit_text(
            numeral, text_box_w, int(height * 0.45),
            draw, max_size=420, min_size=200, max_lines=1,
            family=preset.get("title_font", "bangers"),
        )
        _draw_text_with_stroke(
            draw, (margin_x, cur_y), numeral,
            num_font, fill=preset["accent"],
            stroke_width=10, shadow=True,
        )
        cur_y += int(num_font.size * 1.05)

    # Headline title. In poster mode (no AI image) the full title is the
    # hero — we don't truncate. The auto-fit shrinks the font until the
    # full string fits in 3 lines, going down to 60px if needed.
    headline_text = rest.upper()
    headline_bottom = cur_y
    if headline_text:
        # Reserve space for tagline + bottom badges so the title doesn't
        # collide with them.
        reserved_bottom = int(height * 0.20) if (tagline.strip() or brand_badge.strip() or feature_tags) else int(height * 0.10)
        remaining_h = max(int(height * 0.22), height - cur_y - reserved_bottom)
        head_font, head_lines, head_lh = _fit_text(
            headline_text, text_box_w, remaining_h,
            draw, max_size=180, min_size=60, max_lines=3,
            family=title_font_family,
        )
        for i, line in enumerate(head_lines):
            _draw_text_with_stroke(
                draw, (margin_x, cur_y + i * head_lh), line,
                head_font, fill=TEXT_PRIMARY,
                stroke_width=8, shadow=True,
            )
        headline_bottom = cur_y + len(head_lines) * head_lh

    # Tagline below the headline — accent-colored, smaller, multi-line OK.
    if tagline.strip():
        tag_size = int(height * 0.045)
        tag_font = _font(tag_size, family=sub_font_family)
        tag_lines = _wrap_text(tagline.strip(), tag_font, text_box_w, draw)[:2]
        tag_y = headline_bottom + int(height * 0.02)
        for line in tag_lines:
            _draw_text_with_stroke(
                draw, (margin_x, tag_y), line,
                tag_font, fill=preset["accent"],
                stroke_width=3, shadow=True,
            )
            tag_y += int(tag_size * 1.2)

    _apply_corner_badges(bg, preset,
                         category_badge=category_badge,
                         brand_badge=brand_badge,
                         feature_tags=feature_tags or [])

    out = BytesIO()
    bg.save(out, "PNG", optimize=True)
    return out.getvalue()


def _apply_corner_badges(bg: Image.Image, preset: dict, *,
                         category_badge: str = "",
                         brand_badge: str = "",
                         feature_tags: list[str]) -> None:
    """Render the 3 standard corner badge slots on top of `bg`:
      - category_badge: top-left, accent-colored pill ("BEDTIME STORY")
      - brand_badge: bottom-left, dark pill with accent text ("PHANTOMLINE")
      - feature_tags: bottom-right, list of small dark pills with accent
        text (each "ORIGINAL STORY", "SOOTHING NARRATION")

    Mutates `bg` in place via _draw_pill_badge — that helper handles the
    RGB/RGBA conversion internally."""
    width, height = bg.size
    badge_font = _font(int(height * 0.034), family="montserrat_black")
    margin = int(width * 0.025)

    if category_badge.strip():
        _draw_pill_badge(
            bg, anchor=(margin, margin),
            text=category_badge.strip().upper(), font=badge_font,
            fill_color=preset["accent"], text_color=(0, 0, 0),
            anchor_corner="top-left",
        )

    if brand_badge.strip():
        small_font = _font(int(height * 0.030), family="montserrat_black")
        _draw_pill_badge(
            bg, anchor=(margin, height - margin),
            text=brand_badge.strip().upper(), font=small_font,
            fill_color=(0, 0, 0), text_color=preset["accent"],
            anchor_corner="bottom-left",
        )

    if feature_tags:
        # Stack right-to-left so the "first" tag ends up rightmost.
        tag_font = _font(int(height * 0.026), family="montserrat_black")
        x_anchor = width - margin
        for tag in feature_tags[:3]:  # cap at 3 to avoid clutter
            x1, _ = _draw_pill_badge(
                bg, anchor=(x_anchor, height - margin),
                text=tag.strip().upper(), font=tag_font,
                fill_color=(0, 0, 0), text_color=preset.get("secondary") or preset["accent"],
                anchor_corner="bottom-right",
                padding=(18, 10),
            )
            # Move next anchor to the left of this badge with a gap.
            # _draw_pill_badge returns (x1, y1) which for bottom-right
            # is the right-most pixel; we need the left edge for stacking.
            # Recompute using the same width formula approximately:
            bbox = ImageDraw.Draw(bg).textbbox((0, 0), tag.upper(), font=tag_font)
            badge_w = (bbox[2] - bbox[0]) + 18 * 2
            x_anchor -= badge_w + int(width * 0.012)


def _compose_minimal_text(bg: Image.Image, title: str, preset: dict, *,
                          subtitle: str,
                          tagline: str = "",
                          category_badge: str = "",
                          brand_badge: str = "",
                          feature_tags: list[str] | None = None) -> bytes:
    """Minimal eyebrow-only composition used when an AI image is doing
    the heavy lifting. Just a small accent eyebrow + a barely-there
    title slug, positioned so it doesn't fight the image subject."""
    width, height = bg.size
    draw = ImageDraw.Draw(bg)

    sub_font_family = preset.get("subtitle_font", "inter")
    title_font_family = preset.get("title_font", "bebas")

    pos = preset["text_position"]

    # Light vignette on the side where text will live.
    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    band_alpha = 100
    if "lower" in pos:
        od.rectangle((0, int(height * 0.62), width, height), fill=(0, 0, 0, band_alpha))
    elif "left" in pos:
        od.rectangle((0, 0, int(width * 0.55), height), fill=(0, 0, 0, band_alpha))
    elif "top" in pos:
        od.rectangle((0, 0, width, int(height * 0.40)), fill=(0, 0, 0, band_alpha))
    bg = Image.alpha_composite(bg.convert("RGBA"), overlay).convert("RGB")
    draw = ImageDraw.Draw(bg)

    margin_x = int(width * 0.05)
    if "lower" in pos:
        anchor_y = int(height * 0.66)
    elif "top" in pos:
        anchor_y = int(height * 0.06)
    else:
        anchor_y = int(height * 0.42)

    if subtitle.strip():
        sub_text = subtitle.strip().upper()
        sub_size = int(height * 0.038)
        sub_font = _font(sub_size, family=sub_font_family)
        _draw_text_with_stroke(
            draw, (margin_x, anchor_y), sub_text,
            sub_font, fill=preset["accent"],
            stroke_width=3, shadow=False,
        )
        sub_bbox = draw.textbbox((0, 0), sub_text, font=sub_font)
        anchor_y += (sub_bbox[3] - sub_bbox[1]) + int(height * 0.012)

    slug = _shorten_for_overlay(title, preset["text_max_words"]).upper()
    title_box_w = int(width * 0.55)
    title_box_h = int(height * 0.20)
    title_font, lines, line_h = _fit_text(
        slug, title_box_w, title_box_h,
        draw, max_size=110, min_size=48, max_lines=2,
        family=title_font_family,
    )
    last_line_y = anchor_y
    for i, line in enumerate(lines):
        _draw_text_with_stroke(
            draw, (margin_x, anchor_y + i * line_h), line,
            title_font, fill=TEXT_PRIMARY,
            stroke_width=6, shadow=True,
        )
        last_line_y = anchor_y + (i + 1) * line_h

    # Tagline below title in accent-secondary color, smaller.
    if tagline.strip():
        tag_size = int(height * 0.038)
        tag_font = _font(tag_size, family=sub_font_family)
        tag_lines = _wrap_text(tagline.strip(), tag_font, title_box_w, draw)[:2]
        tag_y = last_line_y + int(height * 0.012)
        for line in tag_lines:
            _draw_text_with_stroke(
                draw, (margin_x, tag_y), line,
                tag_font, fill=preset["accent"],
                stroke_width=2, shadow=True,
            )
            tag_y += int(tag_size * 1.2)

    _apply_corner_badges(bg, preset,
                         category_badge=category_badge,
                         brand_badge=brand_badge,
                         feature_tags=feature_tags or [])

    out = BytesIO()
    bg.save(out, "PNG", optimize=True)
    return out.getvalue()


# ---------------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------------


def _resolve_preset_name(title: str, style: str, genre: str, recipe: str) -> str:
    if style == "auto" or not style:
        return detect_preset(title, genre=genre, recipe=recipe)
    if style in THUMBNAIL_PRESETS:
        return style
    return "faceless_story"


def generate_thumbnail(title: str, *, aspect: str = "16:9",
                       style: str = "auto",
                       genre: str = "",
                       recipe: str = "",
                       subject_hint: str = "",
                       subtitle: str = "",
                       tagline: str = "",
                       category_badge: str = "",
                       brand_badge: str = "",
                       feature_tags: list[str] | None = None,
                       subject_image_url: str = "",
                       text_overlay: bool | None = None,
                       prefer_forge: bool = True,
                       prefer_pollinations: bool = True,
                       prefer_falai: bool = True,
                       seed: int | None = None) -> dict:
    """Generate a single thumbnail. Tries backends in priority order:

      1. `subject_image_url` if supplied (user paste-your-own-photo path)
      2. Forge SDXL (local, free, runs on the user's machine if installed)
      3. Pollinations.ai (FREE FLUX endpoint, no API key, the default)
      4. fal.ai FLUX schnell (paid, ~$0.005/call, more reliable)
      5. PIL poster fallback (always works, typography-only)

    The first backend to succeed wins. The fallback chain is silent on
    intermediate failures — `fallback_reason` is only populated if all
    AI backends fail."""
    preset_name = _resolve_preset_name(title, style, genre, recipe)
    width, height = THUMBNAIL_SIZES.get(aspect, THUMBNAIL_SIZES["16:9"])

    bg_bytes: bytes | None = None
    used_mode = "pil_fallback"
    fallback_reason = ""
    bg_is_ai_image = False

    # User-supplied URL wins. This is the manual escape hatch for users
    # who don't want to plug in Forge/fal.ai but still want a real photo
    # behind the text — they paste a Pexels/Unsplash URL.
    if subject_image_url and subject_image_url.strip():
        fetched = _fetch_subject_image(subject_image_url.strip())
        if fetched is not None:
            bg_bytes = fetched
            used_mode = "user_image"
            bg_is_ai_image = True  # treat as a real photo, suppresses poster mode
        else:
            fallback_reason = "Provided subject_image_url could not be fetched."

    if bg_bytes is None and prefer_forge and forge_available():
        try:
            bg_bytes = generate_forge_background(
                title, aspect=aspect, preset_name=preset_name,
                subject_hint=subject_hint,
            )
            used_mode = "forge"
            bg_is_ai_image = True
            fallback_reason = ""
        except Exception as exc:
            fallback_reason = (fallback_reason + " | " if fallback_reason else "") + f"Forge: {exc}"

    if bg_bytes is None and prefer_pollinations and pollinations_available():
        try:
            bg_bytes = generate_pollinations_background(
                title, aspect=aspect, preset_name=preset_name,
                subject_hint=subject_hint, seed=seed,
            )
            used_mode = "pollinations"
            bg_is_ai_image = True
            fallback_reason = ""
        except Exception as exc:
            fallback_reason = (fallback_reason + " | " if fallback_reason else "") + f"Pollinations: {exc}"

    if bg_bytes is None and prefer_falai and falai_available():
        try:
            bg_bytes = generate_falai_background(
                title, aspect=aspect, preset_name=preset_name,
                subject_hint=subject_hint, seed=seed,
            )
            used_mode = "falai"
            bg_is_ai_image = True
            fallback_reason = ""
        except Exception as exc:
            fallback_reason = (fallback_reason + " | " if fallback_reason else "") + f"fal.ai: {exc}"

    if bg_bytes is None:
        bg_bytes = generate_pil_background(title, aspect=aspect, preset_name=preset_name)
        if not fallback_reason:
            fallback_reason = "No AI image backend configured (Forge or FAL_KEY)."

    composed = compose_thumbnail(
        title, bg_bytes, aspect=aspect,
        preset_name=preset_name,
        subtitle=subtitle,
        tagline=tagline,
        category_badge=category_badge,
        brand_badge=brand_badge,
        feature_tags=feature_tags,
        text_overlay=text_overlay,
        bg_is_ai_image=bg_is_ai_image,
    )
    return {
        "png_bytes": composed,
        "mode": used_mode,
        "preset": preset_name,
        "width": width,
        "height": height,
        "fallback_reason": fallback_reason if not bg_is_ai_image else "",
    }


def generate_thumbnail_batch(title: str, *, count: int = 4, **kwargs) -> list[dict]:
    """Generate `count` thumbnail variants with different seeds so the
    user has real picks to pull from. Each call lands as a separate API
    request to the AI backend; the PIL fallback gets visual variety from
    randomized grain patterns and seed-driven color shifts in the
    pattern functions.

    Returns a list of dicts with the same shape as `generate_thumbnail`
    plus a `seed` field per variant."""
    count = max(1, min(8, count))
    results = []
    for i in range(count):
        # Use a deterministic-ish seed range so users can re-roll a
        # specific variant by passing seed=N explicitly. Random.randint
        # avoids accidental seed collisions across rapid-fire calls.
        seed = kwargs.pop("seed", None) if i == 0 and "seed" in kwargs else random.randint(1, 2**31 - 1)
        result = generate_thumbnail(title, seed=seed, **kwargs)
        result["seed"] = seed
        results.append(result)
    return results


def save_thumbnail(png_bytes: bytes, out_path: Path) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(png_bytes)
    return out_path

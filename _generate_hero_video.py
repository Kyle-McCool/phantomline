"""Build a hero motion-graphic for phantomline.xyz from real product
artifacts: a mix of generated thumbnails and PIL-rendered feature cards
arranged in a Ken Burns sequence with crossfades and an end card.

Output: output/hero_motion/phantomline_hero.mp4 (1920x1080 16:9 MP4 H.264)

Run:    python _generate_hero_video.py

This is a one-shot local-only script — not wired into the Flask server.
Validate the MP4, then decide if/where to embed it.
"""
from __future__ import annotations

import os
import sys
from io import BytesIO
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

# Make the project importable so we can re-use the thumbnail generator.
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import imageio_ffmpeg  # for the bundled ffmpeg binary path
import imageio.v2 as imageio
import thumbnail_generator as tg


WIDTH, HEIGHT = 1920, 1080
FPS = 30
SHOT_HOLD_S = 1.4   # how long each card sits on screen
CROSSFADE_S = 0.35  # overlap between shots
END_CARD_S = 1.8    # final logo card hold

OUT_DIR = ROOT / "output" / "hero_motion"
OUT_DIR.mkdir(parents=True, exist_ok=True)
SHOTS_DIR = OUT_DIR / "shots"
SHOTS_DIR.mkdir(exist_ok=True)

FONTS = ROOT / "static" / "fonts"
BEBAS = str(FONTS / "BebasNeue-Regular.ttf")
INTER = str(FONTS / "Inter-Bold.ttf")
ANTON = str(FONTS / "Anton-Regular.ttf")
MONT = str(FONTS / "Montserrat-Black.ttf")

LOGO_MARK_PATH = ROOT / "static" / "phantomline-logo-square.png"   # mark only
LOGO_FULL_PATH = ROOT / "static" / "phantomline-logo-primary.png"  # mark + wordmark
LOGO_MARK = Image.open(LOGO_MARK_PATH).convert("RGBA")
LOGO_FULL = Image.open(LOGO_FULL_PATH).convert("RGBA")

# Phantomline brand palette (matches landing.css):
BG = (8, 10, 14)
BG_PANEL = (16, 20, 28)
ACCENT = (49, 215, 255)        # cyan
ACCENT_DIM = (24, 116, 138)
TEXT = (240, 244, 250)
TEXT_DIM = (148, 162, 178)
WARN = (240, 84, 92)


# ---------------------------------------------------------------------------
# 1. THUMBNAIL SHOTS — try Pollinations, fall back to PIL poster mode.
# ---------------------------------------------------------------------------

THUMB_BRIEFS = [
    # (title, preset, subtitle, tagline)
    ("THE WATCHERS IN THE STATIC", "horror_cosmic", "EP 03",
     "what the radio kept hearing"),
    ("THE TREATY'S DARK FREQUENCY", "mystery_documentary", "DECLASSIFIED",
     "case file 1971-Δ"),
    ("HE LIVED IN THE WALLS FOR 9 YEARS", "reddit_story", "FROM r/NOSLEEP",
     "she finally heard him breathe"),
    ("20 EVENTS SCIENCE STILL CAN'T EXPLAIN", "listicle", "CHANNEL DEEP DIVE",
     "ranked by dread"),
]


def _generate_thumbnail_safe(brief) -> Image.Image:
    import hashlib
    title, preset, subtitle, tagline = brief
    # Cache so iterating on cards/end-card doesn't re-fetch Pollinations.
    # Use hashlib (not hash()) so the cache key is stable across processes —
    # Python's hash() is randomized per-interpreter via PYTHONHASHSEED.
    digest = hashlib.md5(title.encode()).hexdigest()[:8]
    cache_key = f"thumb_{preset}_{digest}.png"
    cache_path = OUT_DIR / "_cache" / cache_key
    cache_path.parent.mkdir(exist_ok=True)
    if cache_path.exists():
        print(f"     -> cached")
        return Image.open(cache_path).convert("RGB")
    try:
        out = tg.generate_thumbnail(
            title,
            aspect="16:9",
            style=preset,
            subtitle=subtitle,
            tagline=tagline,
            brand_badge="PHANTOMLINE",
            prefer_forge=False,
            prefer_pollinations=True,
            prefer_falai=False,
        )
        mode = out.get("mode")
        print(f"     -> {mode}")
        png = out["png_bytes"]
        img = Image.open(BytesIO(png)).convert("RGB")
        img.save(cache_path)
        return img
    except Exception as exc:
        print(f"  ! thumbnail failed for {title!r}: {exc}; using blank")
        return Image.new("RGB", (WIDTH, HEIGHT), BG)


# ---------------------------------------------------------------------------
# 2. FEATURE-CARD SHOTS — PIL-rendered product mockups.
# ---------------------------------------------------------------------------

def _font(size: int, family: str = "bebas") -> ImageFont.FreeTypeFont:
    path = {"bebas": BEBAS, "inter": INTER, "anton": ANTON, "mont": MONT}[family]
    return ImageFont.truetype(path, size)


def _gradient_bg() -> Image.Image:
    """Subtle vertical gradient + cyan rim glow on the right edge."""
    img = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(img)
    for y in range(HEIGHT):
        t = y / HEIGHT
        r = int(BG[0] + (BG_PANEL[0] - BG[0]) * t * 0.6)
        g = int(BG[1] + (BG_PANEL[1] - BG[1]) * t * 0.6)
        b = int(BG[2] + (BG_PANEL[2] - BG[2]) * t * 0.6)
        draw.line([(0, y), (WIDTH, y)], fill=(r, g, b))
    # Cyan glow on the right
    glow = Image.new("RGB", (WIDTH, HEIGHT), (0, 0, 0))
    gd = ImageDraw.Draw(glow)
    for x in range(WIDTH):
        t = max(0.0, (x - WIDTH * 0.55) / (WIDTH * 0.45))
        v = int(t * t * 60)
        gd.line([(x, 0), (x, HEIGHT)], fill=(0, int(v * 0.4), int(v * 0.7)))
    glow = glow.filter(ImageFilter.GaussianBlur(radius=80))
    return Image.blend(img, glow, 0.5)


def _badge(draw: ImageDraw.ImageDraw, x: int, y: int, text: str,
           fill=(20, 24, 32), border=ACCENT, text_color=ACCENT,
           font_size: int = 22) -> tuple[int, int]:
    font = _font(font_size, "inter")
    pad_x, pad_y = 18, 10
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    w, h = tw + pad_x * 2, th + pad_y * 2
    draw.rounded_rectangle([x, y, x + w, y + h], radius=h // 2,
                           fill=fill, outline=border, width=2)
    draw.text((x + pad_x, y + pad_y - bbox[1]), text, font=font, fill=text_color)
    return w, h


def _feature_card(eyebrow: str, headline: str, body: str,
                  badges: list[str] = None,
                  metric: tuple[str, str] | None = None) -> Image.Image:
    """A product-feature card. Eyebrow, big headline, supporting body,
    optional metric callout and pill badges."""
    img = _gradient_bg()
    draw = ImageDraw.Draw(img)

    # Top-left brand: actual logo (mark + wordmark) instead of text
    logo_h = 70
    aspect_ratio = LOGO_FULL.width / LOGO_FULL.height
    logo_w = int(logo_h * aspect_ratio)
    logo = LOGO_FULL.resize((logo_w, logo_h), Image.LANCZOS)
    img.paste(logo, (80, 60), logo)
    draw.rectangle([80, 60 + logo_h + 14, 80 + 40, 60 + logo_h + 18], fill=ACCENT)

    # Eyebrow
    eb_font = _font(26, "inter")
    draw.text((80, 240), eyebrow.upper(), font=eb_font, fill=ACCENT)

    # Headline (multiline)
    hl_font = _font(110, "bebas")
    y = 290
    for line in headline.split("\n"):
        draw.text((80, y), line, font=hl_font, fill=TEXT)
        y += int(110 * 1.05)

    # Body copy
    body_font = _font(30, "inter")
    y += 30
    for line in body.split("\n"):
        draw.text((80, y), line, font=body_font, fill=TEXT_DIM)
        y += 44

    # Metric callout on the right
    if metric:
        big, small = metric
        big_font = _font(220, "bebas")
        small_font = _font(28, "inter")
        bbox = draw.textbbox((0, 0), big, font=big_font)
        bw = bbox[2] - bbox[0]
        bx = WIDTH - 180 - bw
        by = 320
        # Draw a subtle accent block behind it
        draw.rectangle([bx - 30, by - 20, bx - 10, by + 240], fill=ACCENT)
        draw.text((bx, by), big, font=big_font, fill=TEXT)
        draw.text((bx, by + 250), small.upper(), font=small_font, fill=ACCENT)

    # Bottom-row badges
    if badges:
        bx = 80
        by = HEIGHT - 130
        for b in badges:
            w, h = _badge(draw, bx, by, b)
            bx += w + 16

    # Bottom-right URL
    url_font = _font(24, "inter")
    url = "phantomline.xyz"
    bbox = draw.textbbox((0, 0), url, font=url_font)
    draw.text((WIDTH - 80 - (bbox[2] - bbox[0]), HEIGHT - 80),
              url, font=url_font, fill=TEXT_DIM)

    return img


def _end_card() -> Image.Image:
    """Final logo card — wordmark center, tagline below."""
    img = _gradient_bg()
    draw = ImageDraw.Draw(img)

    cx = WIDTH // 2

    # Use the actual brand asset (mark + wordmark) instead of redrawing in
    # Bebas. Render it large but leave headroom for tagline + url below.
    logo_h = 280
    aspect_ratio = LOGO_FULL.width / LOGO_FULL.height
    logo_w = int(logo_h * aspect_ratio)
    logo = LOGO_FULL.resize((logo_w, logo_h), Image.LANCZOS)
    logo_top = HEIGHT // 2 - 220
    img.paste(logo, (cx - logo_w // 2, logo_top), logo)

    # Cyan underline below the logo
    uy = logo_top + logo_h + 20
    draw.rectangle([cx - 320, uy, cx + 320, uy + 6], fill=ACCENT)

    tag_font = _font(38, "inter")
    tag = "Local-first AI video studio. No subscriptions. No cloud rendering."
    draw.text((cx, uy + 70), tag, font=tag_font, fill=TEXT_DIM, anchor="ms")

    url_font = _font(32, "inter")
    url = "phantomline.xyz"
    draw.text((cx, uy + 140), url, font=url_font, fill=ACCENT, anchor="ms")

    return img


# ---------------------------------------------------------------------------
# 3. KEN BURNS + CROSSFADE COMPOSITOR
# ---------------------------------------------------------------------------

def _ken_burns_frame(src: Image.Image, t: float, *,
                     start_zoom: float = 1.0,
                     end_zoom: float = 1.08,
                     pan: tuple[float, float] = (0.0, 0.0)) -> np.ndarray:
    """Return a 1920x1080 numpy frame of `src` zoomed and panned."""
    zoom = start_zoom + (end_zoom - start_zoom) * t
    src = src.resize((WIDTH, HEIGHT), Image.LANCZOS)
    crop_w = int(WIDTH / zoom)
    crop_h = int(HEIGHT / zoom)
    cx = WIDTH // 2 + int(pan[0] * (WIDTH - crop_w) * 0.5)
    cy = HEIGHT // 2 + int(pan[1] * (HEIGHT - crop_h) * 0.5)
    left = max(0, cx - crop_w // 2)
    top = max(0, cy - crop_h // 2)
    right = min(WIDTH, left + crop_w)
    bottom = min(HEIGHT, top + crop_h)
    crop = src.crop((left, top, right, bottom))
    crop = crop.resize((WIDTH, HEIGHT), Image.LANCZOS)
    return np.asarray(crop)


def _crossfade(a: np.ndarray, b: np.ndarray, t: float) -> np.ndarray:
    return (a.astype(np.float32) * (1 - t) + b.astype(np.float32) * t).astype(np.uint8)


def _apply_brand_overlay(frame: np.ndarray) -> np.ndarray:
    """Persistent corner watermark (logo mark + wordmark) + edge accent."""
    img = Image.fromarray(frame).convert("RGBA")
    draw = ImageDraw.Draw(img, "RGBA")

    # Bottom-right: small ghost mark + PHANTOMLINE wordmark on a translucent
    # plate so it stays legible over both photo thumbnails and dark cards.
    mark_h = 44
    mark_aspect = LOGO_MARK.width / LOGO_MARK.height
    mark_w = int(mark_h * mark_aspect)
    mark = LOGO_MARK.resize((mark_w, mark_h), Image.LANCZOS)

    font = _font(26, "bebas")
    text = "PHANTOMLINE"
    text_bbox = draw.textbbox((0, 0), text, font=font)
    tw = text_bbox[2] - text_bbox[0]
    th = text_bbox[3] - text_bbox[1]

    gap = 10
    plate_pad_x, plate_pad_y = 16, 10
    plate_w = mark_w + gap + tw + plate_pad_x * 2
    plate_h = max(mark_h, th + 8) + plate_pad_y * 2
    plate_x = WIDTH - plate_w - 50
    plate_y = HEIGHT - plate_h - 40
    draw.rounded_rectangle(
        [plate_x, plate_y, plate_x + plate_w, plate_y + plate_h],
        radius=plate_h // 2,
        fill=(0, 0, 0, 150),
    )
    img.paste(mark, (plate_x + plate_pad_x, plate_y + (plate_h - mark_h) // 2), mark)
    text_x = plate_x + plate_pad_x + mark_w + gap
    text_y = plate_y + (plate_h - th) // 2 - text_bbox[1]
    draw.text((text_x, text_y), text, font=font, fill=(*TEXT, 230))

    # Left edge cyan accent bar
    draw.rectangle([0, 0, 6, HEIGHT], fill=(*ACCENT, 200))
    return np.asarray(img.convert("RGB"))


def _build_shots() -> list[Image.Image]:
    print("Generating shots...")
    shots: list[Image.Image] = []

    # Shot 1: thumbnail — horror
    print("  shot 1: horror thumbnail")
    shots.append(_generate_thumbnail_safe(THUMB_BRIEFS[0]))

    # Shot 2: feature card — local & private
    print("  shot 2: feature card (local-first)")
    shots.append(_feature_card(
        eyebrow="Built for faceless creators",
        headline="100% LOCAL.\nZERO CLOUD.",
        body="Story → voice → music → thumbnail.\nAll on your machine. No API bills.",
        badges=["Ollama", "Kokoro TTS", "MusicGen", "FLUX"],
        metric=("$0", "monthly"),
    ))

    # Shot 3: thumbnail — mystery doc
    print("  shot 3: mystery thumbnail")
    shots.append(_generate_thumbnail_safe(THUMB_BRIEFS[1]))

    # Shot 4: feature card — niche thumbnails
    print("  shot 4: feature card (5-niche thumbnails)")
    shots.append(_feature_card(
        eyebrow="Built-in thumbnail engine",
        headline="5 NICHE PRESETS.\nOFFLINE OR FLUX.",
        body="Reddit story, horror, mystery doc, tutorial, listicle.\nAuto-detected from your title.",
        badges=["FLUX-realism", "Pollinations", "PIL fallback"],
        metric=("5", "presets"),
    ))

    # Shot 5: thumbnail — reddit story
    print("  shot 5: reddit thumbnail")
    shots.append(_generate_thumbnail_safe(THUMB_BRIEFS[2]))

    # Shot 6: feature card — license / pricing
    print("  shot 6: feature card (license / one-time)")
    shots.append(_feature_card(
        eyebrow="One-time payment, lifetime use",
        headline="$79 LIFETIME.\nFOUNDING SEAT.",
        body="Stripe checkout → instant license.\nWorks offline. Yours forever.",
        badges=["500 seats only", "HMAC-signed", "Email delivery"],
        metric=("∞", "renders"),
    ))

    # Shot 7: thumbnail — listicle
    print("  shot 7: listicle thumbnail")
    shots.append(_generate_thumbnail_safe(THUMB_BRIEFS[3]))

    # Shot 8: end card
    print("  shot 8: end card")
    shots.append(_end_card())

    # Save shots for inspection
    for i, s in enumerate(shots, 1):
        s.resize((WIDTH, HEIGHT), Image.LANCZOS).save(SHOTS_DIR / f"shot_{i:02d}.png")
    return shots


def _render_video(shots: list[Image.Image]) -> Path:
    print("Rendering video...")
    # Use imageio_ffmpeg's bundled binary
    os.environ["IMAGEIO_FFMPEG_EXE"] = imageio_ffmpeg.get_ffmpeg_exe()

    out_path = OUT_DIR / "phantomline_hero.mp4"

    # Pre-compute Ken Burns parameters per shot — alternate pan direction
    pans = [
        (0.0, -0.4), (0.4, 0.0), (-0.4, 0.0), (0.0, 0.4),
        (0.4, -0.2), (-0.4, 0.2), (0.0, 0.0), (0.0, 0.0),
    ]
    # Last shot (end card) gets a calmer zoom
    zooms = [(1.0, 1.06)] * (len(shots) - 1) + [(1.02, 1.05)]

    hold_frames = int(SHOT_HOLD_S * FPS)
    fade_frames = int(CROSSFADE_S * FPS)
    end_extra = int((END_CARD_S - SHOT_HOLD_S) * FPS)

    writer = imageio.get_writer(
        str(out_path), fps=FPS, codec="libx264",
        quality=8, macro_block_size=1,
        ffmpeg_params=["-pix_fmt", "yuv420p", "-preset", "medium"],
    )

    try:
        for i, shot in enumerate(shots):
            sz = zooms[i]
            pan = pans[i] if i < len(pans) else (0.0, 0.0)
            n_frames = hold_frames + (end_extra if i == len(shots) - 1 else 0)

            for f in range(n_frames):
                t = f / max(1, n_frames - 1)
                frame = _ken_burns_frame(shot, t,
                                         start_zoom=sz[0], end_zoom=sz[1],
                                         pan=pan)
                frame = _apply_brand_overlay(frame)
                writer.append_data(frame)

            # Crossfade into next shot
            if i < len(shots) - 1:
                next_shot = shots[i + 1]
                next_pan = pans[i + 1] if (i + 1) < len(pans) else (0.0, 0.0)
                next_sz = zooms[i + 1]
                last_a = _ken_burns_frame(shot, 1.0,
                                          start_zoom=sz[0], end_zoom=sz[1],
                                          pan=pan)
                last_a = _apply_brand_overlay(last_a)
                for f in range(fade_frames):
                    t = (f + 1) / fade_frames
                    b = _ken_burns_frame(next_shot, 0.0,
                                         start_zoom=next_sz[0],
                                         end_zoom=next_sz[1],
                                         pan=next_pan)
                    b = _apply_brand_overlay(b)
                    writer.append_data(_crossfade(last_a, b, t))
    finally:
        writer.close()

    return out_path


def main() -> int:
    shots = _build_shots()
    out = _render_video(shots)
    size_mb = out.stat().st_size / (1024 * 1024)
    duration = (
        len(shots) * SHOT_HOLD_S
        + (len(shots) - 1) * CROSSFADE_S
        + (END_CARD_S - SHOT_HOLD_S)
    )
    print()
    print(f"OK: {out}")
    print(f"   {WIDTH}x{HEIGHT} @ {FPS}fps  ~{duration:.1f}s  {size_mb:.1f} MB")
    print(f"   Per-shot PNGs in: {SHOTS_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

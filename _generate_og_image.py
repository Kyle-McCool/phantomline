"""One-shot OG image generator.

Produces static/phantomline-og.png at the canonical 1200x630 OG/Twitter
card aspect ratio (1.91:1). Without this, social platforms and Google
were falling back to phantomline-logo-primary.png (1155x393), which gets
center-cropped by Twitter and letterboxed by LinkedIn — both hurt CTR.

Re-run only if the brand logo or tagline changes:
    .venv/Scripts/python.exe _generate_og_image.py
"""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
OUT = STATIC / "phantomline-og.png"

W, H = 1200, 630
BG = (9, 11, 12, 255)            # #090b0c — site theme-color
ACCENT = (52, 211, 219, 255)     # phantomline cyan
SUBTEXT = (180, 195, 200, 255)
TAGLINE = "Local AI video studio for faceless YouTube"
WORDMARK = "PHANTOMLINE"


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    """Try a few common system fonts so this runs on Windows + Render
    Linux + macOS without an extra dependency."""
    candidates = (
        ["arialbd.ttf", "Arial Bold.ttf", "DejaVuSans-Bold.ttf"]
        if bold
        else ["arial.ttf", "Arial.ttf", "DejaVuSans.ttf"]
    )
    for name in candidates:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def main() -> None:
    img = Image.new("RGBA", (W, H), BG)
    draw = ImageDraw.Draw(img)

    # Soft radial accent glow behind the wordmark — keeps the card from
    # looking flat on dark social feeds.
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gdraw = ImageDraw.Draw(glow)
    cx, cy = W // 2, H // 2 - 30
    for r in range(420, 0, -20):
        alpha = int(40 * (r / 420))
        gdraw.ellipse(
            (cx - r, cy - r // 2, cx + r, cy + r // 2),
            fill=(52, 211, 219, max(0, 25 - alpha // 4)),
        )
    img.alpha_composite(glow)

    # Try to drop the actual logo art on top of the wordmark for visual
    # richness; fall back to typeset wordmark if the source PNG is missing.
    logo_path = STATIC / "phantomline-logo-primary.png"
    if logo_path.exists():
        logo = Image.open(logo_path).convert("RGBA")
        target_w = 760
        scale = target_w / logo.width
        target_h = int(logo.height * scale)
        logo = logo.resize((target_w, target_h), Image.LANCZOS)
        img.alpha_composite(
            logo, (W // 2 - target_w // 2, H // 2 - target_h // 2 - 40)
        )
    else:
        font_word = _font(120, bold=True)
        bbox = draw.textbbox((0, 0), WORDMARK, font=font_word)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        draw.text(
            ((W - tw) // 2, (H - th) // 2 - 40), WORDMARK, fill=ACCENT, font=font_word
        )

    # Tagline below the lockup.
    font_tag = _font(36)
    bbox = draw.textbbox((0, 0), TAGLINE, font=font_tag)
    tw = bbox[2] - bbox[0]
    draw.text(((W - tw) // 2, H - 150), TAGLINE, fill=SUBTEXT, font=font_tag)

    # Bottom-right URL stamp so screenshots without context still attribute.
    font_url = _font(24)
    url = "phantomline.xyz"
    bbox = draw.textbbox((0, 0), url, font=font_url)
    draw.text((W - (bbox[2] - bbox[0]) - 40, H - 50), url, fill=ACCENT, font=font_url)

    # Hairline accent border at the top — adds "polish" cue at thumbnail size.
    draw.rectangle((0, 0, W, 4), fill=ACCENT)

    img.convert("RGB").save(OUT, "PNG", optimize=True)
    print(f"Wrote {OUT} ({W}x{H})")


if __name__ == "__main__":
    main()

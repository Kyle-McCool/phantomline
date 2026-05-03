"""One-shot logo optimizer.

Source PNGs from the brand kit live in C:/Users/kylem/Downloads/ as
Primary-logo.png, Flat-logo.png, Favicon.png. Some were exported with
a baked-in white background instead of transparency, and the wordmark
master has heavy internal padding (visible content fills only ~50%
of the frame). This script fixes both issues and emits the small,
browser-ready PNGs the templates reference.

Pipeline per image: dewhite (if opaque) -> auto_trim -> resize.

Run with: .venv/Scripts/python.exe _resize_logos.py
Originals in static/ are backed up to *.orig.png the first time.
"""
from pathlib import Path
from PIL import Image
import numpy as np

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
MASTERS = Path("C:/Users/kylem/Downloads")


def dewhite(im: Image.Image, threshold: int = 240, feather: int = 15) -> Image.Image:
    """Convert near-white pixels to transparent.

    For each pixel, alpha is driven by min(R,G,B): pixels at or above
    `threshold` become fully transparent, pixels below `threshold-feather`
    keep their original alpha, and pixels in between get a smooth ramp so
    anti-aliased edges of the cyan logo don't get a hard cutoff."""
    arr = np.array(im.convert("RGBA"))
    rgb = arr[..., :3].astype(np.int16)
    min_rgb = rgb.min(axis=-1)
    # Linear ramp: alpha_factor = 1.0 below threshold-feather, 0.0 at threshold.
    factor = np.clip((threshold - min_rgb) / float(feather), 0.0, 1.0)
    arr[..., 3] = (arr[..., 3].astype(np.float32) * factor).astype(np.uint8)
    return Image.fromarray(arr, "RGBA")


def auto_trim(im: Image.Image, padding_pct: float = 8.0, alpha_threshold: int = 30) -> Image.Image:
    """Crop to the bbox of pixels with alpha > threshold, then re-pad
    with `padding_pct` of the longer side as transparent margin so CSS
    drop-shadow has room to render outside the visible content."""
    im = im.convert("RGBA")
    a = im.split()[-1]
    mask = a.point(lambda p: 255 if p > alpha_threshold else 0)
    bbox = mask.getbbox()
    if bbox is None:
        return im
    cropped = im.crop(bbox)
    cw, ch = cropped.size
    pad = int(round(max(cw, ch) * padding_pct / 100.0))
    if pad <= 0:
        return cropped
    out = Image.new("RGBA", (cw + 2 * pad, ch + 2 * pad), (0, 0, 0, 0))
    out.paste(cropped, (pad, pad), cropped)
    return out


def resize_long_side(im: Image.Image, target: int) -> Image.Image:
    w, h = im.size
    if max(w, h) <= target:
        return im
    scale = target / max(w, h)
    new = (max(1, int(w * scale)), max(1, int(h * scale)))
    return im.resize(new, Image.LANCZOS)


def backup_existing(out_name: str) -> None:
    out = STATIC / out_name
    if not out.exists():
        return
    orig = out.with_suffix(".orig.png")
    if orig.exists():
        return
    orig.write_bytes(out.read_bytes())
    print(f"backup: {out.name} -> {orig.name}")


def process(master_name: str, out_name: str, target_size: int, *, needs_dewhite: bool) -> None:
    src = MASTERS / master_name
    if not src.exists():
        print(f"skip: master {src} not found")
        return
    backup_existing(out_name)
    with Image.open(src) as im:
        im = im.convert("RGBA")
        sw, sh = im.size
        if needs_dewhite:
            im = dewhite(im)
        im = auto_trim(im)
        tw, th = im.size
        im = resize_long_side(im, target_size)
        out = STATIC / out_name
        im.save(out, format="PNG", optimize=True)
    after = out.stat().st_size
    print(
        f"build: {master_name} -> {out_name} "
        f"(src {sw}x{sh}, trimmed {tw}x{th}, final {im.size[0]}x{im.size[1]}, {after:,}B)"
    )


def dewhite_inplace(out_name: str, target_size: int) -> None:
    """Fallback for assets that don't have a high-res master in Downloads:
    dewhite + trim + resize the existing static/ file in place."""
    src = STATIC / out_name
    if not src.exists():
        print(f"skip: {src} not found")
        return
    backup_existing(out_name)
    with Image.open(src) as im:
        im = im.convert("RGBA")
        sw, sh = im.size
        im = dewhite(im)
        im = auto_trim(im)
        im = resize_long_side(im, target_size)
        im.save(src, format="PNG", optimize=True)
    after = src.stat().st_size
    print(f"dewhite-inplace: {out_name} ({sw}x{sh} -> {im.size[0]}x{im.size[1]}, {after:,}B)")


def main() -> None:
    # Wordmark — the master HAS proper transparency but heavy internal
    # padding. Trim only.
    process("Primary-logo.png", "phantomline-logo-primary.png", 1200, needs_dewhite=False)

    # Flat horizontal — master has baked-in white background. Dewhite + trim.
    process("Flat-logo.png", "phantomline-logo-flat.png", 1200, needs_dewhite=True)

    # Favicon — master has baked-in white background.
    process("Favicon.png", "phantomline-favicon.png", 96, needs_dewhite=True)

    # Square (PWA + apple-touch-icon) — same source as favicon, larger output.
    process("Favicon.png", "phantomline-logo-square.png", 512, needs_dewhite=True)

    # Mono — no high-res master available; fix the existing static file in place.
    dewhite_inplace("phantomline-logo-mono.png", 1200)


if __name__ == "__main__":
    main()

"""One-shot logo optimizer.

Source PNGs from the brand kit are 1-1.6 MB each (1024-2048px native
resolution, full RGBA). Browsers don't need that for icons. We resize
to typical browser-icon dimensions and keep transparency.

Run with: .venv/Scripts/python.exe _resize_logos.py
Then commit the smaller files. Originals are renamed to *.orig.png in
case we need them later for press kits.
"""
from pathlib import Path
from PIL import Image

STATIC = Path(__file__).resolve().parent / "static"


def resize_to(src_name: str, target_size: int, out_name: str | None = None) -> None:
    """Resize <src_name> in place to <target_size> px on the long side.
    Preserves aspect ratio. RGBA -> RGBA. Saves with optimize=True."""
    src = STATIC / src_name
    if not src.exists():
        print(f"skip: {src} (not found)")
        return
    out = STATIC / (out_name or src_name)
    with Image.open(src) as im:
        im = im.convert("RGBA")
        w, h = im.size
        if max(w, h) <= target_size and src == out:
            print(f"keep: {src.name} already {w}x{h}, no resize needed")
            return
        scale = target_size / max(w, h)
        new = (max(1, int(w * scale)), max(1, int(h * scale)))
        im = im.resize(new, Image.LANCZOS)
        im.save(out, format="PNG", optimize=True)
    before = src.stat().st_size
    after = out.stat().st_size
    print(f"resize: {src.name} -> {out.name} ({w}x{h} -> {new[0]}x{new[1]}, {before:,}B -> {after:,}B)")


def main() -> None:
    # Favicons need to be tiny — browsers cache them but the first hit hurts.
    # 96px on the long side is plenty for retina displays at favicon scale.
    resize_to("phantomline-favicon.png", 96)

    # Apple touch icon + PWA app icon — 192px is the minimum standard size,
    # 512px is the maximum useful size. We keep one at 512 for high-DPI
    # devices and let CSS pick the right one.
    resize_to("phantomline-logo-square.png", 512)

    # Primary logo — used in nav (44px tall) and hero (up to 460px wide) +
    # social previews (1200x630 ideal). 1200px on long side covers all.
    resize_to("phantomline-logo-primary.png", 1200)

    # Flat horizontal — same usage as primary.
    resize_to("phantomline-logo-flat.png", 1200)

    # Mono — press kit / dark-on-light. Less hot path but still resize.
    resize_to("phantomline-logo-mono.png", 1200)


if __name__ == "__main__":
    main()

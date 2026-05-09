"""One-off poster-frame extractor for the Phantomline footage library.

Sibling to `_compress_footage.py`. Reads each `_footage_compressed/demo_NN.mp4`
and writes `_footage_thumbs/demo_NN.jpg` — a 540x960 JPEG sampled at ~10% of
duration (later than the picker's old 5% mark, so the frame is past intros).

Run from the project root:
    python _extract_thumbs.py
    python _extract_thumbs.py --force   # re-extract everything

Idempotent: skips files whose JPG already exists with a non-trivial size.

The output JPGs are intended to be uploaded to Supabase Storage at
    public-videos/thumbs/demo_NN.jpg
so the studio picker modal can render an instant <img> per tile and lazy-load
the full <video> only on hover.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


def _resolve_ffmpeg() -> str | None:
    on_path = shutil.which("ffmpeg")
    if on_path:
        return on_path
    try:
        import imageio_ffmpeg  # type: ignore
        bundled = imageio_ffmpeg.get_ffmpeg_exe()
        if bundled and Path(bundled).exists():
            return bundled
    except ImportError:
        pass
    return None


FFMPEG = _resolve_ffmpeg()

SRC_DIR = Path(__file__).resolve().parent / "_footage_compressed"
OUT_DIR = Path(__file__).resolve().parent / "_footage_thumbs"
SRC_RANGE = range(2, 21)
THUMB_HEIGHT = 960   # 540x960 keeps 9:16, ~30-60 KB per JPG at q=4


def _probe_duration(src: Path) -> float:
    """Return duration in seconds, parsed from ffmpeg's stderr."""
    if not FFMPEG:
        return 0.0
    try:
        result = subprocess.run(
            [FFMPEG, "-hide_banner", "-i", str(src), "-f", "null", "-"],
            capture_output=True, text=True,
        )
        log = result.stderr or ""
    except OSError:
        return 0.0
    import re
    m = re.search(r"Duration:\s+(\d+):(\d+):(\d+(?:\.\d+)?)", log)
    if not m:
        return 0.0
    h, mn, sec = int(m.group(1)), int(m.group(2)), float(m.group(3))
    return h * 3600 + mn * 60 + sec


def _extract(src: Path, dst: Path, at_seconds: float) -> bool:
    """Extract a single JPG at the given timestamp. Returns True on success."""
    cmd = [
        FFMPEG, "-y",
        "-ss", f"{at_seconds:.2f}",
        "-i", str(src),
        "-frames:v", "1",
        "-vf", f"scale=-2:{THUMB_HEIGHT}",
        "-q:v", "4",   # JPEG quality (2=best, 31=worst). 4 is small + clean.
        str(dst),
    ]
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        return dst.exists() and dst.stat().st_size > 1024
    except subprocess.CalledProcessError:
        return False


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Extract poster frames for the footage library.")
    parser.add_argument("--force", action="store_true", help="Re-extract even if JPG exists.")
    args = parser.parse_args(argv)

    if not FFMPEG:
        print("[error] No ffmpeg available. Install ffmpeg or `pip install imageio-ffmpeg`.")
        return 1
    print(f"[info] ffmpeg: {FFMPEG}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    ok = skipped = failed = 0
    for n in SRC_RANGE:
        src = SRC_DIR / f"demo_{n:02d}.mp4"
        dst = OUT_DIR / f"demo_{n:02d}.jpg"
        if not src.exists():
            print(f"[skip] {src.name} not found")
            failed += 1
            continue
        if dst.exists() and dst.stat().st_size > 1024 and not args.force:
            print(f"[skip-existing] {dst.name} ({dst.stat().st_size // 1024} KB)")
            skipped += 1
            continue
        duration = _probe_duration(src)
        at = max(0.5, duration * 0.10)
        if _extract(src, dst, at):
            print(f"[ok] {dst.name} @ {at:.1f}s ({dst.stat().st_size // 1024} KB)")
            ok += 1
        else:
            print(f"[fail] {dst.name}")
            failed += 1

    print(f"\nDone. ok={ok} skipped={skipped} failed={failed}. Output: {OUT_DIR}")
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

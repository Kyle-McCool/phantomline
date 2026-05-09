"""One-off batch compression + manifest generator for the Phantomline
footage library.

Reads C:/Users/kylem/Downloads/Demo_2.mp4 .. Demo_20.mp4, runs each
through ffmpeg with library-friendly settings (1080p cap, 30fps cap,
H.264 CRF 26, no audio, faststart), writes outputs to
_footage_compressed/, then emits a manifest.json with metadata for
each clip.

Run from the project root:
    python _compress_footage.py

Designed to be re-runnable: skips files whose compressed counterpart
already exists with a non-trivial size, so you can re-run after dropping
in new clips without re-encoding the existing ones. Pass --force to
re-encode everything.

Output naming: source filenames are normalised to demo_NN.mp4 (zero-
padded) so the picker shows them in numeric order. Human-friendly titles
go in the manifest, not in filenames — keeps the upload step idempotent
and lets you rename in the manifest without re-uploading bytes.

This script is intentionally outside the regular routes/ layout because
it's a one-time content prep tool, not a runtime endpoint. It uses
ffmpeg + ffprobe from PATH (already required by Phantomline).
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path


def _resolve_ffmpeg() -> str | None:
    """Find an ffmpeg binary. Prefer one on PATH, then fall back to the
    one shipped with the imageio-ffmpeg pip package (which Phantomline
    uses elsewhere). Returns absolute path or None if neither available.

    Note: imageio-ffmpeg bundles ONLY ffmpeg, not ffprobe — so this script
    parses metadata from ffmpeg's own stderr output instead of relying on
    ffprobe being available."""
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

# ---------------------------------------------------------------------------
# Config — tuned for B-roll / Shorts-orientation clips bundled as a curated
# library. Bigger CRF = smaller files; 26 is the "perceptually good" sweet
# spot for 1080p H.264 mid-bitrate content. Audio dropped because these
# clips overlay a narrator — keeping their audio is wasted bandwidth.
# ---------------------------------------------------------------------------
SRC_DIR = Path("C:/Users/kylem/Downloads")
SRC_PATTERN = "Demo_{n}.mp4"          # Demo_2.mp4 .. Demo_20.mp4
SRC_RANGE = range(2, 21)              # 2..20 inclusive => 19 files
OUT_DIR = Path(__file__).resolve().parent / "_footage_compressed"
MANIFEST_PATH = Path(__file__).resolve().parent / "static" / "library" / "footage-manifest.json"

CRF = "26"
PRESET = "medium"   # encoder speed/efficiency tradeoff. medium is the default.
MAX_RES = 1080      # downscale if higher
MAX_FPS = 30        # downscale if higher

# Replace once Kyle creates the bucket+folder. Following his note:
#   bucket: "public videos"  (slug: "public-videos" — Supabase strips spaces)
#   folder: "videos"
# Public URL pattern Supabase emits:
#   {SUPABASE_URL}/storage/v1/object/public/{bucket}/{path}
SUPABASE_URL = "https://vdzydhrgazqeyaalguuy.supabase.co"
BUCKET = "public-videos"
FOLDER = "videos"


def probe_via_ffmpeg(src: Path) -> dict:
    """Get width/height/duration/fps by running ffmpeg in info-only mode
    and parsing stderr. Eliminates the ffprobe dependency (imageio-ffmpeg
    bundles only ffmpeg).

    ffmpeg without an output target prints input metadata to stderr in
    a stable format like:
        Duration: 00:00:14.32, start: 0.000000, bitrate: 5320 kb/s
        Stream #0:0[0x1]: Video: h264 ..., yuv420p(tv, bt709), 1080x1920 [SAR 1:1 DAR 9:16], 5183 kb/s, 30 fps, 30 tbr, 90k tbn

    Returns empty dict on parse failure."""
    if not FFMPEG:
        return {}
    try:
        result = subprocess.run(
            [FFMPEG, "-hide_banner", "-i", str(src), "-f", "null", "-"],
            capture_output=True, text=True,
        )
        # ffmpeg returns nonzero when there's no output target (expected) —
        # use stderr regardless. text=True so it's already decoded.
        log = result.stderr or ""
    except OSError:
        return {}

    width = height = 0
    fps = 0.0
    duration = 0.0

    # Duration: HH:MM:SS.cc
    m = re.search(r"Duration:\s+(\d+):(\d+):(\d+(?:\.\d+)?)", log)
    if m:
        h, mn, sec = int(m.group(1)), int(m.group(2)), float(m.group(3))
        duration = h * 3600 + mn * 60 + sec

    # Find the first WxH on a "Video:" line. Don't try to anchor to a
    # specific column count — codec descriptors like
    # "h264 (High) (avc1 / 0x...)" contain commas inside parentheses, and
    # color descriptors like "yuv420p(tv, bt709, progressive)" do too,
    # so [^,]+ matching positions wrong. Search for any DDDxDDD on a line
    # that starts with "    Stream" and contains "Video:".
    for line in log.splitlines():
        if "Video:" not in line:
            continue
        wm = re.search(r"\b(\d{2,5})x(\d{2,5})\b", line)
        if wm:
            width, height = int(wm.group(1)), int(wm.group(2))
            break

    # FPS — look for "X fps" or "X.XX fps" in the video stream line.
    m = re.search(r"(\d+(?:\.\d+)?)\s*fps", log)
    if m:
        try:
            fps = float(m.group(1))
        except ValueError:
            fps = 0.0

    size_bytes = 0
    try:
        size_bytes = src.stat().st_size
    except OSError:
        pass

    return {
        "width": width,
        "height": height,
        "fps": round(fps, 2),
        "duration_seconds": round(duration, 2),
        "size_bytes": size_bytes,
    }


# Keep the old name as an alias so the rest of the script doesn't change.
ffprobe_dimensions = probe_via_ffmpeg


def build_ffmpeg_cmd(src: Path, dst: Path, src_meta: dict) -> list[str]:
    """Construct the ffmpeg arg list for a single clip. Includes the scale
    + fps + crf settings; conditionally downscales only when the source
    exceeds the cap (avoids re-scaling a clip that's already 720p)."""
    # Scale filter: downscale to MAX_RES on the long edge while preserving
    # aspect. min(MAX_RES,iw) keeps source size when it's already small.
    # `force_original_aspect_ratio=decrease` lets us fit within 1080xH or
    # Wx1080 without stretching.
    width = src_meta.get("width") or 0
    height = src_meta.get("height") or 0
    long_edge = max(width, height)

    vf_parts = []
    if long_edge > MAX_RES:
        # Vertical (height > width) gets MAX_RES on height, horizontal on width.
        if height >= width:
            vf_parts.append(f"scale=-2:{MAX_RES}")
        else:
            vf_parts.append(f"scale={MAX_RES}:-2")
    if (src_meta.get("fps") or 0) > MAX_FPS:
        vf_parts.append(f"fps={MAX_FPS}")
    vf = ",".join(vf_parts) if vf_parts else None

    cmd = [
        FFMPEG, "-y", "-i", str(src),
        "-c:v", "libx264",
        "-crf", CRF,
        "-preset", PRESET,
        "-pix_fmt", "yuv420p",     # universally compatible (Safari, Chrome, mobile)
        "-movflags", "+faststart", # moov atom at the front for streamable preview
        "-an",                     # drop audio (B-roll overlays narration)
    ]
    if vf:
        cmd.extend(["-vf", vf])
    cmd.append(str(dst))
    return cmd


def humanize_bytes(n: int) -> str:
    if n <= 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB"]
    i = 0
    f = float(n)
    while f >= 1024 and i < len(units) - 1:
        f /= 1024.0
        i += 1
    return f"{f:.1f} {units[i]}"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Batch compress Phantomline footage library.")
    parser.add_argument("--force", action="store_true",
                        help="Re-encode even if compressed output already exists.")
    args = parser.parse_args(argv)

    if not FFMPEG:
        print("[error] No ffmpeg available. Either install ffmpeg on PATH, or install")
        print("        the imageio-ffmpeg pip package which bundles a binary:")
        print("            pip install imageio-ffmpeg")
        return 1
    print(f"[info] using ffmpeg at {FFMPEG}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)

    manifest_clips: list[dict] = []
    total_in = 0
    total_out = 0
    summary_rows: list[tuple[str, int, int, str]] = []  # (name, in_bytes, out_bytes, status)

    for n in SRC_RANGE:
        src_name = SRC_PATTERN.format(n=n)
        src_path = SRC_DIR / src_name
        if not src_path.exists():
            print(f"[skip] {src_name} not found in {SRC_DIR}")
            summary_rows.append((src_name, 0, 0, "missing"))
            continue

        # Output naming: zero-pad so picker UI sorts cleanly.
        out_name = f"demo_{n:02d}.mp4"
        out_path = OUT_DIR / out_name

        in_size = src_path.stat().st_size
        total_in += in_size

        if out_path.exists() and out_path.stat().st_size > 1024 and not args.force:
            out_size = out_path.stat().st_size
            total_out += out_size
            print(f"[skip-existing] {src_name} -> {out_name} ({humanize_bytes(in_size)} -> {humanize_bytes(out_size)})")
            summary_rows.append((out_name, in_size, out_size, "skip"))
            src_meta = ffprobe_dimensions(src_path)
            out_meta = ffprobe_dimensions(out_path)
        else:
            src_meta = ffprobe_dimensions(src_path)
            cmd = build_ffmpeg_cmd(src_path, out_path, src_meta)
            print(f"[encode] {src_name} -> {out_name} (in: {humanize_bytes(in_size)}, "
                  f"src {src_meta.get('width')}x{src_meta.get('height')}@{src_meta.get('fps')}fps "
                  f"{src_meta.get('duration_seconds')}s)")
            try:
                # Pipe stderr through so user can see ffmpeg progress; suppress
                # stdout so the script's own progress lines don't get buried.
                result = subprocess.run(
                    cmd, check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE, text=True,
                )
                out_size = out_path.stat().st_size
                total_out += out_size
                pct = (out_size / in_size * 100) if in_size else 0
                print(f"           done. out: {humanize_bytes(out_size)} ({pct:.1f}% of source)")
                summary_rows.append((out_name, in_size, out_size, "ok"))
                out_meta = ffprobe_dimensions(out_path)
            except subprocess.CalledProcessError as exc:
                print(f"[error] ffmpeg failed on {src_name}: {exc.stderr[-500:] if exc.stderr else exc}")
                summary_rows.append((out_name, in_size, 0, "error"))
                continue

        # Manifest entry. URLs assume Kyle uploads each `out_name` into
        # bucket/folder as configured at top of file. Title left blank for
        # Kyle to fill in (or we can suggest titles after uploading).
        public_url = f"{SUPABASE_URL}/storage/v1/object/public/{BUCKET}/{FOLDER}/{out_name}"
        manifest_clips.append({
            "id": f"demo-{n:02d}",
            "filename": out_name,
            "url": public_url,
            "title": "",                              # human-fill: e.g. "Foggy mountain road"
            "description": "",                        # human-fill
            "categories": [],                         # human-fill: e.g. ["horror", "atmospheric"]
            "duration_seconds": out_meta.get("duration_seconds") or src_meta.get("duration_seconds") or 0,
            "width": out_meta.get("width") or src_meta.get("width") or 0,
            "height": out_meta.get("height") or src_meta.get("height") or 0,
            "fps": out_meta.get("fps") or src_meta.get("fps") or 0,
            "size_bytes": out_meta.get("size_bytes") or 0,
            "aspect_ratio": _aspect_label(
                out_meta.get("width") or src_meta.get("width") or 0,
                out_meta.get("height") or src_meta.get("height") or 0,
            ),
            "attribution": "Phantomline",             # change if any clip is third-party
        })

    # Write manifest. Pretty-printed so it's reviewable in a code editor and
    # easy to hand-edit titles/categories later.
    manifest = {
        "version": 1,
        "generated_by": "_compress_footage.py",
        "bucket": BUCKET,
        "folder": FOLDER,
        "clip_count": len(manifest_clips),
        "clips": manifest_clips,
    }
    MANIFEST_PATH.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    # Summary table.
    print("\n" + "=" * 64)
    print(f"{'file':<22} {'before':>10} {'after':>10} {'ratio':>8}  status")
    print("-" * 64)
    for name, in_b, out_b, status in summary_rows:
        ratio = f"{(out_b / in_b * 100):.1f}%" if in_b and out_b else "—"
        print(f"{name:<22} {humanize_bytes(in_b):>10} {humanize_bytes(out_b):>10} {ratio:>8}  {status}")
    print("-" * 64)
    print(f"{'TOTAL':<22} {humanize_bytes(total_in):>10} {humanize_bytes(total_out):>10} "
          f"{(total_out / total_in * 100):.1f}%  ({len(manifest_clips)} clips)" if total_in else "")
    print("=" * 64)
    print(f"\nCompressed files: {OUT_DIR}")
    print(f"Manifest: {MANIFEST_PATH}")
    return 0


def _aspect_label(w: int, h: int) -> str:
    """Return '16:9' / '9:16' / '1:1' / '4:3' label for common aspects, else
    the literal ratio. Used by the picker UI to filter clips by orientation."""
    if not w or not h:
        return "unknown"
    from math import gcd
    g = gcd(w, h)
    rw, rh = w // g, h // g
    common = {(16, 9): "16:9", (9, 16): "9:16", (1, 1): "1:1", (4, 3): "4:3", (3, 4): "3:4"}
    if (rw, rh) in common:
        return common[(rw, rh)]
    # Approximate: bucket near-16:9 / near-9:16 to canonical labels.
    ratio = w / h
    if 1.7 <= ratio <= 1.85:
        return "16:9"
    if 0.54 <= ratio <= 0.59:
        return "9:16"
    return f"{rw}:{rh}"


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

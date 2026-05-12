"""
Draft video assembly for Ghostline.

This renders a narration-aligned MP4 from a timeline. If real visual assets
exist for a scene, the renderer uses them first:

- motion clips such as ``scene_001.mp4``
- stills such as ``scene_001.png`` / ``scene_001.jpg``

When no matching asset exists, the renderer falls back to a readable scene
card so draft videos still finish end-to-end.
"""

from __future__ import annotations

import math
import random
import re
import shutil
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps
from moviepy import AudioFileClip, ColorClip, CompositeVideoClip, ImageClip, VideoFileClip, concatenate_videoclips


W, H = 1920, 1080
BG = (14, 13, 10)
PANEL = (31, 28, 22)
GOLD = (212, 162, 62)
TEXT = (255, 248, 236)
MUTED = (187, 176, 160)
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp")
VIDEO_EXTS = (".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv", ".gif")


def _target_size(aspect="16:9"):
    aspect = (aspect or "16:9").strip()
    if aspect == "9:16":
        return 1080, 1920
    if aspect == "1:1":
        return 1080, 1080
    return 1920, 1080


def _font(size, bold=False):
    """Cross-platform font lookup. Tries system-specific paths in order;
    falls back to PIL's default if nothing else loads."""
    if bold:
        candidates = [
            "C:/Windows/Fonts/segoeuib.ttf",
            "C:/Windows/Fonts/arialbd.ttf",
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
            "/System/Library/Fonts/HelveticaNeue.ttc",
            "/System/Library/Fonts/SFNS.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
            "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
            "DejaVuSans-Bold.ttf",
            "Arial Bold.ttf",
        ]
    else:
        candidates = [
            "C:/Windows/Fonts/segoeui.ttf",
            "C:/Windows/Fonts/arial.ttf",
            "/System/Library/Fonts/Supplemental/Arial.ttf",
            "/System/Library/Fonts/HelveticaNeue.ttc",
            "/System/Library/Fonts/SFNS.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
            "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
            "DejaVuSans.ttf",
            "Arial.ttf",
        ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _wrap(draw, text, font, max_width):
    words = (text or "").split()
    lines = []
    current = []
    for word in words:
        candidate = " ".join(current + [word])
        if draw.textbbox((0, 0), candidate, font=font)[2] <= max_width:
            current.append(word)
        else:
            if current:
                lines.append(" ".join(current))
            current = [word]
    if current:
        lines.append(" ".join(current))
    return lines


def _draw_card(scene, title, out_path):
    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)
    title_font = _font(54, bold=True)
    scene_font = _font(28, bold=True)
    body_font = _font(34)
    small_font = _font(24)

    draw.rectangle((120, 110, W - 120, H - 110), fill=PANEL, outline=(62, 55, 42), width=2)
    draw.rectangle((120, 110, W - 120, 118), fill=GOLD)

    draw.text((170, 165), title[:80], font=title_font, fill=TEXT)
    draw.text(
        (170, 255),
        f"Scene {scene.get('id')}  |  {scene.get('start')} - {scene.get('end')}",
        font=scene_font,
        fill=GOLD,
    )

    y = 335
    for line in _wrap(draw, scene.get("narration", ""), body_font, W - 340)[:8]:
        draw.text((170, y), line, font=body_font, fill=TEXT)
        y += 48

    y = 760
    draw.text((170, y), "VISUAL PROMPT", font=small_font, fill=GOLD)
    y += 42
    for line in _wrap(draw, scene.get("video_prompt", ""), small_font, W - 340)[:4]:
        draw.text((170, y), line, font=small_font, fill=MUTED)
        y += 34

    img.save(out_path, quality=95)


def _scene_seed(scene, title=""):
    text = f"{scene.get('id', '')}|{title}|{scene.get('narration', '')}|{scene.get('video_prompt', '')}"
    return sum(ord(ch) for ch in text) % (2 ** 32)


def _pick_palette(text):
    text = (text or "").lower()
    if any(word in text for word in ("ocean", "sea", "water", "underwater", "coast")):
        return ((7, 19, 36), (18, 54, 91), (58, 119, 165))
    if any(word in text for word in ("desert", "arizona", "dusk", "sunset", "horizon")):
        return ((28, 17, 14), (102, 53, 29), (197, 114, 53))
    if any(word in text for word in ("forest", "appalach", "mountain", "woods", "night")):
        return ((8, 12, 19), (19, 34, 47), (62, 88, 92))
    return ((9, 10, 18), (24, 31, 46), (67, 82, 118))


def _gradient_background(img, palette):
    top, mid, bottom = palette
    px = img.load()
    for y in range(H):
        t = y / float(max(1, H - 1))
        if t < 0.5:
            u = t / 0.5
            color = tuple(int(top[i] * (1 - u) + mid[i] * u) for i in range(3))
        else:
            u = (t - 0.5) / 0.5
            color = tuple(int(mid[i] * (1 - u) + bottom[i] * u) for i in range(3))
        for x in range(W):
            px[x, y] = color


def _draw_stars(draw, rng, density=120):
    for _ in range(density):
        x = rng.randint(0, W - 1)
        y = rng.randint(0, int(H * 0.68))
        r = rng.choice((1, 1, 1, 2))
        a = rng.randint(150, 255)
        color = (a, a, min(255, a + 10))
        draw.ellipse((x - r, y - r, x + r, y + r), fill=color)


def _draw_fog(draw, rng, tint=(210, 220, 235)):
    for _ in range(22):
        x = rng.randint(-200, W)
        y = rng.randint(int(H * 0.18), int(H * 0.86))
        w = rng.randint(240, 720)
        h = rng.randint(60, 180)
        alpha = rng.randint(10, 28)
        draw.ellipse((x, y, x + w, y + h), fill=(*tint, alpha))


def _draw_mountains(draw, rng):
    base_y = int(H * 0.73)
    points = [(0, H)]
    x = 0
    while x < W:
        peak_y = rng.randint(int(H * 0.34), int(H * 0.72))
        points.append((x, peak_y))
        x += rng.randint(120, 260)
    points.extend([(W, base_y), (W, H)])
    draw.polygon(points, fill=(14, 17, 24, 255))


def _draw_ground(draw):
    draw.rectangle((0, int(H * 0.77), W, H), fill=(8, 9, 12, 255))


def _draw_signal(draw, rng):
    cx = rng.randint(int(W * 0.2), int(W * 0.8))
    cy = rng.randint(int(H * 0.18), int(H * 0.45))
    for radius in range(80, 320, 48):
        bbox = (cx - radius, cy - radius, cx + radius, cy + radius)
        draw.arc(bbox, start=210, end=340, fill=(116, 206, 255, 180), width=3)
    draw.ellipse((cx - 8, cy - 8, cx + 8, cy + 8), fill=(220, 245, 255, 220))


def _draw_telescope(draw, x=None):
    x = x if x is not None else int(W * 0.68)
    base_y = int(H * 0.78)
    draw.polygon(
        [(x - 18, base_y - 160), (x + 74, base_y - 112), (x + 58, base_y - 86), (x - 36, base_y - 138)],
        fill=(28, 32, 40, 255),
    )
    draw.rectangle((x - 12, base_y - 126, x + 18, base_y - 96), fill=(20, 24, 31, 255))
    for dx in (-24, 10, 46):
        draw.line((x + 2, base_y - 92, x + dx, base_y), fill=(20, 24, 31, 255), width=6)


def _draw_observatory(draw):
    dome_x = int(W * 0.3)
    ground_y = int(H * 0.77)
    draw.rectangle((dome_x - 90, ground_y - 70, dome_x + 90, ground_y), fill=(22, 25, 31, 255))
    draw.pieslice((dome_x - 95, ground_y - 165, dome_x + 95, ground_y + 25), 180, 360, fill=(28, 31, 39, 255))
    draw.rectangle((dome_x + 8, ground_y - 155, dome_x + 30, ground_y - 75), fill=(12, 14, 19, 255))


def _draw_screens(draw):
    left = int(W * 0.58)
    top = int(H * 0.46)
    for i in range(3):
        x1 = left + i * 120
        draw.rounded_rectangle((x1, top, x1 + 98, top + 68), radius=8, fill=(18, 34, 42, 255), outline=(76, 164, 193, 120), width=2)
        for row in range(4):
            y = top + 12 + row * 13
            draw.line((x1 + 10, y, x1 + 86, y), fill=(88, 200, 222, 140), width=2)


def _draw_figure(draw):
    cx = int(W * 0.52)
    ground_y = int(H * 0.77)
    draw.ellipse((cx - 16, ground_y - 132, cx + 16, ground_y - 100), fill=(15, 16, 20, 255))
    draw.rectangle((cx - 12, ground_y - 102, cx + 12, ground_y - 40), fill=(15, 16, 20, 255))
    draw.line((cx - 8, ground_y - 40, cx - 22, ground_y), fill=(15, 16, 20, 255), width=6)
    draw.line((cx + 8, ground_y - 40, cx + 20, ground_y), fill=(15, 16, 20, 255), width=6)


def _draw_town(draw, rng):
    ground_y = int(H * 0.77)
    x = 60
    while x < int(W * 0.46):
        w = rng.randint(36, 88)
        h = rng.randint(30, 120)
        draw.rectangle((x, ground_y - h, x + w, ground_y), fill=(18, 20, 27, 255))
        x += w + rng.randint(8, 22)


def _draw_fleet(draw, rng):
    count = rng.randint(4, 8)
    for i in range(count):
        cx = rng.randint(int(W * 0.52), int(W * 0.9))
        cy = rng.randint(int(H * 0.14), int(H * 0.44))
        scale = rng.randint(18, 46)
        draw.ellipse((cx - scale, cy - scale // 3, cx + scale, cy + scale // 3), fill=(34, 36, 44, 230))
        draw.arc((cx - scale, cy - scale // 3, cx + scale, cy + scale // 3), 180, 360, fill=(122, 198, 238, 130), width=2)


def _draw_planet(draw, rng):
    r = rng.randint(46, 90)
    cx = rng.randint(int(W * 0.1), int(W * 0.86))
    cy = rng.randint(int(H * 0.08), int(H * 0.28))
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(220, 226, 235, 44))


def _draw_generated_scene(scene, title, out_path):
    seed = _scene_seed(scene, title)
    rng = random.Random(seed)
    text = f"{scene.get('narration', '')} {scene.get('video_prompt', '')}".lower()
    palette = _pick_palette(text)

    base = Image.new("RGB", (W, H), BG)
    _gradient_background(base, palette)
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay, "RGBA")

    _draw_stars(draw, rng, density=160 if "stars" in text or "cosmos" in text or "space" in text else 90)
    _draw_planet(draw, rng)
    _draw_fog(draw, rng)
    _draw_mountains(draw, rng)
    _draw_ground(draw)

    if any(word in text for word in ("town", "city", "street", "houses")):
        _draw_town(draw, rng)
    if any(word in text for word in ("observer", "astronomer", "team", "figure", "person")):
        _draw_figure(draw)
    if any(word in text for word in ("telescope", "observatory", "receiver", "console", "instrument")):
        _draw_observatory(draw)
        _draw_telescope(draw)
    if any(word in text for word in ("signal", "frequency", "hum", "whisper", "melody", "data", "screens")):
        _draw_signal(draw, rng)
    if any(word in text for word in ("screen", "console", "data")):
        _draw_screens(draw)
    if any(word in text for word in ("alien", "fleet", "ships", "vessels", "ufo")):
        _draw_fleet(draw, rng)

    # soft vignette
    for i in range(7):
        inset = i * 18
        alpha = 10 + i * 6
        draw.rectangle((inset, inset, W - inset, H - inset), outline=(0, 0, 0, alpha), width=24)

    composed = Image.alpha_composite(base.convert("RGBA"), overlay).convert("RGB")
    composed.save(out_path, quality=95)


def _safe_stem(value):
    value = "".join(ch for ch in (value or "") if ch.isalnum() or ch in ("_", "-", "."))
    return value.strip("._-")


def _fit_image(source_path, out_path, target_size=None):
    target_size = target_size or (W, H)
    with Image.open(source_path) as img:
        fitted = ImageOps.fit(img.convert("RGB"), target_size, method=Image.Resampling.LANCZOS)
        fitted.save(out_path, quality=95)


def _call_clip_method(clip, names, *args, **kwargs):
    for name in names:
        fn = getattr(clip, name, None)
        if callable(fn):
            return fn(*args, **kwargs)
    raise AttributeError(f"Clip has no supported method from {names}")


def _loop_or_trim_video(clip, duration):
    if duration <= 0:
        return _call_clip_method(clip, ("with_duration", "set_duration"), 0.1)

    clip_duration = float(getattr(clip, "duration", 0) or 0)
    if clip_duration <= 0:
        return _call_clip_method(clip, ("with_duration", "set_duration"), duration)

    pieces = []
    remaining = duration
    while remaining > 0.001:
        take = min(clip_duration, remaining)
        pieces.append(_call_clip_method(clip, ("subclipped", "subclip"), 0, take))
        remaining -= take
    if len(pieces) == 1:
        return _call_clip_method(pieces[0], ("with_duration", "set_duration"), duration)
    joined = concatenate_videoclips(pieces, method="compose")
    return _call_clip_method(joined, ("with_duration", "set_duration"), duration)


def _cover_video_clip(clip, target_size=None):
    target_w, target_h = target_size or (W, H)
    size = getattr(clip, "size", None) or (W, H)
    src_w, src_h = size
    if not src_w or not src_h:
        return _call_clip_method(clip, ("resized", "resize"), new_size=(target_w, target_h))

    scale = max(target_w / float(src_w), target_h / float(src_h))
    resized = _call_clip_method(
        clip,
        ("resized", "resize"),
        new_size=(max(1, int(round(src_w * scale))), max(1, int(round(src_h * scale)))),
    )
    resized_w, resized_h = getattr(resized, "size", (target_w, target_h))
    x1 = max(0, int(round((resized_w - target_w) / 2.0)))
    y1 = max(0, int(round((resized_h - target_h) / 2.0)))
    x2 = x1 + target_w
    y2 = y1 + target_h
    return _call_clip_method(resized, ("cropped", "crop"), x1=x1, y1=y1, x2=x2, y2=y2)


def _scene_stems(scene, index):
    stems = []
    clip_file = scene.get("clip_file") or f"scene_{index:03d}.mp4"
    clip_stem = _safe_stem(Path(str(clip_file)).stem)
    if clip_stem:
        stems.append(clip_stem)

    scene_id = scene.get("id") or index
    numeric = None
    try:
        numeric = int(scene_id)
    except (TypeError, ValueError):
        pass

    if numeric is not None:
        stems.extend([
            f"scene_{numeric:03d}",
            f"scene-{numeric:03d}",
            f"scene_{numeric}",
            f"scene-{numeric}",
            f"{numeric:03d}",
            str(numeric),
        ])

    explicit = (_safe_stem(scene.get("asset_file")) if scene.get("asset_file") else "")
    if explicit:
        stems.insert(0, explicit)

    seen = set()
    ordered = []
    for stem in stems:
        if stem and stem not in seen:
            ordered.append(stem)
            seen.add(stem)
    return ordered


def _candidate_asset_paths(scene, index, asset_dirs):
    stems = _scene_stems(scene, index)
    for directory in asset_dirs:
        if not directory or not directory.exists() or not directory.is_dir():
            continue
        for stem in stems:
            for ext in VIDEO_EXTS + IMAGE_EXTS:
                candidate = directory / f"{stem}{ext}"
                if candidate.exists():
                    yield candidate


def _resolve_asset(scene, index, asset_dirs):
    for candidate in _candidate_asset_paths(scene, index, asset_dirs):
        suffix = candidate.suffix.lower()
        if suffix in VIDEO_EXTS:
            return {"kind": "video", "path": candidate}
        if suffix in IMAGE_EXTS:
            return {"kind": "image", "path": candidate}
    return None


def _build_scene_clip(scene, title, index, duration, tmp_dir, fps, asset_dirs, target_size=None):
    target_size = target_size or (W, H)
    asset = _resolve_asset(scene, index, asset_dirs)
    if asset:
        if asset["kind"] == "image":
            frame = tmp_dir / f"scene_{index:03d}_still.jpg"
            _fit_image(asset["path"], frame, target_size=target_size)
            clip = ImageClip(str(frame)).with_duration(duration).with_fps(fps)
            return clip, asset, []

        base = VideoFileClip(str(asset["path"]), audio=False)
        covered = _cover_video_clip(base, target_size=target_size)
        fitted = _loop_or_trim_video(covered, duration)
        fitted = _call_clip_method(fitted, ("with_fps", "set_fps"), fps)
        return fitted, asset, [base]

    raw_frame = tmp_dir / f"scene_{index:03d}_generated_raw.jpg"
    frame = tmp_dir / f"scene_{index:03d}_generated.jpg"
    _draw_generated_scene(scene, title, raw_frame)
    _fit_image(raw_frame, frame, target_size=target_size)
    clip = ImageClip(str(frame)).with_duration(duration).with_fps(fps)
    return clip, {"kind": "generated", "path": frame}, []


def render_draft_video(timeline, narration_path, output_path, fps=24, progress_cb=None):
    """Render a narration-aligned draft MP4 and return output_path."""
    narration_path = Path(narration_path)
    output_path = Path(output_path)
    scenes = timeline.get("scenes") or []
    if not scenes:
        raise ValueError("Timeline has no scenes.")
    if not narration_path.exists():
        raise FileNotFoundError(f"Narration audio not found: {narration_path}")

    asset_dirs = []
    for raw in timeline.get("asset_directories") or []:
        try:
            asset_dirs.append(Path(raw))
        except (TypeError, ValueError):
            continue

    tmp_dir = Path(tempfile.mkdtemp(prefix="ghostline_video_"))
    target_size = _target_size(timeline.get("aspect"))
    clips = []
    extras = []
    audio = None
    final = None
    try:
        title = timeline.get("source_plan_title") or timeline.get("title") or "Ghostline video"
        total = len(scenes)
        for index, scene in enumerate(scenes, start=1):
            duration = max(0.2, float(scene.get("duration_seconds") or 1.0))
            clip, asset, opened = _build_scene_clip(
                scene,
                title,
                index,
                duration,
                tmp_dir,
                fps,
                asset_dirs,
                target_size=target_size,
            )
            clips.append(clip)
            extras.extend(opened)
            if progress_cb:
                if asset["kind"] == "video":
                    progress_cb(f"Using motion clip for scene {index}/{total}: {asset['path'].name}")
                elif asset["kind"] == "image":
                    progress_cb(f"Using still image for scene {index}/{total}: {asset['path'].name}")
                else:
                    progress_cb(f"No asset for scene {index}/{total}; generating fallback still")

        if progress_cb:
            progress_cb("Combining visual scenes")
        final = concatenate_videoclips(clips, method="compose")
        audio = AudioFileClip(str(narration_path))
        final = final.with_audio(audio)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if progress_cb:
            progress_cb("Encoding MP4")
        final.write_videofile(
            str(output_path),
            fps=fps,
            codec="libx264",
            audio_codec="aac",
            preset="medium",
            threads=4,
            logger=None,
        )
        return output_path
    finally:
        for clip in clips:
            try:
                clip.close()
            except Exception:
                pass
        for clip in extras:
            try:
                clip.close()
            except Exception:
                pass
        if audio:
            audio.close()
        if final:
            final.close()
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _caption_phrase_weight(text):
    words = re.findall(r"\b[\w'-]+\b", text or "")
    if not words:
        return 0.1
    # Approximate spoken time: short words pass quickly, long words take longer,
    # and punctuation creates small pauses. The values are normalized later to
    # the exact audio duration, so they only need to be relative.
    char_cost = sum(max(3, len(w)) for w in words) * 0.018
    word_cost = len(words) * 0.23
    pause_cost = 0.0
    pause_cost += len(re.findall(r"[,:;]", text or "")) * 0.12
    pause_cost += len(re.findall(r"[.!?]", text or "")) * 0.28
    return max(0.18, char_cost + word_cost + pause_cost)


def _caption_chunks_from_segments(segments, max_words=5):
    """Build caption display chunks from pre-timed Kokoro TTS segments.

    `segments` is a list of {"text", "start", "duration"} dicts straight from
    tts.synthesize_with_timing(). Each segment is one Kokoro-internal
    pronunciation unit (typically a sentence), with start/duration measured
    directly from the synthesized audio.

    For short segments (single short sentence), emit one caption screen
    spanning the full segment. For longer segments, sub-chunk into ~max_words
    word screens and linearly distribute their start times within the
    segment's known time window — that keeps individual screens readable
    without breaking sync at segment boundaries.

    Result has the same shape as _caption_chunks(): list of
    {"text", "start", "duration"} ready for the video assembler to
    rasterize and timeline.
    """
    if not segments:
        return []

    out = []
    for seg in segments:
        text = re.sub(r"\s+", " ", (seg.get("text") or "")).strip()
        if not text:
            continue
        seg_start = float(seg.get("start", 0.0))
        seg_duration = float(seg.get("duration", 0.0))
        if seg_duration <= 0:
            continue

        words = text.split()
        if len(words) <= max_words:
            # Short segment: one caption screen, exact segment timing.
            out.append({
                "text": text,
                "start": max(0.0, seg_start),
                "duration": max(0.18, seg_duration),
            })
            continue

        # Longer segment: split into sub-chunks. Use the same break logic as
        # _caption_chunks (sentence-end / soft-pause / max_words) but constrained
        # to this segment's text only.
        sub_chunks = []
        current = []
        for w in words:
            current.append(w)
            sentence_end = bool(re.search(r"[.!?][\"')\]]?$", w))
            soft_pause = bool(re.search(r"[,;:][\"')\]]?$", w))
            if (len(current) >= max_words
                    or (sentence_end and len(current) >= 2)
                    or (soft_pause and len(current) >= 4)):
                sub_chunks.append(" ".join(current))
                current = []
        if current:
            sub_chunks.append(" ".join(current))
        if not sub_chunks:
            continue

        # Distribute sub-chunk durations weighted by character count so a
        # 2-word sub-chunk doesn't get the same screen time as a 5-word one.
        weights = [max(1, len(c)) for c in sub_chunks]
        total_w = sum(weights) or 1
        cursor = seg_start
        for j, (chunk, w) in enumerate(zip(sub_chunks, weights)):
            if j == len(sub_chunks) - 1:
                # Last sub-chunk takes the remainder so totals match exactly
                # — sub-second floor errors don't accumulate across segments.
                chunk_dur = max(0.18, (seg_start + seg_duration) - cursor)
            else:
                chunk_dur = max(0.30, seg_duration * (w / total_w))
            out.append({
                "text": chunk,
                "start": max(0.0, cursor),
                "duration": chunk_dur,
            })
            cursor += chunk_dur

    return out


def _caption_chunks(text, duration, max_words=5):
    cleaned = re.sub(r"\s+", " ", text or "").strip()
    if not cleaned or duration <= 0:
        return []

    words = cleaned.split()
    chunks = []
    current = []
    for word in words:
        current.append(word)
        sentence_end = bool(re.search(r"[.!?][\"')\]]?$", word))
        soft_pause = bool(re.search(r"[,;:][\"')\]]?$", word))
        if len(current) >= max_words or (sentence_end and len(current) >= 2) or (soft_pause and len(current) >= 4):
            chunks.append(" ".join(current))
            current = []
    if current:
        chunks.append(" ".join(current))
    if not chunks:
        return []

    weights = [_caption_phrase_weight(chunk) for chunk in chunks]
    total_weight = sum(weights) or 1.0
    raw_durations = [duration * (weight / total_weight) for weight in weights]

    # Keep captions readable without letting minimums extend past the audio.
    min_read = 0.52 if duration / max(1, len(chunks)) < 0.8 else 0.7
    adjusted = [max(min_read, d) for d in raw_durations]
    adjusted_total = sum(adjusted)
    if adjusted_total > duration:
        scale = duration / adjusted_total
        adjusted = [max(0.18, d * scale) for d in adjusted]

    result = []
    cursor = 0.0
    for index, (chunk, chunk_duration) in enumerate(zip(chunks, adjusted)):
        if index == len(chunks) - 1:
            chunk_duration = max(0.18, duration - cursor)
        result.append({
            "text": chunk,
            "start": max(0.0, cursor),
            "duration": max(0.18, chunk_duration),
        })
        cursor += chunk_duration
    return result


def _caption_palette(style):
    style = (style or "tiktok").strip().lower()
    palettes = {
        "clean": {"box": (0, 0, 0, 150), "text": (255, 255, 255, 255), "accent": (238, 238, 238, 255), "stroke": (0, 0, 0, 220)},
        "horror": {"box": (10, 0, 0, 185), "text": (250, 244, 235, 255), "accent": (225, 67, 67, 255), "stroke": (0, 0, 0, 240)},
        "kids": {"box": (25, 25, 35, 150), "text": (255, 255, 255, 255), "accent": (255, 211, 64, 255), "stroke": (42, 35, 78, 240)},
        "documentary": {"box": (0, 0, 0, 165), "text": (250, 246, 235, 255), "accent": (212, 162, 62, 255), "stroke": (0, 0, 0, 230)},
        "tiktok": {"box": (0, 0, 0, 170), "text": (255, 255, 255, 255), "accent": (255, 221, 64, 255), "stroke": (0, 0, 0, 245)},
    }
    return palettes.get(style, palettes["tiktok"])


def _is_keyword(word, mode="auto"):
    mode = (mode or "auto").strip().lower()
    if mode == "off":
        return False
    cleaned = "".join(ch for ch in word.lower() if ch.isalnum() or ch == "'")
    strong = {
        "never", "always", "first", "mistake", "secret", "danger", "lost", "money",
        "survive", "survival", "stop", "warning", "before", "after", "why", "how",
        "most", "worst", "best", "only", "hidden", "scam", "safe", "avoid",
    }
    if cleaned in strong:
        return True
    return mode == "auto" and len(cleaned) >= 8


def _draw_centered_rich_line(draw, line, font, center_x, y, palette, keyword_mode, stroke_width=3):
    parts = line.split(" ")
    widths = []
    space_w = draw.textlength(" ", font=font)
    total = 0
    for part in parts:
        w = draw.textlength(part, font=font)
        widths.append(w)
        total += w
    total += max(0, len(parts) - 1) * space_w
    x = center_x - total / 2
    for part, width in zip(parts, widths):
        fill = palette["accent"] if _is_keyword(part, keyword_mode) else palette["text"]
        draw.text((int(x), y), part, font=font, fill=fill,
                  stroke_width=stroke_width, stroke_fill=palette["stroke"])
        x += width + space_w


def _draw_caption_frame(text, out_path, target_size, style="tiktok", keyword_mode="auto"):
    target_w, target_h = target_size
    is_vertical = target_h > target_w
    img = Image.new("RGBA", target_size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    palette = _caption_palette(style)
    if is_vertical:
        font_scale = 0.047 if style in ("tiktok", "kids") else 0.041
    else:
        font_scale = 0.052 if style in ("tiktok", "kids") else 0.044
    font_size = max(42, int(target_h * font_scale))
    font = _font(font_size, bold=True)
    if is_vertical:
        left_safe = int(target_w * 0.055)
        right_safe = int(target_w * 0.18)
        safe_w = target_w - left_safe - right_safe
        max_width = int(safe_w * 0.96)
        line_limit = 2
    else:
        left_safe = int(target_w * 0.08)
        right_safe = int(target_w * 0.08)
        safe_w = target_w - left_safe - right_safe
        max_width = int(safe_w * 0.98)
        line_limit = 3
    lines = _wrap(draw, text.upper() if style in ("tiktok", "kids") else text, font, max_width)[:line_limit]
    if not lines:
        img.save(out_path)
        return
    line_h = int(font_size * 1.22)
    pad_x = int(target_w * 0.035)
    pad_y = int(target_h * 0.018)
    box_w = max(draw.textbbox((0, 0), line, font=font)[2] for line in lines) + pad_x * 2
    box_h = len(lines) * line_h + pad_y * 2
    box_w = min(box_w, safe_w)
    center_x = left_safe + safe_w / 2
    x1 = int(left_safe + (safe_w - box_w) / 2)
    x1 = max(left_safe, min(x1, target_w - right_safe - box_w))
    # Caption vertical anchor.
    # Vertical (9:16): place block lower-third — above platform UI strips
    # (TikTok ~18% from bottom, Reels ~22%, Shorts ~18%) but below the
    # visual focal point. Previous 0.56 (effectively center) collided with
    # subjects in shot.
    # Horizontal (16:9): 0.78 works since there's no platform UI overlay.
    y1 = int(target_h * (0.66 if is_vertical else 0.78))
    # bottom_safe is the lowest the caption block bottom edge is allowed to
    # reach. 0.80 keeps captions clear of the bottom 20% strip on TikTok /
    # Shorts / Reels (they each reserve 18-22% for description + CTAs).
    bottom_safe = int(target_h * (0.80 if is_vertical else 0.93))
    y1 = max(int(target_h * 0.40), min(y1, bottom_safe - box_h))
    x2 = x1 + box_w
    y2 = y1 + box_h
    draw.rounded_rectangle((x1, y1, x2, y2), radius=18, fill=palette["box"])
    y = y1 + pad_y
    for line in lines:
        _draw_centered_rich_line(draw, line, font, center_x, y, palette, keyword_mode)
        y += line_h
    img.save(out_path)


def _draw_title_frame(text, out_path, target_size):
    target_w, target_h = target_size
    img = Image.new("RGBA", target_size, (0, 0, 0, 0))
    text = (text or "").strip()
    if not text:
        img.save(out_path)
        return
    draw = ImageDraw.Draw(img)
    font_size = max(38, int(target_h * 0.038))
    font = _font(font_size, bold=True)
    max_width = int(target_w * 0.82)
    lines = _wrap(draw, text, font, max_width)[:2]
    if not lines:
        img.save(out_path)
        return
    line_h = int(font_size * 1.18)
    pad_x = int(target_w * 0.035)
    pad_y = int(target_h * 0.014)
    box_w = max(draw.textbbox((0, 0), line, font=font)[2] for line in lines) + pad_x * 2
    box_h = len(lines) * line_h + pad_y * 2
    x1 = int((target_w - box_w) / 2)
    y1 = int(target_h * 0.20)
    x2 = x1 + box_w
    y2 = y1 + box_h
    draw.rounded_rectangle((x1, y1, x2, y2), radius=18, fill=(0, 0, 0, 135))
    y = y1 + pad_y
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font, stroke_width=3)
        tw = bbox[2] - bbox[0]
        x = int((target_w - tw) / 2)
        draw.text((x, y), line, font=font, fill=(255, 255, 255, 245),
                  stroke_width=3, stroke_fill=(0, 0, 0, 210))
        y += line_h
    img.save(out_path)


def _apply_source_enhance(clip, mode):
    mode = (mode or "none").strip().lower()
    if mode == "none":
        return clip

    def adjust(frame):
        img = Image.fromarray(frame).convert("RGB")
        if mode == "vivid":
            img = ImageOps.autocontrast(img, cutoff=1)
            r, g, b = img.split()
            r = r.point(lambda v: min(255, int(v * 1.05 + 3)))
            g = g.point(lambda v: min(255, int(v * 1.04 + 2)))
            b = b.point(lambda v: min(255, int(v * 1.08 + 4)))
            img = Image.merge("RGB", (r, g, b))
        elif mode == "warm":
            r, g, b = img.split()
            r = r.point(lambda v: min(255, int(v * 1.08 + 5)))
            g = g.point(lambda v: min(255, int(v * 1.03 + 2)))
            b = b.point(lambda v: max(0, int(v * 0.95)))
            img = Image.merge("RGB", (r, g, b))
        elif mode == "dark":
            img = ImageOps.autocontrast(img, cutoff=2)
            r, g, b = img.split()
            r = r.point(lambda v: max(0, int(v * 0.88)))
            g = g.point(lambda v: max(0, int(v * 0.92)))
            b = b.point(lambda v: min(255, int(v * 1.05 + 4)))
            img = Image.merge("RGB", (r, g, b))
        return np.array(img)

    return clip.image_transform(adjust)


def _pattern_interrupt_clips(duration, target_size):
    clips = []
    if duration <= 4:
        return clips
    flash = ColorClip(size=target_size, color=(255, 255, 255)).with_opacity(0.16)
    t = 3.0
    while t < duration - 0.5:
        clips.append(flash.with_start(t).with_duration(0.08))
        t += 4.5
    return clips


def render_source_video(source_video_path, narration_path, output_path, *,
                        caption_text="", title_text="", captions=False, aspect="9:16",
                        fit="cover", fps=30, caption_style="tiktok",
                        keyword_mode="auto", pattern_interrupts=False,
                        source_enhance="none", title_style="top", progress_cb=None,
                        caption_segments=None):
    """Render an uploaded/source video with narration audio and optional captions.

    caption_segments: optional list of {"text", "start", "duration"} dicts with
    PRE-MEASURED timings (typically straight from tts.synthesize_with_timing).
    When supplied, these are used as the ground truth for caption timing so
    captions stay locked to the audio at segment boundaries — this is the
    precise sync path. When None, captions fall back to the heuristic
    proportional-distribution path which can drift over long narrations."""
    source_video_path = Path(source_video_path)
    narration_path = Path(narration_path)
    output_path = Path(output_path)
    if not source_video_path.exists():
        raise FileNotFoundError(f"Source video not found: {source_video_path}")
    if not narration_path.exists():
        raise FileNotFoundError(f"Narration audio not found: {narration_path}")

    target_size = _target_size(aspect)
    tmp_dir = Path(tempfile.mkdtemp(prefix="ghostline_overlay_"))
    base = None
    fitted = None
    visual = None
    audio = None
    final = None
    caption_clips = []
    try:
        if progress_cb:
            progress_cb("Opening uploaded video")
        base = VideoFileClip(str(source_video_path), audio=False)
        audio = AudioFileClip(str(narration_path))
        duration = float(getattr(audio, "duration", 0) or getattr(base, "duration", 0) or 1.0)
        if progress_cb:
            progress_cb("Fitting uploaded video to selected ratio")
        if (fit or "cover") == "contain":
            size = getattr(base, "size", target_size)
            scale = min(target_size[0] / float(size[0] or 1), target_size[1] / float(size[1] or 1))
            fitted = _call_clip_method(base, ("resized", "resize"),
                                       new_size=(max(1, int(size[0] * scale)), max(1, int(size[1] * scale))))
            bg_path = tmp_dir / "background.jpg"
            Image.new("RGB", target_size, (0, 0, 0)).save(bg_path)
            bg = ImageClip(str(bg_path)).with_duration(duration).with_fps(fps)
            visual = CompositeVideoClip([bg, fitted.with_position("center")], size=target_size)
            caption_clips.append(bg)
        else:
            covered = _cover_video_clip(base, target_size=target_size)
            visual = _loop_or_trim_video(covered, duration)

        if source_enhance and source_enhance != "none":
            if progress_cb:
                progress_cb("Enhancing source video")
            visual = _apply_source_enhance(visual, source_enhance)
        visual = _call_clip_method(visual, ("with_fps", "set_fps"), fps)
        layers = [visual]
        if title_text.strip() and title_style != "none":
            if progress_cb:
                progress_cb("Adding title overlay")
            title_frame = tmp_dir / "title_overlay.png"
            _draw_title_frame(title_text, title_frame, target_size)
            # Title overlay persists for the full video length so it acts as a
            # standing brand/topic anchor — works as the always-visible header
            # on a Shorts-style render. Previously capped at 4s which made
            # discoverability much weaker (viewer scrolling past at second 5
            # had no context for what they were watching).
            title_clip = ImageClip(str(title_frame)).with_duration(duration)
            layers.append(title_clip)
            caption_clips.append(title_clip)
        if pattern_interrupts:
            if progress_cb:
                progress_cb("Adding pattern interrupts")
            interrupts = _pattern_interrupt_clips(duration, target_size)
            layers.extend(interrupts)
            caption_clips.extend(interrupts)
        if captions and (caption_text.strip() or caption_segments):
            if progress_cb:
                progress_cb("Building captions")
            # Prefer pre-measured segments if the caller passed them in. That's
            # the precise-sync path: each chunk's start time is anchored to the
            # exact wall-clock time Kokoro spent on the corresponding text.
            # Fallback path (heuristic proportional distribution from raw text)
            # remains for callers that don't have timing info — e.g. when the
            # narration audio came from a non-Phantomline source.
            if caption_segments:
                chunks_iter = _caption_chunks_from_segments(caption_segments)
            else:
                chunks_iter = _caption_chunks(caption_text, duration)
            for idx, chunk in enumerate(chunks_iter, start=1):
                frame = tmp_dir / f"caption_{idx:04d}.png"
                _draw_caption_frame(chunk["text"], frame, target_size,
                                    style=caption_style, keyword_mode=keyword_mode)
                clip = (
                    ImageClip(str(frame))
                    .with_start(chunk["start"])
                    .with_duration(chunk["duration"])
                )
                layers.append(clip)
                caption_clips.append(clip)
            final = CompositeVideoClip(layers, size=target_size)
        else:
            final = visual

        final = final.with_audio(audio)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if progress_cb:
            progress_cb("Encoding MP4")
        final.write_videofile(
            str(output_path),
            fps=fps,
            codec="libx264",
            audio_codec="aac",
            preset="medium",
            threads=4,
            logger=None,
        )
        return output_path
    finally:
        for clip in caption_clips:
            try:
                clip.close()
            except Exception:
                pass
        for clip in (final, visual, fitted, base, audio):
            if clip:
                try:
                    clip.close()
                except Exception:
                    pass
        shutil.rmtree(tmp_dir, ignore_errors=True)

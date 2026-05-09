"""One-off: render the 'The statue started crying' reddit-scary demo using
Phantomline's Kokoro TTS + video_assembler.render_source_video.

Pipeline this script runs (same code paths the studio uses):
  1. tts.synthesize(...) on the scary-story text using bm_george (calm
     British male — classic horror narrator register).
  2. soundfile.write the resulting waveform to output/_demo_narration.wav.
  3. video_assembler.render_source_video(...) with:
       - source_video_path = _footage_compressed/demo_06.mp4
       - narration_path    = the .wav we just wrote
       - title persistent for full duration (post-patch)
       - captions in the lower-third safe zone (post-patch)
"""
from __future__ import annotations

import sys
from pathlib import Path

import soundfile as sf

import tts
import video_assembler

ROOT = Path(__file__).resolve().parent

SOURCE = ROOT / "_footage_compressed" / "demo_06.mp4"
NARRATION_WAV = ROOT / "output" / "_demo_narration.wav"
OUT = ROOT / "output" / "demo_the_statue_started_crying.mp4"

TITLE = "The statue started crying"

# Reddit-scary script — surreal/architectural horror, paired with demo_06's
# blue-tile + classical-sculpture + reaching-marble-hand visual.
# Target ~60-70s of narration at typical Kokoro pace (~135 wpm).
SCRIPT = """\
I work nights at a museum.

Last Tuesday, the Roman statue in Gallery Three started crying.

Not condensation. Not a leak. Real water. From her marble eyes. Pooling in the corner.

By Wednesday, the puddle was four feet wide. Mosaic tiles I never installed had appeared on the wall behind her. Blue. Like deep water.

By Friday, the gallery was longer than it should have been. I walked it. Counted my steps. It took twice as long to get to the back wall.

There were towers there. Brick towers, taller than the building itself. Through windows that hadn't been there a week ago.

Last night, I saw a hand. White. Marble. Reaching out from one of the towers. Toward me.

I'm typing this from my car. The parking lot is gone.

The towers are everywhere now.

She's still crying.
"""

# bm_george: British male, calm, measured — the standard horror narrator
# register (think Lazy Masquerade / Mr. Nightmare-adjacent). Slowed slightly
# from default speed because horror narration reads better with breath.
VOICE = "bm_george"
SPEED = 0.92

# Captions follow the same script as narration, in the same order. The
# video_assembler segments + times this against the audio automatically.
CAPTION_TEXT = SCRIPT


def main() -> int:
    if not SOURCE.exists():
        print(f"[error] source video not found: {SOURCE}")
        return 1
    OUT.parent.mkdir(parents=True, exist_ok=True)

    print(f"[demo] source: {SOURCE.name}")
    print(f"[demo] script: {len(SCRIPT.split())} words")
    print(f"[demo] voice: {VOICE} @ speed={SPEED}")
    print(f"[demo] output: {OUT}")
    print()

    # 1. Generate narration audio via Kokoro TTS, capturing per-segment
    # timing so captions can lock to the audio at segment boundaries
    # (precise-sync path — eliminates the drift that pure-text caption
    # timing has over a 60s narration).
    print("[tts] synthesising narration with per-segment timing...")
    audio, sr, tts_segments = tts.synthesize_with_timing(
        SCRIPT.strip(),
        voice=VOICE,
        speed=SPEED,
        progress_cb=lambda p: print(f"   segment {p['segment']}: {p['chars_done']}/{p['chars_total']} chars"),
    )
    if audio is None or len(audio) == 0:
        print("[error] TTS produced empty audio")
        return 1
    duration_sec = len(audio) / sr
    print(f"[tts] {duration_sec:.1f}s of narration at {sr} Hz")
    print(f"[tts] {len(tts_segments)} timed segments captured for caption sync")

    # 2. Write narration to disk so video_assembler can read it back.
    sf.write(str(NARRATION_WAV), audio, sr, subtype="PCM_16")
    print(f"[tts] wrote {NARRATION_WAV} ({NARRATION_WAV.stat().st_size/1024/1024:.1f} MB)")
    print()

    # 3. Render the video.
    def progress(msg: str) -> None:
        print(f"  -> {msg}")

    print("[render] starting video_assembler.render_source_video")
    try:
        video_assembler.render_source_video(
            source_video_path=SOURCE,
            narration_path=NARRATION_WAV,
            output_path=OUT,
            caption_text=CAPTION_TEXT.strip(),
            caption_segments=tts_segments,   # precise-sync path
            title_text=TITLE,
            captions=True,
            aspect="9:16",
            fit="cover",
            fps=30,
            caption_style="horror",     # darker palette, red accent (matches horror preset)
            pattern_interrupts=False,
            source_enhance="none",
            title_style="top",
            progress_cb=progress,
        )
    except Exception as exc:
        print(f"[error] render failed: {exc}")
        import traceback
        traceback.print_exc()
        return 1

    if OUT.exists():
        size_mb = OUT.stat().st_size / 1024 / 1024
        print(f"\n[done] {OUT}  ({size_mb:.1f} MB)")
        return 0
    print("[error] render finished but output file missing")
    return 1


if __name__ == "__main__":
    sys.exit(main())

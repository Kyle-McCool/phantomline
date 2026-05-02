"""One-shot generator for the bundled royalty-free ambient music pack.

Synthesizes a set of layered drone / pad / soft-rhythm beds across mood
categories that match Ghostline's recipes (cinematic story, mystery,
horror, chill, uplifting, etc.). Output goes to static/library/music/ as
MP3s plus a manifest JSON that the mobile MusicLibrary class consumes.

Run once after editing PRESETS:

    python _generate_music_library.py

Re-running overwrites the existing files. Each track is 100% generated in
this script — no copyrighted samples, no third-party assets, owned outright.
You can ship them on the Play Store and the App Store without any
licensing footnote. Public-domain by virtue of being procedural.
"""
from __future__ import annotations

import json
import math
import struct
from pathlib import Path

import numpy as np
import lameenc


SAMPLE_RATE = 44100
ROOT = Path(__file__).resolve().parent
OUT_DIR = ROOT / "static" / "library" / "music"
MANIFEST_PATH = ROOT / "static" / "library" / "music.json"


# ---------------------------------------------------------------------------
# Synth primitives.
# ---------------------------------------------------------------------------

def sine(freq, dur, phase=0.0):
    t = np.arange(int(dur * SAMPLE_RATE)) / SAMPLE_RATE
    return np.sin(2 * np.pi * freq * t + phase).astype(np.float32)


def detuned_saw(freq, dur, voices=5, detune_cents=14):
    out = np.zeros(int(dur * SAMPLE_RATE), dtype=np.float32)
    for i in range(voices):
        cents = (i - (voices - 1) / 2) * detune_cents
        f = freq * (2 ** (cents / 1200))
        t = np.arange(int(dur * SAMPLE_RATE)) / SAMPLE_RATE
        # Naive saw: 2 * (t*f - floor(0.5 + t*f)). Soften with bandlimited hint
        # via mild lowpass-equivalent rolling average.
        s = 2 * (t * f - np.floor(0.5 + t * f))
        # Light smoothing to reduce aliasing artifacts.
        if SAMPLE_RATE > 22050:
            kernel = 4
            s = np.convolve(s, np.ones(kernel) / kernel, mode="same")
        out += s.astype(np.float32) / voices
    return out


def soft_noise(dur, lp_alpha=0.04):
    n = int(dur * SAMPLE_RATE)
    rng = np.random.default_rng()
    raw = rng.standard_normal(n).astype(np.float32) * 0.7
    # One-pole lowpass to make it pad-like.
    out = np.zeros_like(raw)
    prev = 0.0
    for i in range(n):
        prev = prev + lp_alpha * (raw[i] - prev)
        out[i] = prev
    return out


def lfo(rate_hz, dur, depth=1.0, offset=0.0):
    t = np.arange(int(dur * SAMPLE_RATE)) / SAMPLE_RATE
    return offset + depth * np.sin(2 * np.pi * rate_hz * t).astype(np.float32)


def envelope(dur, attack=2.0, release=2.0):
    n = int(dur * SAMPLE_RATE)
    env = np.ones(n, dtype=np.float32)
    a = int(attack * SAMPLE_RATE)
    r = int(release * SAMPLE_RATE)
    if a > 0:
        env[:a] = np.linspace(0, 1, a, dtype=np.float32) ** 2
    if r > 0:
        env[-r:] = np.linspace(1, 0, r, dtype=np.float32) ** 2
    return env


def lowpass(signal, cutoff_hz, q=0.7):
    # Biquad lowpass — stable and warm.
    w0 = 2 * math.pi * cutoff_hz / SAMPLE_RATE
    cosw0 = math.cos(w0)
    alpha = math.sin(w0) / (2 * q)
    b0 = (1 - cosw0) / 2
    b1 = 1 - cosw0
    b2 = (1 - cosw0) / 2
    a0 = 1 + alpha
    a1 = -2 * cosw0
    a2 = 1 - alpha
    b = np.array([b0, b1, b2]) / a0
    a = np.array([1.0, a1 / a0, a2 / a0])
    out = np.zeros_like(signal)
    x1 = x2 = y1 = y2 = 0.0
    for i, x in enumerate(signal):
        y = b[0] * x + b[1] * x1 + b[2] * x2 - a[1] * y1 - a[2] * y2
        out[i] = y
        x2, x1 = x1, x
        y2, y1 = y1, y
    return out


def soft_compress(signal, threshold=0.6, ratio=2.5):
    out = np.zeros_like(signal)
    for i, x in enumerate(signal):
        ax = abs(x)
        if ax > threshold:
            over = ax - threshold
            new_amp = threshold + over / ratio
            out[i] = math.copysign(new_amp, x)
        else:
            out[i] = x
    return out


def stereo_pair(mono, width=0.35):
    n = len(mono)
    rng = np.random.default_rng(seed=hash(mono.tobytes()) & 0xFFFF)
    haas = rng.integers(50, 500)  # Haas-style micro-delay for width
    left = mono.copy()
    right = np.concatenate([np.zeros(haas, dtype=np.float32), mono[:-haas]])
    mid = (left + right) / 2
    side = (left - right) / 2 * width
    return np.stack([mid + side, mid - side], axis=1)


def normalize_peak(signal, peak=0.85):
    m = float(np.max(np.abs(signal))) if signal.size else 0.0
    if m < 1e-6:
        return signal
    return signal * (peak / m)


# ---------------------------------------------------------------------------
# Track recipes — each preset shapes a 90-second loopable bed.
# ---------------------------------------------------------------------------

def render_cinematic(seed):
    np.random.seed(seed)
    dur = 90
    base = 55.0  # A1
    s = np.zeros(int(dur * SAMPLE_RATE), dtype=np.float32)
    s += sine(base, dur) * 0.45
    s += sine(base * 1.5, dur) * 0.18      # perfect fifth
    s += sine(base * 2.0, dur) * 0.12      # octave
    s += detuned_saw(base * 4, dur, voices=5, detune_cents=8) * 0.06
    s *= envelope(dur, attack=4.0, release=5.0)
    pulse = (1 + 0.18 * lfo(0.12, dur)) * 1.0
    s = s * pulse
    s += soft_noise(dur, lp_alpha=0.02) * 0.05
    s = lowpass(s, cutoff_hz=1800)
    return stereo_pair(normalize_peak(soft_compress(s)), width=0.35)


def render_mystery(seed):
    np.random.seed(seed)
    dur = 90
    base = 49.0
    s = np.zeros(int(dur * SAMPLE_RATE), dtype=np.float32)
    s += sine(base, dur) * 0.45
    s += sine(base * 1.5, dur) * 0.20
    s += sine(base * 2.0, dur) * 0.10
    # Minor 6th interval for unease.
    s += sine(base * (8 / 5), dur) * 0.08
    s += detuned_saw(base * 4, dur, voices=4, detune_cents=12) * 0.05
    s *= envelope(dur, attack=4.0, release=5.0)
    s = s * (1 + 0.22 * lfo(0.08, dur))
    s += soft_noise(dur, lp_alpha=0.012) * 0.07
    s = lowpass(s, cutoff_hz=1100)
    return stereo_pair(normalize_peak(soft_compress(s)), width=0.4)


def render_horror(seed):
    np.random.seed(seed)
    dur = 90
    base = 41.0
    s = np.zeros(int(dur * SAMPLE_RATE), dtype=np.float32)
    s += sine(base, dur) * 0.55
    s += sine(base * 1.06, dur) * 0.20    # detuned dissonance
    s += sine(base * 2.03, dur) * 0.10
    s += detuned_saw(base * 4, dur, voices=6, detune_cents=22) * 0.05
    s *= envelope(dur, attack=5.0, release=6.0)
    # Very slow shimmer + occasional soft swell.
    s = s * (1 + 0.30 * lfo(0.06, dur))
    s += soft_noise(dur, lp_alpha=0.008) * 0.10
    s = lowpass(s, cutoff_hz=720)
    return stereo_pair(normalize_peak(soft_compress(s, threshold=0.5)), width=0.45)


def render_uplifting(seed):
    np.random.seed(seed)
    dur = 90
    base = 65.4  # C2
    s = np.zeros(int(dur * SAMPLE_RATE), dtype=np.float32)
    # Major triad spread.
    s += sine(base, dur) * 0.40
    s += sine(base * (5 / 4), dur) * 0.22  # major 3rd
    s += sine(base * (3 / 2), dur) * 0.18  # 5th
    s += sine(base * 2.0, dur) * 0.14
    s += detuned_saw(base * 4, dur, voices=5, detune_cents=6) * 0.05
    s *= envelope(dur, attack=2.5, release=4.0)
    s = s * (1 + 0.16 * lfo(0.20, dur))
    s = lowpass(s, cutoff_hz=2200)
    return stereo_pair(normalize_peak(soft_compress(s)), width=0.30)


def render_chill(seed):
    np.random.seed(seed)
    dur = 90
    base = 110.0  # A2
    s = np.zeros(int(dur * SAMPLE_RATE), dtype=np.float32)
    s += sine(base, dur) * 0.35
    s += sine(base * (3 / 2), dur) * 0.20
    s += sine(base * 2.0, dur) * 0.16
    s += sine(base * 3.0, dur) * 0.10
    s += detuned_saw(base * 2, dur, voices=4, detune_cents=4) * 0.06
    s *= envelope(dur, attack=3.0, release=4.0)
    s = s * (1 + 0.14 * lfo(0.18, dur))
    s = lowpass(s, cutoff_hz=2000)
    return stereo_pair(normalize_peak(soft_compress(s)), width=0.28)


def render_dark(seed):
    np.random.seed(seed)
    dur = 90
    base = 36.7  # D1, very low
    s = np.zeros(int(dur * SAMPLE_RATE), dtype=np.float32)
    s += sine(base, dur) * 0.55
    s += sine(base * 1.5, dur) * 0.18
    s += sine(base * 1.06, dur) * 0.12
    s += detuned_saw(base * 4, dur, voices=6, detune_cents=18) * 0.04
    s *= envelope(dur, attack=5.0, release=6.0)
    s = s * (1 + 0.25 * lfo(0.07, dur))
    s += soft_noise(dur, lp_alpha=0.010) * 0.07
    s = lowpass(s, cutoff_hz=850)
    return stereo_pair(normalize_peak(soft_compress(s, threshold=0.55)), width=0.4)


def render_hopeful(seed):
    np.random.seed(seed)
    dur = 90
    base = 73.4  # D2
    s = np.zeros(int(dur * SAMPLE_RATE), dtype=np.float32)
    s += sine(base, dur) * 0.38
    s += sine(base * (5 / 4), dur) * 0.20
    s += sine(base * (3 / 2), dur) * 0.18
    s += sine(base * (15 / 8), dur) * 0.10
    s += sine(base * 2, dur) * 0.16
    s += detuned_saw(base * 4, dur, voices=5, detune_cents=5) * 0.05
    s *= envelope(dur, attack=2.5, release=4.0)
    s = s * (1 + 0.18 * lfo(0.16, dur))
    s = lowpass(s, cutoff_hz=2400)
    return stereo_pair(normalize_peak(soft_compress(s)), width=0.32)


def render_tense(seed):
    np.random.seed(seed)
    dur = 90
    base = 58.27  # B♭1
    s = np.zeros(int(dur * SAMPLE_RATE), dtype=np.float32)
    s += sine(base, dur) * 0.45
    s += sine(base * 1.4142, dur) * 0.16   # tritone for tension
    s += sine(base * 2.0, dur) * 0.12
    s += detuned_saw(base * 4, dur, voices=5, detune_cents=15) * 0.06
    s *= envelope(dur, attack=3.5, release=5.0)
    s = s * (1 + 0.30 * lfo(0.11, dur))
    s += soft_noise(dur, lp_alpha=0.014) * 0.06
    s = lowpass(s, cutoff_hz=1300)
    return stereo_pair(normalize_peak(soft_compress(s)), width=0.40)


PRESETS = [
    ("cinematic", "Cinematic Drift",   "Slow swelling sci-fi opener. Pairs with wonder, exploration, tech.", render_cinematic),
    ("mystery",   "Quiet Investigation", "Low-Q minor pad with subtle dissonance. Detective, theory, doc.", render_mystery),
    ("horror",    "The Hollow",        "Sub-heavy slow rumble with slow swells. Rule horror, ghost story.", render_horror),
    ("uplifting", "First Light",       "Major-triad pad with gentle bloom. Tutorial, achievement, pep.",   render_uplifting),
    ("chill",     "Long Drive",        "Mid-range warm bed with soft motion. Lifestyle, vlog, education.", render_chill),
    ("dark",      "Beneath the Floor", "Very low minor drone for dread. True crime, rule-based horror.",  render_dark),
    ("hopeful",   "Open Window",       "Bright major bed with airy harmonics. Story finale, motivation.",  render_hopeful),
    ("tense",     "Inevitable",        "Tritone-laced rising bed. Climax, reveal, breaking-point story.",  render_tense),
]


# ---------------------------------------------------------------------------
# MP3 encoder.
# ---------------------------------------------------------------------------

def encode_mp3(stereo_f32, bitrate_kbps=128):
    """Encode interleaved stereo float32 [-1, 1] to MP3 bytes via lameenc."""
    int16 = np.clip(stereo_f32, -1, 1) * 32767.0
    int16 = int16.astype(np.int16)
    # Interleave channels: lameenc wants [L, R, L, R, ...].
    interleaved = int16.flatten().tobytes()
    enc = lameenc.Encoder()
    enc.set_bit_rate(bitrate_kbps)
    enc.set_in_sample_rate(SAMPLE_RATE)
    enc.set_channels(2)
    enc.set_quality(2)  # 2 = highest, 7 = fastest
    out = enc.encode(interleaved)
    out += enc.flush()
    return out


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest = []
    total_bytes = 0
    for idx, (mood, title, desc, render_fn) in enumerate(PRESETS):
        print(f"  {idx + 1}/{len(PRESETS)}  {mood:11s}  {title}")
        stereo = render_fn(seed=1337 + idx)
        mp3 = encode_mp3(stereo, bitrate_kbps=128)
        filename = f"{mood}.mp3"
        path = OUT_DIR / filename
        path.write_bytes(mp3)
        size = len(mp3)
        total_bytes += size
        manifest.append({
            "id": mood,
            "title": title,
            "mood": mood,
            "description": desc,
            "duration_seconds": 90,
            "url": f"/static/library/music/{filename}",
            "license": "Procedurally generated by Ghostline. Public domain — ship it anywhere.",
            "size_bytes": size,
        })
    MANIFEST_PATH.write_text(json.dumps({
        "version": 1,
        "generator": "_generate_music_library.py",
        "tracks": manifest,
    }, indent=2), encoding="utf-8")
    print(f"\nWrote {len(manifest)} tracks ({total_bytes / 1024 / 1024:.1f} MB total) + manifest.")


if __name__ == "__main__":
    main()

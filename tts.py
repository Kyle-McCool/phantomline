"""
Local TTS using Kokoro (https://github.com/hexgrad/kokoro).

Synthesizes long narration to mono 24 kHz audio and encodes to MP3
(via lameenc) or WAV (built-in). Pipelines are lazy-loaded and cached
so we don't pay model-load cost on import.
"""

import io
import re
import threading
import wave

import numpy as np


# (voice_id, friendly_label, lang_code, gender)
# Lang code 'a' = American English, 'b' = British English in Kokoro.
VOICES = [
    ("af_heart",    "Heart - American female, warm",         "a", "F"),
    ("af_nicole",   "Nicole - American female, calm",        "a", "F"),
    ("af_bella",    "Bella - American female, smooth",       "a", "F"),
    ("af_sarah",    "Sarah - American female, gentle",       "a", "F"),
    ("af_nova",     "Nova - American female, bright",        "a", "F"),
    ("af_sky",      "Sky - American female, light",          "a", "F"),
    ("am_michael",  "Michael - American male, calm",         "a", "M"),
    ("am_fenrir",   "Fenrir - American male, deep",          "a", "M"),
    ("am_adam",     "Adam - American male, neutral",         "a", "M"),
    ("am_onyx",     "Onyx - American male, smooth",          "a", "M"),
    ("bf_emma",     "Emma - British female, warm",           "b", "F"),
    ("bf_isabella", "Isabella - British female, smooth",     "b", "F"),
    ("bf_alice",    "Alice - British female, gentle",        "b", "F"),
    ("bm_george",   "George - British male, calm",           "b", "M"),
    ("bm_lewis",    "Lewis - British male, deep",            "b", "M"),
    ("bm_daniel",   "Daniel - British male, neutral",        "b", "M"),
]

# voice_id -> lang_code lookup
LANG_BY_VOICE = {v[0]: v[2] for v in VOICES}

_pipeline_cache = {}
_pipeline_lock = threading.Lock()


def get_pipeline(lang_code):
    """Lazy-load and cache a Kokoro pipeline per language code."""
    with _pipeline_lock:
        if lang_code not in _pipeline_cache:
            from kokoro import KPipeline  # heavy import; do it on demand
            _pipeline_cache[lang_code] = KPipeline(lang_code=lang_code)
        return _pipeline_cache[lang_code]


def strip_title_prefix(text):
    """Remove a leading Ghostline TITLE line from text before narration."""
    return re.sub(r"^\s*TITLE:\s*[^\r\n]*(?:\r?\n)+", "", text, count=1).strip()


def _to_numpy(audio):
    """Kokoro yields torch tensors; convert to a 1-D float32 numpy array."""
    try:
        return audio.detach().cpu().numpy().astype(np.float32, copy=False)
    except AttributeError:
        return np.asarray(audio, dtype=np.float32)


def synthesize(text, voice="af_heart", speed=1.0, progress_cb=None):
    """
    Run text through Kokoro and return (mono float32 audio, sample_rate).

    progress_cb is invoked once per generated segment with:
        {"segment": int, "chars_done": int, "chars_total": int}

    Use synthesize_with_timing() if you also need per-segment text + start +
    duration metadata for caption-audio sync.
    """
    audio, sr, _segments = synthesize_with_timing(text, voice=voice, speed=speed, progress_cb=progress_cb)
    return audio, sr


def synthesize_with_timing(text, voice="af_heart", speed=1.0, progress_cb=None):
    """Like synthesize(), but ALSO returns per-segment timing metadata captured
    directly from Kokoro's internal segmentation. Returns:

        (mono float32 audio, sample_rate, segments)

    where `segments` is a list of dicts:

        [
            {"text": "I work nights at a museum.", "start": 0.0, "duration": 1.83},
            {"text": "Last Tuesday, ...",          "start": 1.83, "duration": 3.27},
            ...
        ]

    Each segment's `duration` is measured directly from the audio array Kokoro
    yielded for that text — so caption sync downstream is exact at segment
    boundaries (typically sentence-level), eliminating the proportional-
    distribution drift that pure-text caption timing has.

    Within a segment, captions can still sub-chunk the text for short readable
    screens — sub-chunk start times are then linearly interpolated within the
    segment's exact start/duration window. Sub-second drift only, bounded by
    segment length, vs the unbounded drift the old text-only path could hit
    over a 60-second narration.
    """
    text = (text or "").strip()
    if not text:
        return np.zeros(0, dtype=np.float32), 24000, []

    lang = LANG_BY_VOICE.get(voice, "a")
    pipeline = get_pipeline(lang)

    chunks = []
    segments = []
    total_chars = max(1, len(text))
    chars_done = 0
    cursor_seconds = 0.0
    sample_rate = 24000

    generator = pipeline(text, voice=voice, speed=speed)
    for i, (graphemes, _phonemes, audio) in enumerate(generator):
        arr = _to_numpy(audio)
        if arr.ndim > 1:
            arr = arr.mean(axis=-1)
        chunks.append(arr)

        # Measured duration straight from the audio array length. This is the
        # ground-truth wall-clock time Kokoro spent on this text segment.
        segment_duration = float(len(arr)) / float(sample_rate) if len(arr) else 0.0
        segment_text = (graphemes or "").strip()
        if segment_text:
            segments.append({
                "text": segment_text,
                "start": cursor_seconds,
                "duration": segment_duration,
            })
        cursor_seconds += segment_duration

        if graphemes:
            chars_done += len(graphemes)
        if progress_cb:
            progress_cb({
                "segment": i + 1,
                "chars_done": min(chars_done, total_chars),
                "chars_total": total_chars,
            })

    if not chunks:
        return np.zeros(0, dtype=np.float32), sample_rate, []

    audio_concat = np.concatenate(chunks).astype(np.float32, copy=False)
    return audio_concat, sample_rate, segments


def to_wav_bytes(audio_f32, sample_rate):
    """Encode mono float32 audio to a 16-bit PCM WAV in memory."""
    int16 = (np.clip(audio_f32, -1.0, 1.0) * 32767.0).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(int(sample_rate))
        wf.writeframes(int16.tobytes())
    return buf.getvalue()


def to_mp3_bytes(audio_f32, sample_rate, bitrate_kbps=96):
    """
    Encode mono float32 audio to MP3 bytes using the lameenc Python wheel.
    Raises ImportError if lameenc is not installed - caller should fall back to WAV.
    """
    import lameenc  # optional dependency
    int16 = (np.clip(audio_f32, -1.0, 1.0) * 32767.0).astype(np.int16)
    enc = lameenc.Encoder()
    enc.set_bit_rate(int(bitrate_kbps))
    enc.set_in_sample_rate(int(sample_rate))
    enc.set_channels(1)
    enc.set_quality(2)  # 2 = high; 0 = best (slower)
    out = enc.encode(int16.tobytes())
    out += enc.flush()
    return out


def encode(audio_f32, sample_rate, fmt="mp3"):
    """
    Encode audio to the requested format. Returns (bytes, file_extension).
    Falls back to WAV if MP3 was requested but lameenc isn't installed.
    """
    fmt = (fmt or "mp3").lower()
    if fmt == "mp3":
        try:
            return to_mp3_bytes(audio_f32, sample_rate), "mp3"
        except ImportError:
            return to_wav_bytes(audio_f32, sample_rate), "wav"
    return to_wav_bytes(audio_f32, sample_rate), "wav"

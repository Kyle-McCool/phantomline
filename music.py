"""
Local music generation + crossfade looping + narration mixdown.

Generates a short ambient bed with Meta's MusicGen (via HuggingFace
transformers - sidesteps audiocraft's spacy/thinc build issues on
Python 3.12), crossfade-loops it to any target length (good for
hour-long bedtime videos), and optionally mixes with a Kokoro
narration MP3 to produce a single upload-ready file.
"""

import threading

import numpy as np

import tts as tts_mod  # reuse our WAV/MP3 encoders


# MusicGen's audio decoder runs at 32kHz and emits ~50 tokens per second.
MUSICGEN_SR = 32000
MUSICGEN_TOKENS_PER_SEC = 50

_model_cache = {}
_model_lock = threading.Lock()


def get_model(size="small"):
    """Lazy-load and cache a MusicGen model + processor.

    size ∈ {small, medium, large}. Returns (processor, model).
    """
    with _model_lock:
        if size not in _model_cache:
            from transformers import (
                AutoProcessor,
                MusicgenForConditionalGeneration,
            )
            model_id = f"facebook/musicgen-{size}"
            processor = AutoProcessor.from_pretrained(model_id)
            model = MusicgenForConditionalGeneration.from_pretrained(model_id)
            try:
                import torch
                if torch.cuda.is_available():
                    model = model.to("cuda")
            except Exception:
                pass
            _model_cache[size] = (processor, model)
        return _model_cache[size]


def generate_clip(prompt, duration_seconds=30, model_size="small", progress_cb=None):
    """Generate a single MusicGen clip and return (mono float32, sample_rate)."""
    if progress_cb:
        progress_cb({"event": "status", "message": f"Loading MusicGen-{model_size} (first run downloads ~1.5 GB)..."})
    processor, model = get_model(model_size)

    duration = max(5, min(30, int(duration_seconds)))
    max_new_tokens = duration * MUSICGEN_TOKENS_PER_SEC

    if progress_cb:
        progress_cb({"event": "status", "message": "Composing ambient bed..."})

    import torch
    inputs = processor(text=[prompt], padding=True, return_tensors="pt")
    if next(model.parameters()).is_cuda:
        inputs = {k: v.to("cuda") for k, v in inputs.items()}

    with torch.no_grad():
        audio_values = model.generate(
            **inputs,
            do_sample=True,
            guidance_scale=3.0,
            max_new_tokens=max_new_tokens,
        )

    # audio_values: (batch, channels, samples). Take batch 0, fold channels to mono.
    audio = audio_values[0].detach().cpu().numpy()
    if audio.ndim > 1:
        audio = audio.mean(axis=0)
    return audio.astype(np.float32, copy=False), MUSICGEN_SR


def crossfade_loop(clip, sample_rate, target_seconds, fade_seconds=2.0):
    """
    Tile `clip` to `target_seconds`, crossfading neighbours by `fade_seconds`.
    Each copy fades in at the start and out at the end; consecutive copies
    overlap by `fade_seconds`, so their fade-out + fade-in sum to ~unity.
    """
    clip = np.asarray(clip, dtype=np.float32)
    fade_samples = int(fade_seconds * sample_rate)
    fade_samples = max(1, min(fade_samples, len(clip) // 4))

    fade_in = np.linspace(0.0, 1.0, fade_samples, dtype=np.float32)
    fade_out = 1.0 - fade_in

    body = clip.copy()
    body[:fade_samples] *= fade_in
    body[-fade_samples:] *= fade_out

    target_samples = int(target_seconds * sample_rate)
    step = len(body) - fade_samples  # advance per loop
    output = np.zeros(target_samples + len(body), dtype=np.float32)

    pos = 0
    while pos < target_samples:
        output[pos:pos + len(body)] += body
        pos += step

    return output[:target_samples]


def _resample_linear(audio, src_sr, dst_sr):
    """Cheap linear resampling - fine for music/voice mixing."""
    if src_sr == dst_sr:
        return audio.astype(np.float32, copy=False)
    src_len = len(audio)
    dst_len = int(round(src_len * dst_sr / src_sr))
    src_t = np.arange(src_len, dtype=np.float64) / src_sr
    dst_t = np.arange(dst_len, dtype=np.float64) / dst_sr
    return np.interp(dst_t, src_t, audio).astype(np.float32)


def _peak_normalize(audio, peak_dbfs):
    """Scale audio so its peak hits the requested dBFS level."""
    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    if peak < 1e-8:
        return audio
    target_amp = 10.0 ** (peak_dbfs / 20.0)
    return audio * (target_amp / peak)


def mix_narration_and_music(narration, n_sr, music, m_sr,
                            music_db_below_speech=18.0):
    """
    Layer narration over music with simple peak normalization and a static
    'duck' (music sits N dB under narration). Loops/extends music to cover
    the narration length. Returns (mono float32, sample_rate).
    """
    if narration.ndim > 1:
        narration = narration.mean(axis=1)
    if music.ndim > 1:
        music = music.mean(axis=1)

    music = _resample_linear(music, m_sr, n_sr)
    target_len = len(narration)
    if len(music) < target_len:
        # Tile to cover; cheaper than another crossfade pass since the bed is
        # already smooth from generate -> crossfade_loop.
        repeats = (target_len // len(music)) + 1
        music = np.tile(music, repeats)
    music = music[:target_len]

    narration = _peak_normalize(narration, peak_dbfs=-1.0)
    music = _peak_normalize(music, peak_dbfs=-1.0 - music_db_below_speech)

    mixed = narration + music
    # Soft safety limiter - prevent clipping after the sum.
    peak = float(np.max(np.abs(mixed))) if mixed.size else 0.0
    if peak > 0.99:
        mixed = mixed * (0.99 / peak)
    return mixed.astype(np.float32, copy=False), n_sr


def encode(audio_f32, sample_rate, fmt="mp3"):
    """Encode mono float32 audio to mp3 (or wav fallback). Returns (bytes, ext)."""
    return tts_mod.encode(audio_f32, sample_rate, fmt=fmt)


def load_audio_file(path):
    """Return (mono float32, sample_rate) from any soundfile-readable path."""
    import soundfile as sf
    audio, sr = sf.read(str(path), dtype="float32")
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    return audio.astype(np.float32, copy=False), int(sr)

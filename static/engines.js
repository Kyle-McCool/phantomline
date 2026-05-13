/**
 * Phantomline AI engine adapters.
 *
 * The Make Video flow doesn't care WHERE inference happens — it just calls
 * `currentEngine().generate(prompt, opts)`. This file defines two engines:
 *
 *   ServerEngine   — calls Ollama on the user's machine via /api/start_short
 *                    etc. The original desktop path. Fast on a beefy PC.
 *   WebLLMEngine   — runs Llama 3.2 1B in the browser via WebGPU. The
 *                    mobile/PWA path. Slower but works without a backend.
 *
 * The engine choice persists in localStorage and is applied at app boot so
 * the same workflow code runs on desktop or on a phone.
 *
 * NOTE: This file is loaded as a regular script (not module) so it can
 * coexist with the existing globals in phantomline.js. The WebLLM SDK is
 * dynamically imported only when a user actually enables the device
 * engine — keeps the desktop path zero-cost.
 */

(function () {
  'use strict';

  const ENGINE_PREF_KEY = 'ghostline.engine.v1';
  const CLOUD_PROVIDER_KEY = 'ghostline.cloud.provider';
  const CLOUD_KEY_KEY = 'ghostline.cloud.key';
  const CLOUD_MODEL_KEY = 'ghostline.cloud.model';

  // Has the user pasted a BYO cloud API key? Used by defaultEngine() so a
  // fresh user with a key gets the cheap+fast Claude/OpenAI path by default
  // without having to dig in settings.
  function hasCloudKey() {
    try {
      return !!(localStorage.getItem(CLOUD_KEY_KEY) || '').trim();
    } catch { return false; }
  }

  // Default engine priority:
  //   1. cloud (BYO key) if user has pasted one — best UX, no install, $/run is on them
  //   2. device (WebLLM in browser) for standalone PWA on touch devices
  //   3. server (Ollama) for desktop installs — the original path
  function defaultEngine() {
    if (hasCloudKey()) return 'cloud';
    const isStandalone =
      window.matchMedia('(display-mode: standalone)').matches ||
      window.navigator.standalone === true;
    const isTouch = window.matchMedia('(pointer: coarse)').matches;
    return isStandalone && isTouch ? 'device' : 'server';
  }

  function readPref() {
    try {
      const v = localStorage.getItem(ENGINE_PREF_KEY);
      return v === 'device' || v === 'server' || v === 'cloud'
        ? v
        : defaultEngine();
    } catch {
      return defaultEngine();
    }
  }

  function writePref(value) {
    try { localStorage.setItem(ENGINE_PREF_KEY, value); } catch {}
  }

  /**
   * ServerEngine: thin wrapper over the existing /api/start_short flow.
   * Returns a promise that resolves with `{ text, title, project_id }`.
   */
  class ServerEngine {
    constructor() {
      this.id = 'server';
      this.label = 'Local server (Ollama)';
      this.requiresInit = false;
    }
    async init() { return true; }
    async generateScript({ topic, niche, audience, format, recipe, hookStyle,
                           tone, words, model, description }) {
      const r = await fetch('/api/start_short', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          topic, niche, audience, format, recipe, hook_style: hookStyle,
          genre: niche, tone, word_count: words, model, description,
        }),
      });
      const data = await r.json();
      if (!data.ok) throw new Error(data.error || 'Script generation failed');
      // Caller polls /api/status/<job_id>; the existing waitForStoryJob handles this.
      return { mode: 'server-job', job_id: data.job_id, inputs: data.inputs };
    }
  }

  /**
   * WebLLMEngine: runs Llama 3.2 1B Instruct in the browser. Lazy-loads the
   * @mlc-ai/web-llm SDK on first use; caches the model in IndexedDB after
   * the initial download (~1GB). Subsequent visits are instant.
   *
   * Fails gracefully if WebGPU is unavailable — caller must `await init()`
   * and handle the rejection.
   */
  class WebLLMEngine {
    constructor() {
      this.id = 'device';
      this.label = 'This device (WebGPU + Llama 3.2 1B)';
      this.requiresInit = true;
      this._engine = null;
      this._initPromise = null;
      // Llama 3.2 1B Instruct quantized — small enough for phones.
      this.modelId = 'Llama-3.2-1B-Instruct-q4f16_1-MLC';
    }

    available() {
      return typeof navigator !== 'undefined' && 'gpu' in navigator;
    }

    async init(progressCb) {
      if (this._engine) return this._engine;
      if (this._initPromise) return this._initPromise;
      if (!this.available()) {
        throw new Error('WebGPU not available in this browser. Try Chrome on Android, or Safari 17+ on iPhone.');
      }
      this._initPromise = (async () => {
        // Dynamic import keeps WebLLM out of the desktop hot path.
        const webllm = await import('https://cdn.jsdelivr.net/npm/@mlc-ai/web-llm@0.2.79/+esm');
        const engine = await webllm.CreateMLCEngine(this.modelId, {
          initProgressCallback: (p) => {
            if (progressCb) progressCb({
              progress: p.progress,
              text: p.text || `Loading ${this.modelId}…`,
            });
          },
        });
        this._engine = engine;
        return engine;
      })();
      return this._initPromise;
    }

    /**
     * Generate a short script. Returns `{ text, title }` synchronously
     * after streaming completes. The Make Video flow's existing job-status
     * loop is bypassed for device runs — there's no polling needed.
     */
    async generateScript({ topic, niche, audience, format, recipe, hookStyle,
                           tone, words, description, onProgress }) {
      const engine = await this.init(onProgress);
      const sys = (
        "You are a YouTube voiceover writer for faceless channels. " +
        "You output ONLY narration prose, no markdown, no scene labels, " +
        "no 'Here is', no author notes. Plain text only."
      );
      const direction = description ? `\n\nCustom direction: ${description}` : '';
      const user = (
        `Write a complete YouTube voiceover narration script.\n\n` +
        `Topic: ${topic || 'creator-defined'}\n` +
        `Niche: ${niche || 'faceless YouTube'}\n` +
        `Audience: ${audience || 'general curious viewers'}\n` +
        `Format: ${format || 'short-form'}\n` +
        `Recipe: ${recipe || 'general'}\n` +
        `Hook style: ${hookStyle || 'curiosity'}\n` +
        `Tone: ${tone || 'cinematic, calm'}\n` +
        `Target length: ~${words || 280} words.${direction}\n\n` +
        `Open with a strong hook. End with a memorable final line.\n` +
        `Begin the narration now:`
      );
      let text = '';
      const stream = await engine.chat.completions.create({
        messages: [
          { role: 'system', content: sys },
          { role: 'user', content: user },
        ],
        stream: true,
        temperature: 0.85,
        max_tokens: Math.min(2000, Math.max(200, Math.round((words || 280) * 1.6))),
      });
      for await (const chunk of stream) {
        const piece = chunk.choices?.[0]?.delta?.content || '';
        if (piece) {
          text += piece;
          if (onProgress) onProgress({ streaming: true, words: text.split(/\s+/).length });
        }
      }
      // Strip any inadvertent meta lines.
      text = text.replace(/^(Here['']s|Sure[!,].*?\n|Title:.*\n)/i, '').trim();
      const firstLine = text.split('\n')[0] || '';
      const title = firstLine.length < 100 && firstLine.length > 0 && /^[A-Z]/.test(firstLine)
        ? firstLine.replace(/[*#"']/g, '').trim()
        : 'Untitled video';
      return { mode: 'device-direct', text, title };
    }

    async terminate() {
      if (this._engine && this._engine.unload) {
        try { await this._engine.unload(); } catch {}
      }
      this._engine = null;
      this._initPromise = null;
    }
  }

  /**
   * CloudKeyEngine: BYO-API-key path. The user pastes a personal Anthropic
   * or OpenAI key into Settings; we call the provider directly from the
   * browser. Nothing routes through the Phantomline server. Their key,
   * their tokens, their bill.
   *
   * Why this exists:
   * - Ollama install is the biggest install-funnel drop-off. A BYO-key
   *   path lets users skip it entirely and start generating in 30 seconds.
   * - Quality of frontier models (Claude/GPT-4o) crushes any 7B local
   *   model for script writing, while cost is trivial (~$0.005/script
   *   on Haiku).
   * - We don't pay for inference and don't proxy traffic, so it scales
   *   for free.
   *
   * Provider/key/model are stored in localStorage:
   *   ghostline.cloud.provider  → 'anthropic' | 'openai' | 'gemini' | 'openrouter'
   *   ghostline.cloud.key       → raw API key (never sent to our server)
   *   ghostline.cloud.model     → e.g. 'claude-haiku-4-5' or 'gpt-4o-mini'
   *
   * SECURITY NOTE: API keys in localStorage are visible to any JS that
   * runs on the page. We mitigate by (a) strict CSP in production, (b)
   * never logging keys, (c) never sending them to our backend. Users
   * who don't want browser-visible keys should use the server (Ollama)
   * or device (WebLLM) engine.
   */
  class CloudKeyEngine {
    constructor() {
      this.id = 'cloud';
      this.label = 'Cloud (your API key)';
      this.requiresInit = false;
    }
    available() {
      // Available iff a key is present. Otherwise the UI shows a hint to
      // paste one in Settings → AI engine.
      try {
        return !!(localStorage.getItem(CLOUD_KEY_KEY) || '').trim();
      } catch { return false; }
    }
    provider() {
      try {
        const p = (localStorage.getItem(CLOUD_PROVIDER_KEY) || 'anthropic').toLowerCase();
        if (p === 'openai' || p === 'gemini' || p === 'openrouter') return p;
        return 'anthropic';
      } catch { return 'anthropic'; }
    }
    key() {
      try { return (localStorage.getItem(CLOUD_KEY_KEY) || '').trim(); }
      catch { return ''; }
    }
    model() {
      try {
        const m = (localStorage.getItem(CLOUD_MODEL_KEY) || '').trim();
        if (m) return m;
      } catch {}
      // Sensible defaults — cheap and fast for each provider.
      const prov = this.provider();
      if (prov === 'openai') return 'gpt-4o-mini';
      if (prov === 'gemini') return 'gemini-2.0-flash';
      if (prov === 'openrouter') return 'openai/gpt-oss-120b:free';
      return 'claude-haiku-4-5';
    }
    setProvider(p) {
      try { localStorage.setItem(CLOUD_PROVIDER_KEY, (p || 'anthropic').toLowerCase()); } catch {}
    }
    setKey(k) {
      try { localStorage.setItem(CLOUD_KEY_KEY, (k || '').trim()); } catch {}
    }
    setModel(m) {
      try { localStorage.setItem(CLOUD_MODEL_KEY, (m || '').trim()); } catch {}
    }
    async init() { return true; }

    /**
     * Low-level chat call. Returns the assistant's plain-text reply.
     * Throws RuntimeError-style strings on HTTP failure so callers can
     * surface them to the user verbatim.
     */
    async _chat({ system, user, temperature = 0.85, maxTokens = 2000 }) {
      const provider = this.provider();
      const apiKey = this.key();
      if (!apiKey) throw new Error('No cloud API key set. Paste one in Settings → AI engine.');
      const model = this.model();
      if (provider === 'gemini') {
        const endpoint = `https://generativelanguage.googleapis.com/v1beta/models/${encodeURIComponent(model)}:generateContent?key=${encodeURIComponent(apiKey)}`;
        const r = await fetch(endpoint, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            contents: [{ parts: [{ text: user }] }],
            systemInstruction: { parts: [{ text: system }] },
            generationConfig: { temperature, maxOutputTokens: maxTokens },
          }),
        });
        if (!r.ok) {
          const errText = await r.text().catch(() => '');
          throw new Error(`Gemini API ${r.status}: ${errText.slice(0, 300)}`);
        }
        const d = await r.json();
        return (d.candidates?.[0]?.content?.parts?.[0]?.text || '').trim();
      }
      if (provider === 'openrouter') {
        const _orFallbackChain = [
          model,
          'openai/gpt-oss-120b:free',
          'nvidia/nemotron-3-super-120b-a12b:free',
          'qwen/qwen3-next-80b-a3b-instruct:free',
          'meta-llama/llama-3.3-70b-instruct:free',
          'google/gemma-4-31b-it:free',
          'nvidia/nemotron-3-nano-30b-a3b:free',
        ];
        const tried = new Set();
        let lastErr = '';
        for (const tryModel of _orFallbackChain) {
          if (tried.has(tryModel)) continue;
          tried.add(tryModel);
          const r = await fetch('https://openrouter.ai/api/v1/chat/completions', {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              'Authorization': `Bearer ${apiKey}`,
              'HTTP-Referer': 'https://phantomline.xyz',
              'X-Title': 'Phantomline',
            },
            body: JSON.stringify({
              model: tryModel,
              messages: [
                { role: 'system', content: system },
                { role: 'user', content: user },
              ],
              temperature,
              max_tokens: maxTokens,
            }),
          });
          if (r.ok) {
            const d = await r.json();
            return (d.choices?.[0]?.message?.content || '').trim();
          }
          if (r.status === 429 || r.status === 503 || r.status === 502) {
            lastErr = `${tryModel} → ${r.status}`;
            continue;
          }
          const errText = await r.text().catch(() => '');
          throw new Error(`OpenRouter API ${r.status}: ${errText.slice(0, 300)}`);
        }
        throw new Error(`All OpenRouter free models rate-limited. Last: ${lastErr}. Try again in a minute.`);
      }
      if (provider === 'openai') {
        const r = await fetch('https://api.openai.com/v1/chat/completions', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${apiKey}`,
          },
          body: JSON.stringify({
            model,
            messages: [
              { role: 'system', content: system },
              { role: 'user', content: user },
            ],
            temperature,
            max_tokens: maxTokens,
          }),
        });
        if (!r.ok) {
          const errText = await r.text().catch(() => '');
          throw new Error(`OpenAI API ${r.status}: ${errText.slice(0, 300)}`);
        }
        const d = await r.json();
        return (d.choices?.[0]?.message?.content || '').trim();
      }
      // Anthropic. Requires the dangerous-direct-browser-access opt-in.
      const r = await fetch('https://api.anthropic.com/v1/messages', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'x-api-key': apiKey,
          'anthropic-version': '2023-06-01',
          'anthropic-dangerous-direct-browser-access': 'true',
        },
        body: JSON.stringify({
          model,
          max_tokens: maxTokens,
          temperature,
          system,
          messages: [{ role: 'user', content: user }],
        }),
      });
      if (!r.ok) {
        const errText = await r.text().catch(() => '');
        throw new Error(`Anthropic API ${r.status}: ${errText.slice(0, 300)}`);
      }
      const d = await r.json();
      // content is an array of {type, text} blocks; concatenate text blocks.
      const blocks = Array.isArray(d.content) ? d.content : [];
      return blocks.map(b => b.text || '').join('').trim();
    }

    /**
     * Same return contract as WebLLMEngine.generateScript: `{ mode, text, title }`.
     * The Make Video flow's existing post-script handler treats `mode:
     * 'device-direct'` and `mode: 'cloud-direct'` the same — both bypass the
     * server job-polling loop.
     */
    async generateScript({ topic, niche, audience, format, recipe, hookStyle,
                           tone, words, description, onProgress }) {
      const sys = (
        "You are a YouTube voiceover writer for faceless channels. " +
        "You output ONLY narration prose, no markdown, no scene labels, " +
        "no 'Here is', no author notes. Plain text only."
      );
      const direction = description ? `\n\nCustom direction: ${description}` : '';
      const user = (
        `Write a complete YouTube voiceover narration script.\n\n` +
        `Topic: ${topic || 'creator-defined'}\n` +
        `Niche: ${niche || 'faceless YouTube'}\n` +
        `Audience: ${audience || 'general curious viewers'}\n` +
        `Format: ${format || 'short-form'}\n` +
        `Recipe: ${recipe || 'general'}\n` +
        `Hook style: ${hookStyle || 'curiosity'}\n` +
        `Tone: ${tone || 'cinematic, calm'}\n` +
        `Target length: ~${words || 280} words.${direction}\n\n` +
        `Open with a strong hook. End with a memorable final line.\n` +
        `Begin the narration now:`
      );
      if (onProgress) onProgress({ streaming: false, status: 'Calling ' + this.provider() + '…' });
      const text = await this._chat({
        system: sys,
        user,
        temperature: 0.85,
        maxTokens: Math.min(4000, Math.max(400, Math.round((words || 280) * 1.8))),
      });
      const cleaned = text.replace(/^(Here['']s|Sure[!,].*?\n|Title:.*\n)/i, '').trim();
      const firstLine = cleaned.split('\n')[0] || '';
      const title = firstLine.length < 100 && firstLine.length > 0 && /^[A-Z]/.test(firstLine)
        ? firstLine.replace(/[*#"']/g, '').trim()
        : 'Untitled video';
      if (onProgress) onProgress({ streaming: false, words: cleaned.split(/\s+/).length });
      return { mode: 'cloud-direct', text: cleaned, title, provider: this.provider(), model: this.model() };
    }

    /** Quick "did my key work?" probe for the settings panel. */
    async testKey() {
      const reply = await this._chat({
        system: 'You answer in exactly one short word.',
        user: 'Reply with the single word: ready',
        temperature: 0,
        maxTokens: 12,
      });
      return /ready|ok|yes/i.test(reply);
    }
  }

  /**
   * SpeechEngine: narration via the browser's built-in `speechSynthesis`.
   * Works on every modern browser, zero download, on-device. Voice quality
   * varies by OS (iOS is excellent, Android decent, desktop varies).
   *
   * Returns a Blob URL of the synthesized audio for downstream assembly.
   * The browser doesn't expose raw audio bytes from speechSynthesis on
   * most platforms, so we capture the speaker output via MediaRecorder
   * tied to a MediaStreamAudioDestinationNode. Works in Chrome and Edge;
   * Safari and Firefox fall back to a pre-rendered approach using the
   * speakAndCapture polyfill.
   */
  class SpeechEngine {
    constructor() {
      this.id = 'speech';
      this.label = 'Browser TTS (Web Speech API)';
    }
    available() {
      return typeof window !== 'undefined' && 'speechSynthesis' in window;
    }
    listVoices() {
      if (!this.available()) return [];
      // voices populate asynchronously on first call
      const voices = window.speechSynthesis.getVoices() || [];
      return voices.map((v) => ({
        id: v.voiceURI || v.name,
        label: `${v.name}${v.lang ? ' (' + v.lang + ')' : ''}`,
        lang: v.lang,
        local: v.localService,
      }));
    }
    /**
     * Speak text and return { url, duration } when done. Captures audio via
     * a MediaStreamAudioDestinationNode + MediaRecorder so the result is a
     * real Blob the rest of the pipeline can mux into video.
     */
    async synthesize(text, { voice, rate = 1.0, pitch = 1.0, onProgress } = {}) {
      if (!this.available()) throw new Error('Speech synthesis unsupported in this browser.');
      if (!text || !text.trim()) throw new Error('Empty narration text.');
      const synth = window.speechSynthesis;
      // Wait for voices to populate (some browsers load them async).
      if (synth.getVoices().length === 0) {
        await new Promise((resolve) => {
          let done = false;
          const tick = () => {
            if (done) return;
            if (synth.getVoices().length > 0) { done = true; resolve(); }
            else setTimeout(tick, 100);
          };
          synth.onvoiceschanged = () => { if (!done) { done = true; resolve(); } };
          tick();
          setTimeout(resolve, 2000);
        });
      }
      const utter = new SpeechSynthesisUtterance(text);
      utter.rate = rate;
      utter.pitch = pitch;
      const voices = synth.getVoices();
      const v = voices.find((vv) => vv.voiceURI === voice || vv.name === voice) || voices[0];
      if (v) utter.voice = v;

      // Capture path: route synthesizer output to MediaRecorder. Note that
      // not all browsers route speechSynthesis through the AudioContext
      // graph; on Safari/Firefox this captures only silence. In that case
      // the caller should fall back to playing the utterance live and
      // recording externally, or skip narration generation on-device.
      const start = performance.now();
      let blob = null;
      try {
        const ctx = new (window.AudioContext || window.webkitAudioContext)();
        const dest = ctx.createMediaStreamDestination();
        const recorder = new MediaRecorder(dest.stream);
        const chunks = [];
        recorder.ondataavailable = (e) => { if (e.data.size > 0) chunks.push(e.data); };
        recorder.start();
        await new Promise((resolve) => {
          utter.onend = resolve;
          utter.onerror = resolve;
          synth.speak(utter);
        });
        recorder.stop();
        await new Promise((resolve) => { recorder.onstop = resolve; });
        blob = new Blob(chunks, { type: 'audio/webm' });
        ctx.close();
      } catch (err) {
        if (onProgress) onProgress({ warning: `Capture failed (${err.message}); using uncaptured fallback.` });
      }
      const duration = (performance.now() - start) / 1000;
      const url = blob && blob.size > 100 ? URL.createObjectURL(blob) : null;
      return { url, blob, duration, voice: v ? v.name : null };
    }
  }

  /**
   * AmbientMusicEngine: procedurally generates a layered drone/pad bed via
   * the Web Audio API, then captures it to a WAV blob. Always available,
   * zero download, zero API. Quality is "ambient mood" not "songs" — meant
   * for narration backing on mobile when MusicGen isn't available.
   */
  class AmbientMusicEngine {
    constructor() {
      this.id = 'ambient';
      this.label = 'Procedural ambient (Web Audio API)';
    }
    available() {
      return typeof window !== 'undefined' && (window.AudioContext || window.webkitAudioContext);
    }
    /**
     * Generate `seconds` of ambient bed at a given mood. Returns a Blob URL.
     */
    async generate({ seconds = 30, mood = 'cinematic' } = {}) {
      if (!this.available()) throw new Error('Web Audio API unsupported.');
      const sampleRate = 44100;
      const length = Math.floor(seconds * sampleRate);
      const ctx = new OfflineAudioContext(2, length, sampleRate);

      // Mood presets: pick base frequencies, detune, lowpass cutoff.
      const presets = {
        cinematic:   { roots: [55, 82.4, 110], detune: 7, cutoff: 1200, lfoRate: 0.18 },
        mystery:     { roots: [49, 73.4, 98],  detune: 12, cutoff: 800,  lfoRate: 0.12 },
        uplifting:   { roots: [65, 98, 130],   detune: 4, cutoff: 1800, lfoRate: 0.25 },
        horror:      { roots: [41, 55, 65],    detune: 18, cutoff: 600,  lfoRate: 0.08 },
        chill:       { roots: [110, 165, 220], detune: 3, cutoff: 1500, lfoRate: 0.20 },
      };
      const p = presets[mood] || presets.cinematic;
      const master = ctx.createGain();
      master.gain.value = 0.0;
      master.connect(ctx.destination);
      // Slow fade in/out so it doesn't pop.
      master.gain.setValueAtTime(0.0001, 0);
      master.gain.exponentialRampToValueAtTime(0.32, 1.5);
      master.gain.setValueAtTime(0.32, Math.max(2, seconds - 1.5));
      master.gain.exponentialRampToValueAtTime(0.0001, seconds);

      const filter = ctx.createBiquadFilter();
      filter.type = 'lowpass';
      filter.frequency.value = p.cutoff;
      filter.Q.value = 0.8;
      filter.connect(master);

      // Layered detuned oscillators per root note.
      for (const root of p.roots) {
        for (let i = 0; i < 3; i++) {
          const osc = ctx.createOscillator();
          osc.type = i === 0 ? 'sine' : i === 1 ? 'triangle' : 'sawtooth';
          osc.frequency.value = root;
          osc.detune.value = (i - 1) * p.detune;
          // LFO for slow shimmer.
          const lfo = ctx.createOscillator();
          lfo.frequency.value = p.lfoRate + (i * 0.02);
          const lfoGain = ctx.createGain();
          lfoGain.gain.value = 4;
          lfo.connect(lfoGain);
          lfoGain.connect(osc.detune);
          const oscGain = ctx.createGain();
          oscGain.gain.value = 0.06;
          osc.connect(oscGain);
          oscGain.connect(filter);
          osc.start(0);
          osc.stop(seconds);
          lfo.start(0);
          lfo.stop(seconds);
        }
      }
      const buffer = await ctx.startRendering();
      // Encode as WAV (small, lossless, decoded by every browser).
      const wav = audioBufferToWav(buffer);
      const blob = new Blob([wav], { type: 'audio/wav' });
      return { url: URL.createObjectURL(blob), blob, duration: seconds, mood };
    }
  }

  /**
   * FfmpegRenderEngine: assembles narration + visuals + captions into a
   * single MP4, all in the browser via ffmpeg.wasm. Lazy-loads the WASM
   * core (~25 MB) on first use; cached forever via the service worker.
   *
   * Use:
   *   const eng = new FfmpegRenderEngine();
   *   await eng.init(p => updateProgress(p));
   *   const mp4 = await eng.assemble({ narration, visuals, captions, aspect: '9:16' });
   */
  class FfmpegRenderEngine {
    constructor() {
      this.id = 'ffmpeg-wasm';
      this.label = 'In-browser MP4 render (ffmpeg.wasm)';
      this._instance = null;
      this._initPromise = null;
    }
    available() {
      // Multi-threaded ffmpeg.wasm needs SharedArrayBuffer (which requires
      // COOP/COEP cross-origin isolation headers). Single-threaded fallback
      // works with plain WebAssembly support — slower but always loads.
      return typeof WebAssembly !== 'undefined';
    }
    isMultiThreaded() {
      return typeof SharedArrayBuffer !== 'undefined';
    }
    async init(progressCb) {
      if (this._instance) return this._instance;
      if (this._initPromise) return this._initPromise;
      this._initPromise = (async () => {
        const FFmpegMod = await import('https://cdn.jsdelivr.net/npm/@ffmpeg/ffmpeg@0.12.10/dist/esm/index.js');
        const UtilMod = await import('https://cdn.jsdelivr.net/npm/@ffmpeg/util@0.12.1/dist/esm/index.js');
        const ffmpeg = new FFmpegMod.FFmpeg();
        if (progressCb) {
          ffmpeg.on('log', ({ message }) => progressCb({ log: message }));
          ffmpeg.on('progress', ({ progress }) => progressCb({ progress }));
        }
        await ffmpeg.load({
          coreURL: await UtilMod.toBlobURL(
            'https://cdn.jsdelivr.net/npm/@ffmpeg/core@0.12.6/dist/esm/ffmpeg-core.js',
            'text/javascript'
          ),
          wasmURL: await UtilMod.toBlobURL(
            'https://cdn.jsdelivr.net/npm/@ffmpeg/core@0.12.6/dist/esm/ffmpeg-core.wasm',
            'application/wasm'
          ),
        });
        this._instance = { ffmpeg, util: UtilMod };
        return this._instance;
      })();
      return this._initPromise;
    }
    /**
     * Mux narration audio + a single source video into an MP4 sized to
     * `aspect`. Used as the simplest mobile path: user uploads source
     * footage, we narrate over it, this writes the MP4.
     */
    async assemble({ narrationBlob, sourceVideoBlob, aspect = '9:16',
                     captions = null, onProgress }) {
      const { ffmpeg, util } = await this.init(onProgress);
      await ffmpeg.writeFile('input.mp4', await util.fetchFile(sourceVideoBlob));
      await ffmpeg.writeFile('voice.webm', await util.fetchFile(narrationBlob));

      const dim = aspect === '9:16' ? '1080:1920' : aspect === '1:1' ? '1080:1080' : '1920:1080';
      const filterPad = `scale=${dim}:force_original_aspect_ratio=decrease,pad=${dim}:(ow-iw)/2:(oh-ih)/2:black,setsar=1`;

      const args = [
        '-i', 'input.mp4',
        '-i', 'voice.webm',
        '-filter_complex', `[0:v]${filterPad}[v]`,
        '-map', '[v]',
        '-map', '1:a',
        '-c:v', 'libx264',
        '-preset', 'ultrafast',
        '-crf', '24',
        '-c:a', 'aac',
        '-b:a', '128k',
        '-shortest',
        'out.mp4',
      ];
      await ffmpeg.exec(args);
      const data = await ffmpeg.readFile('out.mp4');
      const blob = new Blob([data.buffer], { type: 'video/mp4' });
      return { url: URL.createObjectURL(blob), blob };
    }
  }

  // ----- Helper: encode an AudioBuffer to a WAV blob (ambient music) -----
  function audioBufferToWav(buffer) {
    const numChannels = buffer.numberOfChannels;
    const sampleRate = buffer.sampleRate;
    const format = 1; // PCM
    const bitDepth = 16;
    const samples = buffer.length;
    const bytesPerSample = bitDepth / 8;
    const blockAlign = numChannels * bytesPerSample;
    const byteRate = sampleRate * blockAlign;
    const dataSize = samples * blockAlign;
    const ab = new ArrayBuffer(44 + dataSize);
    const view = new DataView(ab);
    const writeStr = (offset, str) => {
      for (let i = 0; i < str.length; i++) view.setUint8(offset + i, str.charCodeAt(i));
    };
    writeStr(0, 'RIFF');
    view.setUint32(4, 36 + dataSize, true);
    writeStr(8, 'WAVE');
    writeStr(12, 'fmt ');
    view.setUint32(16, 16, true);
    view.setUint16(20, format, true);
    view.setUint16(22, numChannels, true);
    view.setUint32(24, sampleRate, true);
    view.setUint32(28, byteRate, true);
    view.setUint16(32, blockAlign, true);
    view.setUint16(34, bitDepth, true);
    writeStr(36, 'data');
    view.setUint32(40, dataSize, true);
    let offset = 44;
    const channels = [];
    for (let c = 0; c < numChannels; c++) channels.push(buffer.getChannelData(c));
    for (let i = 0; i < samples; i++) {
      for (let c = 0; c < numChannels; c++) {
        let s = Math.max(-1, Math.min(1, channels[c][i]));
        s = s < 0 ? s * 0x8000 : s * 0x7fff;
        view.setInt16(offset, s, true);
        offset += 2;
      }
    }
    return ab;
  }

  /**
   * BundledMusicLibrary: ships with the app. Pure-procedural ambient pack
   * generated by `_generate_music_library.py` and shipped under
   * /static/library/music/. ~11 MB total, 8 mood-tagged tracks. Public
   * domain (procedurally generated by us — owned outright, no licensing
   * footnote needed).
   *
   * Use:
   *   const lib = window.GhostlineMobileLibs.bundledMusic;
   *   await lib.load();
   *   const tracks = lib.byMood('cinematic');
   *   const url = tracks[0].url;
   */
  class BundledMusicLibrary {
    constructor() {
      this.id = 'bundled-music';
      this.label = 'Bundled royalty-free music';
      this._manifest = null;
      this._loadPromise = null;
    }
    async load() {
      if (this._manifest) return this._manifest;
      if (this._loadPromise) return this._loadPromise;
      this._loadPromise = (async () => {
        const r = await fetch('/static/library/music.json');
        if (!r.ok) throw new Error(`Music manifest fetch failed: ${r.status}`);
        const data = await r.json();
        this._manifest = data;
        return data;
      })();
      return this._loadPromise;
    }
    async list() {
      const m = await this.load();
      return m.tracks || [];
    }
    async byMood(mood) {
      const tracks = await this.list();
      const want = (mood || '').toLowerCase();
      const exact = tracks.filter(t => (t.mood || '').toLowerCase() === want);
      if (exact.length) return exact;
      // Soft mapping for common synonyms.
      const synonyms = {
        scary: 'horror', creepy: 'horror', tension: 'tense',
        epic: 'cinematic', sci_fi: 'cinematic', adventure: 'cinematic',
        relaxed: 'chill', lofi: 'chill', ambient: 'chill',
        sad: 'dark', somber: 'dark',
        happy: 'uplifting', motivational: 'uplifting',
        bright: 'hopeful', sunrise: 'hopeful',
        mystery: 'mystery', detective: 'mystery',
      };
      const fallback = synonyms[want];
      if (fallback) {
        return tracks.filter(t => (t.mood || '').toLowerCase() === fallback);
      }
      return [];
    }
    async pickForRecipe(recipe) {
      // Map recipe IDs to moods. Mirrors the desktop recipe presets so the
      // chosen track matches the script's emotional shape.
      const recipeMoods = {
        'viral-story': 'tense',
        'rule-horror': 'horror',
        'survival-tips': 'chill',
        'mystery-doc': 'mystery',
        'education': 'cinematic',
        'finance': 'uplifting',
        'history': 'cinematic',
        'motivation': 'hopeful',
        'scary-story': 'horror',
        'listicle': 'uplifting',
        'reddit-story': 'tense',
        'confession': 'dark',
        'betrayal': 'tense',
      };
      const mood = recipeMoods[recipe] || 'cinematic';
      const matches = await this.byMood(mood);
      if (matches.length) return matches[0];
      const all = await this.list();
      return all[0] || null;
    }
  }

  /**
   * Pexels stock-video search. Free API, requires a key from
   * https://www.pexels.com/api/. Returns video URLs + thumbnail + duration.
   * The user pastes their key in Settings — we never proxy through your
   * server, so quota is theirs not yours.
   */
  class PexelsLibrary {
    constructor() {
      this.id = 'pexels';
      this.label = 'Pexels stock videos';
    }
    apiKey() {
      try { return localStorage.getItem('ghostline.pexelsKey') || ''; }
      catch { return ''; }
    }
    setApiKey(key) {
      try { localStorage.setItem('ghostline.pexelsKey', (key || '').trim()); }
      catch {}
    }
    async search(query, { perPage = 12, orientation = 'portrait' } = {}) {
      const key = this.apiKey();
      if (!key) throw new Error('Add your free Pexels API key in Settings.');
      const url = new URL('https://api.pexels.com/videos/search');
      url.searchParams.set('query', query);
      url.searchParams.set('per_page', String(perPage));
      url.searchParams.set('orientation', orientation);
      const r = await fetch(url, { headers: { Authorization: key } });
      if (!r.ok) throw new Error(`Pexels API error: ${r.status}`);
      const d = await r.json();
      return (d.videos || []).map((v) => ({
        id: v.id,
        url: (v.video_files || []).find(f => f.quality === 'sd')?.link
              || v.video_files?.[0]?.link,
        thumbnail: v.image,
        duration: v.duration,
        width: v.width,
        height: v.height,
        author: v.user?.name,
      }));
    }
  }

  const engines = {
    server: new ServerEngine(),
    device: new WebLLMEngine(),
    cloud: new CloudKeyEngine(),
  };

  // Mobile-first companion engines. Available regardless of which AI engine
  // is selected (you can run scripts on the server but render on the device).
  window.GhostlineMobileLibs = {
    speech: new SpeechEngine(),
    ambient: new AmbientMusicEngine(),
    render: new FfmpegRenderEngine(),
    pexels: new PexelsLibrary(),
    bundledMusic: new BundledMusicLibrary(),
    async mixAudio(narrationBlob, musicBlob, musicDbReduction = -18) {
      const ctx = new (window.AudioContext || window.webkitAudioContext)();
      const narAb = await narrationBlob.arrayBuffer();
      const narBuf = await ctx.decodeAudioData(narAb);
      let musBuf = null;
      if (musicBlob && musicBlob.size > 100) {
        const musAb = await musicBlob.arrayBuffer();
        musBuf = await ctx.decodeAudioData(musAb);
      }
      const duration = narBuf.duration + 1;
      const sampleRate = narBuf.sampleRate;
      const offline = new OfflineAudioContext(2, Math.ceil(duration * sampleRate), sampleRate);
      const narSrc = offline.createBufferSource();
      narSrc.buffer = narBuf;
      narSrc.connect(offline.destination);
      narSrc.start(0);
      if (musBuf) {
        const musSrc = offline.createBufferSource();
        musSrc.buffer = musBuf;
        musSrc.loop = true;
        const musGain = offline.createGain();
        musGain.gain.value = Math.pow(10, musicDbReduction / 20);
        musSrc.connect(musGain);
        musGain.connect(offline.destination);
        musSrc.start(0);
      }
      const rendered = await offline.startRendering();
      const wav = audioBufferToWav(rendered);
      ctx.close();
      return new Blob([wav], { type: 'audio/wav' });
    },
  };

  let activeId = readPref();

  function setEngine(id) {
    if (!engines[id]) return false;
    activeId = id;
    writePref(id);
    document.body.dataset.aiEngine = id;
    return true;
  }

  function currentEngine() {
    return engines[activeId] || engines.server;
  }

  function listEngines() {
    return Object.values(engines).map((e) => ({
      id: e.id,
      label: e.label,
      available: typeof e.available === 'function' ? e.available() : true,
    }));
  }

  // Apply the persisted engine on boot so CSS can react via [data-ai-engine].
  document.body.dataset.aiEngine = activeId;

  // Expose on window so phantomline.js can call without imports.
  window.GhostlineEngines = {
    current: currentEngine,
    set: setEngine,
    list: listEngines,
    activeId: () => activeId,
  };
  // Direct access to engine instances for settings UIs that need to call
  // getters/setters not exposed via the lightweight `list()` summary.
  // Treat as semi-internal: not for routine use.
  window._GhostlineEngineRegistry = engines;
})();

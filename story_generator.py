#!/usr/bin/env python3
"""
Phantomline story engine (local, Ollama-powered).

Generates a long-form, calm, slow-burn narration script in sections,
keeping a rolling summary so a local LLM can stay coherent across
many thousands of words. Saves partial drafts after every section
so nothing is lost if generation is interrupted.

Output format on disk:
    TITLE: <Generated Title>

    <full narration script>

Usage (interactive):
    python story_generator.py

Usage (non-interactive):
    python story_generator.py --non-interactive --topic "..." --genre "..." --words 10000 --model llama3.1

Resume a previous run:
    python story_generator.py --resume output/My_Story.state.json
"""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

try:
    import requests
except ImportError:
    print("ERROR: the 'requests' package is required. Install it with:\n    pip install requests")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Backend selection
# ---------------------------------------------------------------------------
# Phantomline supports three text-generation backends in priority order:
#   1. Cloud BYO-key (Anthropic Claude or OpenAI GPT) when env vars are set.
#      Best quality, ~$0.005/script on Haiku/mini, no install friction.
#   2. Ollama on localhost:11434 — the original desktop path.
#   3. text.pollinations.ai — last-resort free public endpoint for hosted
#      deploys that have neither a cloud key nor Ollama.
#
# Set ANTHROPIC_API_KEY (preferred) or OPENAI_API_KEY in your .env / shell
# to enable the cloud path. Override model with PHANTOMLINE_CLOUD_MODEL.
# Set PHANTOMLINE_LLM_BACKEND=ollama to force the old path even if a key
# is present — useful when testing the local pipeline.

def _cloud_backend():
    """Return (provider, key, model) or None.
    Supports Anthropic, OpenAI, Gemini, and OpenRouter."""
    forced = (os.environ.get("PHANTOMLINE_LLM_BACKEND") or "").strip().lower()
    if forced == "ollama" or forced == "pollinations":
        return None
    a_key = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    o_key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    g_key = (os.environ.get("GEMINI_API_KEY") or "").strip()
    or_key = (os.environ.get("OPENROUTER_API_KEY") or "").strip()
    model_override = (os.environ.get("PHANTOMLINE_CLOUD_MODEL") or "").strip()
    if a_key:
        return ("anthropic", a_key, model_override or "claude-haiku-4-5")
    if o_key:
        return ("openai", o_key, model_override or "gpt-4o-mini")
    if g_key:
        return ("gemini", g_key, model_override or "gemini-2.0-flash")
    if or_key:
        return ("openrouter", or_key, model_override or "google/gemma-4-31b-it:free")
    return None


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

OLLAMA_BASE = "http://localhost:11434"
OLLAMA_GENERATE_URL = f"{OLLAMA_BASE}/api/generate"
OLLAMA_TAGS_URL = f"{OLLAMA_BASE}/api/tags"

DEFAULT_TOPIC = (
    "A small town's deep-space telescope picks up a signal that proves an alien "
    "fleet has been silently approaching Earth for centuries"
)
DEFAULT_GENRE = "sci-fi alien invasion"
DEFAULT_TONE = "cinematic, slow-burn, eerie, calm narration"
DEFAULT_WORDS = 10000
DEFAULT_MODEL = "llama3.1"

# How many words to aim for per generated section. Most local models can
# reliably produce ~1200-1800 words per turn. Keep this conservative.
SECTION_TARGET_WORDS = 1500
# Hard cap so a misbehaving model can't loop forever.
MAX_SECTIONS = 20

GENRE_HINTS = [
    "sci-fi alien invasion",
    "mystery",
    "strange disappearances",
    "deep ocean horror",
    "ancient alien discoveries",
    "abandoned towns",
    "lost transmissions",
    "government coverups",
    "cosmic horror",
]

# System prompt used for every Ollama call. Kept short on purpose - the
# per-task prompts carry the heavy instructions.
SYSTEM_PROMPT = (
    "You are Phantomline's senior YouTube script strategist for faceless channels. "
    "You write spoken narration that is built for retention, captions, and visual production. "
    "For Shorts, you prioritize first-frame clarity, unresolved tension, fast payoff, and loopable endings. "
    "For long-form, you prioritize promise, progression, proof, examples, and clean section-to-section momentum. "
    "You can write stories, tutorials, explainers, documentaries, education, listicles, and channel-specific formats. "
    "Output ONLY the requested deliverable: no preface, no meta commentary, no headings unless explicitly requested, "
    "no markdown, no bracketed alternate words, no repeated options, no 'Here is', no 'I hope you enjoyed'. "
    "Plain spoken prose only."
)


def build_system_prompt(insights=None):
    """Return SYSTEM_PROMPT enriched with channel-level constraints when insights are loaded.

    The channel data block gives the model a hard operating frame — which content
    territory this channel actually owns, which performance rules override defaults,
    and which hook patterns have proven out. Per-generation signal (titles, keywords,
    search terms) still goes in the user prompt via channel_insights.to_prompt_block().
    """
    if not insights:
        return SYSTEM_PROMPT

    lines = []

    keywords = (insights.get("seo_keywords") or [])[:4]
    if keywords:
        lines.append(
            f"This channel's proven content territory: {', '.join(str(k) for k in keywords)}. "
            "Anchor all topic choices, angles, and keyword selection here unless the user explicitly overrides."
        )

    next_rules = (insights.get("next_video_rules") or [])[:3]
    if next_rules:
        lines.append("Channel performance rules (override generic defaults when they conflict):")
        lines.extend(f"- {str(r).strip()}" for r in next_rules)

    hook_rules = (insights.get("hook_guidance") or [])[:2]
    if hook_rules:
        lines.append("Proven hook patterns for this channel:")
        lines.extend(f"- {str(h).strip()}" for h in hook_rules)

    if not lines:
        return SYSTEM_PROMPT

    return (
        SYSTEM_PROMPT
        + "\n\nCHANNEL CONSTRAINTS (treat as hard rules, not suggestions):\n"
        + "\n".join(lines)
    )

STYLE_RULES = """\
Style and rules for the narration:
- YouTube voiceover written for one narrator and one clear audience.
- Match the requested niche, audience, format, tone, and creative direction.
- Make the script visual: each paragraph should imply something clear that could appear on screen.
- No excessive gore, no sexual content, no heavy profanity.
- Open with a concrete hook: person/topic + problem/stakes + unexpected detail.
- Do not begin with generic worldbuilding, greetings, channel housekeeping, or slow scene-setting.
- Keep momentum by adding new information, examples, stakes, steps, discoveries, reversals, visuals, or questions.
- Use short, spoken sentences when the format is short-form so captions can land in 2-5 word chunks.
- Avoid filler, moralizing, repeated phrasing, repeated sentences, and bracketed alternates like [not] or [pause].
- Never write the title as narration unless the user explicitly asks for a spoken title card.
- End with a memorable final thought, image, lesson, or unresolved question suited to the format.
- Output narration prose only. No markdown, no bullet points, no headings, no timestamps, no scene labels, no author notes."""


# Per-format overrides layered ON TOP OF STYLE_RULES. Each override
# tightens the voice for a specific format so a "tutorial" doesn't read
# like a "horror documentary." Detection is keyword-based on the
# genre/tone string passed by the caller — see _detect_format() below.
# Keep these short — they're appended verbatim to the prompt.
STYLE_OVERRIDES = {
    "story": (
        "STORY-FORMAT OVERRIDE:\n"
        "- Treat the script as a single narrative arc with one protagonist and one stake.\n"
        "- Sensory detail beats exposition: name the room, the smell, the sound, then move.\n"
        "- Keep dialogue rare; when used, attribute by action, not 'said' tags.\n"
        "- The reversal lands no later than 60% of the way through. The ending must echo a phrase or image from the opening minute."
    ),
    "tutorial": (
        "TUTORIAL-FORMAT OVERRIDE:\n"
        "- Open with the outcome the viewer will leave with, in concrete terms.\n"
        "- Each step gets one sentence stating the action and one stating the verifiable result.\n"
        "- Name specific tools, settings, file paths, or numbers — not 'a button somewhere.'\n"
        "- Skip motivational filler. The viewer hit play to learn, not to be inspired."
    ),
    "listicle": (
        "LISTICLE-FORMAT OVERRIDE:\n"
        "- Each item starts with a 4-9 word punchy claim, then 2-3 sentences of proof.\n"
        "- Order items by escalating surprise — the most counterintuitive item lands second-to-last.\n"
        "- Do NOT count down or up by number out loud unless the title explicitly references the count.\n"
        "- The final item must land like a punchline, not a recap."
    ),
    "documentary": (
        "DOCUMENTARY-FORMAT OVERRIDE:\n"
        "- Anchor every claim in a specific date, place, name, document, or recording.\n"
        "- Reveal information chronologically when possible; use 'meanwhile' / 'six months earlier' to jump.\n"
        "- The narrator never speculates without flagging it ('the most plausible reading is...').\n"
        "- End with a question the historical record cannot yet answer."
    ),
    "explainer": (
        "EXPLAINER-FORMAT OVERRIDE:\n"
        "- Open with the misconception or knot the viewer arrived holding.\n"
        "- Each paragraph either retires a wrong assumption or introduces a load-bearing analogy.\n"
        "- Use one running analogy throughout — don't switch metaphors mid-script.\n"
        "- End with the cleanest one-sentence version of the answer, then one implication."
    ),
    "true_crime": (
        "TRUE-CRIME-FORMAT OVERRIDE:\n"
        "- Structure like a case file: cold open on one haunting detail, then timeline, evidence, suspects, resolution or open questions.\n"
        "- Name the victim, the date, and the location within the first two sentences.\n"
        "- Present evidence without sensationalizing — let the facts carry the weight.\n"
        "- Never speculate about guilt; use 'investigators believe' or 'evidence suggests.'\n"
        "- End with what remains unanswered or what happened at sentencing — never editorialize justice."
    ),
    "asmr": (
        "ASMR/SLEEP-FORMAT OVERRIDE:\n"
        "- Write at half the normal pacing — longer sentences, gentle rhythm, no urgency.\n"
        "- Zero conflict, zero tension, zero stakes. The viewer is trying to fall asleep.\n"
        "- Paint serene sensory scenes: warm light, soft textures, quiet sounds, slow movement.\n"
        "- Avoid sudden shifts in tone, volume cues, or dramatic reveals.\n"
        "- Repetition is a feature, not a bug — revisit calming images and phrases like a lullaby.\n"
        "- End by trailing off gently, not with a conclusion or call to action."
    ),
    "science": (
        "SCIENCE-EXPLAINER-FORMAT OVERRIDE:\n"
        "- Open with a surprising fact or counterintuitive question that makes the viewer lean in.\n"
        "- Use one concrete analogy per concept — scale abstract numbers to everyday objects.\n"
        "- Build from simple to complex: each paragraph adds one layer of understanding.\n"
        "- Name specific researchers, experiments, dates, or papers when possible.\n"
        "- Never dumb it down with hedging like 'basically' or 'sort of' — be precise and clear.\n"
        "- End with an implication or unanswered frontier question that makes the viewer feel smarter."
    ),
    "conspiracy": (
        "CONSPIRACY/WHAT-IF-FORMAT OVERRIDE:\n"
        "- Present the theory with genuine curiosity, never endorsement or mockery.\n"
        "- Use 'what if' and 'some researchers point to' framing — never state theories as fact.\n"
        "- For each claim, present the evidence proponents cite AND the mainstream counterpoint.\n"
        "- Name specific documents, dates, declassified files, or quotes — vague claims feel lazy.\n"
        "- Build the rabbit hole in layers: surface oddity → deeper pattern → the big question.\n"
        "- End with 'the question remains' or 'you decide' — never a verdict."
    ),
    "travel": (
        "TRAVEL/GEOGRAPHY-FORMAT OVERRIDE:\n"
        "- Open with a vivid sense-of-place moment: a sound, a smell, a view, a taste.\n"
        "- Mix geography, culture, food, history, and one surprising local detail the internet hasn't beaten to death.\n"
        "- Write as an immersive narrator who has been there, not a Wikipedia summary.\n"
        "- Name specific streets, dishes, landmarks, seasons, or local customs.\n"
        "- Vary the scale: pull from a macro aerial view to a close-up human detail.\n"
        "- End with a single image or moment that captures why this place stays with you."
    ),
    "philosophy": (
        "PHILOSOPHY-FORMAT OVERRIDE:\n"
        "- Open with a question that feels personal and urgent, not academic.\n"
        "- Introduce the thinker or framework with a one-sentence biography, then their core idea.\n"
        "- Ground every abstract concept in an everyday scenario the viewer has experienced.\n"
        "- Present at least one strong counterargument — philosophy without tension is a lecture.\n"
        "- Never preach or tell the viewer what to believe. Pose the dilemma and let it sit.\n"
        "- End with a question, not an answer. The best philosophy videos haunt the viewer after."
    ),
    "urban_legend": (
        "URBAN-LEGEND/FOLKLORE-FORMAT OVERRIDE:\n"
        "- Tell it like a local who grew up hearing the story — campfire register, not Wikipedia.\n"
        "- Start with the place and the rule everyone in town follows.\n"
        "- Build with specific sensory details: the creaking, the cold spot, the smell, the sound.\n"
        "- Use pacing tricks: slow down before the reveal, speed up during the scare.\n"
        "- Never fully explain the supernatural element — ambiguity is the engine.\n"
        "- End with a 'they say if you go there today...' or a detail that makes the viewer check behind them."
    ),
    "news": (
        "NEWS-RECAP-FORMAT OVERRIDE:\n"
        "- Cover 3-5 stories. Each gets a one-line hook, the key facts, and one 'why it matters' sentence.\n"
        "- Zero opinion, zero editorializing. Report, don't react.\n"
        "- Use present tense for active stories, past tense for concluded events.\n"
        "- Transitions between stories are one sentence max — no filler bridges.\n"
        "- Name specific people, organizations, numbers, and dates in every story.\n"
        "- End with a forward-looking line about what to watch next, not a sign-off."
    ),
    "review": (
        "PRODUCT-REVIEW-FORMAT OVERRIDE:\n"
        "- Open with who this product is for and what problem it solves — not the unboxing.\n"
        "- Cover build quality, key features, daily-use experience, and value for money in that order.\n"
        "- Give clear, specific pros and cons — 'the screen is good' is useless; 'the 120Hz display eliminates scroll jitter' is useful.\n"
        "- Compare to one or two named competitors at the same price point.\n"
        "- Never sound sponsored. If something is bad, say it plainly.\n"
        "- End with a verdict and one sentence on who should skip it."
    ),
    "motivational": (
        "MOTIVATIONAL-FORMAT OVERRIDE:\n"
        "- Open with a specific person, moment, or failure — not a generic 'we all face challenges.'\n"
        "- Earn the emotional beat: show the struggle in concrete detail before delivering the lesson.\n"
        "- Use short, punchy sentences that land like spoken-word. Every line should caption well.\n"
        "- One core message per video — don't stack three life lessons into one script.\n"
        "- Avoid clichés: 'believe in yourself,' 'never give up,' 'chase your dreams' are banned unless reframed with a specific twist.\n"
        "- End with a line the viewer wants to screenshot and save, not a generic call to action."
    ),
    "survival": (
        "SURVIVAL-FORMAT OVERRIDE:\n"
        "- Open with the scenario: where you are, what went wrong, and what you have.\n"
        "- Each tip gets a clear 'do this, because' structure — action first, then the reason.\n"
        "- Name specific materials, quantities, temperatures, or time windows.\n"
        "- Order by priority: immediate danger first, comfort last.\n"
        "- Include one counterintuitive tip that contradicts common instinct.\n"
        "- End with the one thing the viewer should remember if they forget everything else."
    ),
    "horror": (
        "HORROR-FORMAT OVERRIDE:\n"
        "- Open in the middle of something wrong — a sound, a detail, a feeling that shouldn't be there.\n"
        "- Build dread through what ISN'T shown or said. The scariest detail is the one left to imagination.\n"
        "- Pace the reveals: one unsettling detail per paragraph, escalating in wrongness.\n"
        "- Ground the horror in a real, mundane setting — the familiar made wrong is scarier than fantasy.\n"
        "- Use sensory language: cold, wet, the smell of copper, static on skin.\n"
        "- End ambiguously — did it stop, or did the narrator just stop noticing?"
    ),
    "history": (
        "HISTORY-FORMAT OVERRIDE:\n"
        "- Open with a specific moment in time: a date, a person making a decision, a door opening.\n"
        "- Anchor every claim in primary sources: letters, speeches, records, eyewitness accounts.\n"
        "- Make historical figures feel human — mention their age, their doubts, their personal stakes.\n"
        "- Connect past events to something the modern viewer recognizes or feels.\n"
        "- Use dramatic irony: tell the viewer what the historical figure didn't know yet.\n"
        "- End with the consequence that echoes into today, or the detail history forgot."
    ),
    "kids_edu": (
        "KIDS-EDU-FORMAT OVERRIDE:\n"
        "- Write for ages 6-12: clear vocabulary, short sentences, no jargon without immediate explanation.\n"
        "- Open with a 'did you know' or 'imagine if' that makes a kid say 'whoa.'\n"
        "- Use comparisons to things kids know: school buses, swimming pools, pizza, dinosaurs.\n"
        "- One concept per video — don't overload. Depth over breadth.\n"
        "- Keep it parent-safe: no violence, no fear, no anxiety-inducing content.\n"
        "- End with a fun challenge or question the kid can try or think about after watching."
    ),
    "reddit": (
        "REDDIT-STORY-FORMAT OVERRIDE:\n"
        "- Write in casual first-person as if reading a real post — 'So this happened at work today.'\n"
        "- The hook is the most unbelievable part of the story, stated matter-of-factly.\n"
        "- Include specific mundane details that make it feel real: the time, the coworker's reaction, the exact text message.\n"
        "- Build to a moral dilemma or a 'was I wrong?' moment that divides the audience.\n"
        "- Use conversational asides: 'I know how this sounds, but hear me out.'\n"
        "- End with an unresolved question that makes viewers rush to the comments."
    ),
}


def _detect_format(genre, tone, recipe=""):
    """Best-effort format detection from the user-supplied genre/tone strings.
    Order: recipe-specific matches first (most precise), then broader keyword
    fallbacks. Returns None if no override applies, in which case the prompts
    use STYLE_RULES alone."""
    blob = f"{genre} {tone} {recipe}".lower()
    if "true-crime" in blob or "true crime" in blob or "case file" in blob:
        return "true_crime"
    if "asmr" in blob or "sleep" in blob:
        return "asmr"
    if "conspiracy" in blob or "what-if" in blob or "what if" in blob:
        return "conspiracy"
    if "urban-legend" in blob or "urban legend" in blob or "folklore" in blob:
        return "urban_legend"
    if "news-recap" in blob or "news recap" in blob or "current events" in blob:
        return "news"
    if "product-review" in blob or "product review" in blob or "review" in blob:
        return "review"
    if "motivat" in blob or "mindset" in blob:
        return "motivational"
    if "reddit" in blob or "aita" in blob or "storytime" in blob:
        return "reddit"
    if "kids-edu" in blob or "kids edu" in blob or "children" in blob:
        return "kids_edu"
    if "survival" in blob or "prepper" in blob:
        return "survival"
    if "tutorial" in blob or "how to" in blob or "how-to" in blob or "guide" in blob:
        return "tutorial"
    if "listicle" in blob or "top-list" in blob or "top 10" in blob or "ranked" in blob or "list of" in blob:
        return "listicle"
    if "travel" in blob or "geography" in blob or "wanderlust" in blob:
        return "travel"
    if "philosophy" in blob or "existential" in blob or "ethics" in blob:
        return "philosophy"
    if "science" in blob or "physics" in blob or "biology" in blob or "chemistry" in blob:
        return "science"
    if "history" in blob or "historical" in blob or "ancient" in blob:
        return "history"
    if "documentary" in blob or "true story" in blob or "investigation" in blob:
        return "documentary"
    if "explainer" in blob or "explain" in blob or "why does" in blob or "how does" in blob:
        return "explainer"
    if "horror" in blob or "scary" in blob or "creepypasta" in blob:
        return "horror"
    story_signals = (
        "story", "narration", "mystery", "fiction", "tale",
        "thriller", "drama", "sci-fi", "scifi", "supernatural", "cosmic",
        "creepy", "eerie",
    )
    if any(sig in blob for sig in story_signals):
        return "story"
    return None


def _style_block(genre, tone, recipe=""):
    """Returns STYLE_RULES plus a format-specific override appended."""
    fmt = _detect_format(genre, tone, recipe)
    if fmt and fmt in STYLE_OVERRIDES:
        return f"{STYLE_RULES}\n\n{STYLE_OVERRIDES[fmt]}"
    return STYLE_RULES


# ---------------------------------------------------------------------------
# Ollama wrapper
# ---------------------------------------------------------------------------

def check_ollama():
    """Return list of installed model names, or None if Ollama is not reachable."""
    try:
        r = requests.get(OLLAMA_TAGS_URL, timeout=5)
        r.raise_for_status()
        return [m.get("name", "") for m in r.json().get("models", [])]
    except Exception:
        return None


_POLLINATIONS_TEXT_URL = "https://text.pollinations.ai/"
_ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
_OPENAI_URL = "https://api.openai.com/v1/chat/completions"
_OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


def _generate_via_cloud(prompt, system=SYSTEM_PROMPT, label="",
                        show_progress=True, temperature=0.85,
                        num_predict=None):
    """BYO-API-key text generation. Hits Anthropic or OpenAI directly using
    keys from environment variables. Same return contract as generate():
    plain string. Raises RuntimeError on transport / HTTP errors so callers
    can fall back to Ollama or Pollinations.

    Picked over Ollama when a key is present because the user has explicitly
    opted in (by setting the env var) and frontier-model quality + speed
    crushes any local 8B for script-shaped work.
    """
    backend = _cloud_backend()
    if not backend:
        raise RuntimeError("No cloud LLM key configured (set ANTHROPIC_API_KEY or OPENAI_API_KEY).")
    provider, api_key, model = backend
    max_tokens = int(num_predict) if num_predict else 2000
    if label and show_progress:
        print(f"  [{label}/{provider}:{model}] ", end="", flush=True)
    start = time.time()
    try:
        if provider == "gemini":
            endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
            body = {
                "contents": [{"parts": [{"text": prompt}]}],
                "systemInstruction": {"parts": [{"text": system}]},
                "generationConfig": {"temperature": float(temperature), "maxOutputTokens": max_tokens},
            }
            res = requests.post(endpoint, json=body, timeout=180)
            if not res.ok:
                raise RuntimeError(f"Gemini HTTP {res.status_code}: {res.text[:300]}")
            data = res.json()
            text = ((data.get("candidates") or [{}])[0]
                    .get("content", {})
                    .get("parts", [{}])[0]
                    .get("text", "") or "").strip()
        elif provider == "openrouter":
            body = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                "temperature": float(temperature),
                "max_tokens": max_tokens,
            }
            res = requests.post(
                _OPENROUTER_URL,
                json=body,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://phantomline.xyz",
                    "X-Title": "Phantomline",
                },
                timeout=180,
            )
            if not res.ok:
                raise RuntimeError(f"OpenRouter HTTP {res.status_code}: {res.text[:300]}")
            data = res.json()
            text = ((data.get("choices") or [{}])[0]
                    .get("message", {})
                    .get("content", "") or "").strip()
        elif provider == "openai":
            body = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                "temperature": float(temperature),
                "max_tokens": max_tokens,
            }
            res = requests.post(
                _OPENAI_URL,
                json=body,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                timeout=180,
            )
            if not res.ok:
                raise RuntimeError(f"OpenAI HTTP {res.status_code}: {res.text[:300]}")
            data = res.json()
            text = ((data.get("choices") or [{}])[0]
                    .get("message", {})
                    .get("content", "") or "").strip()
        else:  # anthropic
            body = {
                "model": model,
                "max_tokens": max_tokens,
                "temperature": float(temperature),
                "system": system,
                "messages": [{"role": "user", "content": prompt}],
            }
            res = requests.post(
                _ANTHROPIC_URL,
                json=body,
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json",
                },
                timeout=180,
            )
            if not res.ok:
                raise RuntimeError(f"Anthropic HTTP {res.status_code}: {res.text[:300]}")
            data = res.json()
            blocks = data.get("content") or []
            text = "".join(b.get("text", "") for b in blocks).strip()
    except requests.RequestException as exc:
        raise RuntimeError(f"Cloud LLM unreachable ({provider}): {exc}")
    elapsed = time.time() - start
    if show_progress:
        print(f" done ({len(text.split())} words, {elapsed:.0f}s)")
    return text


def _generate_via_pollinations(prompt, system=SYSTEM_PROMPT, label="",
                               show_progress=True, temperature=0.85,
                               num_predict=None):
    """Fallback text generation via the free public text.pollinations.ai
    endpoint. Used on hosted (phantomline.xyz) where there's no local
    Ollama daemon. Same return contract as generate(): plain string.

    Pollinations mirrors several models (openai, mistral, llama, etc.).
    We use 'openai' as the default since it has the most consistent
    instruction-following for the prompt-engineering-heavy flows
    Phantomline uses (script structure, JSON outputs, title batches)."""
    if label and show_progress:
        print(f"  [{label}/pollinations] ", end="", flush=True)
    start = time.time()
    body = {
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "model": "openai",
        "private": "true",
    }
    # Pollinations honors temperature only on some models; pass it
    # anyway so the request shape matches the OpenAI chat completions
    # contract that their gateway expects.
    if temperature is not None:
        body["temperature"] = float(temperature)
    if num_predict is not None:
        body["max_tokens"] = int(num_predict)
    try:
        # Generous timeout because long-form generations can run 60-90s.
        # Pollinations has occasional slow responses on free tier.
        res = requests.post(_POLLINATIONS_TEXT_URL, json=body, timeout=180)
    except requests.RequestException as exc:
        raise RuntimeError(f"text.pollinations.ai unreachable: {exc}")
    if res.status_code == 429:
        raise RuntimeError("text.pollinations.ai rate-limited (429). Try again in a minute.")
    if not res.ok:
        raise RuntimeError(f"text.pollinations.ai HTTP {res.status_code}: {res.text[:200]}")
    text = (res.text or "").strip()
    elapsed = time.time() - start
    if show_progress:
        print(f" done ({len(text.split())} words, {elapsed:.0f}s)")
    return text


def generate(model, prompt, system=SYSTEM_PROMPT, label="", show_progress=True,
             temperature=0.85, num_predict=None):
    """
    Three-tier text-generation dispatcher:

    1. Cloud BYO-key (Anthropic/OpenAI) when ANTHROPIC_API_KEY or
       OPENAI_API_KEY is set in the environment. Best quality, ~$0.005
       per script, no install friction.
    2. Ollama on localhost:11434 — the desktop default. Streams chunks
       so the user sees progress.
    3. text.pollinations.ai — free public fallback for hosted deploys
       (phantomline.xyz on Render) where neither key nor Ollama exist.

    Returns the full string regardless of which backend served it. Each
    tier's failure is logged and the dispatcher falls through to the
    next; only the last tier's exception bubbles up.

    The `model` arg is only honored by the Ollama path; cloud uses the
    model from PHANTOMLINE_CLOUD_MODEL or a sensible default.
    """
    # Tier 1: cloud BYO key when configured.
    if _cloud_backend():
        try:
            return _generate_via_cloud(
                prompt, system=system, label=label,
                show_progress=show_progress,
                temperature=temperature, num_predict=num_predict,
            )
        except RuntimeError as exc:
            # Don't crash — fall through to Ollama. Operators who set the
            # env var probably do still have Ollama installed.
            if show_progress:
                print(f"\n  (cloud LLM failed: {exc}; falling back to Ollama)")

    # Tier 2: Ollama (desktop original path).
    payload = {
        "model": model,
        "prompt": prompt,
        "system": system,
        "stream": True,
        "options": {"temperature": temperature},
    }
    if num_predict is not None:
        payload["options"]["num_predict"] = num_predict

    if label and show_progress:
        print(f"  [{label}] ", end="", flush=True)

    start = time.time()
    try:
        with requests.post(OLLAMA_GENERATE_URL, json=payload, stream=True, timeout=None) as r:
            if r.status_code == 404:
                # Ollama returns 404 when the model isn't pulled.
                raise RuntimeError(
                    f"Ollama returned 404 - the model '{model}' is not installed.\n"
                    f"Pull it first with:  ollama pull {model}"
                )
            r.raise_for_status()
            chunks = []
            tick = 0
            for line in r.iter_lines():
                if not line:
                    continue
                try:
                    obj = json.loads(line.decode("utf-8"))
                except json.JSONDecodeError:
                    continue
                piece = obj.get("response", "")
                if piece:
                    chunks.append(piece)
                    tick += 1
                    if show_progress and tick % 25 == 0:
                        print(".", end="", flush=True)
                if obj.get("done"):
                    break
    except requests.ConnectionError:
        # Tier 3: Pollinations fallback (hosted mode has no Ollama).
        if show_progress:
            print(" (Ollama unreachable, trying pollinations)")
        return _generate_via_pollinations(
            prompt, system=system, label=label,
            show_progress=show_progress,
            temperature=temperature, num_predict=num_predict,
        )

    elapsed = time.time() - start
    text = "".join(chunks).strip()
    if show_progress:
        print(f" done ({len(text.split())} words, {elapsed:.0f}s)")
    return text


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

def title_prompt(topic, genre, tone):
    """Single-title prompt. Kept for back-compat with anything calling this
    directly. New code should call title_batch_prompt + pick_best_title so the
    runner has 5 candidates to score against channel insights instead of
    being stuck with whatever the model returned first."""
    return f"""\
Generate ONE strong title for a YouTube voiceover video.

Story idea: {topic}
Genre: {genre}
Tone: {tone}

Requirements:
- 4 to 8 words, under 50 characters (mobile-safe).
- Intriguing and clickable without sounding spammy.
- No quotes around the title.
- No emojis. No markdown. No subtitle.
- Output ONLY the title on a single line. Nothing else, no explanation.

Title:"""


# Each candidate is built around a different psychological pull. Mixing
# strategies across the 5-candidate batch dramatically increases the
# chance one of them clicks against the user's existing audience pattern.
# vidIQ-style title-fit scoring picks the winner downstream.
TITLE_STRATEGIES = [
    ("curiosity_gap", "Promise a specific revelation but withhold the key detail. Example shape: 'The lighthouse keeper found a second one — and it wasn't supposed to exist.'"),
    ("contrarian_truth", "State a confident-sounding claim that contradicts the obvious framing. Example shape: 'Why faceless creators stopped caring about subscribers.'"),
    ("specific_number", "Anchor on a concrete number that signals hard-won knowledge. Example shape: '7 minutes that changed how a small town remembered the war.'"),
    ("named_artifact", "Lead with a named object/place/person tied to one strange consequence. Example shape: 'The Vermillion Tape — and the family that watched it twice.'"),
    ("proof_or_receipts", "Lead with proof, evidence, or stakes the viewer can verify. Example shape: 'Inside the satellite log that proves the signal arrived twice.'"),
]


def title_batch_prompt(topic, genre, tone, count=5):
    """Generate `count` title candidates, each built around a distinct
    strategy. Returns a JSON array so the runner can score and rank.

    Why JSON instead of newline-delimited: the model often slips in
    explanations like 'Title 1:' or trailing notes that wreck regex parsing.
    Strict JSON with a hard rule has been more reliable across Llama 3.1,
    3.2, Mistral, and Qwen in our local tests.
    """
    strategies_to_use = TITLE_STRATEGIES[: max(3, min(count, len(TITLE_STRATEGIES)))]
    strategy_block = "\n".join(
        f"- {name}: {desc}" for name, desc in strategies_to_use
    )
    return f"""\
Generate {count} title candidates for a YouTube voiceover video. Each candidate must use a DIFFERENT strategy from the list below — diversity is the goal so we have real options to score, not five rewrites of the same idea.

Story idea: {topic}
Genre: {genre}
Tone: {tone}

STRATEGIES (use a different one for each title):
{strategy_block}

Return ONLY valid JSON. No markdown. No preface.
Schema:
[
  {{"title": "the title", "strategy": "one of: {", ".join(name for name, _ in strategies_to_use)}", "why": "one short sentence on the angle"}}
]

Hard rules:
- 4 to 8 words per title, UNDER 50 CHARACTERS (mobile-safe — YouTube truncates at ~50 chars on mobile).
- No quotes around titles. No emojis. No subtitles. No "Part 1".
- Each title must use a DIFFERENT strategy. Do not repeat a strategy.
- "why" stays under 18 words.
- Output the JSON array only. Nothing before or after."""


def parse_title_batch(raw):
    """Parse the JSON array returned by title_batch_prompt. Falls back to a
    single-title list if the model returned a plain string instead of JSON
    — this keeps backward compat with older models that don't reliably emit
    structured output."""
    if not raw or not raw.strip():
        return []
    text = raw.strip()
    # Strip code fences the model occasionally adds despite the rule.
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```\s*$", "", text)
    try:
        data = json.loads(text)
    except ValueError:
        # Try to find the first [...] block — handles models that prefix
        # explanations despite the prompt's "no preface" rule.
        match = re.search(r"\[[\s\S]*\]", text)
        if not match:
            return [{"title": clean_title(text), "strategy": "fallback", "why": ""}]
        try:
            data = json.loads(match.group(0))
        except ValueError:
            return [{"title": clean_title(text), "strategy": "fallback", "why": ""}]
    if not isinstance(data, list):
        return [{"title": clean_title(str(data)), "strategy": "fallback", "why": ""}]
    out = []
    for item in data:
        if isinstance(item, str):
            t = clean_title(item)
            if t:
                out.append({"title": t, "strategy": "unspecified", "why": ""})
            continue
        if not isinstance(item, dict):
            continue
        title = clean_title(str(item.get("title") or ""))
        if not title:
            continue
        out.append({
            "title": title,
            "strategy": str(item.get("strategy") or "unspecified")[:40],
            "why": str(item.get("why") or "")[:200],
        })
    return out


def _load_insights_safely():
    """Best-effort load of channel insights for title scoring.

    Returns the insights dict if available, None otherwise. Wrapped in
    try/except so the CLI entrypoint (which runs in a fresh process with
    no project wiring) doesn't crash when channel_insights or BASE_DIR
    aren't importable. The runner already falls back to "first candidate"
    when insights are absent."""
    try:
        import channel_insights as _ci
        from core import BASE_DIR as _BASE_DIR
        return _ci.load(_BASE_DIR) or None
    except Exception:
        return None


def pick_best_title(candidates, insights=None):
    """Pick the strongest title from a batch.

    If channel insights are loaded, use channel_insights.title_fit to score
    each and return the highest verdict-rank winner. Otherwise fall back to
    the first candidate (which the model implicitly orders by its own
    confidence — usually fine for fresh channels with no insights yet).

    Returns (title_str, all_scored_candidates). The list is exposed so the
    caller can show the runner-up titles in the UI.
    """
    if not candidates:
        return "Untitled Story", []
    if not insights:
        return candidates[0]["title"], candidates

    # Lazy import: channel_insights pulls Path/json — fine, but story_generator
    # is also CLI-runnable and we don't want a hard dep at import time.
    try:
        import channel_insights as _ci
    except ImportError:
        return candidates[0]["title"], candidates

    verdict_rank = {"strong_fit": 0, "good_fit": 1, "stretch": 2, "neutral": 3, "risky": 4}
    scored = []
    for c in candidates:
        fit = _ci.title_fit(c["title"], insights)
        scored.append({**c, "fit": fit})
    scored.sort(
        key=lambda c: (
            verdict_rank.get(c["fit"].get("verdict", "neutral"), 3),
            -1 * (c["fit"].get("score") or 0),
        )
    )
    return scored[0]["title"], scored


def _seo_keyword_block(seo_keywords=None):
    """Build a prompt block instructing the model to naturally weave SEO
    keywords into the narration so YouTube's auto-captions index them."""
    if not seo_keywords:
        return ""
    phrases = [str(k).strip() for k in seo_keywords if str(k).strip()][:8]
    if not phrases:
        return ""
    return (
        "\n\nSEO KEYWORD THREADING (for YouTube closed-caption indexing):\n"
        "The following keywords/phrases should appear naturally in the spoken narration. "
        "YouTube auto-generates captions from the audio, and these captions are indexed for search. "
        "Weave them in where they fit organically — never force them, never list them, never break "
        "the narrative voice. Aim to use each at least once across the full script. "
        "Prioritize the first 2-3 keywords; the rest are bonus.\n"
        "Keywords: " + ", ".join(f'"{p}"' for p in phrases)
    )


def plan_prompt(topic, genre, tone, title, target_words, seo_keywords=None):
    fmt = _detect_format(genre, tone) or "story"
    kw_block = _seo_keyword_block(seo_keywords)
    return f"""\
Plan a long-form YouTube voiceover of approximately {target_words} words.

Title: {title}
Story idea: {topic}
Genre: {genre}
Tone: {tone}
Detected format: {fmt}

Write a compact internal plan with these labelled sections:
HOOK: the vivid opening promise, problem, mystery, mistake, or story moment (one sentence).
AUDIENCE: who the script is for and what they should get from it.
VISUAL LANGUAGE: recurring images, actions, settings, host moments, or visual metaphors that should appear.
STRUCTURE: the format to follow, such as story, tutorial, listicle, documentary, or explainer.
BEATS: 8 to 12 numbered beats in order. Each beat is one sentence and must introduce a new scene-worthy action, fact, example, discovery, step, reversal, or unanswered question.
ENDING: the memorable final thought, lesson, image, or unresolved question.

EXAMPLE (study the shape, do not copy the content — your topic is different):
HOOK: A small-town librarian receives a return slip for a book that was never checked out and has been missing for forty years.
AUDIENCE: Curious viewers who like slow-burn mysteries grounded in real-feeling small-town texture.
VISUAL LANGUAGE: Rain-streaked library windows, the librarian's index finger tracing card-catalog drawers, the missing book's empty slot, a Polaroid found between pages.
STRUCTURE: Story (mystery), with a single protagonist and a slow reveal across nine beats.
BEATS:
1. The return slip arrives in Monday's mail addressed to a librarian who's only worked there six months.
2. The slip cites a book the catalog confirms was logged out in 1984 and never returned.
3. She finds the original record card initialled by a librarian who died in 1991.
4. The town's oldest patron remembers the borrower — a teenage boy who left town that summer.
5. She drives to the address on the slip; it's an abandoned barn now, but the mailbox is new.
6. Inside the mailbox: the missing book, dust-free, with a Polaroid tucked into chapter seven.
7. The Polaroid shows the dead librarian as a young woman, holding the same book, smiling at someone behind the camera.
8. She compares handwriting on the return slip to the 1984 record card — same hand.
9. Driving home, she sees the barn's mailbox flag is up again. She does not stop.
ENDING: Some books only get returned when the right reader notices they were missing — and some readers are still circling.

{kw_block}

Output the plan only. No commentary before or after."""


def section_prompt(title, plan, rolling_summary, position, sec_target,
                   total_words, target_words, topic, genre, tone,
                   seo_keywords=None):
    if position == "opening":
        position_guidance = (
            "This is the very beginning of the script. Open with the HOOK from the plan "
            "in the first 2-3 sentences. Do NOT begin with generic worldbuilding, "
            "weather descriptions, or 'It was a quiet evening' style openings. "
            "Begin with a vivid problem, question, promise, strange fact, surprising mistake, "
            "or compelling story moment."
        )
    elif position == "ending":
        position_guidance = (
            "This is the FINAL section of the entire script. Bring the narration "
            "to a memorable close consistent with the ENDING in the plan. "
            "Resolve the main promise or arc while leaving the viewer with a clear final "
            "thought, image, lesson, or unanswered question. Do NOT use 'it was all a dream'."
        )
    else:
        position_guidance = (
            "Continue building momentum. Introduce at least one new scene-worthy action, "
            "example, discovery, step, reversal, or unanswered question. "
            "Do NOT rush toward the ending yet."
        )

    summary_block = rolling_summary.strip() if rolling_summary.strip() else "Nothing yet - this is the opening of the story."

    return f"""\
You are continuing a long-form YouTube voiceover.

TITLE: {title}

GENRE: {genre}
TONE: {tone}

INTERNAL PLAN (follow this; do NOT output it):
{plan}

WHAT HAS HAPPENED SO FAR:
{summary_block}

YOUR TASK:
Write the next part of the narration. Aim for roughly {sec_target} words.
Position in the overall story: {position}.
Total story target: ~{target_words} words. Approximately {total_words} words written so far.

{position_guidance}

{_style_block(genre, tone)}
{_seo_keyword_block(seo_keywords)}

Hard rules for THIS section:
- Output narration prose only. Plain text. No markdown, no headings, no scene labels, no chapter titles, no "Section X", no "Part X", no timestamps, no author notes, no asterisks for emphasis.
- Do NOT recap what already happened - continue from it.
- Do NOT repeat phrasing or imagery from the running summary verbatim.
- Do NOT write the title again.
- Do NOT begin with "Here is", "In this section", "Continuing", or any meta phrasing.
- Begin immediately with narration.

EXAMPLES of opening sentences for narration sections (study the texture, never copy the words):
- GOOD opening (concrete, sensory, forward): "She found the second key on the back of the door, taped behind a photograph she didn't remember being taken."
- GOOD opening (mid-action): "By the time the second voicemail played, two things were already true that he could not undo."
- BAD opening (worldbuilding filler): "It was a quiet evening in the small town of Millhaven, where the autumn leaves drifted slowly to the ground."
- BAD opening (meta): "In this section we'll continue the story and find out what happened next to our heroes."
Begin like the GOOD examples — straight into something a viewer can see.

Begin the narration now:"""


def short_script_prompt(topic, genre, tone, title, words, description="",
                        seo_keywords=None):
    viral_mode = "VIRAL SHORTS PLAYBOOK" in (description or "")
    """Prompt for one-shot short-form scripts (30s–10min)."""
    if words < 150:
        guide = ("Keep it tight: one clear hook, one useful idea or story beat, and one memorable payoff. "
                 "Every sentence must advance the story.")
    elif words < 500:
        guide = ("Write one contained video script with a setup, clear progression, "
                 "and a memorable close. No subplots.")
    elif words < 1000:
        guide = ("Write a contained script with a hook, escalating information or action, "
                 "one reversal or payoff, and a memorable close.")
    else:
        guide = ("Write a complete short-form voiceover with a hook, clear structure, "
                 "two beats of escalation, a payoff, and a memorable close.")

    minutes = max(1, round(words / 140))
    direction_block = ""
    if description and description.strip():
        direction_block = f"\nCustom direction (must be honored):\n{description.strip()}\n"

    viral_rules = ""
    if viral_mode:
        viral_rules = """
Viral Story Short hard rules:
- Write addictive unresolved tension, not a normal story.
- Target 35 to 45 seconds unless the requested word count forces otherwise.
- The first sentence is the product. It must be the strongest, clearest line in the script.
- Hook formula: close person + concrete action/object + impossible, suspicious, or emotionally dangerous detail.
- Hook length: 6-13 words when possible.
- Start mid-crisis. Do not introduce the topic. Do not explain context first.
- Good hook examples: "I checked his phone... and saw my name." / "My best friend sent me the wrong screenshot." / "Someone was texting me from my number."
- Bad hooks to avoid: "This is a story about..." / "One day..." / "Have you ever wondered..." / "Let me tell you..."
- Follow this order: shock hook, setup, escalation, twist, loop ending.
- The final line must echo the hook, end mid-thought, or leave missing information.
- Use short spoken lines that caption well: no long winding sentences.
- Every 1-2 sentences should add a new fact, new suspicion, new danger, or new visual.
- Do not explain the lesson. Do not soften the ending. Do not add "Part 1".
"""

    return f"""\
Write a complete YouTube voiceover narration script.

Title: {title}
Story idea: {topic}
Genre: {genre}
Tone: {tone}
{direction_block}
Target length: approximately {words} words (about a {minutes}-minute read at a calm pace).

{guide}
{viral_rules}
{_seo_keyword_block(seo_keywords)}

Hard rules:
- Output narration prose ONLY. Plain text. No markdown, no headings, no scene labels, no chapter titles, no timestamps, no "Here is", no author notes, no asterisks for emphasis.
- Open with a strong hook in the first one or two sentences: a vivid problem, question, promise, strange fact, surprising mistake, or compelling story moment.
- Do NOT begin with generic worldbuilding ("It was a quiet evening", weather descriptions, etc.).
- End with a memorable final line suited to the video format. Do NOT use 'it was all a dream'.
- Follow the requested tone and Custom direction.
- No excessive gore, no sexual content, no heavy profanity.
- Do not write bracketed alternates or correction artifacts like [not], [pause], (beat), or repeated duplicate words.
- Do not write the title as a spoken line. The app displays titles visually.
- Begin immediately with the narration.
"""


def summary_prompt(old_summary, new_section):
    return f"""\
Below is the running summary of a long YouTube voiceover so far, followed by the most recent section.

PREVIOUS SUMMARY:
{old_summary if old_summary.strip() else "(none yet)"}

NEW SECTION:
{new_section}

Update the running summary into a single tight paragraph of under 200 words that captures:
- Characters introduced and their roles
- Key events in order
- Current location
- Current threat or mystery
- Unresolved questions and clues

Output ONLY the updated summary paragraph. No headings, no list, no preface."""


# ---------------------------------------------------------------------------
# Cleaning
# ---------------------------------------------------------------------------

# Lines that are obviously meta and should be dropped entirely.
META_LINE_PATTERNS = [
    re.compile(r"^here(?:'s| is)\b.*", re.IGNORECASE),
    re.compile(r"^(?:i hope|hope you).*", re.IGNORECASE),
    re.compile(r"^(?:section|part|chapter)\s+\d+\b.*", re.IGNORECASE),
    re.compile(r"^title:\s*.+", re.IGNORECASE),
    re.compile(r"^#+\s.*"),                 # markdown headings
    re.compile(r"^---+$"),                  # markdown rules
    re.compile(r"^\*{3,}$"),                # *** dividers
    re.compile(r"^\[[^\]]+\]$"),            # [SCENE: something] standalone
    re.compile(r"^\([^)]+\)$"),             # parenthetical-only stage directions
    re.compile(r"^\*\*[^*]+\*\*\s*$"),      # bold-only "scene label" line
    re.compile(r"^_{2,}[^_]+_{2,}\s*$"),    # __bold__-only line
    re.compile(r"^\(?(?:end of section|to be continued).*", re.IGNORECASE),
]


def clean_section(text):
    """Strip meta lines, markdown emphasis, and tidy whitespace."""
    if not text:
        return ""
    # Strip code fences if any
    text = re.sub(r"^```.*?$", "", text, flags=re.MULTILINE)
    # Some local models emit self-correction artifacts such as:
    # "There's not [not] a soul listening" or "the door opened [opened]".
    # Keep the spoken sentence clean before it reaches TTS/captions.
    text = re.sub(
        r"\b([A-Za-z][A-Za-z'-]*)\s+\[\s*\1\s*\]",
        r"\1",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\b([A-Za-z][A-Za-z'-]*)\s+\(\s*\1\s*\)",
        r"\1",
        text,
        flags=re.IGNORECASE,
    )
    out = []
    for raw in text.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped:
            out.append("")
            continue
        if any(p.match(stripped) for p in META_LINE_PATTERNS):
            continue
        # Remove inline markdown emphasis but keep the words.
        line = re.sub(r"\*\*(.+?)\*\*", r"\1", line)
        line = re.sub(r"__(.+?)__", r"\1", line)
        line = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"\1", line)
        line = re.sub(r"(?<!_)_([^_\n]+)_(?!_)", r"\1", line)
        line = re.sub(r"\s+\[([A-Za-z][A-Za-z'-]{0,24})\](?=\s|[,.!?;:]|$)", "", line)
        out.append(line)
    cleaned = "\n".join(out).strip()
    cleaned = re.sub(r"\b([A-Za-z][A-Za-z'-]*)\s+\1\b", r"\1", cleaned, flags=re.IGNORECASE)
    # Collapse 3+ blank lines to a single blank line between paragraphs.
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned


def sanitize_filename(title, max_len=80):
    safe = re.sub(r"[^A-Za-z0-9 _-]", "", title).strip()
    safe = re.sub(r"\s+", "_", safe)
    safe = safe[:max_len].strip("_")
    return safe or "story"


def clean_title(raw):
    """Pull a single clean title out of whatever the model returned."""
    if not raw:
        return "Untitled Story"
    # Often the model says: Title: Foo  -> strip the prefix
    text = raw.strip().splitlines()[0].strip()
    text = re.sub(r"^title:\s*", "", text, flags=re.IGNORECASE)
    text = text.strip().strip('"').strip("'").strip("*").strip("#").strip()
    # Drop trailing punctuation other than ? !
    text = re.sub(r"[.,;:]+$", "", text)
    return text or "Untitled Story"


# ---------------------------------------------------------------------------
# State / disk I/O
# ---------------------------------------------------------------------------

def write_partial(path, title, sections):
    body = "\n\n".join(sections)
    path.write_text(f"TITLE: {title}\n\n{body}\n", encoding="utf-8")


def write_final(path, title, body):
    path.write_text(f"TITLE: {title}\n\n{body}\n", encoding="utf-8")


def save_state(path, state):
    path.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def load_state(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# CLI / interactive prompts
# ---------------------------------------------------------------------------

def ask(msg, default):
    suffix = f" [{default}]" if default not in (None, "") else ""
    ans = input(f"{msg}{suffix}: ").strip()
    return ans if ans else default


def ask_int(msg, default):
    while True:
        ans = input(f"{msg} [{default}]: ").strip()
        if not ans:
            return default
        try:
            return int(ans.replace(",", ""))
        except ValueError:
            print("  Please enter a whole number.")


def collect_inputs(args):
    """Merge CLI flags + interactive prompts. Flags win; missing fields are asked for."""
    if args.non_interactive:
        return {
            "topic": args.topic or DEFAULT_TOPIC,
            "genre": args.genre or DEFAULT_GENRE,
            "tone": args.tone or DEFAULT_TONE,
            "word_count": args.words or DEFAULT_WORDS,
            "model": args.model or DEFAULT_MODEL,
        }

    print("\n=== Phantomline story engine ===")
    print("Press Enter on any prompt to accept the default in [brackets].\n")
    print("Suggested genres:", ", ".join(GENRE_HINTS), "\n")

    topic = args.topic or ask("Story idea / topic", DEFAULT_TOPIC)
    genre = args.genre or ask("Genre", DEFAULT_GENRE)
    tone = args.tone or ask("Tone", DEFAULT_TONE)
    words = args.words or ask_int("Target word count", DEFAULT_WORDS)
    model = args.model or ask("Ollama model name", DEFAULT_MODEL)

    return {
        "topic": topic,
        "genre": genre,
        "tone": tone,
        "word_count": int(words),
        "model": model,
    }


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def generate_story(inputs, out_dir, resume_state=None, progress_cb=None):
    """
    Run the full story-generation pipeline.

    progress_cb, if given, is called with structured dicts at every stage
    so a web UI (or any other consumer) can render live progress without
    parsing stdout. The CLI also prints to stdout regardless.
    """
    def emit(event, **data):
        if progress_cb:
            try:
                progress_cb({"event": event, **data})
            except Exception:
                pass  # never let UI plumbing crash the generator

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    model = inputs["model"]
    target_words = int(inputs["word_count"])
    topic = inputs["topic"]
    if inputs.get("description"):
        topic = f"{topic}\n\nCreative direction to honor:\n{inputs['description']}"
    seo_keywords = inputs.get("seo_keywords") or []

    # Load insights once — used for both title scoring and system prompt enrichment.
    insights = _load_insights_safely()
    active_system = build_system_prompt(insights)

    if resume_state:
        title = resume_state["title"]
        plan = resume_state["plan"]
        sections = list(resume_state.get("sections", []))
        rolling_summary = resume_state.get("rolling_summary", "")
        total_words = sum(len(s.split()) for s in sections)
        print(f"\n[resume] Continuing '{title}' - {total_words}/{target_words} words "
              f"across {len(sections)} sections.\n")
        emit("resume", title=title, words=total_words, target=target_words, sections=len(sections))
    else:
        # Step 1: title (batch + best-fit pick)
        print("\n[1/3] Generating title candidates...")
        emit("status", message="Generating title candidates...")
        raw_titles = generate(
            model,
            title_batch_prompt(topic, inputs["genre"], inputs["tone"], count=5),
            system=active_system,
            label="titles",
            temperature=0.9,
            num_predict=420,
        )
        candidates = parse_title_batch(raw_titles)
        title, scored = pick_best_title(candidates, insights=insights)
        print(f"      Title: {title}  ({len(candidates)} candidate(s))")
        emit("title", title=title, candidates=scored or candidates)

        # Step 2: plan
        print("\n[2/3] Generating internal story plan...")
        emit("status", message="Generating internal story plan...")
        plan = generate(
            model,
            plan_prompt(topic, inputs["genre"], inputs["tone"], title, target_words,
                       seo_keywords=seo_keywords),
            system=active_system,
            label="plan",
            temperature=0.8,
            num_predict=900,
        )
        emit("plan_done")

        sections = []
        rolling_summary = ""
        total_words = 0

    safe = sanitize_filename(title)
    partial_path = out_dir / f"{safe}.partial.txt"
    state_path = out_dir / f"{safe}.state.json"
    final_path = out_dir / f"{safe}.txt"

    # Persist immediately so resume works even if section 1 crashes.
    state = {
        "title": title,
        "plan": plan,
        "inputs": inputs,
        "sections": sections,
        "rolling_summary": rolling_summary,
    }
    save_state(state_path, state)

    print(f"\n[3/3] Generating narration in sections (~{SECTION_TARGET_WORDS} words each).")
    print(f"      Partial output: {partial_path}")
    print(f"      Resume file:    {state_path}\n")
    emit("paths", partial=str(partial_path), state=str(state_path), final=str(final_path))

    section_num = len(sections) + 1
    while total_words < target_words and section_num <= MAX_SECTIONS:
        remaining = target_words - total_words

        if remaining <= int(SECTION_TARGET_WORDS * 1.3):
            # Close enough to the target - write the ending in this pass.
            sec_target = max(remaining, 800)
            position = "ending"
        else:
            sec_target = SECTION_TARGET_WORDS
            position = "opening" if section_num == 1 else "middle"

        print(f"--- Section {section_num} (target ~{sec_target} words, {position}) ---")
        emit("section_start", section_num=section_num, target=sec_target,
             position=position, total_words=total_words, target_words=target_words)
        prompt = section_prompt(
            title, plan, rolling_summary, position, sec_target,
            total_words, target_words,
            topic, inputs["genre"], inputs["tone"],
            seo_keywords=seo_keywords,
        )
        # num_predict in tokens is roughly 1.4x word count. Give it slack.
        raw = generate(
            model, prompt, system=active_system, label="write",
            temperature=0.85,
            num_predict=int(sec_target * 2),
        )
        section = clean_section(raw)
        words_in_section = len(section.split())

        # Retry once if we got something suspiciously short.
        if words_in_section < 200 and position != "ending":
            print("  (section came back unusually short - retrying once)")
            emit("status", message=f"Section {section_num} came back short - retrying...")
            raw = generate(
                model, prompt, system=active_system, label="retry",
                temperature=0.9,
                num_predict=int(sec_target * 2),
            )
            section = clean_section(raw)
            words_in_section = len(section.split())

        if words_in_section == 0:
            print("  (section is empty after cleaning - skipping and stopping)")
            emit("status", message="Empty section after cleaning - stopping.")
            break

        sections.append(section)
        total_words += words_in_section

        # Update rolling summary from the latest section.
        print(f"  Updating rolling summary...")
        emit("status", message=f"Section {section_num} written ({words_in_section} words). Updating summary...")
        new_summary = generate(
            model,
            summary_prompt(rolling_summary, section),
            label="summary",
            temperature=0.4,
            num_predict=400,
        )
        rolling_summary = new_summary.strip()

        # Save partial draft + state after every section.
        write_partial(partial_path, title, sections)
        state["sections"] = sections
        state["rolling_summary"] = rolling_summary
        save_state(state_path, state)

        print(f"  +{words_in_section} words. Running total: {total_words}/{target_words}.\n")
        emit("section_done", section_num=section_num, words_in_section=words_in_section,
             total_words=total_words, target_words=target_words, position=position)

        if position == "ending":
            break
        section_num += 1

    # Final output
    body = "\n\n".join(sections).strip()
    body = re.sub(r"\n{3,}", "\n\n", body)
    write_final(final_path, title, body)

    final_words = len(body.split())
    print("=" * 60)
    print(f"DONE. {final_words} words across {len(sections)} sections.")
    print(f"Final script: {final_path}")
    print(f"Partial draft kept at: {partial_path}")
    print(f"Resume state kept at:  {state_path}")
    print("=" * 60)
    emit("done", title=title, final_path=str(final_path), words=final_words,
         sections=len(sections))
    return final_path


def generate_short_script(inputs, out_dir, progress_cb=None):
    """
    One-shot short-form generation (30s to ~10min). Skips the multi-section
    orchestration entirely and asks the model for a single tight script.
    Emits the same progress events as generate_story() so the web UI can
    reuse its polling logic.
    """
    def emit(event, **data):
        if progress_cb:
            try:
                progress_cb({"event": event, **data})
            except Exception:
                pass

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    model = inputs["model"]
    target_words = int(inputs["word_count"])
    seo_keywords = inputs.get("seo_keywords") or []

    insights = _load_insights_safely()
    active_system = build_system_prompt(insights)

    print(f"\n[short] Generating {target_words}-word script via {model}...")

    emit("status", message="Generating title candidates...")
    raw_titles = generate(
        model,
        title_batch_prompt(inputs["topic"], inputs["genre"], inputs["tone"], count=5),
        system=active_system,
        label="titles", temperature=0.9, num_predict=420,
    )
    candidates = parse_title_batch(raw_titles)
    title, scored = pick_best_title(candidates, insights=insights)
    print(f"        Title: {title}  ({len(candidates)} candidate(s))")
    emit("title", title=title, candidates=scored or candidates)

    safe = sanitize_filename(title)
    final_path = out_dir / f"{safe}.txt"

    emit("status", message=f"Writing {target_words}-word script...")
    emit("section_start", section_num=1, target=target_words,
         position="short", total_words=0, target_words=target_words)

    raw = generate(
        model,
        short_script_prompt(inputs["topic"], inputs["genre"], inputs["tone"],
                            title, target_words,
                            description=inputs.get("description", ""),
                            seo_keywords=seo_keywords),
        system=active_system,
        label="write", temperature=0.85,
        # Generous token cap so the model isn't truncated; tokens ≈ 1.4 × words.
        num_predict=int(target_words * 2 + 300),
    )
    script = clean_section(raw)
    final_words = len(script.split())

    write_final(final_path, title, script)
    print(f"        {final_words} words -> {final_path}")
    emit("section_done", section_num=1, words_in_section=final_words,
         total_words=final_words, target_words=target_words, position="short")
    emit("done", title=title, final_path=str(final_path),
         words=final_words, sections=1)
    return final_path


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(
        description="Generate a long-form YouTube bedtime story script using local Ollama.",
    )
    p.add_argument("--topic", help="Story idea / topic")
    p.add_argument("--genre", help="Genre")
    p.add_argument("--tone", help="Tone")
    p.add_argument("--words", type=int, help="Target word count")
    p.add_argument("--model", help="Ollama model name (must be installed locally)")
    p.add_argument("--out-dir", default="output", help="Output folder (default: output)")
    p.add_argument("--non-interactive", action="store_true",
                   help="Skip prompts; use flags + defaults only")
    p.add_argument("--resume", metavar="STATE_JSON",
                   help="Path to a *.state.json file from a previous run")
    return p.parse_args()


def main():
    args = parse_args()

    # Resume path - load saved state and skip the input prompts.
    if args.resume:
        state = load_state(args.resume)
        inputs = state["inputs"]
        models = check_ollama()
        if models is None:
            print("ERROR: Ollama is not running at http://localhost:11434.")
            print("Start it (open the Ollama app, or run 'ollama serve') and try again.")
            sys.exit(1)
        if inputs["model"] not in models:
            print(f"WARNING: model '{inputs['model']}' not found in: {models}")
            print(f"Try:  ollama pull {inputs['model']}")
        try:
            generate_story(inputs, args.out_dir, resume_state=state)
        except RuntimeError as e:
            print(f"\nERROR: {e}")
            sys.exit(2)
        return

    inputs = collect_inputs(args)

    print("\nChecking Ollama...")
    models = check_ollama()
    if models is None:
        print("ERROR: Ollama is not running at http://localhost:11434.")
        print("Open the Ollama app, or run 'ollama serve' in another terminal, then retry.")
        sys.exit(1)
    print(f"  Ollama OK. Installed models: {', '.join(models) or '(none)'}")

    if inputs["model"] not in models:
        print(f"\nWARNING: model '{inputs['model']}' is not in the installed list.")
        print(f"  If generation fails, run:  ollama pull {inputs['model']}")
        cont = input("  Continue anyway? [Y/n]: ").strip().lower()
        if cont == "n":
            sys.exit(0)

    try:
        generate_story(inputs, args.out_dir)
    except RuntimeError as e:
        print(f"\nERROR: {e}")
        sys.exit(2)
    except KeyboardInterrupt:
        print("\n\nInterrupted. Partial draft + state file have been saved in the output folder.")
        print("Resume later with:  python story_generator.py --resume <path-to-.state.json>")
        sys.exit(130)


if __name__ == "__main__":
    main()

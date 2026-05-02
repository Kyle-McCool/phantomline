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


def generate(model, prompt, system=SYSTEM_PROMPT, label="", show_progress=True,
             temperature=0.85, num_predict=None):
    """
    Call Ollama's /api/generate with streaming. Prints a dot every ~25 chunks
    so the user can see something is happening. Returns the full string.
    """
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
        raise RuntimeError(
            "Cannot reach Ollama at http://localhost:11434.\n"
            "Make sure Ollama is running ('ollama serve' in a terminal, or open the Ollama app)."
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
    return f"""\
Generate ONE strong title for a YouTube voiceover video.

Story idea: {topic}
Genre: {genre}
Tone: {tone}

Requirements:
- 4 to 10 words.
- Intriguing and clickable without sounding spammy.
- No quotes around the title.
- No emojis. No markdown. No subtitle.
- Output ONLY the title on a single line. Nothing else, no explanation.

Title:"""


def plan_prompt(topic, genre, tone, title, target_words):
    return f"""\
Plan a long-form YouTube voiceover of approximately {target_words} words.

Title: {title}
Story idea: {topic}
Genre: {genre}
Tone: {tone}

Write a compact internal plan with these labelled sections:
HOOK: the vivid opening promise, problem, mystery, mistake, or story moment (one sentence).
AUDIENCE: who the script is for and what they should get from it.
VISUAL LANGUAGE: recurring images, actions, settings, host moments, or visual metaphors that should appear.
STRUCTURE: the format to follow, such as story, tutorial, listicle, documentary, or explainer.
BEATS: 8 to 12 numbered beats in order. Each beat is one sentence and must introduce a new scene-worthy action, fact, example, discovery, step, reversal, or unanswered question.
ENDING: the memorable final thought, lesson, image, or unresolved question.

Output the plan only. No commentary before or after."""


def section_prompt(title, plan, rolling_summary, position, sec_target,
                   total_words, target_words, topic, genre, tone):
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

{STYLE_RULES}

Hard rules for THIS section:
- Output narration prose only. Plain text. No markdown, no headings, no scene labels, no chapter titles, no "Section X", no "Part X", no timestamps, no author notes, no asterisks for emphasis.
- Do NOT recap what already happened - continue from it.
- Do NOT repeat phrasing or imagery from the running summary verbatim.
- Do NOT write the title again.
- Do NOT begin with "Here is", "In this section", "Continuing", or any meta phrasing.
- Begin immediately with narration.

Begin the narration now:"""


def short_script_prompt(topic, genre, tone, title, words, description=""):
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
        # Step 1: title
        print("\n[1/3] Generating title...")
        emit("status", message="Generating title...")
        raw_title = generate(
            model,
            title_prompt(topic, inputs["genre"], inputs["tone"]),
            label="title",
            temperature=0.9,
            num_predict=60,
        )
        title = clean_title(raw_title)
        print(f"      Title: {title}")
        emit("title", title=title)

        # Step 2: plan
        print("\n[2/3] Generating internal story plan...")
        emit("status", message="Generating internal story plan...")
        plan = generate(
            model,
            plan_prompt(topic, inputs["genre"], inputs["tone"], title, target_words),
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
        )
        # num_predict in tokens is roughly 1.4x word count. Give it slack.
        raw = generate(
            model, prompt, label="write",
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
                model, prompt, label="retry",
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

    print(f"\n[short] Generating {target_words}-word script via {model}...")

    emit("status", message="Generating title...")
    raw_title = generate(
        model,
        title_prompt(inputs["topic"], inputs["genre"], inputs["tone"]),
        label="title", temperature=0.9, num_predict=60,
    )
    title = clean_title(raw_title)
    print(f"        Title: {title}")
    emit("title", title=title)

    safe = sanitize_filename(title)
    final_path = out_dir / f"{safe}.txt"

    emit("status", message=f"Writing {target_words}-word script...")
    emit("section_start", section_num=1, target=target_words,
         position="short", total_words=0, target_words=target_words)

    raw = generate(
        model,
        short_script_prompt(inputs["topic"], inputs["genre"], inputs["tone"],
                            title, target_words,
                            description=inputs.get("description", "")),
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

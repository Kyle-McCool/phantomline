# Phantomline

Phantomline creates local AI videos without showing your face. Write stories from scratch,
narrate any book or script with a local TTS, score the result with
ambient music, and mix everything into a single upload-ready file.
Everything runs on your own machine via Ollama, Kokoro, and MusicGen.
Nothing leaves your computer.

You get two ways to run it:

1. **Web UI** on `http://localhost:5000` - clean, professional, runs locally.
2. **Command line** - same engine, no browser needed.

The output file is just:

```
TITLE: <Generated Title>

<full narration script - plain prose, no markdown>
```

No outline, no notes, no "Here is your script", no scene labels, no fluff.

---

## What it does

- Generates a **title** from your idea.
- Builds an **internal story plan** (never written to the final file).
- Writes the script in **sections** of ~1,500 words at a time, because
  most local models can't reliably produce 10,000 words in one shot.
- Maintains a **rolling summary** that's fed back into the model before
  each section, so the story stays coherent across the whole length.
- After every section, saves a **partial draft** + a **state file** so
  you never lose work if generation crashes or you stop it.
- Cleans the output: strips markdown, "Here is", "Section X:", scene
  labels, asterisks for emphasis, etc.
- Saves the final file as `<Title>.txt` in `output/`.
- **Narrates the script with Kokoro TTS** (fully local) and downloads
  it as MP3 - voice picker, speed control, ready for YouTube upload.

---

## Requirements

- **Windows / Mac / Linux**
- **Python 3.9+**
- **Ollama** running locally - install from https://ollama.com
- A pulled model. Recommended: `llama3.1` (or any newer Llama / Mistral / Qwen you prefer).
  ```
  ollama pull llama3.1
  ```
- **Disk space**: ~2 GB free. The TTS dependencies (PyTorch + Kokoro) are
  large. The Kokoro voice model itself (~330 MB) is pulled from
  Hugging Face on the first time you press *Generate audio*.

---

## Setup (Windows)

Open **Command Prompt** or **PowerShell** in the `bedtime-story-gen` folder.

```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Make sure Ollama is running. Either:

- Open the Ollama desktop app, **or**
- Run `ollama serve` in another terminal.

---

## Run the Web UI (recommended)

```
python server.py
```

Then open **http://localhost:5000** in your browser.

Features:

- Form with sensible defaults (sci-fi alien invasion bedtime story, 10,000 words, llama3.1).
- Click-to-fill genre chips for the supported types.
- Auto-detects which Ollama models you have installed and lets you pick one.
- Live progress: current section, running word count, progress bar, log.
- Shows the finished script inline with **Copy** and **Download .txt** buttons.
- Shows the path the file was saved to (`output/<Title>.txt`).
- **Kokoro TTS panel**: pick a voice, set speed, click **Generate audio**,
  preview in the page, and **Download MP3** for your YouTube upload.
  16 calm voices to choose from (American + British, female + male).
  Edits to the script textarea are sent to Kokoro, so you can tweak the
  copy before narration.

The page polls the server every 1.5 seconds - there is no websocket, no
build step, nothing to install on the front end.

---

## Run from the command line

Interactive (prompts you for everything; press Enter to accept defaults):

```
python story_generator.py
```

Non-interactive:

```
python story_generator.py --non-interactive ^
  --topic "A small town's deep-space telescope picks up a centuries-old approach signal" ^
  --genre "sci-fi alien invasion" ^
  --tone "cinematic, slow-burn, eerie, calm bedtime narration" ^
  --words 10000 ^
  --model llama3.1
```

(`^` is the Windows line-continuation character; on Mac/Linux use `\`.)

Resume a job that crashed or was interrupted:

```
python story_generator.py --resume output/<Title>.state.json
```

The CLI prints progress dots while each section streams and prints a
summary line after every section.

---

## Supported story types

The defaults work great for:

- sci-fi alien invasion *(default)*
- mystery
- strange disappearances
- deep ocean horror
- ancient alien discoveries
- abandoned towns
- lost transmissions
- government coverups
- cosmic horror

You can also type any custom genre / tone - the pipeline is generic.

---

## Output

Everything lands in `output/`:

| File | What |
| --- | --- |
| `<Title>.txt` | **Final clean script.** This is the file you use. |
| `<Title>.partial.txt` | Saved after every section while generating. Same format. Useful if generation fails halfway. |
| `<Title>.state.json` | Internal plan + sections + rolling summary. Used for `--resume`. Safe to delete after the final file is written. |
| `<Title>.mp3` | Kokoro narration audio (when you press **Generate audio**). |

The final file is exactly:

```
TITLE: The Signal That Was Always There

It started the night the antenna stopped lying...
```

No markdown. No bullet points. No scene labels. No author notes.

---

## Tuning notes

- **Length**: bigger `--words` = longer script. Each section is ~1,500
  words, so 10,000 words is roughly 7 sections. The final section is
  reserved for a coherent ending.
- **Model**: `llama3.1` is the default. Larger models (e.g. `llama3.1:70b`)
  give better prose but are much slower. Smaller models (e.g. `llama3.2:3b`)
  are faster but may need more cleanup.
- **Speed**: a 10,000-word story on a typical 8B model takes 5–15 minutes
  on a modern PC. Watch the log - if it stalls for minutes, your model
  may be too large for your VRAM and is swapping to CPU.
- **Quality**: if a story comes out flat, try a different genre/tone, or
  edit the topic to specify one *concrete strange thing* that happens -
  e.g. "a lighthouse keeper finds a second lighthouse on the same
  island that wasn't there yesterday".

---

## Troubleshooting

**"Cannot reach Ollama at http://localhost:11434"**
Open the Ollama app, or run `ollama serve` in another terminal.

**"Ollama returned 404 - the model 'X' is not installed"**
Run `ollama pull X` first.

**The script came out short**
Increase `--words`, or your local model is hitting its `num_predict`
limit per turn. The generator already retries short sections once.

**The web UI shows "ollama offline"**
Same fix as above - the badge re-checks every 15 seconds.

**Generation crashed midway**
Re-run with `python story_generator.py --resume output/<Title>.state.json`.
You'll continue from the last saved section.

---

## File map

```
bedtime-story-gen/
├── story_generator.py   # long + short story generation engine + CLI
├── tts.py               # Kokoro TTS wrapper
├── music.py             # MusicGen + crossfade-loop + narration mixdown
├── projects.py          # persistent project store (survives restart)
├── server.py            # Flask web UI on localhost:5000
├── templates/
│   ├── index.html       # the studio app
│   └── landing.html     # marketing page at /landing
├── requirements.txt
├── README.md
└── output/
    ├── projects/        # one folder per saved project
    │   └── <id>/        # script.txt, audio.mp3, etc + tracked in projects.json
    ├── projects.json    # project index, atomic writes
    └── uploaded/        # one-shot uploads (also indexed as projects)
```

## What's where in the UI

The studio uses a left sidebar (Create / Library) instead of top tabs.

**Create**
- **Long Story** - section-by-section 10,000+ word generation with rolling summary.
- **Short Script** - 30-second to 10-minute one-shot scripts. Includes 12 niche presets and a "describe the script" field.
- **Narrate Text** - paste any text, narrate with Kokoro, download MP3.
- **Music & Mix** - generate background music with MusicGen, crossfade-loop to any length, mix with narration (or an uploaded narration file) into a single MP3.

**Library**
- **All Projects** - every story, narration, music bed, mix, and uploaded file you've created. Filter by type. Inline audio preview. Download / delete per card. Persists across server restarts.

A landing page is served at `/landing` for sharing screenshots or demoing the app.

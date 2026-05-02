"""Shared module-level state for Phantomline.

server.py and the route Blueprints under routes/ all import from here.
This module imports nothing from the rest of the project, so the import
graph stays acyclic:

    core         <- (no project imports)
    server.py    <- core, project modules, blueprints
    routes/*.py  <- core, flask, project modules

Anything that two or more route groups need to share should live here:
output paths, the persistent ProjectStore singleton, JSON read/write
helpers. Domain-specific state (job tables, locks, prompt builders) stays
in the route module that owns it.
"""

from __future__ import annotations

import csv
import json
import os
import re
from io import StringIO
from pathlib import Path

import projects as project_store
import story_generator as sg


BASE_DIR: Path = Path(__file__).resolve().parent
OUTPUT_DIR: Path = BASE_DIR / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Persistent project store. Survives server restarts. Singleton-ish.
PROJECTS = project_store.ProjectStore(OUTPUT_DIR)


def _read_json_file(path: Path, fallback):
    """Best-effort read of a JSON file. Returns `fallback` if missing or
    corrupt. Never raises."""
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass
    return fallback


def _write_json_file(path: Path, payload) -> None:
    """Atomic JSON write via tmp + rename. Creates parent dirs as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _parse_analytics_upload(file_storage):
    """Parse a YouTube Studio CSV/TSV export into a list of normalized dict
    rows. Tolerant of varying delimiters, BOMs, and pre-header garbage.
    Used by the channel insights import routes and the analytics analyzer."""
    raw = file_storage.read()
    text = raw.decode("utf-8-sig", errors="replace")
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;")
    except csv.Error:
        dialect = csv.excel
    raw_rows = list(csv.reader(StringIO(text), dialect=dialect))
    header_index = 0
    for idx, row in enumerate(raw_rows[:40]):
        normalized = [re.sub(r"\s+", " ", str(cell or "")).strip().lower() for cell in row]
        joined = " | ".join(normalized)
        if "video title" in normalized or "title" in normalized or ("video title" in joined and "views" in joined):
            header_index = idx
            break
    if not raw_rows:
        return []
    headers = [
        re.sub(r"\s+", " ", str(cell or "").strip())
        for cell in raw_rows[header_index]
    ]
    rows = []
    for raw_row in raw_rows[header_index + 1:]:
        if not any(str(cell or "").strip() for cell in raw_row):
            continue
        row = {}
        for i, header in enumerate(headers):
            if not header:
                continue
            row[header] = raw_row[i] if i < len(raw_row) else ""
        rows.append(row)
    cleaned = []
    for row in rows[:5000]:
        item = {}
        for key, value in row.items():
            if not key:
                continue
            k = re.sub(r"\s+", " ", str(key)).strip()
            v = str(value or "").strip()
            if k and v:
                item[k] = v
        if item:
            cleaned.append(item)
    return cleaned


# ---------------------------------------------------------------------------
# YouTube OAuth connection — used by both the publish flow and the
# optimize-library flow. The on-disk JSON sits next to the publish state
# files. Stored in core.py so multiple blueprints can read without coupling
# to publish.py.
# ---------------------------------------------------------------------------

PUBLISH_DIR: Path = OUTPUT_DIR / "publishing"
PUBLISH_DIR.mkdir(parents=True, exist_ok=True)
YOUTUBE_CONNECTION_PATH: Path = PUBLISH_DIR / "youtube_connection.json"


def _youtube_connection():
    data = _read_json_file(YOUTUBE_CONNECTION_PATH, None)
    return data if isinstance(data, dict) else None


def _save_youtube_connection(connection):
    _write_json_file(YOUTUBE_CONNECTION_PATH, connection)


# ---------------------------------------------------------------------------
# LLM helpers used across multiple route blueprints.
# ---------------------------------------------------------------------------

def _resolve_ollama_model(model):
    """Return an installed Ollama model tag when the UI sends a short alias."""
    requested = (model or sg.DEFAULT_MODEL).strip() or sg.DEFAULT_MODEL
    models = sg.check_ollama()
    if models is None:
        return requested
    if requested in models:
        return requested
    if ":" not in requested:
        latest = f"{requested}:latest"
        if latest in models:
            return latest
    base = requested.split(":", 1)[0]
    for installed in models:
        if installed.split(":", 1)[0] == base:
            return installed
    return requested


def _extract_json_array(text):
    """Best-effort JSON array extraction from local model output."""
    if not text:
        return []
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?", "", cleaned, flags=re.I).strip()
    cleaned = re.sub(r"```$", "", cleaned).strip()
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("[")
        end = cleaned.rfind("]")
        if start == -1 or end == -1 or end <= start:
            return []
        try:
            parsed = json.loads(cleaned[start:end + 1])
        except json.JSONDecodeError:
            return []
    return parsed if isinstance(parsed, list) else []


def _extract_json_object(text):
    """Best-effort JSON object extraction from local model output."""
    if not text:
        return {}
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?", "", cleaned, flags=re.I).strip()
    cleaned = re.sub(r"```$", "", cleaned).strip()
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return {}
        try:
            parsed = json.loads(cleaned[start:end + 1])
        except json.JSONDecodeError:
            return {}
    return parsed if isinstance(parsed, dict) else {}


def _pick_focus_keyword(*, topic="", title="", niche="", research_keyword="",
                        insights=None, extra_phrases=None):
    """Pick ONE focus keyword + variations to thread through title/description/tags.

    vidIQ rewards a single keyword that appears verbatim in the title, the
    first line of the description, and the tag list (the "tripled keyword").
    This helper picks that anchor by preferring channel-insight phrases that
    overlap the current topic, then research keyword, then any phrase pool
    we have, then the niche/topic itself."""
    insights = insights or {}
    extra_phrases = extra_phrases or []
    seed_text = " ".join(filter(None, [str(topic or ""), str(title or "")])).lower()
    seed_words = set(re.findall(r"[a-z0-9']+", seed_text))

    def overlap(phrase):
        words = set(re.findall(r"[a-z0-9']+", (phrase or "").lower()))
        if not words:
            return 0
        return len(words & seed_words)

    insight_pool = []
    for g in (insights.get("gap_keywords") or []):
        if str(g).strip():
            insight_pool.append(str(g).strip())
    for k in (insights.get("seo_keywords") or []):
        if str(k).strip():
            insight_pool.append(str(k).strip())
    for term in (insights.get("search_terms") or []):
        q = term.get("query") if isinstance(term, dict) else term
        if q and str(q).strip():
            insight_pool.append(str(q).strip())

    sorted_insights = sorted(insight_pool, key=lambda p: -overlap(p)) if seed_words else list(insight_pool)
    best_insight = next((p for p in sorted_insights if seed_words and overlap(p) > 0), "")

    focus = (
        best_insight
        or (research_keyword or "").strip()
        or (sorted_insights[0] if sorted_insights else "")
        or next((p.strip() for p in extra_phrases if str(p).strip()), "")
        or (niche or "").strip()
        or (topic or title or "").strip().split(".")[0]
    )
    focus = re.sub(r"\s+", " ", focus).strip().lower()[:60]

    variations = []
    seen = {focus}
    pool = list(sorted_insights) + list(extra_phrases)
    for raw in pool:
        v = re.sub(r"\s+", " ", str(raw or "")).strip().lower()
        if not v or v in seen or len(v) > 60:
            continue
        seen.add(v)
        variations.append(v)
        if len(variations) >= 6:
            break
    return {"focus": focus, "variations": variations}

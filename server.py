"""
Local web UI for Ghostline, the local AI video studio.

Runs a small Flask server on http://localhost:5000.
The page sends form input to /api/start, the server runs generation
in a background thread, and the page polls /api/status/<job_id> for
progress until the script is ready to download or copy.
"""

import base64
import csv
import json
import os
import re
import threading
import time
import uuid
import wave
from difflib import SequenceMatcher
from io import BytesIO
from io import StringIO
from pathlib import Path

import requests
from flask import Flask, jsonify, redirect, render_template, request, send_file
from PIL import Image, ImageStat

import story_generator as sg
# Heavy ML modules are imported defensively so the hosted (Render) tier can
# boot without kokoro / transformers / pytorch installed. When these are
# None, any route that needs server-side TTS or music generation returns
# 503 — see _require_local_ai() below. The desktop install pulls in
# requirements-desktop.txt and gets the real modules.
try:
    import tts as tts_mod
except Exception as _tts_import_err:
    tts_mod = None
try:
    import music as music_mod
except Exception as _music_import_err:
    music_mod = None
import projects as project_store
try:
    import video_assembler
except Exception:
    video_assembler = None
import youtube_publish
import youtube_research
import channel_insights

# Shared module-level state — paths, the persistent ProjectStore singleton,
# and JSON read/write helpers. Lives in core.py so route Blueprints can
# import from it without dragging in the rest of server.py.
from core import (
    BASE_DIR,
    OUTPUT_DIR,
    PROJECTS,
    PUBLISH_DIR,
    YOUTUBE_CONNECTION_PATH,
    _read_json_file,
    _write_json_file,
    _parse_analytics_upload,
    _resolve_ollama_model,
    _extract_json_array,
    _extract_json_object,
    _pick_focus_keyword,
    _youtube_connection,
    _save_youtube_connection,
)
from routes.system import system_bp
from routes.launch import launch_bp
from routes.insights import insights_bp
from routes.bundles import bundles_bp
from routes.research import research_bp
from routes.optimize import optimize_bp
from routes.billing import billing_bp, consume_quota, enforce_tier


app = Flask(__name__)
app.register_blueprint(system_bp)
app.register_blueprint(launch_bp)
app.register_blueprint(insights_bp)
app.register_blueprint(bundles_bp)
app.register_blueprint(research_bp)
app.register_blueprint(optimize_bp)
app.register_blueprint(billing_bp)
# Stories are tiny but uploaded narration audio can be hundreds of MB
# (an hour of WAV is ~330 MB, MP3 ~50 MB). Allow up to 1 GB.
app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024 * 1024
# Re-read templates on every request so HTML/CSS edits show up without a restart.
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.jinja_env.auto_reload = True


# ---------------------------------------------------------------------------
# Security hardening. The Flask server only listens on 127.0.0.1, so the
# threat model is mostly: malicious browser tabs / extensions / iframes that
# try to reach Ghostline through the user's localhost. These defenses make
# that materially harder.
# ---------------------------------------------------------------------------

_ALLOWED_ORIGINS = {
    "http://127.0.0.1:5000", "http://localhost:5000",
    "https://127.0.0.1:5000", "https://localhost:5000",
    # Production origins — without these the same-origin CSRF guard below
    # would 403 every POST coming from the deployed site (browsers send the
    # actual page Origin on POST, even for same-origin requests).
    "https://phantomline.xyz", "https://www.phantomline.xyz",
    "https://phantomline.onrender.com",
}


@app.before_request
def _enforce_same_origin_writes():
    """Reject cross-origin writes. Localhost services are notoriously easy
    to attack from random web pages; require POST/PUT/PATCH/DELETE to come
    from a tab the user actually has open on Ghostline itself."""
    if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
        return None
    origin = (request.headers.get("Origin") or "").strip()
    referer = (request.headers.get("Referer") or "").strip()
    # Empty Origin is allowed (server-to-server, curl, browser navigation
    # form posts) — Flask's flow doesn't include those for our endpoints.
    # If Origin is set, require it to match. Same for Referer if Origin
    # is missing.
    if origin:
        if origin not in _ALLOWED_ORIGINS:
            return jsonify({"ok": False, "error": "Cross-origin request blocked."}), 403
    elif referer:
        if not any(referer.startswith(o + "/") or referer == o for o in _ALLOWED_ORIGINS):
            return jsonify({"ok": False, "error": "Cross-origin request blocked."}), 403
    return None


@app.after_request
def _security_headers(response):
    """Defense-in-depth headers. CSP keeps scripts and styles to the same
    origin (we already self-host every asset; only wavesurfer is a CDN).
    X-Frame-Options stops other sites from embedding Ghostline in an
    iframe to clickjack the user."""
    # WebLLM (used by the mobile/PWA on-device inference path) loads its ES
    # module bundle from jsdelivr and downloads model weights from
    # Hugging Face. Both must be allow-listed even if the user never opts in,
    # so the engine can lazy-load when they do.
    # WebLLM, Web Speech, Web Audio, ffmpeg.wasm, and Pexels search all run
    # client-side from the user's browser. CSP allow-lists the specific
    # CDNs each one fetches from. img + media broadened to blob:/data: so
    # generated MP4 / WAV blobs render in <video>/<audio>.
    csp = (
        "default-src 'self'; "
        "img-src 'self' data: blob: https://images.pexels.com; "
        "media-src 'self' blob: https://*.pexels.com https://cdn.pixabay.com; "
        "font-src 'self' data:; "
        "style-src 'self' 'unsafe-inline'; "  # inline styles still used in places
        "script-src 'self' 'wasm-unsafe-eval' https://unpkg.com https://cdn.jsdelivr.net; "
        "worker-src 'self' blob:; "
        "connect-src 'self' https://huggingface.co https://*.huggingface.co "
        "https://raw.githubusercontent.com https://cdn.jsdelivr.net "
        "https://api.pexels.com https://*.pexels.com https://pixabay.com https://cdn.pixabay.com; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'"
    )
    response.headers.setdefault("Content-Security-Policy", csp)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault(
        "Permissions-Policy",
        "geolocation=(), microphone=(), camera=(), interest-cohort=(), payment=()",
    )
    # Hide the Flask/werkzeug version string.
    response.headers["Server"] = "Phantomline"
    return response


@app.template_filter("versioned")
def _versioned_filter(path):
    """Append a cache-busting ?v=<mtime> to a /static/... path so browsers
    pick up new assets without manual hard-refresh."""
    if not path or not path.startswith("/static/"):
        return path
    rel = path[len("/static/"):]
    full = Path(__file__).resolve().parent / "static" / rel
    try:
        mtime = int(full.stat().st_mtime)
    except OSError:
        return path
    sep = "&" if "?" in path else "?"
    return f"{path}{sep}v={mtime}"


# ---------------------------------------------------------------------------
# SEO infrastructure
#
# - SITE_URL: canonical origin for absolute URLs in canonical/og/sitemap.
#   Override via SITE_URL env var if we ever change domains.
# - context processor: every Jinja template gets `site_url` plus a
#   pre-computed `canonical_url` for the current request, so templates
#   can render `<link rel="canonical" href="{{ canonical_url }}">` without
#   each route having to pass it explicitly.
# - /robots.txt and /sitemap.xml: standard discovery files. Robots is
#   static-ish text; sitemap is generated from a small route registry so
#   we don't drift from reality every time we add a page.
# ---------------------------------------------------------------------------
SITE_URL = os.environ.get("SITE_URL", "https://phantomline.xyz").rstrip("/")


@app.context_processor
def _seo_globals():
    """Inject canonical-URL helpers into every template render. The
    canonical_url is the production-domain URL for whatever path the
    current request is on, with no query string and no trailing slash
    (except for "/" itself)."""
    path = request.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    return {
        "site_url": SITE_URL,
        "canonical_url": f"{SITE_URL}{path}",
    }


# Static portion of the sitemap. Per-competitor /alternatives/<slug>
# pages are appended dynamically in sitemap_xml() from alternatives.py.
# Routes that are app-internal (e.g. /api/*, /app, /studio) stay out —
# Google should index marketing surfaces, not the workspace.
_SITEMAP_ROUTES = [
    ("/",                              "1.0", "weekly"),
    ("/pricing",                       "0.9", "monthly"),
    ("/local-ai-video-generator",      "0.85", "monthly"),
    ("/faceless-youtube",              "0.85", "monthly"),
    ("/alternatives",                  "0.8", "monthly"),
    ("/about",                         "0.7", "monthly"),
    ("/privacy",                       "0.4", "yearly"),
    ("/terms",                         "0.4", "yearly"),
    ("/landing",                       "0.3", "monthly"),  # legacy alias
]


@app.route("/robots.txt")
def robots_txt():
    """Tell crawlers what to index. We allow the marketing surface +
    static assets (Googlebot needs CSS/JS to render the page properly),
    block API/admin/auth, and explicitly opt in well-behaved AI crawlers
    (GPTBot, ClaudeBot, PerplexityBot) — Phantomline benefits from being
    discoverable by AI search."""
    body = (
        "User-agent: *\n"
        "Allow: /\n"
        "Allow: /sw.js\n"
        "Allow: /static/manifest.json\n"
        "Allow: /static/\n"
        "Disallow: /api/\n"
        "Disallow: /admin/\n"
        "Disallow: /auth/\n"
        "Disallow: /account/\n"
        "Disallow: /*?token=\n"
        "Disallow: /*?session=\n"
        "\n"
        "User-agent: GPTBot\n"
        "Allow: /\n"
        "\n"
        "User-agent: ClaudeBot\n"
        "Allow: /\n"
        "\n"
        "User-agent: PerplexityBot\n"
        "Allow: /\n"
        "\n"
        f"Sitemap: {SITE_URL}/sitemap.xml\n"
    )
    response = app.response_class(body, mimetype="text/plain")
    response.headers["Cache-Control"] = "public, max-age=3600"
    return response


@app.route("/sitemap.xml")
def sitemap_xml():
    """Generate sitemap.xml from the static route registry plus the
    dynamic per-competitor alternative pages. lastmod is set to today on
    every render — fine for a small site; revisit if we ever add
    programmatic page generation (then track per-page mtimes)."""
    from alternatives import COMPETITORS
    today = time.strftime("%Y-%m-%d")
    routes = list(_SITEMAP_ROUTES)
    # Each competitor alternative page is a real ranking target.
    for c in COMPETITORS:
        routes.append((f"/alternatives/{c['slug']}", "0.7", "monthly"))
    urls = "\n".join(
        f"  <url>\n"
        f"    <loc>{SITE_URL}{path}</loc>\n"
        f"    <lastmod>{today}</lastmod>\n"
        f"    <changefreq>{changefreq}</changefreq>\n"
        f"    <priority>{priority}</priority>\n"
        f"  </url>"
        for (path, priority, changefreq) in routes
    )
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{urls}\n"
        "</urlset>\n"
    )
    response = app.response_class(body, mimetype="application/xml")
    response.headers["Cache-Control"] = "public, max-age=3600"
    return response

# In-memory job tables. One Python process = one user, so this is fine.
JOBS = {}
JOBS_LOCK = threading.Lock()

TTS_JOBS = {}
TTS_LOCK = threading.Lock()

MUSIC_JOBS = {}
MUSIC_LOCK = threading.Lock()

MIX_JOBS = {}
MIX_LOCK = threading.Lock()

VIDEO_JOBS = {}
VIDEO_LOCK = threading.Lock()

VIDEO_UPLOAD_EXTS = {".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv"}

# OUTPUT_DIR, BASE_DIR, PROJECTS, PUBLISH_DIR, YOUTUBE_CONNECTION_PATH,
# _read_json_file, _write_json_file, _youtube_connection, _save_youtube_connection
# all live in core.py (imported above).
PUBLISH_POSTS_PATH = PUBLISH_DIR / "posts.json"
PUBLISH_TEMPLATES_PATH = PUBLISH_DIR / "templates.json"
PUBLISH_RECURRING_PATH = PUBLISH_DIR / "recurring.json"
PUBLISH_LOCK = threading.Lock()


def _publish_posts():
    data = _read_json_file(PUBLISH_POSTS_PATH, [])
    return data if isinstance(data, list) else []


def _save_publish_posts(posts):
    posts.sort(key=lambda p: p.get("scheduled_at") or p.get("created_at") or "", reverse=True)
    _write_json_file(PUBLISH_POSTS_PATH, posts)


def _publish_templates():
    data = _read_json_file(PUBLISH_TEMPLATES_PATH, [])
    return data if isinstance(data, list) else []


def _save_publish_templates(templates):
    _write_json_file(PUBLISH_TEMPLATES_PATH, templates)


def _publish_recurring():
    data = _read_json_file(PUBLISH_RECURRING_PATH, [])
    return data if isinstance(data, list) else []


def _save_publish_recurring(items):
    _write_json_file(PUBLISH_RECURRING_PATH, items)


# _parse_analytics_upload lives in core (imported above) — used by both the
# insights blueprint and the publish/analytics analyzer here.


def _numberish(value):
    try:
        return float(re.sub(r"[^0-9.\-]", "", str(value)) or 0)
    except ValueError:
        return 0.0


def _percentish(value):
    return _numberish(value)


def _duration_to_seconds(value):
    text = str(value or "").strip()
    if not text:
        return 0
    parts = [p for p in text.split(":") if p != ""]
    try:
        nums = [int(float(p)) for p in parts]
    except ValueError:
        return 0
    if len(nums) == 3:
        return nums[0] * 3600 + nums[1] * 60 + nums[2]
    if len(nums) == 2:
        return nums[0] * 60 + nums[1]
    return nums[0] if nums else 0


def _find_column(columns, exact=(), contains=()):
    lowered = {c: c.lower() for c in columns}
    for wanted in exact:
        for col, low in lowered.items():
            if low == wanted.lower():
                return col
    for needle in contains:
        for col, low in lowered.items():
            if needle.lower() in low:
                return col
    return None


def _analytics_row_metrics(row, keys):
    views = _numberish(row.get(keys.get("views")))
    views_30d = _numberish(row.get(keys.get("views_30d")))
    ctr = _percentish(row.get(keys.get("ctr")))
    avg_viewed = _percentish(row.get(keys.get("avg_viewed")))
    dropoff_pct = _percentish(row.get(keys.get("dropoff_pct")))
    clicks = _numberish(row.get(keys.get("clicks")))
    title = row.get(keys.get("title"), "")
    return {
        "title": title,
        "date": row.get(keys.get("date"), ""),
        "pillar": row.get(keys.get("pillar"), ""),
        "views": views,
        "views_30d": views_30d,
        "ctr": ctr,
        "avg_viewed": avg_viewed,
        "dropoff_timestamp": row.get(keys.get("dropoff_timestamp"), ""),
        "dropoff_pct": dropoff_pct,
        "traffic_source": row.get(keys.get("traffic_source"), ""),
        "clicks": clicks,
        "lesson": row.get(keys.get("lesson"), ""),
        "length_seconds": _duration_to_seconds(row.get(keys.get("length"))),
    }


def _title_features(title):
    text = (title or "").strip()
    words = re.findall(r"\b[\w'-]+\b", text)
    lower = text.lower()
    return {
        "word_count": len(words),
        "has_number": bool(re.search(r"\b\d+\b", text)),
        "has_episode": "episode" in lower,
        "has_ai": "ai" in lower,
        "has_question": "?" in text,
        "has_colon": ":" in text,
        "has_pipe": "|" in text,
        "starts_with_why": lower.startswith("why "),
        "starts_with_how": lower.startswith("how "),
        "has_problem_language": bool(re.search(r"\b(wrong|missing|reason|never|aren't|isn't|bugs?|finish|switching|simple|need)\b", lower)),
    }


def _analytics_summary(rows):
    if not rows:
        return {"rows": 0, "columns": [], "top_rows": []}
    columns = list(rows[0].keys())
    keys = {
        "title": _find_column(columns, exact=("Video Title", "Title", "Content")),
        "date": _find_column(columns, contains=("post date", "published", "date")),
        "pillar": _find_column(columns, contains=("pillar", "topic", "category")),
        "ctr": _find_column(columns, exact=("CTR",), contains=("click-through", "ctr")),
        "avg_viewed": _find_column(columns, contains=("avg % viewed", "average percentage viewed", "retention")),
        "views": _find_column(columns, exact=("Views (Lifetime)", "Views"), contains=("views lifetime",)),
        "views_30d": _find_column(columns, contains=("views at 30d", "30d")),
        "length": _find_column(columns, contains=("video length", "duration", "length")),
        "dropoff_timestamp": _find_column(columns, contains=("drop-off timestamp", "drop off timestamp")),
        "dropoff_pct": _find_column(columns, contains=("drop-off %", "drop off %")),
        "traffic_source": _find_column(columns, contains=("top traffic source", "traffic")),
        "clicks": _find_column(columns, contains=("clicks to", "website clicks", "link clicks")),
        "lesson": _find_column(columns, contains=("lesson", "notes")),
    }
    if not keys["title"]:
        keys["title"] = columns[0]
    if not keys["views"]:
        keys["views"] = next((c for c in columns if "view" in c.lower() and "avg" not in c.lower() and "%" not in c.lower()), None)

    metrics = [_analytics_row_metrics(row, keys) for row in rows]
    metrics = [m for m in metrics if m["title"]]
    top_by_views = sorted(metrics, key=lambda r: r["views"], reverse=True)
    top_by_ctr = sorted([m for m in metrics if m["ctr"]], key=lambda r: r["ctr"], reverse=True)
    top_by_retention = sorted([m for m in metrics if m["avg_viewed"]], key=lambda r: r["avg_viewed"], reverse=True)
    worst_by_retention = sorted([m for m in metrics if m["avg_viewed"]], key=lambda r: r["avg_viewed"])
    worst_by_ctr = sorted([m for m in metrics if m["ctr"]], key=lambda r: r["ctr"])

    def compact(row):
        return {
            "title": row["title"],
            "date": row["date"],
            "pillar": row["pillar"],
            "views": round(row["views"]),
            "views_30d": round(row["views_30d"]),
            "ctr": row["ctr"],
            "avg_viewed": row["avg_viewed"],
            "dropoff": f"{row['dropoff_timestamp']} / {row['dropoff_pct']}%".strip(" /%"),
            "traffic_source": row["traffic_source"],
            "clicks": round(row["clicks"]),
            "lesson": row["lesson"],
            "title_features": _title_features(row["title"]),
        }

    pillars = {}
    traffic = {}
    for row in metrics:
        if row["pillar"]:
            bucket = pillars.setdefault(row["pillar"], {"videos": 0, "views": 0, "ctr_values": [], "retention_values": []})
            bucket["videos"] += 1
            bucket["views"] += row["views"]
            if row["ctr"]:
                bucket["ctr_values"].append(row["ctr"])
            if row["avg_viewed"]:
                bucket["retention_values"].append(row["avg_viewed"])
        if row["traffic_source"]:
            source = re.sub(r"\s*\([^)]*\)", "", row["traffic_source"]).strip()
            traffic[source] = traffic.get(source, 0) + 1
    pillar_summary = []
    for pillar, data in pillars.items():
        pillar_summary.append({
            "pillar": pillar,
            "videos": data["videos"],
            "views": round(data["views"]),
            "avg_ctr": round(sum(data["ctr_values"]) / len(data["ctr_values"]), 2) if data["ctr_values"] else 0,
            "avg_viewed": round(sum(data["retention_values"]) / len(data["retention_values"]), 2) if data["retention_values"] else 0,
        })
    pillar_summary.sort(key=lambda p: p["views"], reverse=True)

    title_patterns = {}
    for row in metrics:
        features = _title_features(row["title"])
        for key, enabled in features.items():
            if not enabled or key == "word_count":
                continue
            bucket = title_patterns.setdefault(key, {"count": 0, "views": 0, "ctr": [], "retention": []})
            bucket["count"] += 1
            bucket["views"] += row["views"]
            if row["ctr"]:
                bucket["ctr"].append(row["ctr"])
            if row["avg_viewed"]:
                bucket["retention"].append(row["avg_viewed"])
    pattern_summary = []
    for name, data in title_patterns.items():
        pattern_summary.append({
            "pattern": name,
            "count": data["count"],
            "avg_views": round(data["views"] / max(1, data["count"])),
            "avg_ctr": round(sum(data["ctr"]) / len(data["ctr"]), 2) if data["ctr"] else 0,
            "avg_viewed": round(sum(data["retention"]) / len(data["retention"]), 2) if data["retention"] else 0,
        })
    pattern_summary.sort(key=lambda p: (p["avg_views"], p["avg_ctr"]), reverse=True)

    return {
        "rows": len(rows),
        "columns": columns[:40],
        "detected": {
            **keys,
        },
        "top_by_views": [compact(r) for r in top_by_views[:10]],
        "top_by_ctr": [compact(r) for r in top_by_ctr[:8]],
        "top_by_retention": [compact(r) for r in top_by_retention[:8]],
        "weakest_retention": [compact(r) for r in worst_by_retention[:8]],
        "weakest_ctr": [compact(r) for r in worst_by_ctr[:8]],
        "pillar_summary": pillar_summary,
        "traffic_source_counts": traffic,
        "title_pattern_summary": pattern_summary,
    }


def _youtube_enrich_analytics(summary, limit=8):
    """Best-effort YouTube API enrichment for uploaded analytics rows.

    User exports often include titles but not video IDs. We search the exact
    title, then fetch current public stats for the closest title match. This
    gives Ollama live context without requiring the CSV to have a specific
    YouTube Studio export shape.
    """
    if not youtube_research.api_key_available():
        return {"available": False, "videos": []}
    candidates = []
    for bucket in ("top_by_views", "weakest_ctr", "weakest_retention"):
        for row in summary.get(bucket) or []:
            title = (row.get("title") or "").strip()
            if title and title not in candidates:
                candidates.append(title)
            if len(candidates) >= limit:
                break
        if len(candidates) >= limit:
            break
    enriched = []
    for title in candidates[:limit]:
        try:
            search = youtube_research._api_request("search", {
                "part": "snippet",
                "q": title,
                "type": "video",
                "maxResults": 3,
                "order": "relevance",
                "relevanceLanguage": "en",
                "regionCode": "US",
            }, quota_cost=100)
            ids = [((item.get("id") or {}).get("videoId")) for item in (search.get("items") or [])]
            videos = youtube_research.fetch_videos([vid for vid in ids if vid])
            best = None
            best_ratio = 0
            for video in videos:
                api_title = (video.get("snippet") or {}).get("title") or ""
                ratio = SequenceMatcher(None, title.lower(), api_title.lower()).ratio()
                if ratio > best_ratio:
                    best = video
                    best_ratio = ratio
            if not best or best_ratio < 0.55:
                continue
            snippet = best.get("snippet") or {}
            stats = best.get("statistics") or {}
            duration = youtube_research.parse_duration((best.get("contentDetails") or {}).get("duration"))
            enriched.append({
                "csv_title": title,
                "youtube_title": snippet.get("title"),
                "match": round(best_ratio, 2),
                "video_id": best.get("id"),
                "published_at": snippet.get("publishedAt"),
                "channel": snippet.get("channelTitle"),
                "duration_seconds": duration,
                "views": int(stats.get("viewCount") or 0),
                "likes": int(stats.get("likeCount") or 0),
                "comments": int(stats.get("commentCount") or 0),
                "description_excerpt": re.sub(r"\s+", " ", snippet.get("description") or "")[:300],
                "tags": (snippet.get("tags") or [])[:12],
            })
        except Exception:
            continue
    return {"available": True, "videos": enriched}


def _fmt_metric(value, suffix="", decimals=1):
    try:
        num = float(value or 0)
    except (TypeError, ValueError):
        num = 0
    if decimals == 0:
        return f"{num:,.0f}{suffix}"
    return f"{num:,.{decimals}f}{suffix}"


def _first_item(items):
    return items[0] if isinstance(items, list) and items else {}


def _api_keyword_pool(youtube_enrichment):
    phrases = []
    for video in (youtube_enrichment or {}).get("videos") or []:
        for tag in video.get("tags") or []:
            tag = re.sub(r"\s+", " ", str(tag)).strip()
            if tag and tag.lower() not in [p.lower() for p in phrases]:
                phrases.append(tag)
    return phrases[:14]


def _deterministic_analytics_analysis(summary, youtube_enrichment=None):
    """Grounded analytics advice that does not depend on Ollama behaving.

    Ollama is still useful for phrasing, but uploaded analytics should always
    produce concrete rules from the real columns we detected.
    """
    top_view = _first_item(summary.get("top_by_views"))
    top_ctr = _first_item(summary.get("top_by_ctr"))
    top_retention = _first_item(summary.get("top_by_retention"))
    weak_ctr = _first_item(summary.get("weakest_ctr"))
    weak_retention = _first_item(summary.get("weakest_retention"))
    pillars = summary.get("pillar_summary") or []
    best_pillar = _first_item(pillars)
    second_pillar = pillars[1] if len(pillars) > 1 else {}
    patterns = summary.get("title_pattern_summary") or []
    best_pattern = _first_item(patterns)
    traffic = summary.get("traffic_source_counts") or {}
    top_traffic = max(traffic.items(), key=lambda item: item[1])[0] if traffic else ""
    api_keywords = _api_keyword_pool(youtube_enrichment or {})

    diagnosis_parts = []
    if top_view:
        diagnosis_parts.append(
            f"Biggest winner: \"{top_view['title']}\" with {_fmt_metric(top_view.get('views'), decimals=0)} views, "
            f"{_fmt_metric(top_view.get('ctr'), '%')} CTR, {_fmt_metric(top_view.get('avg_viewed'), '%')} average viewed, "
            f"and {_fmt_metric(top_view.get('clicks'), decimals=0)} website clicks."
        )
    if weak_ctr:
        diagnosis_parts.append(
            f"Biggest packaging leak: \"{weak_ctr['title']}\" at {_fmt_metric(weak_ctr.get('ctr'), '%')} CTR."
        )
    if weak_retention:
        diagnosis_parts.append(
            f"Biggest retention leak: \"{weak_retention['title']}\" at {_fmt_metric(weak_retention.get('avg_viewed'), '%')} average viewed."
        )
    diagnosis = " ".join(diagnosis_parts) or "Analytics parsed, but Ghostline could not find enough title, CTR, view, or retention columns to produce a confident diagnosis."

    winning_patterns = []
    if top_view:
        winning_patterns.append(
            f"Repeat the visible transformation angle from \"{top_view['title']}\" because it led the export on views ({_fmt_metric(top_view.get('views'), decimals=0)}), CTR ({_fmt_metric(top_view.get('ctr'), '%')}), retention ({_fmt_metric(top_view.get('avg_viewed'), '%')}), and clicks ({_fmt_metric(top_view.get('clicks'), decimals=0)})."
        )
    if top_ctr and top_ctr.get("title") != top_view.get("title"):
        winning_patterns.append(
            f"\"{top_ctr['title']}\" proves broad build/result promises can earn clicks ({_fmt_metric(top_ctr.get('ctr'), '%')} CTR), but only if the video pays off fast."
        )
    if best_pillar:
        winning_patterns.append(
            f"The strongest pillar is {best_pillar['pillar']} with {_fmt_metric(best_pillar.get('views'), decimals=0)} total views across {best_pillar.get('videos')} videos, averaging {_fmt_metric(best_pillar.get('avg_ctr'), '%')} CTR."
        )
    if best_pattern:
        winning_patterns.append(
            f"The best title pattern is {best_pattern['pattern'].replace('_', ' ')}: {best_pattern.get('count')} videos averaging {_fmt_metric(best_pattern.get('avg_views'), decimals=0)} views and {_fmt_metric(best_pattern.get('avg_ctr'), '%')} CTR."
        )
    if top_traffic:
        winning_patterns.append(f"Traffic is most often led by {top_traffic}, so packaging and first-frame clarity matter more than search-only optimization.")

    problems = []
    if weak_ctr:
        problems.append(
            f"\"{weak_ctr['title']}\" has a title/thumbnail packaging problem: {_fmt_metric(weak_ctr.get('ctr'), '%')} CTR means people are not choosing it when shown."
        )
    if weak_retention:
        problems.append(
            f"\"{weak_retention['title']}\" has an opening/pacing problem: {_fmt_metric(weak_retention.get('avg_viewed'), '%')} average viewed and drop-off at {weak_retention.get('dropoff') or 'the first major drop'}."
        )
    if top_ctr and top_ctr.get("avg_viewed") and top_ctr.get("avg_viewed") < 20:
        problems.append(
            f"\"{top_ctr['title']}\" gets clicks ({_fmt_metric(top_ctr.get('ctr'), '%')} CTR) but loses viewers ({_fmt_metric(top_ctr.get('avg_viewed'), '%')} average viewed), so the promise is stronger than the opening payoff."
        )
    if second_pillar and best_pillar:
        problems.append(
            f"{second_pillar['pillar']} trails {best_pillar['pillar']} by views ({_fmt_metric(second_pillar.get('views'), decimals=0)} vs {_fmt_metric(best_pillar.get('views'), decimals=0)}), so it needs a clearer result-driven angle before Ghostline repeats it."
        )

    winner_topic = (top_view.get("title") or "the top video").replace('"', "")
    weak_topic = (weak_ctr.get("title") or weak_retention.get("title") or "the weakest video").replace('"', "")
    next_video_rules = [
        "Open with the finished visual result or surprising before/after in the first 5 seconds, then explain how it was made.",
        "Make every title promise a concrete artifact viewers can picture: sprite animation, playable prototype, game asset, character ability, or finished build.",
        "For long-form topics, cut any context that delays proof. If retention drops around the first minute, show the result before the backstory.",
        "When Ghostline generates scripts for this channel, it should favor Build With Me / visible-progress topics over abstract explanation unless the explanation is tied to a failure viewers already feel.",
    ]
    if best_pattern and "why" in best_pattern.get("pattern", ""):
        next_video_rules.append("Use more 'Why [specific creator group] are switching to [specific workflow/tool]' packaging, because the export shows the Why pattern outperforming other title shapes.")

    hook_guidance = [
        f"Use result-first hooks like: \"I turned one prompt into this working game asset.\"",
        f"Use pain-first hooks like: \"This is where most indie games start looking unfinished.\"",
        f"For a follow-up to \"{winner_topic}\", start with the animated result on screen and say: \"This used to take days. I made it with AI in one session.\"",
    ]
    title_guidance = [
        f"Keep the winning shape: \"Why Indie Devs Are Switching to [specific AI workflow]\".",
        f"Rewrite weak explanatory titles like \"{weak_topic}\" into outcome titles such as \"Your Hitboxes Feel Broken Because of This\" or \"Fix Hitboxes Before Players Quit\".",
        "Avoid parenthetical explanation unless the first 5 words already create a click. Put the curiosity or result first, details second.",
    ]

    fallback_keywords = [
        "AI game development",
        "AI sprite animation",
        "indie game development",
        "2D game development",
        "game assets",
        "game prototype",
        "pixel art animation",
        "creative coding",
        "game dev tutorial",
        "makko ai",
    ]
    seo_keywords = []
    for phrase in api_keywords + fallback_keywords:
        if phrase and phrase.lower() not in [p.lower() for p in seo_keywords]:
            seo_keywords.append(phrase)
    seo_keywords = seo_keywords[:12]

    content_angles = [
        "I built a playable game from one AI-generated sprite.",
        "AI sprite animation pipeline for indie devs who cannot draw every frame.",
        "One character, two game genres: turning the same asset into different playable ideas.",
        "Before/after: the art pipeline that made my prototype look finished.",
        "The fastest way to test a game idea before spending weeks on assets.",
    ]
    posting_guidance = [
        "This upload does not prove a best posting time by itself. Use the findings for topic and packaging first, then run timing tests after at least 30 comparable uploads.",
        "Use YouTube Shorts titles/descriptions to reinforce the same keywords as the long-form winners: AI game development, sprite animation, indie dev, and game prototype.",
    ]
    experiments = [
        {
            "name": "Winner sequel test",
            "why": f"\"{winner_topic}\" is the clearest proof of demand in the export.",
            "how_to_run": "Make 3 follow-ups using the same promise shape: one tutorial, one before/after, and one speed-build. Keep the first visual payoff before second 5.",
        },
        {
            "name": "Packaging rescue test",
            "why": f"\"{weak_topic}\" underperformed on click packaging.",
            "how_to_run": "Repackage the same idea with 3 sharper titles and thumbnails focused on the visible pain/result, then compare CTR after comparable impressions.",
        },
        {
            "name": "Retention front-load test",
            "why": "Several videos show clicks are possible, but average viewed can collapse when payoff is delayed.",
            "how_to_run": "Create two versions of the same script: one normal intro and one result-first intro. Keep all else similar and compare average % viewed plus first drop-off timestamp.",
        },
    ]

    return {
        "diagnosis": diagnosis,
        "winning_patterns": winning_patterns[:6],
        "problems": problems[:6],
        "next_video_rules": next_video_rules[:6],
        "hook_guidance": hook_guidance,
        "title_guidance": title_guidance,
        "seo_keywords": seo_keywords,
        "content_angles": content_angles,
        "posting_guidance": posting_guidance,
        "experiments": experiments,
    }


def _normalize_analytics_analysis(parsed, baseline):
    if not isinstance(parsed, dict):
        return baseline
    required_lists = [
        "winning_patterns",
        "problems",
        "next_video_rules",
        "hook_guidance",
        "title_guidance",
        "seo_keywords",
        "content_angles",
        "posting_guidance",
        "experiments",
    ]
    normalized = dict(baseline)
    diagnosis = str(parsed.get("diagnosis") or "").strip()
    if diagnosis and not diagnosis.lower().startswith("based on the provided"):
        normalized["diagnosis"] = diagnosis
    for key in required_lists:
        value = parsed.get(key)
        if isinstance(value, list) and value:
            normalized[key] = value
    return normalized


# SEO analytics-fit helpers (_analytics_context_for_seo,
# _analytics_channel_fit_score, _apply_analytics_fit_to_keywords) live in
# routes/research.py — they're only used by the SEO research route.


def _parse_iso_to_epoch(value):
    if not value:
        return time.time()
    try:
        from datetime import datetime

        normalized = str(value).strip()
        if normalized.endswith("Z"):
            normalized = normalized[:-1] + "+00:00"
        return datetime.fromisoformat(normalized).timestamp()
    except (TypeError, ValueError):
        return time.time()


def _publish_post_from_request(data):
    video_project_id = (data.get("video_project_id") or "").strip()
    video_path = None
    video_project = PROJECTS.get(video_project_id) if video_project_id else None
    if video_project_id:
        video_path = PROJECTS.file_path(video_project_id, "video")
    if not video_path or not video_path.exists():
        return None, "Video project not found."
    title = (data.get("title") or video_project.get("title") or "Ghostline video").strip()
    tags = youtube_publish.normalize_tags(data.get("tags") or data.get("hashtags") or "")
    caption = (data.get("caption") or data.get("description") or "").strip()
    pinned_comment = (data.get("pinned_comment") or "").strip()
    if tags:
        tag_line = " ".join("#" + t for t in tags[:12])
        caption = (caption + "\n\n" + tag_line).strip()
    post = {
        "id": uuid.uuid4().hex[:12],
        "platform": "YOUTUBE",
        "status": "SCHEDULED",
        "created_at": time.time(),
        "scheduled_at": data.get("scheduled_at") or "",
        "scheduled_epoch": _parse_iso_to_epoch(data.get("scheduled_at")),
        "video_project_id": video_project_id,
        "video_path": str(video_path),
        "title": title[:100],
        "caption": caption,
        "description": caption,
        "tags": tags,
        "pinned_comment": pinned_comment,
        "privacy": data.get("privacy") or "private",
        "categoryId": str(data.get("categoryId") or "24"),
        "madeForKids": bool(data.get("madeForKids", False)),
        "syntheticMedia": bool(data.get("syntheticMedia", True)),
        "playlistIds": data.get("playlistIds") or [],
        "error": None,
        "externalPostId": None,
        "externalUrl": None,
    }
    return post, None


def _strip_title_prefix(text):
    """Return (title, body) from Ghostline's TITLE: file format."""
    text = (text or "").strip()
    if not text:
        return "Untitled Video", ""
    lines = text.splitlines()
    first = lines[0].strip()
    if first.lower().startswith("title:"):
        title = first.split(":", 1)[1].strip() or "Untitled Video"
        body = "\n".join(lines[1:]).strip()
        return title, body
    return "Untitled Video", text


def _split_sentences(text):
    text = re.sub(r"\s+", " ", (text or "").strip())
    if not text:
        return []
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]


def _clean_prompt_text(text, max_words=26):
    words = re.findall(r"[A-Za-z0-9'_-]+", text or "")
    return " ".join(words[:max_words]).strip()


def _format_time(seconds):
    seconds = int(max(0, round(seconds)))
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _audio_duration_seconds(path, project=None):
    """Best-effort local audio duration. Exact for WAV, stored metadata first,
    MP3 fallback uses MPEG frame headers, then conservative bitrate estimate."""
    if project and project.get("duration_seconds"):
        try:
            return float(project["duration_seconds"])
        except (TypeError, ValueError):
            pass
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".wav":
        try:
            with wave.open(str(path), "rb") as wf:
                return wf.getnframes() / float(wf.getframerate() or 1)
        except (wave.Error, OSError, ZeroDivisionError):
            pass
    if suffix == ".mp3":
        duration = _mp3_duration_seconds(path)
        if duration:
            return duration
        # Ghostline encodes MP3 at 96 kbps by default. Use only as a fallback.
        try:
            return max(1.0, (path.stat().st_size * 8) / 96000.0)
        except OSError:
            pass
    return None


def _mp3_duration_seconds(path):
    bitrates = {
        1: {1: 32, 2: 40, 3: 48, 4: 56, 5: 64, 6: 80, 7: 96, 8: 112,
            9: 128, 10: 160, 11: 192, 12: 224, 13: 256, 14: 320},
        2: {1: 8, 2: 16, 3: 24, 4: 32, 5: 40, 6: 48, 7: 56, 8: 64,
            9: 80, 10: 96, 11: 112, 12: 128, 13: 144, 14: 160},
    }
    sample_rates = {
        0b11: [44100, 48000, 32000],
        0b10: [22050, 24000, 16000],
        0b00: [11025, 12000, 8000],
    }
    try:
        data = path.read_bytes()
    except OSError:
        return None
    i = 0
    if data[:3] == b"ID3" and len(data) >= 10:
        size = 0
        for b in data[6:10]:
            size = (size << 7) | (b & 0x7f)
        i = 10 + size
    frames = 0
    samples = 0
    limit = min(len(data) - 4, i + 2_000_000)
    while i < limit:
        if data[i] != 0xff or (data[i + 1] & 0xe0) != 0xe0:
            i += 1
            continue
        header = int.from_bytes(data[i:i + 4], "big")
        version_id = (header >> 19) & 0b11
        layer = (header >> 17) & 0b11
        bitrate_idx = (header >> 12) & 0b1111
        sample_idx = (header >> 10) & 0b11
        padding = (header >> 9) & 0b1
        if layer != 0b01 or version_id == 0b01 or sample_idx == 0b11:
            i += 1
            continue
        version_group = 1 if version_id == 0b11 else 2
        bitrate = bitrates[version_group].get(bitrate_idx)
        sr = sample_rates.get(version_id, [None])[sample_idx]
        if not bitrate or not sr:
            i += 1
            continue
        samples_per_frame = 1152 if version_id == 0b11 else 576
        frame_len = int((144000 * bitrate / sr) + padding)
        if frame_len <= 4:
            i += 1
            continue
        frames += 1
        samples += samples_per_frame
        i += frame_len
        if frames >= 8000:
            break
    if frames and sr:
        scanned_duration = samples / float(sr)
        try:
            return scanned_duration * (path.stat().st_size / max(1, i))
        except OSError:
            return scanned_duration
    return None


def _build_video_plan(script, title=None, scene_seconds=8, visual_style="",
                      visual_ambience="", visual_character="",
                      aspect="16:9", workflow="image-to-video"):
    """Create a deterministic faceless-video scene plan from narration text."""
    guessed_title, body = _strip_title_prefix(script)
    title = (title or guessed_title or "Untitled Video").strip()
    body = body or script or ""
    scene_seconds = max(4, min(12, int(scene_seconds or 8)))
    words_per_scene = max(18, int(scene_seconds * 2.35))
    sentences = _split_sentences(body)

    chunks = []
    current = []
    current_words = 0
    for sentence in sentences:
        count = len(sentence.split())
        if current and current_words + count > words_per_scene:
            chunks.append(" ".join(current).strip())
            current = []
            current_words = 0
        current.append(sentence)
        current_words += count
    if current:
        chunks.append(" ".join(current).strip())

    if not chunks and body.strip():
        words = body.split()
        chunks = [" ".join(words[i:i + words_per_scene])
                  for i in range(0, len(words), words_per_scene)]

    style = visual_style.strip() or (
        "cinematic faceless YouTube visual, atmospheric lighting, clear subject focus, "
        "high detail, soft contrast, film grain"
    )
    ambience = visual_ambience.strip()
    character = visual_character.strip()
    visual_identity = []
    if style:
        visual_identity.append(style)
    if ambience:
        visual_identity.append(f"ambience: {ambience}")
    if character:
        visual_identity.append(
            f"recurring on-screen character: {character}, same character design in every scene"
        )
    visual_identity_text = ", ".join(visual_identity)
    negative = (
        "text, captions, subtitles, logos, watermark, distorted faces, extra limbs, "
        "fast action, jump cuts, gore, low quality"
    )
    camera_cycle = [
        "slow push in",
        "slow lateral drift",
        "gentle parallax",
        "locked-off frame with subtle atmospheric motion",
        "slow pull back",
    ]
    scenes = []
    for idx, narration in enumerate(chunks, start=1):
        seed = _clean_prompt_text(narration, 28)
        camera = camera_cycle[(idx - 1) % len(camera_cycle)]
        image_prompt = (
            f"{seed}, {visual_identity_text}, no readable text, "
            f"composition for {aspect}"
        )
        video_prompt = (
            f"{image_prompt}, {camera}, subtle motion, stable coherent scene, "
            f"{scene_seconds} second clip"
        )
        scenes.append({
            "id": idx,
            "start": _format_time((idx - 1) * scene_seconds),
            "duration_seconds": scene_seconds,
            "narration": narration,
            "image_prompt": image_prompt,
            "video_prompt": video_prompt,
            "negative_prompt": negative,
            "workflow": workflow,
        })

    total_seconds = len(scenes) * scene_seconds
    return {
        "title": title,
        "workflow": workflow,
        "aspect": aspect,
        "scene_seconds": scene_seconds,
        "scene_count": len(scenes),
        "estimated_runtime": _format_time(total_seconds),
        "visual_style": style,
        "visual_ambience": ambience,
        "visual_character": character,
        "wan2gp_suggested_settings": {
            "model": "Wan 2.1",
            "resolution": "512x512 for square tests, 832x480 for 16:9 when stable",
            "frames": "16-24",
            "steps": "20-30",
            "notes": "Generate short clips, then stitch. Prefer image-to-video for long faceless stories.",
        },
        "scenes": scenes,
    }


def _write_video_plan_project(plan):
    safe = re.sub(r"[^A-Za-z0-9 _-]", "", plan["title"]).strip() or "video_plan"
    safe = re.sub(r"\s+", "_", safe)[:80]
    tmp_json = OUTPUT_DIR / f"{safe}_scene_plan.json"
    tmp_txt = OUTPUT_DIR / f"{safe}_wan2gp_prompts.txt"
    tmp_json.write_text(json.dumps(plan, indent=2, ensure_ascii=False),
                        encoding="utf-8")
    lines = [
        f"TITLE: {plan['title']}",
        f"WORKFLOW: {plan['workflow']}",
        f"SCENES: {plan['scene_count']}",
        f"ESTIMATED RUNTIME: {plan['estimated_runtime']}",
        "",
    ]
    for scene in plan["scenes"]:
        lines.extend([
            f"SCENE {scene['id']} | {scene['start']} | {scene['duration_seconds']}s",
            "IMAGE PROMPT:",
            scene["image_prompt"],
            "",
            "VIDEO PROMPT:",
            scene["video_prompt"],
            "",
            "NEGATIVE PROMPT:",
            scene["negative_prompt"],
            "",
        ])
    tmp_txt.write_text("\n".join(lines), encoding="utf-8")

    proj = PROJECTS.create(
        kind=project_store.KIND_VIDEO_PLAN,
        title=plan["title"],
        params={
            "workflow": plan["workflow"],
            "scene_count": plan["scene_count"],
            "estimated_runtime": plan["estimated_runtime"],
        },
    )
    PROJECTS.attach_file(proj["id"], "scene_plan", tmp_json)
    PROJECTS.attach_file(proj["id"], "prompts", tmp_txt)
    PROJECTS.update(
        proj["id"],
        status="ready",
        duration_seconds=plan["scene_count"] * plan["scene_seconds"],
    )
    return PROJECTS.get(proj["id"])


def _load_project_json(project_id, role):
    path = PROJECTS.file_path(project_id, role)
    if not path:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _project_folder(project_id):
    if not project_id:
        return None
    folder = PROJECTS.projects_dir / project_id
    return folder if folder.exists() else None


def _visual_asset_directories(timeline_project_id=None, source_plan_project_id=None, extra_paths=None):
    roots = []
    timeline_folder = _project_folder(timeline_project_id)
    plan_folder = _project_folder(source_plan_project_id)
    for folder in (timeline_folder, plan_folder):
        if not folder:
            continue
        roots.extend([
            folder / "visuals",
            folder / "assets",
            folder / "media",
            folder / "scenes",
            folder,
        ])

    for raw in extra_paths or []:
        if not raw:
            continue
        try:
            roots.append(Path(raw))
        except (TypeError, ValueError):
            continue

    seen = set()
    unique = []
    for path in roots:
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path
        key = str(resolved).lower()
        if key not in seen:
            unique.append(str(path))
            seen.add(key)
    return unique


FORGE_BASE_URL = (
    os.getenv("GHOSTLINE_FORGE_URL")
    or os.getenv("BINDERY_FORGE_URL")  # legacy env name; remove after a release
    or "http://127.0.0.1:7861"
).rstrip("/")
FORGE_DEFAULT_CHECKPOINT = (
    os.getenv("GHOSTLINE_FORGE_CHECKPOINT")
    or os.getenv("BINDERY_FORGE_CHECKPOINT")  # legacy env name
    or "flux1-dev-bnb-nf4-v2.safetensors"
)


def _visual_provider_default():
    return "forge"


def _scene_visuals_dir(timeline_project_id):
    folder = _project_folder(timeline_project_id)
    if not folder:
        return None
    visuals = folder / "visuals"
    visuals.mkdir(parents=True, exist_ok=True)
    return visuals


def _scene_visual_path(timeline_project_id, index):
    visuals = _scene_visuals_dir(timeline_project_id)
    if not visuals:
        return None
    return visuals / f"scene_{index:03d}.png"


def _openai_image_size(aspect):
    aspect = (aspect or "16:9").strip()
    if aspect == "9:16":
        return "1024x1536"
    if aspect == "1:1":
        return "1024x1024"
    return "1536x1024"


def _forge_image_size(aspect):
    aspect = (aspect or "16:9").strip()
    if aspect == "9:16":
        return 576, 1024
    if aspect == "1:1":
        return 1024, 1024
    return 1024, 576


def _scene_asset_exists(scene, index, asset_directories):
    stems = [Path(str(scene.get("clip_file") or f"scene_{index:03d}.mp4")).stem, f"scene_{index:03d}", f"scene-{index:03d}"]
    exts = (".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv", ".gif", ".png", ".jpg", ".jpeg", ".webp", ".bmp")
    for raw in asset_directories or []:
        directory = Path(raw)
        if not directory.exists():
            continue
        for stem in stems:
            for ext in exts:
                if (directory / f"{stem}{ext}").exists():
                    return True
    return False


def _openai_scene_prompt(scene, timeline):
    style = (timeline.get("visual_style") or "").strip()
    base = (scene.get("image_prompt") or scene.get("video_prompt") or scene.get("narration") or "").strip()
    extras = [
        "Premium cinematic still frame for a high-retention faceless YouTube video.",
        "Specific foreground subject, clear action, readable emotion or stakes, strong silhouette.",
        "Moody lighting, strong composition, filmic realism, atmospheric depth, not generic stock art.",
        "No text, no captions, no logos, no watermark, no UI, no split panels.",
    ]
    if style:
        extras.insert(1, f"Style: {style}.")
    return " ".join([base] + extras)


def _forge_scene_prompt(scene, timeline):
    style = (timeline.get("visual_style") or "").strip()
    base = (scene.get("image_prompt") or scene.get("video_prompt") or scene.get("narration") or "").strip()
    extras = [
        "premium cinematic frame",
        "specific foreground subject",
        "clear action and stakes",
        "ultra detailed",
        "dramatic lighting",
        "atmospheric depth",
        "film still",
        "not generic stock art",
        "no text, no captions, no logo, no watermark, no UI",
    ]
    if style:
        extras.insert(0, style)
    return ", ".join([base] + extras)


def _forge_quality_to_steps(quality):
    q = (quality or "high").strip().lower()
    if q == "low":
        return 8
    if q == "medium":
        return 14
    return 20


def _image_looks_blank(image_bytes):
    try:
        with Image.open(BytesIO(image_bytes)) as img:
            stat = ImageStat.Stat(img.convert("RGB").resize((32, 32)))
    except Exception:
        return False
    return max(stat.mean) < 3 and max(stat.stddev) < 3


def _generate_forge_scene_images(timeline_project_id, timeline, asset_directories=None,
                                 base_url=None, checkpoint=None, quality="high",
                                 progress_cb=None):
    visuals_dir = _scene_visuals_dir(timeline_project_id)
    if not visuals_dir:
        raise ValueError("Timeline project folder is missing.")

    forge_url = (base_url or FORGE_BASE_URL or "").strip().rstrip("/")
    if not forge_url:
        raise ValueError("Forge URL is empty.")

    scenes = timeline.get("scenes") or []
    width, height = _forge_image_size(timeline.get("aspect"))
    steps = _forge_quality_to_steps(quality)
    generated = 0
    skipped = 0
    active_checkpoint = (checkpoint or FORGE_DEFAULT_CHECKPOINT or "").strip()

    for index, scene in enumerate(scenes, start=1):
        if _scene_asset_exists(scene, index, asset_directories):
            skipped += 1
            if progress_cb:
                progress_cb(f"Scene {index}/{len(scenes)} already has media; skipping generation")
            continue

        prompt = _forge_scene_prompt(scene, timeline)
        payload = {
            "prompt": prompt,
            "negative_prompt": "",
            "steps": steps,
            "width": width,
            "height": height,
            "cfg_scale": 1.0,
            "distilled_cfg_scale": 3.5,
            "sampler_name": "Euler",
            "sampler_index": "Euler",
            "scheduler": "Simple",
            "batch_size": 1,
            "n_iter": 1,
            "seed": -1,
            "do_not_save_samples": True,
            "do_not_save_grid": True,
            "override_settings": {
                "sd_model_checkpoint": active_checkpoint,
            },
            "override_settings_restore_afterwards": True,
        }
        if progress_cb:
            progress_cb(f"Generating scene image {index}/{len(scenes)} with local Forge")
        response = requests.post(f"{forge_url}/sdapi/v1/txt2img", json=payload, timeout=600)
        try:
            data = response.json()
        except ValueError:
            data = {"error": response.text[:500]}
        if response.status_code >= 400:
            raise RuntimeError(data.get("error") or data.get("errors") or f"Forge request failed with HTTP {response.status_code}")
        images = data.get("images") or []
        if not images:
            raise RuntimeError("Forge did not return an image.")
        image_b64 = images[0].split(",", 1)[-1]
        image_bytes = base64.b64decode(image_b64)
        if _image_looks_blank(image_bytes):
            raise RuntimeError("Forge returned a blank image. Check that the FLUX scheduler is set to Simple and the selected checkpoint is loaded.")
        out_path = _scene_visual_path(timeline_project_id, index)
        out_path.write_bytes(image_bytes)
        generated += 1

    return {"generated": generated, "skipped": skipped, "directory": str(visuals_dir)}


def _generate_openai_scene_images(timeline_project_id, timeline, asset_directories=None,
                                  api_key=None, model="gpt-image-1",
                                  quality="high", progress_cb=None):
    key = (api_key or os.getenv("OPENAI_API_KEY") or "").strip()
    if not key:
        raise ValueError("OpenAI image generation needs an OPENAI_API_KEY or a key entered in the Video Studio.")

    visuals_dir = _scene_visuals_dir(timeline_project_id)
    if not visuals_dir:
        raise ValueError("Timeline project folder is missing.")

    scenes = timeline.get("scenes") or []
    size = _openai_image_size(timeline.get("aspect"))
    generated = 0
    skipped = 0

    for index, scene in enumerate(scenes, start=1):
        if _scene_asset_exists(scene, index, asset_directories):
            skipped += 1
            if progress_cb:
                progress_cb(f"Scene {index}/{len(scenes)} already has media; skipping generation")
            continue

        prompt = _openai_scene_prompt(scene, timeline)
        if progress_cb:
            progress_cb(f"Generating scene image {index}/{len(scenes)} with OpenAI")

        response = requests.post(
            "https://api.openai.com/v1/images/generations",
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "prompt": prompt,
                "size": size,
                "quality": quality or "high",
                "output_format": "png",
            },
            timeout=300,
        )
        try:
            payload = response.json()
        except ValueError:
            payload = {"error": {"message": response.text[:500]}}
        if response.status_code >= 400:
            message = (payload.get("error") or {}).get("message") or f"OpenAI image request failed with HTTP {response.status_code}"
            raise RuntimeError(message)

        data = payload.get("data") or []
        if not data:
            raise RuntimeError("OpenAI image response did not include image data.")
        item = data[0] or {}
        image_b64 = item.get("b64_json") or item.get("b64")
        if not image_b64:
            raise RuntimeError("OpenAI image response did not include base64 image bytes.")

        out_path = _scene_visual_path(timeline_project_id, index)
        out_path.write_bytes(base64.b64decode(image_b64))
        generated += 1

    return {"generated": generated, "skipped": skipped, "directory": str(visuals_dir)}


def _build_timeline(plan, narration_project, narration_path, audio_duration, source_plan_project_id=None):
    scenes = list(plan.get("scenes") or [])
    if not scenes:
        raise ValueError("Video plan has no scenes.")
    weights = [max(1, len((s.get("narration") or "").split())) for s in scenes]
    total_weight = sum(weights) or len(scenes)
    if not audio_duration:
        audio_duration = sum(float(s.get("duration_seconds") or plan.get("scene_seconds") or 8)
                             for s in scenes)
    timeline_scenes = []
    cursor = 0.0
    for idx, (scene, weight) in enumerate(zip(scenes, weights)):
        if idx == len(scenes) - 1:
            duration = max(0.1, audio_duration - cursor)
        else:
            duration = max(0.1, audio_duration * (weight / total_weight))
        end = cursor + duration
        timeline_scenes.append({
            "id": scene.get("id") or idx + 1,
            "start_seconds": round(cursor, 3),
            "end_seconds": round(end, 3),
            "duration_seconds": round(duration, 3),
            "start": _format_time(cursor),
            "end": _format_time(end),
            "narration": scene.get("narration") or "",
            "video_prompt": scene.get("video_prompt") or "",
            "image_prompt": scene.get("image_prompt") or "",
            "negative_prompt": scene.get("negative_prompt") or "",
            "clip_file": f"scene_{idx + 1:03d}.mp4",
            "edit_note": "Trim, loop, or slow the visual clip to this exact duration.",
        })
        cursor = end
    return {
        "title": f"{plan.get('title') or 'Untitled'} timeline",
        "source_plan_title": plan.get("title"),
        "source_plan_project_id": source_plan_project_id,
        "narration_project_id": narration_project.get("id"),
        "narration_title": narration_project.get("title"),
        "narration_audio": str(narration_path),
        "audio_duration_seconds": round(audio_duration, 3),
        "audio_duration": _format_time(audio_duration),
        "scene_count": len(timeline_scenes),
        "workflow": plan.get("workflow"),
        "aspect": plan.get("aspect") or "16:9",
        "visual_style": plan.get("visual_style") or "",
        "visual_ambience": plan.get("visual_ambience") or "",
        "visual_character": plan.get("visual_character") or "",
        "assembly_notes": [
            "Put scene media in the timeline project's visuals/assets/media/scenes folder.",
            "Name motion clips or stills like scene_001.mp4 or scene_001.png.",
            "Ghostline will use motion clips first, then still images, then fallback cards.",
            "Each scene will be trimmed, looped, or held to match duration_seconds.",
            "Place narration audio at 00:00.",
            "Loop music underneath and duck it below narration in the Music & Mix tab.",
        ],
        "scenes": timeline_scenes,
    }


def _write_timeline_project(timeline):
    safe = re.sub(r"[^A-Za-z0-9 _-]", "", timeline["title"]).strip() or "timeline"
    safe = re.sub(r"\s+", "_", safe)[:80]
    tmp_json = OUTPUT_DIR / f"{safe}.timeline.json"
    tmp_txt = OUTPUT_DIR / f"{safe}.edit_list.txt"
    tmp_json.write_text(json.dumps(timeline, indent=2, ensure_ascii=False),
                        encoding="utf-8")
    lines = [
        f"TITLE: {timeline['title']}",
        f"NARRATION: {timeline['narration_title']}",
        f"AUDIO DURATION: {timeline['audio_duration']}",
        f"SCENES: {timeline['scene_count']}",
        "",
    ]
    for scene in timeline["scenes"]:
        lines.extend([
            f"{scene['clip_file']} | {scene['start']} - {scene['end']} | {scene['duration_seconds']}s",
            f"PROMPT: {scene['video_prompt']}",
            f"NARRATION: {scene['narration']}",
            "",
        ])
    tmp_txt.write_text("\n".join(lines), encoding="utf-8")

    proj = PROJECTS.create(
        kind=project_store.KIND_TIMELINE,
        title=timeline["title"],
        params={
            "scene_count": timeline["scene_count"],
            "audio_duration": timeline["audio_duration"],
            "narration_project_id": timeline["narration_project_id"],
        },
    )
    PROJECTS.attach_file(proj["id"], "timeline", tmp_json)
    PROJECTS.attach_file(proj["id"], "edit_list", tmp_txt)
    PROJECTS.update(
        proj["id"],
        status="ready",
        duration_seconds=timeline["audio_duration_seconds"],
    )
    return PROJECTS.get(proj["id"])


def _video_job(job_id, **fields):
    with VIDEO_LOCK:
        job = VIDEO_JOBS.get(job_id)
        if job:
            job.update(fields)


def _video_log(job_id, msg):
    with VIDEO_LOCK:
        job = VIDEO_JOBS.get(job_id)
        if not job:
            return
        job.setdefault("log", []).append({"t": time.time(), "msg": msg})
        job["status"] = msg
        if len(job["log"]) > 120:
            job["log"] = job["log"][-120:]


def _is_safe_under_output(path_str):
    """Reject paths that escape the output/ tree (basic path-traversal guard)."""
    if not path_str:
        return False
    try:
        p = Path(path_str).resolve()
        root = OUTPUT_DIR.resolve()
        return root in p.parents or p == root
    except (OSError, ValueError):
        return False


def _register_project(kind, title, file_path, role="audio", params=None,
                      copy=False, **extra_fields):
    """Create a project record, move/copy the artifact into it, and finalize.
    Returns the project record dict, or None on failure."""
    try:
        proj = PROJECTS.create(kind=kind, title=title or "Untitled",
                               params=params or {})
        if file_path and Path(file_path).exists():
            PROJECTS.attach_file(proj["id"], role, file_path, copy=copy)
        PROJECTS.update(proj["id"], status="ready", **extra_fields)
        return PROJECTS.get(proj["id"])
    except Exception:
        return None


def make_job():
    job_id = uuid.uuid4().hex[:12]
    with JOBS_LOCK:
        JOBS[job_id] = {
            "id": job_id,
            "status": "queued",
            "title": None,
            "log": [],
            "section": 0,
            "words": 0,
            "target": 0,
            "position": None,
            "done": False,
            "error": None,
            "final_path": None,
            "partial_path": None,
            "started_at": time.time(),
        }
    return job_id


def update_job(job_id, **fields):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return
        job.update(fields)


def append_log(job_id, line):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return
        job["log"].append({"t": time.time(), "msg": line})
        # Keep the log bounded so the page stays snappy.
        if len(job["log"]) > 400:
            job["log"] = job["log"][-400:]


def progress_callback_for(job_id, kind=project_store.KIND_STORY):
    """Translate generator events into UI state updates."""
    def cb(event):
        e = event.get("event")
        if e == "status":
            append_log(job_id, event.get("message", ""))
        elif e == "title":
            update_job(job_id, title=event["title"], status="planning")
            append_log(job_id, f"Title: {event['title']}")
        elif e == "plan_done":
            update_job(job_id, status="writing")
            append_log(job_id, "Internal plan ready. Beginning narration.")
        elif e == "paths":
            update_job(job_id, partial_path=event.get("partial"))
        elif e == "resume":
            update_job(job_id,
                       title=event.get("title"),
                       section=event.get("sections", 0),
                       words=event.get("words", 0),
                       target=event.get("target", 0),
                       status="writing")
            append_log(job_id, f"Resumed at {event.get('words', 0)} words.")
        elif e == "section_start":
            update_job(job_id,
                       section=event["section_num"],
                       target=event["target_words"],
                       position=event["position"],
                       status="writing")
            append_log(job_id,
                       f"Section {event['section_num']} ({event['position']}): "
                       f"target ~{event['target']} words.")
        elif e == "section_done":
            update_job(job_id,
                       section=event["section_num"],
                       words=event["total_words"],
                       target=event["target_words"])
            append_log(job_id,
                       f"Section {event['section_num']} done: "
                       f"+{event['words_in_section']} words "
                       f"(running total {event['total_words']}/{event['target_words']}).")
        elif e == "done":
            proj = _register_project(
                kind=kind,
                title=event["title"],
                file_path=event["final_path"],
                role="script",
                params={"sections": event.get("sections")},
                word_count=event.get("words"),
            )
            final_path = event["final_path"]
            if proj:
                project_script_path = PROJECTS.file_path(proj["id"], "script")
                if project_script_path and project_script_path.exists():
                    final_path = str(project_script_path)
            update_job(job_id,
                       done=True,
                       status="done",
                       title=event["title"],
                       words=event["words"],
                       final_path=final_path,
                       project_id=(proj["id"] if proj else None))
            append_log(job_id,
                       f"Finished. {event['words']} words across "
                       f"{event['sections']} sections.")
    return cb


def run_job(job_id, inputs):
    update_job(job_id, status="starting", target=int(inputs["word_count"]))
    append_log(job_id, "Starting...")
    cb = progress_callback_for(job_id)
    try:
        sg.generate_story(inputs, OUTPUT_DIR, progress_cb=cb)
    except Exception as exc:
        update_job(job_id, error=str(exc), status="error", done=True)
        append_log(job_id, f"ERROR: {exc}")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def root():
    """Public root — show the marketing landing page so first-time visitors
    to phantomline.xyz see the pitch and pricing, not the empty studio.
    Logged-in / returning users hit /app for the actual workspace.

    PRICING is lazily imported from routes.billing inside the function
    body to avoid a circular import (routes/billing imports from core,
    which is imported by server)."""
    from routes.billing import PRICING
    return render_template("landing.html", pricing=PRICING)


@app.route("/pricing")
def pricing_page():
    """Dedicated pricing URL — separated from the homepage so /pricing has
    its own ranking target in search. Mirrors the same PRICING data that
    drives the landing page's #pricing section."""
    from routes.billing import PRICING
    return render_template("pricing.html", pricing=PRICING)


@app.route("/about")
def about_page():
    """Long-form 'who and why' page. Important for E-E-A-T signals and
    for visitors deciding whether to trust a brand new domain."""
    return render_template("about.html")


@app.route("/privacy")
def privacy_page():
    """Privacy policy. Linked from the footer; required content for any
    site that takes payments or stores user data."""
    return render_template("privacy.html")


@app.route("/terms")
def terms_page():
    """Terms of Service. Refund window, license grant, content ownership,
    governing law. Required content for any site that takes payments."""
    return render_template("terms.html")


@app.route("/alternatives")
def alternatives_hub():
    """Hub page listing every competitor we have an alternatives page for.
    Internal links from here distribute SEO authority to the per-competitor
    pages — that's the actual ranking-target wedge for a new domain."""
    from alternatives import COMPETITORS
    return render_template("alternatives_hub.html", competitors=COMPETITORS)


@app.route("/alternatives/<slug>")
def alternative_page(slug):
    """Per-competitor alternative page (e.g. /alternatives/submagic).
    Data lives in alternatives.py; the template is shared but each entry
    ships unique copy so Google doesn't penalize as thin content."""
    from alternatives import COMPETITORS_BY_SLUG
    competitor = COMPETITORS_BY_SLUG.get(slug)
    if not competitor:
        return jsonify({"ok": False, "error": "Unknown competitor"}), 404
    return render_template("alternative.html", competitor=competitor)


@app.route("/local-ai-video-generator")
def pillar_local_ai_video_generator():
    """Pillar page for the 'local AI video generator' wedge — the broadest
    keyword Phantomline can realistically rank for as a new domain. Long
    article (~1900 words) explaining what local AI video generation is,
    why faceless creators care, and where Phantomline fits.

    Internal-links to: /alternatives, /alternatives/submagic,
    /alternatives/opus-clip, /pricing, /about. Those interior links pass
    SEO authority back to the comparison and pricing pages."""
    return render_template("pillar_local_ai.html")


@app.route("/faceless-youtube")
def pillar_faceless_youtube():
    """Pillar page for the 'faceless YouTube tool' wedge — angled at the
    workflow rather than the tech. Walks through the 6-step pipeline
    (script, narration, captions, visuals, music, publish), prices out
    the standard subscription stack, and shows how Phantomline collapses
    it into one local install. ~2200 words.

    Internal-links to: /local-ai-video-generator (sibling pillar),
    /alternatives + per-competitor pages, /pricing, /about, /app."""
    return render_template("pillar_faceless_youtube.html")


@app.route("/app")
@app.route("/app/")
@app.route("/studio")
def studio():
    """The real application UI. Moved off `/` so the root URL can show
    marketing content. Both /app and /studio are accepted; the canonical
    is /app (referenced by the landing page CTAs)."""
    return render_template(
        "index.html",
        defaults={
            "topic": sg.DEFAULT_TOPIC,
            "genre": sg.DEFAULT_GENRE,
            "tone": sg.DEFAULT_TONE,
            "words": sg.DEFAULT_WORDS,
            "model": sg.DEFAULT_MODEL,
            "genres": sg.GENRE_HINTS,
        },
    )


@app.route("/sw.js")
def service_worker():
    """Serve the PWA service worker from the origin root so it can control
    the whole site, not just /static/. Service workers can only control URLs
    at or below the path they're served from."""
    sw_path = BASE_DIR / "static" / "sw.js"
    response = send_file(str(sw_path), mimetype="application/javascript")
    response.headers["Service-Worker-Allowed"] = "/"
    response.headers["Cache-Control"] = "no-cache"
    return response


@app.errorhandler(Exception)
def api_error(exc):
    """Log full detail server-side; return a generic message to the client.
    Don't leak Python class names, file paths, or internal exception text."""
    if request.path.startswith("/api/"):
        app.logger.exception("API error on %s", request.path)
        # Werkzeug's HTTPException carries an explicit status the client should
        # see; let it propagate. Everything else is a generic 500.
        from werkzeug.exceptions import HTTPException
        if isinstance(exc, HTTPException):
            return jsonify({"ok": False, "error": exc.description or "Request failed."}), exc.code
        return jsonify({"ok": False, "error": "Server error. Check the local server log."}), 500
    raise exc


@app.route("/api/models")
def api_models():
    models = sg.check_ollama()
    if models is None:
        return jsonify({
            "ok": False,
            "error": "Cannot reach Ollama at http://localhost:11434. "
                     "Open the Ollama app, or run 'ollama serve'.",
        }), 503
    return jsonify({"ok": True, "models": models})


# /api/launch/* lives in routes/launch.py.


# /api/research/youtube/* lives in routes/research.py.


VIRAL_SHORT_FORMATS = {
    "confession_secret": {
        "label": "Confession / Secret",
        "rules": "Use a private lie, hidden habit, secret account, forbidden message, or confession that changes a relationship.",
    },
    "betrayal_drama": {
        "label": "Betrayal / Drama",
        "rules": "Use screenshots, wrong recipients, hidden phones, family/friend betrayal, relationship tension, and instantly understandable stakes.",
    },
    "rule_horror": {
        "label": "Rule-Based Horror",
        "rules": "Introduce one simple rule in the first sentence, make the character break it, and imply the rule is still active at the end.",
    },
    "impossible_situation": {
        "label": "Impossible Situation",
        "rules": "Use an impossible but clear event: a message from tomorrow, a duplicate person, a future invitation, a name used by someone else, or a loop in reality.",
    },
}

VIRAL_LOOP_TYPES = {
    "circular": "End by echoing the hook so the last line sends viewers back to the first line.",
    "missing-info": "Leave one crucial piece of information unstated so viewers replay to catch clues.",
    "hard-cut": "End slightly mid-thought, just before the obvious next action.",
    "question": "End with a direct dilemma viewers can answer in comments.",
}

VIRAL_HOOK_PATTERNS = [
    "I checked his phone... and saw my name.",
    "My best friend sent me the wrong screenshot.",
    "I found a second phone in my house.",
    "My mom told me never to open that door.",
    "Someone was texting me from my number.",
    "He wasn't supposed to see that message.",
    "She married the wrong person.",
    "I got invited to a wedding that already happened.",
]

VIRAL_BAD_HOOKS = [
    "This is a story about betrayal.",
    "One day, something strange happened.",
    "Have you ever wondered what would happen if...",
    "Let me tell you about the scariest night of my life.",
]


def _is_viral_recipe(recipe, video_format=None):
    value = f"{recipe or ''} {video_format or ''}".lower()
    return "viral-story" in value or "viral_short" in value or "viral short" in value


def _viral_payload_rules(tension_format, loop_type):
    fmt = VIRAL_SHORT_FORMATS.get(tension_format) or VIRAL_SHORT_FORMATS["betrayal_drama"]
    loop = VIRAL_LOOP_TYPES.get(loop_type) or VIRAL_LOOP_TYPES["circular"]
    examples = "\n".join(f"  - {hook}" for hook in VIRAL_HOOK_PATTERNS)
    bad_examples = "\n".join(f"  - {hook}" for hook in VIRAL_BAD_HOOKS)
    return f"""
VIRAL SHORTS PLAYBOOK:
- The channel is not "stories"; it is addictive unresolved tension delivered in under 40 seconds.
- Format: {fmt["label"]}. {fmt["rules"]}
- Hook must be the strongest line in the whole output.
- Hook formula: close person + concrete action/object + impossible, suspicious, or emotionally dangerous detail.
- Hook length: 6-13 words when possible. Never use a slow setup sentence.
- Hook must sound like the viewer arrived mid-crisis, not like a narrator introducing a topic.
- Prefer first person and relationship language: I, my boyfriend, my best friend, my mom, my wife, my roommate, my neighbor.
- Strong hook examples:
{examples}
- Weak hooks to avoid:
{bad_examples}
- Structure every idea as 0-1.5s hook, 1.5-10s setup, 10-20s escalation, 20-35s twist, 35-45s loop ending.
- Loop rule: {loop}
- Titles are 3-6 words, emotional, incomplete, and conflict-driven. Never use "Part 1".
- Captions are attention anchors: 2-5 words per beat, one highlighted word, no transcript paragraphs.
- Include a pinned comment that asks the viewer to judge, choose, or confess what they would do.
- Avoid slow worldbuilding, generic lessons, soft bedtime pacing, and normal-sounding first sentences.
""".strip()


@app.route("/api/ideas/video", methods=["POST"])
def api_video_ideas():
    data = request.get_json(force=True) or {}
    model = _resolve_ollama_model(data.get("model"))
    if sg.check_ollama() is None:
        return jsonify({"ok": False, "error": "Ollama is not running on localhost:11434."}), 503

    niche = (data.get("niche") or "faceless YouTube channel").strip()
    audience = (data.get("audience") or "curious general viewers").strip()
    recipe = (data.get("recipe") or "custom").strip()
    video_format = (data.get("format") or "explainer").strip()
    hook_style = (data.get("hook_style") or "mistake warning").strip()
    current_topic = (data.get("current_topic") or "").strip()
    tension_format = (data.get("tension_format") or "betrayal_drama").strip()
    loop_type = (data.get("loop_type") or "circular").strip()
    viral_rules = _viral_payload_rules(tension_format, loop_type) if _is_viral_recipe(recipe, video_format) else ""
    research_keyword = (data.get("research_keyword") or "").strip() or youtube_research.suggest_research_keyword(
        recipe=recipe,
        niche=niche,
        topic=current_topic,
        video_format=video_format,
    )
    research_block = youtube_research.research_context(research_keyword)
    insights_block = channel_insights.to_prompt_block(channel_insights.load(BASE_DIR))

    prompt = f"""
Generate 6 fresh faceless-video ideas for a local AI video studio.

Channel recipe: {recipe}
Niche: {niche}
Audience: {audience}
Video format: {video_format}
Hook style: {hook_style}
Current stale idea to avoid copying: {current_topic}
{viral_rules}
{insights_block}
{research_block}

Return ONLY valid JSON. No markdown. No explanation.
Schema:
[
  {{
    "title": "short clickable idea name",
    "topic": "one specific video idea the script writer can use",
    "hook": "first sentence angle",
    "structure": {{
      "setup": "what happens from 1.5-10s",
      "escalation": "what makes it worse from 10-20s",
      "twist": "the reveal from 20-35s",
      "loop_ending": "last line that points back to the hook"
    }},
    "caption_beats": [
      {{"text": "2 to 5 words", "highlight": "one word"}}
    ],
    "pinned_comment": "short comment prompt that invites debate",
    "hashtags": ["#shorts", "#storytime", "#plotwist"],
    "visual": "suggested visual style or recurring character",
    "music": "short music bed direction"
  }}
]

Rules:
- Ideas must be concrete, varied, and production-ready.
- Avoid generic topics like "tips for success" unless there is a specific angle.
- Make each idea easy to visualize in short-form video.
- If this is a viral story short, every idea must create unresolved tension under 45 seconds.
- First-frame clarity matters: the hook must immediately identify a person/topic, a problem, and a broken expectation.
- Do not write normal topic ideas. Package each idea as a viewer retention bet: why someone stops scrolling, why they keep watching, and why they comment or replay.
- Caption beats are not transcripts. They should be punchy on-screen attention anchors with 2-5 words and one obvious highlight word.
- Pinned comments must create engagement through judgment, dilemma, prediction, or confession.
- If YouTube market signals are present, use their patterns and demand signals but do not copy exact competitor titles.
- Do not include paid API suggestions.
"""
    raw = sg.generate(
        model,
        prompt,
        system="You are Ghostline's local faceless-video strategist. You generate strict JSON only and prioritize retention mechanics for short-form video.",
        label="video ideas",
        show_progress=False,
        temperature=1.0,
        num_predict=1800,
    )
    ideas = []
    for item in _extract_json_array(raw):
        if not isinstance(item, dict):
            continue
        topic = str(item.get("topic") or "").strip()
        if not topic:
            continue
        structure = item.get("structure") if isinstance(item.get("structure"), dict) else {}
        caption_beats = item.get("caption_beats") if isinstance(item.get("caption_beats"), list) else []
        clean_beats = []
        for beat in caption_beats[:8]:
            if not isinstance(beat, dict):
                continue
            text = str(beat.get("text") or "").strip()
            if not text:
                continue
            clean_beats.append({
                "text": text[:60],
                "highlight": str(beat.get("highlight") or "").strip()[:24],
            })
        ideas.append({
            "title": str(item.get("title") or topic[:70]).strip()[:120],
            "topic": topic[:600],
            "hook": str(item.get("hook") or "").strip()[:300],
            "structure": {
                "setup": str(structure.get("setup") or "").strip()[:260],
                "escalation": str(structure.get("escalation") or "").strip()[:260],
                "twist": str(structure.get("twist") or "").strip()[:260],
                "loop_ending": str(structure.get("loop_ending") or "").strip()[:260],
            },
            "caption_beats": clean_beats,
            "pinned_comment": str(item.get("pinned_comment") or "").strip()[:160],
            "hashtags": [str(tag).strip()[:30] for tag in (item.get("hashtags") or []) if str(tag).strip()][:10],
            "visual": str(item.get("visual") or "").strip()[:300],
            "music": str(item.get("music") or "").strip()[:220],
        })
        if len(ideas) >= 6:
            break
    if not ideas:
        return jsonify({"ok": False, "error": "Ollama did not return usable ideas. Try again."}), 502
    return jsonify({"ok": True, "ideas": ideas})


def _title_ideas_prompt(*, focus_keyword, focus_variations, topic,
                        selected_context, niche, audience,
                        video_format, hook_style, viral_rules,
                        insights_block, research_block,
                        title_length_rule):
    """Render the system+user prompt for /api/ideas/titles.

    Extracted out of api_title_ideas so the long prompt body is
    reviewable on its own and the route handler stays focused on
    request parsing + response shaping.
    """
    return f"""
Generate 12 clickable YouTube title options for a faceless video.

FOCUS KEYWORD (must appear verbatim or as a tight variation in EVERY title â€” this is the vidIQ "keywords in title" + "tripled keyword" anchor): {focus_keyword}
ACCEPTED VARIATIONS of the focus keyword: {", ".join(focus_variations) if focus_variations else "(use only the focus keyword; minor pluralization or word order is fine)"}

Topic: {topic or "the user has not chosen a topic yet; infer from the niche"}
Selected idea package:
{selected_context or "No selected idea package was provided."}
Niche: {niche}
Audience: {audience}
Format: {video_format}
Hook style: {hook_style}
{viral_rules}
{insights_block}
{research_block}

Return ONLY valid JSON. No markdown. No explanation.
Schema:
[
  {{
    "title": "clickable title that contains the focus keyword (or accepted variation)",
    "focus_keyword_used": "the exact substring from this title that satisfies the focus keyword rule",
    "angle": "why this title works",
    "platform": "shorts or youtube",
    "pinned_comment": "short comment prompt"
  }}
]

Hard rules (titles failing these are unacceptable â€” vidIQ will score them 0):
- EVERY title must contain "{focus_keyword}" or one of the accepted variations as a contiguous substring. Match is case-insensitive but the words must appear in order.
- The focus keyword must appear in the FIRST 60 characters of the title, ideally in the first half.
- Set "focus_keyword_used" to the exact substring you used. If you cannot include the keyword naturally, drop the title â€” do not return it.
- {title_length_rule}

Quality rules:
- Titles should be specific, curiosity-driven, and not clickbait lies.
- Titles must package the selected idea's hook, twist, and loop promise instead of drifting to a new topic.
- Avoid all caps, emojis, hashtags, and vague titles.
- Mix title styles: warning, question, mystery, list, transformation, proof, and story conflict â€” but every variant still contains the focus keyword.
- Separate title packaging from the in-video hook. Titles sell the click/search fit; hooks win the first second.
- For Shorts/story formats, prefer relationship nouns, secrets, mistakes, forbidden objects, impossible timing, or one clear conflict. Avoid bland titles like "The Secret", "This Changed Everything", or "A Shocking Story".
- When channel insights show winning title patterns (e.g. "Why X are switching to Y"), bias 3â€“4 of the 12 toward that pattern.
- For viral story shorts the focus keyword can ride at the END of a 6â€“10 word hook; that's fine, the rule is presence, not position-first.
- Do not mention AI tools, local generation, or paid APIs.
"""


@app.route("/api/ideas/titles", methods=["POST"])
def api_title_ideas():
    data = request.get_json(force=True) or {}
    model = _resolve_ollama_model(data.get("model"))
    if sg.check_ollama() is None:
        return jsonify({"ok": False, "error": "Ollama is not running on localhost:11434."}), 503

    topic = (data.get("topic") or "").strip()
    niche = (data.get("niche") or "faceless YouTube channel").strip()
    audience = (data.get("audience") or "curious general viewers").strip()
    hook_style = (data.get("hook_style") or "curiosity").strip()
    video_format = (data.get("format") or "explainer").strip()
    recipe = (data.get("recipe") or "").strip()
    tension_format = (data.get("tension_format") or "betrayal_drama").strip()
    loop_type = (data.get("loop_type") or "circular").strip()
    selected_idea = data.get("selected_idea") if isinstance(data.get("selected_idea"), dict) else {}
    selected_structure = selected_idea.get("structure") if isinstance(selected_idea.get("structure"), dict) else {}
    selected_context = ""
    if selected_idea:
        selected_bits = [
            f"Selected idea title: {str(selected_idea.get('title') or '').strip()}",
            f"Selected hook: {str(selected_idea.get('hook') or '').strip()}",
            f"Setup: {str(selected_structure.get('setup') or '').strip()}",
            f"Escalation: {str(selected_structure.get('escalation') or '').strip()}",
            f"Twist: {str(selected_structure.get('twist') or '').strip()}",
            f"Loop ending: {str(selected_structure.get('loop_ending') or '').strip()}",
            f"Visual direction: {str(selected_idea.get('visual') or '').strip()}",
            f"Suggested pinned comment: {str(selected_idea.get('pinned_comment') or '').strip()}",
            f"Suggested hashtags: {' '.join(str(tag).strip() for tag in (selected_idea.get('hashtags') or []) if str(tag).strip())}",
        ]
        selected_context = "\n".join(bit for bit in selected_bits if bit.split(":", 1)[-1].strip())
    viral_rules = _viral_payload_rules(tension_format, loop_type) if _is_viral_recipe(recipe, video_format) else ""
    research_keyword = (data.get("research_keyword") or "").strip() or youtube_research.suggest_research_keyword(
        recipe=recipe,
        niche=niche,
        topic=topic,
        video_format=video_format,
    )
    research_block = youtube_research.research_context(research_keyword)
    insights_loaded = channel_insights.load(BASE_DIR)
    insights_block = channel_insights.to_prompt_block(insights_loaded)
    autocomplete_seeds = youtube_research.autocomplete_phrases(
        research_keyword or topic or niche, limit=10
    ) if (research_keyword or topic or niche) else []
    focus = _pick_focus_keyword(
        topic=topic, title=str(selected_idea.get("title") or ""), niche=niche,
        research_keyword=research_keyword, insights=insights_loaded,
        extra_phrases=autocomplete_seeds,
    )
    focus_keyword = focus["focus"]
    focus_variations = focus["variations"]
    short_title_mode = _is_viral_recipe(recipe, video_format) or any(
        word in f"{recipe} {video_format}".lower()
        for word in ("short", "tiktok", "reels", "reddit", "storytime")
    )
    title_length_rule = (
        "For Shorts/story formats: 4-9 words, 28-62 characters, emotional and incomplete. "
        "For search/tutorial formats: 40-80 characters, clear payoff, still curiosity-driven."
        if short_title_mode else
        "Title length: 40-80 characters. Hard ceiling 100 characters."
    )

    prompt = _title_ideas_prompt(
        focus_keyword=focus_keyword, focus_variations=focus_variations,
        topic=topic, selected_context=selected_context,
        niche=niche, audience=audience, video_format=video_format,
        hook_style=hook_style, viral_rules=viral_rules,
        insights_block=insights_block, research_block=research_block,
        title_length_rule=title_length_rule,
    )
    raw = sg.generate(
        model,
        prompt,
        system=(
            "You are a YouTube Shorts packaging strategist optimizing for vidIQ's "
            "Actionable score. You are ruthless about including the focus keyword "
            "in every title. Output strict JSON only."
        ),
        label="title ideas",
        show_progress=False,
        temperature=0.85,
        num_predict=1300,
    )
    accepted_anchors = [a.lower() for a in [focus_keyword] + focus_variations if a]
    titles = []
    for item in _extract_json_array(raw):
        pinned_comment = ""
        focus_used = ""
        if isinstance(item, str):
            title = item.strip()
            angle = ""
            platform = ""
        elif isinstance(item, dict):
            title = str(item.get("title") or "").strip()
            angle = str(item.get("angle") or "").strip()
            platform = str(item.get("platform") or "").strip()
            pinned_comment = str(item.get("pinned_comment") or "").strip()
            focus_used = str(item.get("focus_keyword_used") or "").strip()
        else:
            continue
        title = re.sub(r"^[-*\d.\s]+", "", title).strip().strip('"')
        if not title:
            continue
        title_lower = title.lower()
        contains_focus = any(anchor in title_lower for anchor in accepted_anchors) if accepted_anchors else True
        if not contains_focus:
            continue  # drop titles that violate the vidIQ keyword-in-title rule
        if not focus_used and accepted_anchors:
            for anchor in accepted_anchors:
                if anchor in title_lower:
                    idx = title_lower.find(anchor)
                    focus_used = title[idx:idx + len(anchor)]
                    break
        titles.append({
            "title": title[:120],
            "angle": angle[:220],
            "platform": platform[:30],
            "pinned_comment": pinned_comment[:160],
            "focus_keyword": focus_keyword,
            "focus_keyword_used": focus_used[:60],
        })
        if len(titles) >= 12:
            break
    if not titles:
        return jsonify({"ok": False, "error": "Ollama did not return usable titles. Try again."}), 502

    # Annotate every title with a channel-fit verdict pulled from persisted insights.
    # Lets the UI surface a green/amber/red badge so users pick titles that
    # actually match their channel's existing search demand.
    insights_for_fit = channel_insights.load(BASE_DIR)
    for t in titles:
        t["fit"] = channel_insights.title_fit(t["title"], insights_for_fit)

    # Sort: strong-fit first, then good-fit, then everything else (preserving
    # generated order within each tier so Ollama's variety isn't lost).
    verdict_rank = {"strong_fit": 0, "good_fit": 1, "stretch": 2, "neutral": 3, "risky": 4}
    titles.sort(key=lambda t: verdict_rank.get(t["fit"]["verdict"], 3))

    return jsonify({
        "ok": True,
        "titles": titles,
        "insights_configured": bool(insights_for_fit),
    })


@app.route("/api/publish/description", methods=["POST"])
def api_publish_description():
    data = request.get_json(force=True) or {}
    model = _resolve_ollama_model(data.get("model"))
    if sg.check_ollama() is None:
        return jsonify({"ok": False, "error": "Ollama is not running on localhost:11434."}), 503

    title = (data.get("title") or "Ghostline video").strip()
    topic = (data.get("topic") or "").strip()
    script = (data.get("script") or "").strip()
    niche = (data.get("niche") or "faceless YouTube Shorts").strip()
    recipe = (data.get("recipe") or "").strip()
    video_format = (data.get("format") or "short-form video").strip()
    hashtags = (data.get("hashtags") or "").strip()
    pinned_comment = (data.get("pinned_comment") or "").strip()
    selected_idea = data.get("selected_idea") if isinstance(data.get("selected_idea"), dict) else {}
    selected_structure = selected_idea.get("structure") if isinstance(selected_idea.get("structure"), dict) else {}
    research_keyword = (data.get("research_keyword") or "").strip() or youtube_research.suggest_research_keyword(
        recipe=recipe,
        niche=niche,
        topic=topic or title,
        video_format=video_format,
    )
    research_block = youtube_research.research_context(research_keyword)
    script_excerpt = re.sub(r"\s+", " ", script)[:1800]
    idea_context = "\n".join([
        f"Idea title: {str(selected_idea.get('title') or '').strip()}",
        f"Hook: {str(selected_idea.get('hook') or '').strip()}",
        f"Setup: {str(selected_structure.get('setup') or '').strip()}",
        f"Escalation: {str(selected_structure.get('escalation') or '').strip()}",
        f"Twist: {str(selected_structure.get('twist') or '').strip()}",
        f"Loop ending: {str(selected_structure.get('loop_ending') or '').strip()}",
    ])
    insights_loaded = channel_insights.load(BASE_DIR)
    insights_block = channel_insights.to_prompt_block(insights_loaded)
    autocomplete_seeds = youtube_research.autocomplete_phrases(
        research_keyword or topic or title or niche, limit=10
    ) if (research_keyword or topic or title or niche) else []
    focus = _pick_focus_keyword(
        topic=topic, title=title, niche=niche,
        research_keyword=research_keyword, insights=insights_loaded,
        extra_phrases=autocomplete_seeds,
    )
    focus_keyword = focus["focus"]
    focus_variations = focus["variations"]

    prompt = _publish_description_prompt(
        focus_keyword=focus_keyword, focus_variations=focus_variations,
        title=title, niche=niche, video_format=video_format,
        topic=topic, idea_context=idea_context,
        script_excerpt=script_excerpt, hashtags=hashtags,
        pinned_comment=pinned_comment,
        insights_block=insights_block, research_block=research_block,
    )
    raw = sg.generate(
        model,
        prompt,
        system=(
            "You are a YouTube SEO strategist optimizing for vidIQ's Actionable score "
            "(keywords-in-title, tripled-keyword, tag-count, tag-volume). You thread a "
            "single focus keyword through the description first line, the description body, "
            "and the tag list. You never spam. Output strict JSON only."
        ),
        label="publish description",
        show_progress=False,
        temperature=0.6,
        num_predict=1500,
    )
    parsed = _extract_json_object(raw)
    description = str(parsed.get("description") or "").strip()
    tags = [str(t).strip().lstrip("#")[:45] for t in (parsed.get("tags") or []) if str(t).strip()]
    out_hashtags = []
    for raw_tag in (parsed.get("hashtags") or []):
        for piece in re.split(r"[\s,]+", str(raw_tag)):
            piece = piece.strip()
            if not piece:
                continue
            out_hashtags.append(piece if piece.startswith("#") else f"#{piece}")
    seen_hashtags = set()
    out_hashtags = [
        tag for tag in out_hashtags
        if not (tag.lower() in seen_hashtags or seen_hashtags.add(tag.lower()))
    ]
    out_pinned = str(parsed.get("pinned_comment") or pinned_comment or "").strip()
    if not description:
        return jsonify({"ok": False, "error": "Ollama did not return a usable description."}), 502

    # vidIQ "tripled keyword" enforcement: focus keyword must be the first tag
    # and present in the first 8 words of the description. Repair if Ollama
    # missed either (it sometimes drifts under temperature even with the rule).
    fk_lower = (focus_keyword or "").strip().lower()
    if fk_lower:
        existing_lower = [t.lower() for t in tags]
        if fk_lower not in existing_lower:
            tags = [focus_keyword] + tags
        elif existing_lower[0] != fk_lower:
            idx = existing_lower.index(fk_lower)
            tags = [tags[idx]] + tags[:idx] + tags[idx + 1:]

        first_8 = " ".join(description.split()[:8]).lower()
        if fk_lower not in first_8:
            # Prepend a natural lead. Don't break the body â€” just insert a
            # focus-keyword sentence ahead of whatever the model wrote.
            description = f"{focus_keyword.capitalize()} â€” {description}"

    # Ensure we hit the 15-tag floor. Backfill from focus variations + research.
    if len(tags) < 15:
        backfill_pool = list(focus_variations)
        backfill_pool.extend(autocomplete_seeds or [])
        for kw in (insights_loaded.get("seo_keywords") or []):
            if str(kw).strip():
                backfill_pool.append(str(kw).strip())
        seen_tags = {t.lower() for t in tags}
        for cand in backfill_pool:
            cand = re.sub(r"\s+", " ", str(cand or "").strip().lstrip("#"))[:45]
            if cand and cand.lower() not in seen_tags:
                tags.append(cand)
                seen_tags.add(cand.lower())
            if len(tags) >= 18:
                break

    if out_hashtags and not re.search(r"#\w+", description):
        description = (description.rstrip() + "\n\n" + " ".join(out_hashtags[:10])).strip()
    return jsonify({
        "ok": True,
        "focus_keyword": focus_keyword,
        "focus_variations": focus_variations,
        "description": description[:5000],
        "tags": tags[:25],
        "hashtags": out_hashtags[:10],
        "pinned_comment": out_pinned[:180],
    })


@app.route("/api/start", methods=["POST"])
def api_start():
    data = request.get_json(force=True) or {}
    inputs = {
        "topic": (data.get("topic") or sg.DEFAULT_TOPIC).strip(),
        "genre": (data.get("genre") or sg.DEFAULT_GENRE).strip(),
        "tone": (data.get("tone") or sg.DEFAULT_TONE).strip(),
        "description": (data.get("description") or "").strip(),
        "word_count": int(data.get("word_count") or sg.DEFAULT_WORDS),
        "model": _resolve_ollama_model(data.get("model")),
    }
    # Surface a clear error if Ollama is down before kicking off a thread.
    if sg.check_ollama() is None:
        return jsonify({"ok": False, "error": "Ollama is not running on localhost:11434."}), 503

    job_id = make_job()
    threading.Thread(target=run_job, args=(job_id, inputs), daemon=True).start()
    return jsonify({"ok": True, "job_id": job_id, "inputs": inputs})


@app.route("/api/start_short", methods=["POST"])
def api_start_short():
    """One-shot short-form generation (30sâ€“10min). Reuses the same JOBS table
    so the existing /api/status/<id> + /api/script/<id> endpoints work."""
    data = request.get_json(force=True) or {}
    inputs = {
        "topic": (data.get("topic") or sg.DEFAULT_TOPIC).strip(),
        "genre": (data.get("genre") or sg.DEFAULT_GENRE).strip(),
        "tone": (data.get("tone") or sg.DEFAULT_TONE).strip(),
        "description": (data.get("description") or "").strip(),
        "word_count": int(data.get("word_count") or 280),
        "model": _resolve_ollama_model(data.get("model")),
    }
    if sg.check_ollama() is None:
        return jsonify({"ok": False, "error": "Ollama is not running on localhost:11434."}), 503

    job_id = make_job()

    def worker():
        update_job(job_id, status="starting", target=inputs["word_count"])
        append_log(job_id, f"Starting short script ({inputs['word_count']} words)...")
        cb = progress_callback_for(job_id, kind=project_store.KIND_SHORT)
        try:
            sg.generate_short_script(inputs, OUTPUT_DIR, progress_cb=cb)
        except Exception as exc:
            update_job(job_id, error=str(exc), status="error", done=True)
            append_log(job_id, f"ERROR: {exc}")

    threading.Thread(target=worker, daemon=True).start()
    return jsonify({"ok": True, "job_id": job_id, "inputs": inputs})


@app.route("/api/status/<job_id>")
def api_status(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return jsonify({"ok": False, "error": "unknown job"}), 404
        # Return a copy so we don't hold the lock while serializing.
        snapshot = dict(job)
    # Send a slim version of the log; client can request full text on demand.
    return jsonify({"ok": True, "job": snapshot})


@app.route("/api/script/<job_id>")
def api_script(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if not job or not job.get("final_path"):
        return jsonify({"ok": False, "error": "not ready"}), 404
    script_path = Path(job["final_path"])
    if not script_path.exists() and job.get("project_id"):
        project_script_path = PROJECTS.file_path(job["project_id"], "script")
        if project_script_path and project_script_path.exists():
            script_path = project_script_path
            update_job(job_id, final_path=str(script_path))
    if not script_path.exists():
        return jsonify({"ok": False, "error": f"Script file is missing: {job['final_path']}"}), 404
    text = script_path.read_text(encoding="utf-8")
    return jsonify({"ok": True, "title": job["title"], "text": text,
                    "path": str(script_path)})


@app.route("/api/download/<job_id>")
def api_download(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if not job or not job.get("final_path"):
        return jsonify({"ok": False, "error": "not ready"}), 404
    script_path = Path(job["final_path"])
    if not script_path.exists() and job.get("project_id"):
        project_script_path = PROJECTS.file_path(job["project_id"], "script")
        if project_script_path and project_script_path.exists():
            script_path = project_script_path
            update_job(job_id, final_path=str(script_path))
    if not script_path.exists():
        return jsonify({"ok": False, "error": f"Script file is missing: {job['final_path']}"}), 404
    return send_file(script_path, as_attachment=True)


# ---------------------------------------------------------------------------
# TTS routes (Kokoro)
# ---------------------------------------------------------------------------

@app.route("/api/voices")
def api_voices():
    """List Kokoro voices for the dropdown. Available even before kokoro is installed."""
    return jsonify({
        "voices": [
            {"id": v[0], "label": v[1], "lang": v[2], "gender": v[3]}
            for v in tts_mod.VOICES
        ],
    })


@app.route("/api/tts/start", methods=["POST"])
def api_tts_start():
    data = request.get_json(force=True) or {}
    raw_text = (data.get("text") or "").strip()
    if not raw_text:
        return jsonify({"ok": False, "error": "empty text"}), 400

    # Don't speak the generated title line; the video renderer can show it visually.
    text = tts_mod.strip_title_prefix(raw_text)

    voice = (data.get("voice") or "af_heart").strip()
    try:
        speed = max(0.5, min(2.0, float(data.get("speed") or 1.0)))
    except (TypeError, ValueError):
        speed = 1.0
    fmt = (data.get("format") or "mp3").lower()
    if fmt not in ("mp3", "wav"):
        fmt = "mp3"
    title = (data.get("title") or "narration").strip() or "narration"

    # Fail fast if kokoro isn't installed, so the UI can surface a clear message.
    try:
        import kokoro  # noqa: F401
    except ImportError:
        return jsonify({
            "ok": False,
            "error": "Kokoro is not installed. Run:\n"
                     "    pip install kokoro soundfile lameenc\n"
                     "(this also pulls PyTorch - large download on first install).",
        }), 503

    job_id = uuid.uuid4().hex[:12]
    with TTS_LOCK:
        TTS_JOBS[job_id] = {
            "id": job_id,
            "status": "queued",
            "title": title,
            "voice": voice,
            "speed": speed,
            "format": fmt,
            "chars_done": 0,
            "chars_total": len(text),
            "segment": 0,
            "done": False,
            "error": None,
            "audio_path": None,
            "audio_ext": None,
            "started_at": time.time(),
        }

    def progress(p):
        with TTS_LOCK:
            j = TTS_JOBS.get(job_id)
            if not j:
                return
            j["status"] = "synthesizing"
            j["chars_done"] = p["chars_done"]
            j["chars_total"] = p["chars_total"]
            j["segment"] = p["segment"]

    def worker():
        try:
            with TTS_LOCK:
                TTS_JOBS[job_id]["status"] = "loading model"
            audio, sr = tts_mod.synthesize(text, voice=voice, speed=speed,
                                           progress_cb=progress)
            with TTS_LOCK:
                TTS_JOBS[job_id]["status"] = "encoding"
            data_bytes, ext = tts_mod.encode(audio, sr, fmt=fmt)
            duration = float(len(audio)) / float(sr or 1)
            safe = sg.sanitize_filename(title)
            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            out_path = OUTPUT_DIR / f"{safe}.{ext}"
            out_path.write_bytes(data_bytes)
            proj = _register_project(
                kind=project_store.KIND_NARRATION,
                title=title, file_path=str(out_path), role="audio",
                params={"voice": voice, "speed": speed},
                duration_seconds=duration,
            )
            with TTS_LOCK:
                j = TTS_JOBS[job_id]
                # The artifact has been moved into the project folder.
                j["audio_path"] = str(PROJECTS.file_path(proj["id"], "audio")) if proj else str(out_path)
                j["audio_ext"] = ext
                j["duration_seconds"] = duration
                j["status"] = "done"
                j["done"] = True
                j["project_id"] = proj["id"] if proj else None
        except Exception as exc:
            with TTS_LOCK:
                j = TTS_JOBS.get(job_id)
                if j:
                    j["error"] = str(exc)
                    j["status"] = "error"
                    j["done"] = True

    threading.Thread(target=worker, daemon=True).start()
    return jsonify({"ok": True, "job_id": job_id})


@app.route("/api/tts/status/<job_id>")
def api_tts_status(job_id):
    with TTS_LOCK:
        j = TTS_JOBS.get(job_id)
        if not j:
            return jsonify({"ok": False, "error": "unknown job"}), 404
        return jsonify({"ok": True, "job": dict(j)})


@app.route("/api/tts/download/<job_id>")
def api_tts_download(job_id):
    with TTS_LOCK:
        j = TTS_JOBS.get(job_id)
    if not j or not j.get("audio_path"):
        return jsonify({"ok": False, "error": "not ready"}), 404
    return send_file(j["audio_path"], as_attachment=True)


# ---------------------------------------------------------------------------
# Music routes (MusicGen + crossfade loop) and Mix routes (music + narration)
# ---------------------------------------------------------------------------

@app.route("/api/music/start", methods=["POST"])
def api_music_start():
    data = request.get_json(force=True) or {}
    prompt = (data.get("prompt") or "").strip()
    if not prompt:
        return jsonify({"ok": False, "error": "empty prompt"}), 400
    try:
        minutes = max(0.5, min(180.0, float(data.get("minutes") or 60)))
    except (TypeError, ValueError):
        minutes = 60.0
    model_size = (data.get("model_size") or "small").strip().lower()
    if model_size not in ("small", "medium", "large"):
        model_size = "small"
    fmt = (data.get("format") or "mp3").lower()
    if fmt not in ("mp3", "wav"):
        fmt = "mp3"
    try:
        fade_seconds = max(0.5, min(8.0, float(data.get("fade_seconds") or 2.0)))
    except (TypeError, ValueError):
        fade_seconds = 2.0
    name = (data.get("name") or "music_bed").strip() or "music_bed"

    # MusicGen ships in the HuggingFace transformers package.
    try:
        from transformers import MusicgenForConditionalGeneration  # noqa: F401
    except ImportError:
        return jsonify({
            "ok": False,
            "error": "transformers is not installed (it should be - kokoro brings it). "
                     "Try: pip install -U transformers",
        }), 503

    job_id = uuid.uuid4().hex[:12]
    with MUSIC_LOCK:
        MUSIC_JOBS[job_id] = {
            "id": job_id,
            "status": "queued",
            "prompt": prompt,
            "minutes": minutes,
            "model_size": model_size,
            "format": fmt,
            "fade_seconds": fade_seconds,
            "name": name,
            "done": False,
            "error": None,
            "audio_path": None,
            "audio_ext": None,
            "started_at": time.time(),
            "log": [],
        }

    def emit(msg):
        with MUSIC_LOCK:
            j = MUSIC_JOBS.get(job_id)
            if not j:
                return
            j["log"].append({"t": time.time(), "msg": msg})
            if len(j["log"]) > 200:
                j["log"] = j["log"][-200:]
            j["status"] = msg

    def cb(event):
        e = event.get("event")
        if e == "status":
            emit(event.get("message", ""))

    def worker():
        try:
            emit("Starting...")
            clip, sr = music_mod.generate_clip(prompt, duration_seconds=30,
                                               model_size=model_size,
                                               progress_cb=cb)
            emit(f"Crossfade-looping to {minutes:.1f} min...")
            looped = music_mod.crossfade_loop(clip, sr, target_seconds=minutes * 60.0,
                                              fade_seconds=fade_seconds)
            emit("Encoding...")
            data_bytes, ext = music_mod.encode(looped, sr, fmt=fmt)
            safe = sg.sanitize_filename(name)
            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            out_path = OUTPUT_DIR / f"{safe}.{ext}"
            out_path.write_bytes(data_bytes)
            proj = _register_project(
                kind=project_store.KIND_MUSIC,
                title=name, file_path=str(out_path), role="audio",
                params={"prompt": prompt, "minutes": minutes,
                        "model_size": model_size, "fade_seconds": fade_seconds},
            )
            with MUSIC_LOCK:
                j = MUSIC_JOBS[job_id]
                j["audio_path"] = str(PROJECTS.file_path(proj["id"], "audio")) if proj else str(out_path)
                j["audio_ext"] = ext
                j["status"] = "done"
                j["done"] = True
                j["project_id"] = proj["id"] if proj else None
                j["log"].append({"t": time.time(), "msg": f"Saved as project {proj['id'] if proj else '(unsaved)'}"})
        except Exception as exc:
            with MUSIC_LOCK:
                j = MUSIC_JOBS.get(job_id)
                if j:
                    j["error"] = str(exc)
                    j["status"] = "error"
                    j["done"] = True
                    j["log"].append({"t": time.time(), "msg": f"ERROR: {exc}"})

    threading.Thread(target=worker, daemon=True).start()
    return jsonify({"ok": True, "job_id": job_id})


@app.route("/api/music/status/<job_id>")
def api_music_status(job_id):
    with MUSIC_LOCK:
        j = MUSIC_JOBS.get(job_id)
        if not j:
            return jsonify({"ok": False, "error": "unknown job"}), 404
        return jsonify({"ok": True, "job": dict(j)})


@app.route("/api/music/download/<job_id>")
def api_music_download(job_id):
    with MUSIC_LOCK:
        j = MUSIC_JOBS.get(job_id)
    if not j or not j.get("audio_path"):
        return jsonify({"ok": False, "error": "not ready"}), 404
    return send_file(j["audio_path"], as_attachment=True)


# ---- Mix narration + music ------------------------------------------------

@app.route("/api/mix/start", methods=["POST"])
def api_mix_start():
    data = request.get_json(force=True) or {}
    tts_job = data.get("tts_job_id")
    music_job = data.get("music_job_id")
    try:
        duck_db = max(0.0, min(30.0, float(data.get("music_db_below_speech") or 18.0)))
    except (TypeError, ValueError):
        duck_db = 18.0
    fmt = (data.get("format") or "mp3").lower()
    if fmt not in ("mp3", "wav"):
        fmt = "mp3"

    # Resolve inputs to file paths.
    narration_path = None
    music_path = None
    name = (data.get("name") or "mixdown").strip() or "mixdown"

    if tts_job:
        with TTS_LOCK:
            j = TTS_JOBS.get(tts_job)
        if j and j.get("audio_path"):
            narration_path = j["audio_path"]
            name = j.get("title") or name

    if music_job:
        with MUSIC_LOCK:
            j = MUSIC_JOBS.get(music_job)
        if j and j.get("audio_path"):
            music_path = j["audio_path"]

    # Allow direct file paths too for power use, but only if they live under
    # OUTPUT_DIR - prevents arbitrary file reads via the mix endpoint.
    raw_n = data.get("narration_path")
    raw_m = data.get("music_path")
    if raw_n:
        if not _is_safe_under_output(raw_n):
            return jsonify({"ok": False, "error": "narration_path must live under output/"}), 400
        narration_path = raw_n
    if raw_m:
        if not _is_safe_under_output(raw_m):
            return jsonify({"ok": False, "error": "music_path must live under output/"}), 400
        music_path = raw_m

    if not narration_path or not music_path:
        return jsonify({"ok": False, "error": "need both narration and music"}), 400

    job_id = uuid.uuid4().hex[:12]
    with MIX_LOCK:
        MIX_JOBS[job_id] = {
            "id": job_id,
            "status": "queued",
            "narration_path": str(narration_path),
            "music_path": str(music_path),
            "duck_db": duck_db,
            "format": fmt,
            "name": name,
            "done": False,
            "error": None,
            "audio_path": None,
            "audio_ext": None,
            "started_at": time.time(),
        }

    def worker():
        try:
            with MIX_LOCK:
                MIX_JOBS[job_id]["status"] = "loading"
            n_audio, n_sr = music_mod.load_audio_file(narration_path)
            m_audio, m_sr = music_mod.load_audio_file(music_path)
            with MIX_LOCK:
                MIX_JOBS[job_id]["status"] = "mixing"
            mixed, sr = music_mod.mix_narration_and_music(
                n_audio, n_sr, m_audio, m_sr,
                music_db_below_speech=duck_db,
            )
            with MIX_LOCK:
                MIX_JOBS[job_id]["status"] = "encoding"
            data_bytes, ext = music_mod.encode(mixed, sr, fmt=fmt)
            safe = sg.sanitize_filename(name)
            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            out_path = OUTPUT_DIR / f"{safe}_with_music.{ext}"
            out_path.write_bytes(data_bytes)
            proj = _register_project(
                kind=project_store.KIND_MIX,
                title=f"{name} (mixed)", file_path=str(out_path), role="audio",
                params={"duck_db": duck_db,
                        "narration_path": str(narration_path),
                        "music_path": str(music_path)},
            )
            with MIX_LOCK:
                j = MIX_JOBS[job_id]
                j["audio_path"] = str(PROJECTS.file_path(proj["id"], "audio")) if proj else str(out_path)
                j["audio_ext"] = ext
                j["status"] = "done"
                j["done"] = True
                j["project_id"] = proj["id"] if proj else None
        except Exception as exc:
            with MIX_LOCK:
                j = MIX_JOBS.get(job_id)
                if j:
                    j["error"] = str(exc)
                    j["status"] = "error"
                    j["done"] = True

    threading.Thread(target=worker, daemon=True).start()
    return jsonify({"ok": True, "job_id": job_id})


@app.route("/api/mix/status/<job_id>")
def api_mix_status(job_id):
    with MIX_LOCK:
        j = MIX_JOBS.get(job_id)
        if not j:
            return jsonify({"ok": False, "error": "unknown job"}), 404
        return jsonify({"ok": True, "job": dict(j)})


@app.route("/api/mix/download/<job_id>")
def api_mix_download(job_id):
    with MIX_LOCK:
        j = MIX_JOBS.get(job_id)
    if not j or not j.get("audio_path"):
        return jsonify({"ok": False, "error": "not ready"}), 404
    return send_file(j["audio_path"], as_attachment=True)


@app.route("/api/upload/narration", methods=["POST"])
def api_upload_narration():
    """Accept an existing narration file (mp3/wav/m4a/flac/ogg) for mixing."""
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"ok": False, "error": "no file uploaded"}), 400

    # Accept any common audio extension; soundfile handles most.
    name = f.filename
    ext = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""
    if ext not in (".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac"):
        return jsonify({"ok": False, "error": f"unsupported file type: {ext}"}), 400

    upload_dir = OUTPUT_DIR / "uploaded"
    upload_dir.mkdir(parents=True, exist_ok=True)
    safe_stem = sg.sanitize_filename(name.rsplit(".", 1)[0])
    tmp_path = upload_dir / f"{uuid.uuid4().hex[:8]}_{safe_stem}{ext}"
    f.save(str(tmp_path))
    size_mb = tmp_path.stat().st_size / (1024 * 1024)

    # Register as a persistent project so the upload survives restarts
    # and shows up in the Library.
    proj = _register_project(
        kind=project_store.KIND_UPLOAD,
        title=name.rsplit(".", 1)[0],
        file_path=str(tmp_path),
        role="audio",
        params={"original_name": name, "size_mb": round(size_mb, 1)},
    )
    final_path = (PROJECTS.file_path(proj["id"], "audio") if proj else tmp_path)

    return jsonify({
        "ok": True,
        "path": str(final_path),
        "filename": Path(final_path).name,
        "original_name": name,
        "size_mb": round(size_mb, 1),
        "project_id": proj["id"] if proj else None,
    })


@app.route("/api/upload/source-video", methods=["POST"])
def api_upload_source_video():
    """Accept a user video that can become the visual layer for a faceless short."""
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"ok": False, "error": "no video uploaded"}), 400

    name = Path(f.filename).name
    ext = Path(name).suffix.lower()
    if ext not in VIDEO_UPLOAD_EXTS:
        return jsonify({
            "ok": False,
            "error": "unsupported video type. Use MP4, MOV, M4V, WEBM, AVI, or MKV.",
        }), 400

    upload_dir = OUTPUT_DIR / "uploaded"
    upload_dir.mkdir(parents=True, exist_ok=True)
    safe_stem = sg.sanitize_filename(Path(name).stem)
    tmp_path = upload_dir / f"{uuid.uuid4().hex[:8]}_{safe_stem}{ext}"
    f.save(str(tmp_path))
    size_mb = tmp_path.stat().st_size / (1024 * 1024)
    duration_seconds = None
    width = None
    height = None
    try:
        clip = video_assembler.VideoFileClip(str(tmp_path), audio=False)
        duration_seconds = float(getattr(clip, "duration", 0) or 0) or None
        size = getattr(clip, "size", None) or []
        if len(size) >= 2:
            width, height = int(size[0]), int(size[1])
        clip.close()
    except Exception:
        pass

    proj = _register_project(
        kind=project_store.KIND_UPLOAD,
        title=safe_stem or "uploaded video",
        file_path=str(tmp_path),
        role="source_video",
        params={
            "original_name": name,
            "size_mb": round(size_mb, 1),
            "upload_type": "video",
            "width": width,
            "height": height,
        },
        duration_seconds=duration_seconds,
    )
    final_path = PROJECTS.file_path(proj["id"], "source_video") if proj else tmp_path

    return jsonify({
        "ok": True,
        "path": str(final_path),
        "filename": Path(final_path).name,
        "original_name": name,
        "size_mb": round(size_mb, 1),
        "duration_seconds": duration_seconds,
        "width": width,
        "height": height,
        "project_id": proj["id"] if proj else None,
        "project": proj,
    })


@app.route("/api/recent/jobs")
def api_recent_jobs():
    """Surface the most recent finished TTS + music jobs so the Music tab
    can pre-select them in the Mix workflow."""
    with TTS_LOCK:
        tts_jobs = [(j["id"], j.get("title") or j["id"], j.get("started_at", 0))
                    for j in TTS_JOBS.values() if j.get("done") and not j.get("error")]
    with MUSIC_LOCK:
        music_jobs = [(j["id"], j.get("name") or j["id"], j.get("started_at", 0))
                      for j in MUSIC_JOBS.values() if j.get("done") and not j.get("error")]
    tts_jobs.sort(key=lambda t: -t[2])
    music_jobs.sort(key=lambda t: -t[2])
    return jsonify({
        "tts": [{"id": i, "label": l} for (i, l, _) in tts_jobs[:10]],
        "music": [{"id": i, "label": l} for (i, l, _) in music_jobs[:10]],
    })


@app.route("/landing")
@app.route("/landing/")
def landing():
    # Render pricing into the landing page server-side so the marketing
    # tiers are SEO-indexable. routes/billing.PRICING is the single source
    # of truth — Settings UI and this page both read from it.
    from routes.billing import PRICING
    return render_template("landing.html", pricing=PRICING)


# ---------------------------------------------------------------------------
# Video planner
# ---------------------------------------------------------------------------

@app.route("/api/video/plan", methods=["POST"])
def api_video_plan():
    data = request.get_json(force=True) or {}
    script = (data.get("script") or "").strip()
    if not script:
        return jsonify({"ok": False, "error": "Paste a script first."}), 400
    try:
        scene_seconds = int(data.get("scene_seconds") or 8)
    except (TypeError, ValueError):
        scene_seconds = 8
    plan = _build_video_plan(
        script=script,
        title=(data.get("title") or "").strip() or None,
        scene_seconds=scene_seconds,
        visual_style=(data.get("visual_style") or "").strip(),
        visual_ambience=(data.get("visual_ambience") or "").strip(),
        visual_character=(data.get("visual_character") or "").strip(),
        aspect=(data.get("aspect") or "16:9").strip(),
        workflow=(data.get("workflow") or "image-to-video").strip(),
    )
    project = _write_video_plan_project(plan)
    return jsonify({"ok": True, "plan": plan, "project": project})


@app.route("/api/video/assets")
def api_video_assets():
    items = PROJECTS.all()
    plans = [p for p in items if p.get("kind") == project_store.KIND_VIDEO_PLAN]
    narrations = [
        p for p in items
        if p.get("kind") in (project_store.KIND_NARRATION, project_store.KIND_UPLOAD)
        and (p.get("files") or {}).get("audio")
    ]
    timelines = [p for p in items if p.get("kind") == project_store.KIND_TIMELINE]
    return jsonify({
        "ok": True,
        "plans": plans,
        "narrations": narrations,
        "timelines": timelines,
    })


@app.route("/api/video/timeline", methods=["POST"])
def api_video_timeline():
    data = request.get_json(force=True) or {}
    plan_id = (data.get("plan_project_id") or "").strip()
    narration_id = (data.get("narration_project_id") or "").strip()
    if not plan_id or not narration_id:
        return jsonify({"ok": False, "error": "Choose a video plan and narration."}), 400
    plan_project = PROJECTS.get(plan_id)
    narration_project = PROJECTS.get(narration_id)
    if not plan_project or plan_project.get("kind") != project_store.KIND_VIDEO_PLAN:
        return jsonify({"ok": False, "error": "Video plan not found."}), 404
    if not narration_project:
        return jsonify({"ok": False, "error": "Narration not found."}), 404
    plan = _load_project_json(plan_id, "scene_plan")
    narration_path = PROJECTS.file_path(narration_id, "audio")
    if not plan:
        return jsonify({"ok": False, "error": "Video plan file is missing."}), 404
    if not narration_path:
        return jsonify({"ok": False, "error": "Narration audio is missing."}), 404
    audio_duration = _audio_duration_seconds(narration_path, narration_project)
    timeline = _build_timeline(
        plan,
        narration_project,
        narration_path,
        audio_duration,
        source_plan_project_id=plan_id,
    )
    project = _write_timeline_project(timeline)
    if project:
        timeline["asset_directories"] = _visual_asset_directories(
            timeline_project_id=project["id"],
            source_plan_project_id=plan_id,
        )
    return jsonify({"ok": True, "timeline": timeline, "project": project})


@app.route("/api/video/draft/start", methods=["POST"])
def api_video_draft_start():
    # Free tier: monthly render quota. Pro/Studio: unlimited.
    allowed, quota_err, quota_state = consume_quota("renders_per_month")
    if not allowed:
        return jsonify({"ok": False, "error": quota_err, "code": "QUOTA", "quota": quota_state}), 402
    data = request.get_json(force=True) or {}
    timeline_id = (data.get("timeline_project_id") or "").strip()
    audio_project_id = (data.get("audio_project_id") or "").strip()
    image_provider = (data.get("image_provider") or _visual_provider_default()).strip().lower()
    image_quality = (data.get("image_quality") or "high").strip() or "high"
    forge_url = (data.get("forge_url") or FORGE_BASE_URL).strip() or FORGE_BASE_URL
    forge_checkpoint = (data.get("forge_checkpoint") or FORGE_DEFAULT_CHECKPOINT).strip() or FORGE_DEFAULT_CHECKPOINT
    if not timeline_id:
        return jsonify({"ok": False, "error": "Choose a timeline first."}), 400
    timeline_project = PROJECTS.get(timeline_id)
    if not timeline_project or timeline_project.get("kind") != project_store.KIND_TIMELINE:
        return jsonify({"ok": False, "error": "Timeline not found."}), 404
    timeline = _load_project_json(timeline_id, "timeline")
    if not timeline:
        return jsonify({"ok": False, "error": "Timeline file is missing."}), 404
    narration_id = timeline.get("narration_project_id")
    narration_path = PROJECTS.file_path(narration_id, "audio") if narration_id else None
    if not narration_path:
        return jsonify({"ok": False, "error": "Timeline narration audio is missing."}), 404
    render_audio_project_id = narration_id
    render_audio_path = narration_path
    if audio_project_id:
        audio_project = PROJECTS.get(audio_project_id)
        if not audio_project or not (audio_project.get("files") or {}).get("audio"):
            return jsonify({"ok": False, "error": "Render audio project not found."}), 404
        render_audio_project_id = audio_project_id
        render_audio_path = PROJECTS.file_path(audio_project_id, "audio")
        if not render_audio_path.exists():
            return jsonify({"ok": False, "error": "Render audio file is missing."}), 404

    asset_directories = _visual_asset_directories(
        timeline_project_id=timeline_id,
        source_plan_project_id=timeline.get("source_plan_project_id"),
        extra_paths=timeline.get("asset_directories"),
    )
    timeline["asset_directories"] = asset_directories

    job_id = uuid.uuid4().hex[:12]
    with VIDEO_LOCK:
        VIDEO_JOBS[job_id] = {
            "id": job_id,
            "status": "queued",
            "title": timeline.get("source_plan_title") or timeline.get("title") or "video",
            "timeline_project_id": timeline_id,
            "done": False,
            "error": None,
            "video_path": None,
            "project_id": None,
            "asset_directories": asset_directories,
            "image_provider": image_provider,
            "log": [],
            "started_at": time.time(),
        }

    def worker():
        try:
            safe = sg.sanitize_filename(VIDEO_JOBS[job_id]["title"])
            out_path = OUTPUT_DIR / f"{safe}_draft.mp4"
            _video_log(job_id, "Preparing draft video")
            if asset_directories:
                _video_log(job_id, "Searching visual assets in: " + " | ".join(asset_directories))
            if image_provider == "forge":
                result = _generate_forge_scene_images(
                    timeline_project_id=timeline_id,
                    timeline=timeline,
                    asset_directories=asset_directories,
                    base_url=forge_url,
                    checkpoint=forge_checkpoint,
                    quality=image_quality,
                    progress_cb=lambda msg: _video_log(job_id, msg),
                )
                _video_log(
                    job_id,
                    f"Local Forge scene generation complete: {result['generated']} new, {result['skipped']} reused",
                )
            video_assembler.render_draft_video(
                timeline,
                render_audio_path,
                out_path,
                progress_cb=lambda msg: _video_log(job_id, msg),
            )
            proj = _register_project(
                kind=project_store.KIND_VIDEO,
                title=f"{timeline.get('source_plan_title') or 'Video'} draft",
                file_path=str(out_path),
                role="video",
                params={
                    "timeline_project_id": timeline_id,
                    "narration_project_id": narration_id,
                    "audio_project_id": render_audio_project_id,
                    "draft": True,
                },
                duration_seconds=timeline.get("audio_duration_seconds"),
            )
            with VIDEO_LOCK:
                job = VIDEO_JOBS[job_id]
                job["video_path"] = str(PROJECTS.file_path(proj["id"], "video")) if proj else str(out_path)
                job["project_id"] = proj["id"] if proj else None
                job["status"] = "done"
                job["done"] = True
        except Exception as exc:
            with VIDEO_LOCK:
                job = VIDEO_JOBS.get(job_id)
                if job:
                    job["error"] = str(exc)
                    job["status"] = "error"
                    job["done"] = True

    threading.Thread(target=worker, daemon=True).start()
    return jsonify({"ok": True, "job_id": job_id})


@app.route("/api/video/draft/status/<job_id>")
def api_video_draft_status(job_id):
    with VIDEO_LOCK:
        job = VIDEO_JOBS.get(job_id)
        if not job:
            return jsonify({"ok": False, "error": "unknown job"}), 404
        return jsonify({"ok": True, "job": dict(job)})


@app.route("/api/video/draft/download/<job_id>")
def api_video_draft_download(job_id):
    with VIDEO_LOCK:
        job = VIDEO_JOBS.get(job_id)
        path = job.get("video_path") if job else None
    if not path or not Path(path).exists():
        return jsonify({"ok": False, "error": "not ready"}), 404
    inline = request.args.get("inline") in ("1", "true", "yes")
    filename = (request.args.get("filename") or "").strip()
    if filename:
        filename = sg.sanitize_filename(Path(filename).stem) + ".mp4"
    else:
        filename = sg.sanitize_filename((job or {}).get("title") or Path(path).stem) + ".mp4"
    return send_file(path, as_attachment=not inline, download_name=filename)


@app.route("/api/video/source/start", methods=["POST"])
def api_video_source_start():
    data = request.get_json(force=True) or {}
    source_video_project_id = (data.get("source_video_project_id") or "").strip()
    source_video_path = (data.get("source_video_path") or "").strip()
    audio_project_id = (data.get("audio_project_id") or "").strip()
    aspect = (data.get("aspect") or "9:16").strip()
    fit = (data.get("fit") or "cover").strip()
    captions = bool(data.get("captions"))
    caption_text = (data.get("caption_text") or "").strip()
    title = (data.get("title") or "source video").strip() or "source video"
    caption_style = (data.get("caption_style") or "tiktok").strip()
    keyword_mode = (data.get("keyword_mode") or "auto").strip()
    pattern_interrupts = bool(data.get("pattern_interrupts"))
    source_enhance = (data.get("source_enhance") or "none").strip()
    title_style = (data.get("title_style") or "top").strip()
    caption_title, caption_body = _strip_title_prefix(caption_text)
    if title == "source video" and caption_title:
        title = caption_title
    caption_text = caption_body or caption_text

    source_project = PROJECTS.get(source_video_project_id) if source_video_project_id else None
    if source_video_project_id:
        source_path = PROJECTS.file_path(source_video_project_id, "source_video")
    elif source_video_path and _is_safe_under_output(source_video_path):
        source_path = Path(source_video_path)
    else:
        source_path = None
    if not source_path or not source_path.exists():
        return jsonify({"ok": False, "error": "Source video is missing."}), 404

    if not audio_project_id:
        return jsonify({"ok": False, "error": "Audio project is missing."}), 400
    audio_project = PROJECTS.get(audio_project_id)
    audio_path = PROJECTS.file_path(audio_project_id, "audio") if audio_project_id else None
    if not audio_project or not audio_path:
        return jsonify({"ok": False, "error": "Final audio project not found."}), 404

    job_id = uuid.uuid4().hex[:12]
    with VIDEO_LOCK:
        VIDEO_JOBS[job_id] = {
            "id": job_id,
            "status": "queued",
            "title": title,
            "done": False,
            "error": None,
            "video_path": None,
            "project_id": None,
            "source_video_project_id": source_video_project_id,
            "audio_project_id": audio_project_id,
            "log": [],
            "started_at": time.time(),
        }

    def worker():
        try:
            safe = sg.sanitize_filename(title)
            out_path = OUTPUT_DIR / f"{safe}_source_video.mp4"
            _video_log(job_id, "Preparing uploaded-video render")
            video_assembler.render_source_video(
                source_path,
                audio_path,
                out_path,
                caption_text=caption_text,
                title_text=title,
                captions=captions,
                aspect=aspect,
                fit=fit,
                caption_style=caption_style,
                keyword_mode=keyword_mode,
                pattern_interrupts=pattern_interrupts,
                source_enhance=source_enhance,
                title_style=title_style,
                progress_cb=lambda msg: _video_log(job_id, msg),
            )
            proj = _register_project(
                kind=project_store.KIND_VIDEO,
                title=f"{title} final",
                file_path=str(out_path),
                role="video",
                params={
                    "source_video_project_id": source_video_project_id,
                    "source_video_title": source_project.get("title") if source_project else None,
                    "audio_project_id": audio_project_id,
                    "source_video": True,
                    "aspect": aspect,
                    "fit": fit,
                    "captions": captions,
                    "caption_style": caption_style,
                    "keyword_mode": keyword_mode,
                    "pattern_interrupts": pattern_interrupts,
                    "source_enhance": source_enhance,
                    "title_style": title_style,
                },
                duration_seconds=audio_project.get("duration_seconds"),
            )
            with VIDEO_LOCK:
                job = VIDEO_JOBS[job_id]
                job["video_path"] = str(PROJECTS.file_path(proj["id"], "video")) if proj else str(out_path)
                job["project_id"] = proj["id"] if proj else None
                job["status"] = "done"
                job["done"] = True
        except Exception as exc:
            with VIDEO_LOCK:
                job = VIDEO_JOBS.get(job_id)
                if job:
                    job["error"] = str(exc)
                    job["status"] = "error"
                    job["done"] = True

    threading.Thread(target=worker, daemon=True).start()
    return jsonify({"ok": True, "job_id": job_id})


# ---------------------------------------------------------------------------
# Publishing workspace (Cadence port)
# ---------------------------------------------------------------------------

@app.route("/api/youtube/connect")
def api_youtube_connect():
    try:
        return redirect(youtube_publish.auth_url(BASE_DIR))
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.route("/api/youtube/callback")
def api_youtube_callback():
    code = request.args.get("code")
    if not code:
        return "Missing YouTube OAuth code.", 400
    try:
        connection = youtube_publish.exchange_code(BASE_DIR, code)
        with PUBLISH_LOCK:
            _save_youtube_connection(connection)
        return """
        <html><body style="font-family:system-ui;background:#090b0c;color:#f4f8f5;padding:40px;">
        <h2>YouTube connected.</h2>
        <p>You can close this tab and return to Ghostline.</p>
        <script>setTimeout(() => window.close(), 1200);</script>
        </body></html>
        """
    except Exception as exc:
        return f"YouTube connection failed: {exc}", 500


@app.route("/api/publish/status")
def api_publish_status():
    connection = _youtube_connection()
    return jsonify({
        "ok": True,
        "youtube_configured": youtube_publish.configured(BASE_DIR),
        "youtube_connected": bool(connection),
        "youtube_channel": {
            "id": connection.get("external_id"),
            "title": connection.get("display_name"),
        } if connection else None,
    })


@app.route("/api/youtube/playlists")
def api_youtube_playlists():
    connection = _youtube_connection()
    if not connection:
        return jsonify({"ok": False, "error": "YouTube is not connected."}), 400
    try:
        playlists, updated = youtube_publish.fetch_playlists(BASE_DIR, connection)
        if updated != connection:
            _save_youtube_connection(updated)
        return jsonify({"ok": True, "playlists": playlists})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 502


# ---------------------------------------------------------------------------
# Optimize Library â€” read existing channel videos and propose per-video
# title/description/tag improvements grounded in channel insights.
# Phase 1: suggest only. No writes back to YouTube.
# /api/optimize/* lives in routes/optimize.py.


@app.route("/api/publish/posts")
def api_publish_posts():
    return jsonify({"ok": True, "posts": _publish_posts()})


@app.route("/api/publish/schedule", methods=["POST"])
def api_publish_schedule():
    ok, err = enforce_tier("pro")
    if not ok:
        return jsonify({"ok": False, "error": err, "code": "UPGRADE"}), 402
    data = request.get_json(force=True) or {}
    post, error = _publish_post_from_request(data)
    if error:
        return jsonify({"ok": False, "error": error}), 400
    with PUBLISH_LOCK:
        posts = _publish_posts()
        posts.append(post)
        _save_publish_posts(posts)
    return jsonify({"ok": True, "post": post})


@app.route("/api/publish/upload-now", methods=["POST"])
def api_publish_upload_now():
    ok, err = enforce_tier("pro")
    if not ok:
        return jsonify({"ok": False, "error": err, "code": "UPGRADE"}), 402
    data = request.get_json(force=True) or {}
    post_id = (data.get("post_id") or "").strip()
    with PUBLISH_LOCK:
        posts = _publish_posts()
        post = next((p for p in posts if p.get("id") == post_id), None)
    if not post:
        return jsonify({"ok": False, "error": "Scheduled post not found."}), 404
    result = _publish_post_to_youtube(post)
    return jsonify(result)


@app.route("/api/publish/templates", methods=["GET", "POST"])
def api_publish_templates():
    if request.method == "GET":
        return jsonify({"ok": True, "templates": _publish_templates()})
    data = request.get_json(force=True) or {}
    template = {
        "id": uuid.uuid4().hex[:12],
        "name": (data.get("name") or data.get("title") or "Untitled template").strip(),
        "caption": (data.get("caption") or "").strip(),
        "title": (data.get("title") or "").strip(),
        "tags": youtube_publish.normalize_tags(data.get("tags") or ""),
        "privacy": data.get("privacy") or "private",
        "created_at": time.time(),
    }
    with PUBLISH_LOCK:
        templates = _publish_templates()
        templates.append(template)
        _save_publish_templates(templates)
    return jsonify({"ok": True, "template": template})


@app.route("/api/publish/recurring", methods=["GET", "POST"])
def api_publish_recurring():
    if request.method == "GET":
        return jsonify({"ok": True, "recurring": _publish_recurring()})
    data = request.get_json(force=True) or {}
    item = {
        "id": uuid.uuid4().hex[:12],
        "name": (data.get("name") or "Recurring slot").strip(),
        "active": bool(data.get("active", True)),
        "days": data.get("days") or [1, 3, 5],
        "time": data.get("time") or "09:00",
        "timezone": data.get("timezone") or "local",
        "template_id": data.get("template_id") or "",
        "created_at": time.time(),
    }
    with PUBLISH_LOCK:
        items = _publish_recurring()
        items.append(item)
        _save_publish_recurring(items)
    return jsonify({"ok": True, "recurring": item})


@app.route("/api/publish/analytics/analyze", methods=["POST"])
def api_publish_analytics_analyze():
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"ok": False, "error": "Upload a YouTube analytics CSV or TSV first."}), 400
    model = _resolve_ollama_model(request.form.get("model"))
    if sg.check_ollama() is None:
        return jsonify({"ok": False, "error": "Ollama is not running on localhost:11434."}), 503
    try:
        rows = _parse_analytics_upload(f)
    except Exception as exc:
        return jsonify({"ok": False, "error": f"Could not parse analytics file: {exc}"}), 400
    summary = _analytics_summary(rows)
    youtube_enrichment = _youtube_enrich_analytics(summary)
    baseline_analysis = _deterministic_analytics_analysis(summary, youtube_enrichment)
    prompt = f"""
You are Ghostline's YouTube growth analyst.

The user uploaded a YouTube analytics table. Analyze it like a practical creator strategist who is allergic to generic advice.

DATA SUMMARY:
{json.dumps(summary, indent=2, ensure_ascii=False)[:14000]}

YOUTUBE API ENRICHMENT:
{json.dumps(youtube_enrichment, indent=2, ensure_ascii=False)[:7000]}

BASELINE ANALYSIS TO IMPROVE, NOT IGNORE:
{json.dumps(baseline_analysis, indent=2, ensure_ascii=False)[:7000]}

Give recommendations that Ghostline can act on when generating future videos.

Return ONLY valid JSON with this schema:
{{
  "diagnosis": "short direct assessment naming the biggest winner and biggest leak",
  "winning_patterns": ["specific pattern from the data with exact title or metric"],
  "problems": ["specific weakness or risk with exact title or metric"],
  "next_video_rules": ["rule Ghostline should follow when making scripts/titles/captions"],
  "hook_guidance": ["hook pattern to use more, with example wording for this channel"],
  "title_guidance": ["title pattern to use more, including at least one rewrite example"],
  "seo_keywords": ["specific keyword/phrase this channel should target based on titles, traffic, API tags, or API descriptions"],
  "content_angles": ["specific video idea based on winners/losers"],
  "posting_guidance": ["timing/frequency/platform note if supported by data"],
  "experiments": [
    {{"name": "experiment name", "why": "reason", "how_to_run": "concrete action"}}
  ]
}}

Rules:
- Use exact titles and metrics from DATA SUMMARY. Do not say metrics are missing if they appear in detected fields.
- Separate packaging problems from retention/content problems. CTR = packaging/title/thumbnail. Avg % viewed/drop-off = opening/content pacing.
- Compare winners against losers. Explain why the top video likely worked and why weak videos likely failed.
- Do not recommend emojis unless the data explicitly shows emoji titles outperforming non-emoji titles.
- Do not give generic advice like "post consistently" unless dates/rows support it.
- Prioritize retention, CTR, title/hook clarity, topic selection, SEO phrases, and repeatable formats.
- Make the advice concrete enough that Ghostline can use it in prompts.
- If using YouTube API enrichment, mention API tags/descriptions only when they help explain keywords or positioning.
- Improve the baseline analysis, but keep its specificity. If unsure, preserve the baseline claim.
- Turn the data into operating rules, not a report. Each next_video_rule should be something a generator can obey.
- Name the audience segment implied by winners: beginner, buyer, fan, problem-aware, trend-watcher, or binge-story viewer.
- Extract reusable packaging formulas from winners, for example "Why X are switching to Y", "I tried X so you don't have to", or "The mistake ruining X".
- Mark unsupported advice as "needs test" inside experiments, not as a fact.
"""
    raw = sg.generate(
        model,
        prompt,
        system="You are a strict JSON YouTube analytics strategist for Ghostline. Every claim must be grounded in provided rows, metrics, titles, traffic sources, or YouTube API enrichment. No generic advice.",
        label="analytics strategy",
        show_progress=False,
        temperature=0.35,
        num_predict=2000,
    )
    parsed = _extract_json_object(raw)
    parsed = _normalize_analytics_analysis(parsed, baseline_analysis)

    # Persist the analysis as channel insights so every idea/title generated
    # afterward is automatically informed by it. This is the closed-loop:
    # analytics in -> Ollama analyzes -> save -> auto-injected into prompts.
    try:
        videos = summary.get("rows") or summary.get("videos") or []
        winning_titles = summary.get("winning_titles") or [r.get("title") for r in videos[:5] if r.get("title")]
        weak_titles = summary.get("weak_titles") or []
        seo_assets = channel_insights.compute_seo_assets(videos)
        insights_payload = {
            "summary": {
                "total_videos": len(videos),
                "avg_ctr": summary.get("avg_ctr"),
                "avg_avd_pct": summary.get("avg_viewed") or summary.get("avg_avd_pct"),
            },
            "videos": videos[:50],
            "winning_titles": [str(t).strip() for t in winning_titles if t][:10],
            "weak_titles": [str(t).strip() for t in weak_titles if t][:10],
            "seo_assets": seo_assets,
            "seo_keywords": parsed.get("seo_keywords") or [],
            "content_angles": parsed.get("content_angles") or [],
            "next_video_rules": parsed.get("next_video_rules") or [],
            "hook_guidance": parsed.get("hook_guidance") or [],
            "title_guidance": parsed.get("title_guidance") or [],
            "winning_patterns": parsed.get("winning_patterns") or [],
            "diagnosis": parsed.get("diagnosis") or "",
            "source": "analytics_csv",
        }
        # Preserve any traffic_sources/search_terms/gap_keywords from prior imports.
        prior = channel_insights.load(BASE_DIR)
        for keep_key in ("traffic_sources", "search_terms", "gap_keywords"):
            if prior.get(keep_key):
                insights_payload[keep_key] = prior[keep_key]
        # Recompute gap_keywords if we have search_terms persisted.
        if insights_payload.get("search_terms"):
            insights_payload["gap_keywords"] = channel_insights.compute_gap_keywords(
                insights_payload["search_terms"],
                [v.get("title") for v in videos],
            )
        channel_insights.save(BASE_DIR, insights_payload)
    except Exception:
        pass  # never let persistence failure break the user-facing response

    return jsonify({"ok": True, "summary": summary, "youtube_enrichment": youtube_enrichment, "analysis": parsed})


# /api/insights/* lives in routes/insights.py.


def _publish_post_to_youtube(post):
    connection = _youtube_connection()
    if not connection:
        return {"ok": False, "error": "YouTube is not connected."}
    video_path = Path(post.get("video_path") or "")
    if not video_path.exists():
        return {"ok": False, "error": "Video file no longer exists."}
    with PUBLISH_LOCK:
        posts = _publish_posts()
        for p in posts:
            if p.get("id") == post.get("id"):
                p["status"] = "POSTING"
                p["error"] = None
        _save_publish_posts(posts)
    try:
        result, updated = youtube_publish.upload_video(BASE_DIR, connection, video_path, post)
        _save_youtube_connection(updated)
        with PUBLISH_LOCK:
            posts = _publish_posts()
            for p in posts:
                if p.get("id") == post.get("id"):
                    p["status"] = "POSTED"
                    p["externalPostId"] = result.get("externalPostId")
                    p["externalUrl"] = result.get("externalUrl")
                    p["posted_at"] = time.time()
            _save_publish_posts(posts)
        return {"ok": True, "result": result}
    except Exception as exc:
        with PUBLISH_LOCK:
            posts = _publish_posts()
            for p in posts:
                if p.get("id") == post.get("id"):
                    p["status"] = "FAILED"
                    p["error"] = str(exc)
            _save_publish_posts(posts)
        return {"ok": False, "error": str(exc)}


def _publish_worker_loop():
    while True:
        try:
            now = time.time()
            posts = _publish_posts()
            due = [
                p for p in posts
                if p.get("status") == "SCHEDULED"
                and float(p.get("scheduled_epoch") or 0) <= now
            ]
            for post in due[:1]:
                _publish_post_to_youtube(post)
        except Exception:
            pass
        time.sleep(30)


threading.Thread(target=_publish_worker_loop, daemon=True).start()


# ---------------------------------------------------------------------------
# Project library (persistent across restarts)
# ---------------------------------------------------------------------------

@app.route("/api/projects")
def api_projects_list():
    kind = (request.args.get("kind") or "").strip().lower() or None
    items = PROJECTS.all()
    if kind:
        items = [p for p in items if p.get("kind") == kind]
    return jsonify({"ok": True, "projects": items})


@app.route("/api/projects/<project_id>")
def api_project_get(project_id):
    p = PROJECTS.get(project_id)
    if not p:
        return jsonify({"ok": False, "error": "not found"}), 404
    return jsonify({"ok": True, "project": p})


@app.route("/api/projects/<project_id>", methods=["DELETE"])
def api_project_delete(project_id):
    ok = PROJECTS.delete(project_id)
    if not ok:
        return jsonify({"ok": False, "error": "not found"}), 404
    return jsonify({"ok": True})


@app.route("/api/projects/<project_id>/file/<role>")
def api_project_file(project_id, role):
    """Stream a file owned by a project. role is the registered key
    (e.g. 'audio', 'script')."""
    # role must be a simple identifier - no path traversal.
    if not role.replace("_", "").isalnum():
        return jsonify({"ok": False, "error": "bad role"}), 400
    full = PROJECTS.file_path(project_id, role)
    if not full or not full.exists():
        return jsonify({"ok": False, "error": "not ready"}), 404
    as_attach = request.args.get("download") in ("1", "true", "yes")
    filename = (request.args.get("filename") or "").strip()
    kwargs = {}
    if filename:
        kwargs["download_name"] = sg.sanitize_filename(Path(filename).stem) + full.suffix
    return send_file(str(full), as_attachment=as_attach, **kwargs)


# Settings, telemetry, and feedback routes live in routes/system.py
# (registered as system_bp at the top of this file).


if __name__ == "__main__":
    # Cloud hosts (Render, Railway, Fly, Heroku) inject PORT and expect us
    # to bind 0.0.0.0. Locally we want 127.0.0.1 so other machines on the
    # LAN can't reach the dev server by accident. PORT env signals "cloud."
    cloud_port = os.environ.get("PORT")
    if cloud_port:
        host = "0.0.0.0"
        port = int(cloud_port)
        print(f"Ghostline - listening on {host}:{port} (cloud mode)")
    else:
        host = "127.0.0.1"
        port = 5000
        print("Phantomline - open http://localhost:5000 in your browser.")
    # threaded=True so a generation thread does not block the status polling.
    app.run(host=host, port=port, debug=False, threaded=True)



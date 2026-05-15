"""SEO infrastructure routes: robots.txt, sitemap.xml, feed.xml, llms.txt,
search-engine verification files, and IndexNow submission.

These are all crawler-facing endpoints that no human visitor hits directly.
Grouped into a blueprint to keep server.py focused on app factory and
middleware."""

import os
import threading
import time
from xml.sax.saxutils import escape as xml_escape

import requests
from flask import Blueprint, jsonify, request

seo_bp = Blueprint("seo", __name__)

SITE_URL = os.environ.get("SITE_URL", "https://phantomline.xyz").rstrip("/")

# Static portion of the sitemap. Per-competitor /alternatives/<slug>
# pages are appended dynamically in sitemap_xml() from alternatives.py.
_SITEMAP_ROUTES = [
    ("/",                              "1.0", "weekly"),
    ("/pricing",                       "0.9", "monthly"),
    # Original category pillars (broad keywords).
    ("/local-ai-video-generator",      "0.85", "monthly"),
    ("/faceless-youtube",              "0.85", "monthly"),
    ("/ai-voice-generator",            "0.85", "monthly"),
    ("/youtube-scheduler",             "0.85", "monthly"),
    ("/youtube-seo-tool",              "0.85", "monthly"),
    # Original niche use-case pillars.
    ("/reddit-stories-video-tool",     "0.8", "monthly"),
    ("/horror-narration-tool",         "0.8", "monthly"),
    ("/mystery-docs-tool",             "0.8", "monthly"),
    # Phase 1 audience-expansion niche pillars (added 2026-05-08).
    ("/asmr-sleep-story-generator",    "0.8", "monthly"),
    ("/true-crime-video-generator",    "0.8", "monthly"),
    ("/motivational-video-generator",  "0.8", "monthly"),
    ("/history-video-generator",       "0.8", "monthly"),
    ("/science-explainer-video-generator", "0.8", "monthly"),
    # Phase 2 audience-expansion pillars (added 2026-05-09).
    ("/faceless-youtube-niches",       "0.85", "monthly"),
    ("/ai-video-editing",              "0.85", "monthly"),
    ("/text-to-video",                 "0.85", "monthly"),
    ("/ai-voice-over",                 "0.8", "monthly"),
    ("/youtube-automation-tools",      "0.85", "monthly"),
    ("/faceless-video-production",     "0.85", "monthly"),
    ("/ai-content-creation",           "0.85", "monthly"),
    ("/youtube-growth-strategy",       "0.8", "monthly"),
    ("/video-monetization",            "0.8", "monthly"),
    ("/ai-script-writing",             "0.8", "monthly"),
    ("/short-form-video",              "0.8", "monthly"),
    ("/content-repurposing",           "0.8", "monthly"),
    # BYOK + offline differentiation pillars (from keyword research 2026-05).
    ("/bring-your-own-api-key",        "0.85", "monthly"),
    ("/ai-video-generator-offline",    "0.8", "monthly"),
    ("/ollama-video-generation",       "0.8", "monthly"),
    ("/ai-youtube-shorts-generator",   "0.8", "monthly"),
    ("/claude-api-video-generator",    "0.8", "monthly"),
    ("/webgpu-video-generation",       "0.8", "monthly"),
    # Persona pages.
    ("/for-solopreneurs",              "0.75", "monthly"),
    ("/for-course-creators",           "0.75", "monthly"),
    ("/for-content-marketers",         "0.75", "monthly"),
    ("/for-content-creators",          "0.75", "monthly"),
    ("/for-agencies",                  "0.75", "monthly"),
    ("/for-educators",                 "0.75", "monthly"),
    # Listicle.
    ("/best-faceless-youtube-tools",   "0.85", "monthly"),
    # Blog index. Per-article URLs are appended dynamically below.
    ("/blog",                          "0.7", "weekly"),
    # Comparison hub.
    ("/alternatives",                  "0.8", "monthly"),
    # Install guides.
    ("/install/gemini-api",            "0.7", "monthly"),
    ("/install/openrouter-api",        "0.7", "monthly"),
    # Static.
    ("/llms.txt",                      "0.5", "monthly"),
    ("/about",                         "0.7", "monthly"),
    ("/privacy",                       "0.4", "yearly"),
    ("/terms",                         "0.4", "yearly"),
]


@seo_bp.route("/robots.txt")
def robots_txt():
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
    from flask import current_app
    response = current_app.response_class(body, mimetype="text/plain")
    response.headers["Cache-Control"] = "public, max-age=3600, s-maxage=86400"
    return response


def _rfc822(iso_date: str) -> str:
    """Convert 'YYYY-MM-DD' to RFC-822 date for RSS pubDate."""
    try:
        t = time.strptime(iso_date, "%Y-%m-%d")
        return time.strftime("%a, %d %b %Y 00:00:00 +0000", t)
    except (ValueError, TypeError):
        return time.strftime("%a, %d %b %Y 00:00:00 +0000", time.gmtime())


@seo_bp.route("/feed.xml")
def rss_feed():
    from alternatives import COMPETITORS
    from blog import published_articles
    items = []
    for a in published_articles():
        url = f"{SITE_URL}/blog/{a['slug']}"
        items.append(
            f"    <item>\n"
            f"      <title>{xml_escape(a['title'])}</title>\n"
            f"      <link>{url}</link>\n"
            f"      <guid isPermaLink=\"true\">{url}</guid>\n"
            f"      <description>{xml_escape(a.get('meta_description', ''))}</description>\n"
            f"      <pubDate>{_rfc822(a.get('published_date', ''))}</pubDate>\n"
            f"    </item>"
        )
    for c in COMPETITORS:
        url = f"{SITE_URL}/alternatives/{c['slug']}"
        items.append(
            f"    <item>\n"
            f"      <title>{xml_escape(c['name'])} Alternative | Phantomline</title>\n"
            f"      <link>{url}</link>\n"
            f"      <guid isPermaLink=\"true\">{url}</guid>\n"
            f"      <description>{xml_escape(c.get('meta_description', ''))}</description>\n"
            f"      <pubDate>{_rfc822('2026-05-02')}</pubDate>\n"
            f"    </item>"
        )
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">\n'
        "  <channel>\n"
        "    <title>Phantomline</title>\n"
        f"    <link>{SITE_URL}/</link>\n"
        f"    <atom:link href=\"{SITE_URL}/feed.xml\" rel=\"self\" type=\"application/rss+xml\" />\n"
        "    <description>Local-first AI video studio for faceless YouTube creators</description>\n"
        f"    <language>en-us</language>\n"
        f"{''.join(chr(10) + i for i in items)}\n"
        "  </channel>\n"
        "</rss>\n"
    )
    from flask import current_app
    response = current_app.response_class(body, mimetype="application/rss+xml")
    response.headers["Cache-Control"] = "public, max-age=3600, s-maxage=86400"
    return response


@seo_bp.route("/llms.txt")
def llms_txt():
    body = (
        "# Phantomline\n"
        "\n"
        "> Phantomline is a local-first AI video studio for faceless YouTube creators.\n"
        "> It replaces the typical 7-tool SaaS stack (script generator, voiceover,\n"
        "> music library, stock visuals, video editor, scheduler, thumbnail tool)\n"
        "> with one local install. Creators can bring their own API key from\n"
        "> Anthropic (Claude), OpenAI (GPT), Google (Gemini), or OpenRouter —\n"
        "> Gemini and OpenRouter offer free tiers with no credit card. Or run\n"
        "> fully offline with Ollama (Llama 3.1), Kokoro TTS, MusicGen, and\n"
        "> ffmpeg. A browser-only version runs via WebGPU and WebAssembly.\n"
        "\n"
        "## Key facts\n"
        "\n"
        "- One-time $79 Founding Lifetime license (replaces ~$200-300/month in subscriptions)\n"
        "- Free tier: 5 videos/month, no credit card required\n"
        "- Desktop install: Python + Ollama + ffmpeg on Mac, Windows, or Linux\n"
        "- Browser version: phantomline.xyz/app (WebLLM + Web Speech + ffmpeg.wasm)\n"
        "- Supports 10+ faceless YouTube niches: Reddit stories, horror narration,\n"
        "  true crime, ASMR sleep stories, motivational, history, science explainers,\n"
        "  mystery documentaries, and more\n"
        "- Built by Kyle Makarski (kyle@makko.ai)\n"
        "\n"
        "## Core capabilities\n"
        "\n"
        "- Script generation (Claude, GPT, Gemini, OpenRouter, local Llama 3.1, or WebGPU)\n"
        "- Narration (local Kokoro TTS, 16 voice profiles, unlimited renders)\n"
        "- Music generation (local MusicGen for ambient backing tracks)\n"
        "- Stock visuals (Pexels API, free key)\n"
        "- Auto-generated captions with niche-appropriate styles\n"
        "- Local MP4 rendering via ffmpeg\n"
        "- YouTube publishing (direct OAuth, schedule + queue)\n"
        "- Optimization Library (detect underperformers, rebuild titles/thumbnails/scripts)\n"
        "- Channel analytics ingest + SEO tuning\n"
        "\n"
        "## Pages\n"
        "\n"
        f"- [Home]({SITE_URL}/)\n"
        f"- [Pricing]({SITE_URL}/pricing)\n"
        f"- [About]({SITE_URL}/about)\n"
        f"- [Blog]({SITE_URL}/blog)\n"
        f"- [Alternatives]({SITE_URL}/alternatives)\n"
        f"- [Faceless YouTube Tool]({SITE_URL}/faceless-youtube)\n"
        f"- [Local AI Video Generator]({SITE_URL}/local-ai-video-generator)\n"
        f"- [AI Voice Generator]({SITE_URL}/ai-voice-generator)\n"
        f"- [YouTube Scheduler]({SITE_URL}/youtube-scheduler)\n"
        f"- [YouTube SEO Tool]({SITE_URL}/youtube-seo-tool)\n"
        f"- [Text to Video]({SITE_URL}/text-to-video)\n"
        f"- [AI Video Editing]({SITE_URL}/ai-video-editing)\n"
        f"- [AI Script Writing]({SITE_URL}/ai-script-writing)\n"
        f"- [YouTube Automation Tools]({SITE_URL}/youtube-automation-tools)\n"
        f"- [Best Faceless YouTube Tools]({SITE_URL}/best-faceless-youtube-tools)\n"
        f"- [For Solopreneurs]({SITE_URL}/for-solopreneurs)\n"
        f"- [For Content Creators]({SITE_URL}/for-content-creators)\n"
        f"- [For Agencies]({SITE_URL}/for-agencies)\n"
        f"- [For Educators]({SITE_URL}/for-educators)\n"
        f"- [For Course Creators]({SITE_URL}/for-course-creators)\n"
        f"- [For Content Marketers]({SITE_URL}/for-content-marketers)\n"
    )
    from flask import current_app
    response = current_app.response_class(body, mimetype="text/plain")
    response.headers["Cache-Control"] = "public, max-age=3600, s-maxage=86400"
    return response


# Search-engine ownership-verification files.
GOOGLE_VERIFICATION_TOKEN = "googleb4a45914f974c6ff"
INDEXNOW_KEY = "6c739122ea404ea19895937dced6fc92"


@seo_bp.route(f"/{GOOGLE_VERIFICATION_TOKEN}.html")
def google_site_verification():
    body = f"google-site-verification: {GOOGLE_VERIFICATION_TOKEN}.html\n"
    from flask import current_app
    response = current_app.response_class(body, mimetype="text/html")
    response.headers["Cache-Control"] = "public, max-age=86400"
    return response


@seo_bp.route(f"/{INDEXNOW_KEY}.txt")
def indexnow_key_file():
    from flask import current_app
    response = current_app.response_class(INDEXNOW_KEY + "\n", mimetype="text/plain")
    response.headers["Cache-Control"] = "public, max-age=86400"
    return response


def _indexnow_submit_all() -> dict:
    """Submit every public URL on the site to IndexNow in one bulk POST."""
    from alternatives import COMPETITORS
    urls: list[str] = [f"{SITE_URL}{path}" for path, _, _ in _SITEMAP_ROUTES]
    for c in COMPETITORS:
        urls.append(f"{SITE_URL}/alternatives/{c['slug']}")
    payload = {
        "host": SITE_URL.replace("https://", "").replace("http://", "").rstrip("/"),
        "key": INDEXNOW_KEY,
        "keyLocation": f"{SITE_URL}/{INDEXNOW_KEY}.txt",
        "urlList": urls,
    }
    try:
        res = requests.post(
            "https://api.indexnow.org/IndexNow",
            json=payload,
            timeout=15,
        )
    except requests.RequestException as exc:
        return {"ok": False, "error": str(exc), "submitted": 0}
    return {
        "ok": res.status_code in (200, 202),
        "status": res.status_code,
        "submitted": len(urls),
        "body": res.text[:200],
    }


@seo_bp.route("/api/admin/indexnow-ping", methods=["POST"])
def indexnow_admin_ping():
    expected = (os.environ.get("PHANTOMLINE_ADMIN_TOKEN") or "").strip()
    presented = (request.headers.get("X-Admin-Token") or "").strip()
    if not expected or presented != expected:
        return jsonify({"ok": False, "error": "Unauthorized."}), 401
    return jsonify(_indexnow_submit_all())


def _indexnow_deploy_ping_once() -> None:
    """Fire IndexNow once per process startup, ~20s after boot."""
    def _runner():
        try:
            time.sleep(20)
            result = _indexnow_submit_all()
            print(f"[indexnow] deploy ping: {result}", flush=True)
        except Exception as exc:
            print(f"[indexnow] deploy ping failed: {exc}", flush=True)
    if os.environ.get("RENDER"):
        threading.Thread(target=_runner, daemon=True, name="indexnow-deploy-ping").start()


_indexnow_deploy_ping_once()


@seo_bp.route("/sitemap.xml")
def sitemap_xml():
    from alternatives import COMPETITORS
    from blog import published_articles
    entries: list[str] = []
    for path, priority, changefreq in _SITEMAP_ROUTES:
        entries.append(
            f"  <url>\n"
            f"    <loc>{SITE_URL}{path}</loc>\n"
            f"    <changefreq>{changefreq}</changefreq>\n"
            f"    <priority>{priority}</priority>\n"
            f"  </url>"
        )
    for c in COMPETITORS:
        entries.append(
            f"  <url>\n"
            f"    <loc>{SITE_URL}/alternatives/{c['slug']}</loc>\n"
            f"    <changefreq>monthly</changefreq>\n"
            f"    <priority>0.7</priority>\n"
            f"  </url>"
        )
    for a in published_articles():
        date = a.get("published_date", "")
        lastmod = f"\n    <lastmod>{date}</lastmod>" if date else ""
        entries.append(
            f"  <url>\n"
            f"    <loc>{SITE_URL}/blog/{a['slug']}</loc>{lastmod}\n"
            f"    <changefreq>monthly</changefreq>\n"
            f"    <priority>0.65</priority>\n"
            f"  </url>"
        )
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(entries) + "\n"
        "</urlset>\n"
    )
    from flask import current_app
    response = current_app.response_class(body, mimetype="application/xml")
    response.headers["Cache-Control"] = "public, max-age=3600, s-maxage=86400"
    return response

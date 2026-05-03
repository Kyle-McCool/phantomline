"""YouTube research + SEO routes.

Surfaces:
- /api/research/youtube/health           - quota state and key rotation status
- /api/research/youtube/reset-cooldowns  - clear per-key cooldown flags
- /api/research/youtube/niche            - niche+competitor scan
- /api/research/youtube/outliers         - over-performing videos for a channel
- /api/research/youtube/curated          - hand-picked starter niches
- /api/research/youtube/seo              - keyword opportunity research, optionally
                                            biased by uploaded channel analytics

The seo route owns the analytics-fit scoring helpers; nothing outside this
blueprint reads them."""

from __future__ import annotations

import re

from flask import Blueprint, jsonify, request

import story_generator as sg
import youtube_research

from core import _extract_json_object, _resolve_ollama_model
from routes.billing import enforce_tier


research_bp = Blueprint("research", __name__)


@research_bp.before_request
def _gate_seo_to_pro():
    """SEO Finder is a Pro feature; basic research surfaces (curated picks,
    health) stay free so the upgrade decision happens after a glimpse."""
    if request.path == "/api/research/youtube/seo":
        ok, err = enforce_tier("pro")
        if not ok:
            return jsonify({"ok": False, "error": err, "code": "UPGRADE"}), 402


# ---------------------------------------------------------------------------
# Simple research surfaces.
# ---------------------------------------------------------------------------

@research_bp.route("/api/research/youtube/health")
def api_youtube_research_health():
    return jsonify({"ok": True, **youtube_research.health()})


@research_bp.route("/api/research/youtube/reset-cooldowns", methods=["POST"])
def api_youtube_research_reset_cooldowns():
    """Clear any per-key quota-cooldown flags. Useful after adding a new key
    or when you believe a key has recovered earlier than the cooldown window."""
    youtube_research.reset_key_cooldowns()
    return jsonify({"ok": True, **youtube_research.health()})


@research_bp.route("/api/research/youtube/niche", methods=["POST"])
def api_youtube_research_niche():
    data = request.get_json(force=True) or {}
    keyword = (data.get("keyword") or "").strip()
    if not keyword:
        keyword = youtube_research.suggest_research_keyword(
            recipe=data.get("recipe") or "",
            niche=data.get("niche") or "",
            topic=data.get("topic") or "",
            video_format=data.get("format") or "",
        )
    try:
        return jsonify({"ok": True, "research": youtube_research.build_niche_search(keyword)})
    except youtube_research.YouTubeResearchError as exc:
        status = 429 if exc.code == "QUOTA_EXCEEDED" else 400 if exc.code == "BAD_INPUT" else 503
        return jsonify({"ok": False, "error": str(exc), "code": exc.code}), status


@research_bp.route("/api/research/youtube/outliers", methods=["POST"])
def api_youtube_research_outliers():
    data = request.get_json(force=True) or {}
    channel = (data.get("channel") or data.get("input") or "").strip()
    if not channel:
        return jsonify({"ok": False, "error": "Channel handle, URL, or video URL required."}), 400
    try:
        return jsonify({"ok": True, "outliers": youtube_research.build_outliers(channel)})
    except youtube_research.YouTubeResearchError as exc:
        status = 404 if exc.code == "NOT_FOUND" else 429 if exc.code == "QUOTA_EXCEEDED" else 503
        return jsonify({"ok": False, "error": str(exc), "code": exc.code}), status


@research_bp.route("/api/research/youtube/curated")
def api_youtube_research_curated():
    count = int(request.args.get("count") or 9)
    return jsonify({"ok": True, "picks": youtube_research.curated_niches(count)})


# ---------------------------------------------------------------------------
# SEO keyword opportunity research. Bridges autocomplete + Ollama-generated
# candidates + channel analytics into one ranked phrase list.
# ---------------------------------------------------------------------------

def _analytics_context_for_seo(payload):
    if not isinstance(payload, dict):
        return {}
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    analysis = payload.get("analysis") if isinstance(payload.get("analysis"), dict) else {}
    enrichment = payload.get("youtube_enrichment") if isinstance(payload.get("youtube_enrichment"), dict) else {}
    if not summary and not analysis:
        return {}

    top_titles = []
    weak_titles = []
    for row in (summary.get("top_by_views") or [])[:5]:
        if row.get("title"):
            top_titles.append(row["title"])
    for row in ((summary.get("weakest_ctr") or []) + (summary.get("weakest_retention") or []))[:6]:
        if row.get("title") and row.get("title") not in weak_titles:
            weak_titles.append(row["title"])
    phrases = []
    for key in ("seo_keywords", "content_angles", "title_guidance", "hook_guidance"):
        values = analysis.get(key) if isinstance(analysis.get(key), list) else []
        for value in values:
            if isinstance(value, str):
                phrases.append(value)
    for video in enrichment.get("videos") or []:
        phrases.extend(video.get("tags") or [])
    return {
        "diagnosis": str(analysis.get("diagnosis") or "")[:1000],
        "top_titles": top_titles[:5],
        "weak_titles": weak_titles[:5],
        "seo_keywords": [p for p in analysis.get("seo_keywords", []) if isinstance(p, str)][:12],
        "content_angles": [p for p in analysis.get("content_angles", []) if isinstance(p, str)][:8],
        "phrases": youtube_research._dedupe_phrases(phrases, limit=24),
        "best_pillars": (summary.get("pillar_summary") or [])[:4],
    }


def _analytics_channel_fit_score(phrase, analytics_context):
    if not analytics_context:
        return 0, []
    phrase_tokens = set(youtube_research._keyword_tokens(phrase))
    if not phrase_tokens:
        return 0, []
    signals = []
    reasons = []
    for title in analytics_context.get("top_titles") or []:
        tokens = set(youtube_research._keyword_tokens(title))
        overlap = len(phrase_tokens & tokens) / max(1, len(phrase_tokens))
        if overlap:
            signals.append(overlap * 55)
            if overlap >= 0.34:
                reasons.append(f"matches winning title \"{title}\"")
    for keyword in analytics_context.get("seo_keywords") or []:
        tokens = set(youtube_research._keyword_tokens(keyword))
        overlap = len(phrase_tokens & tokens) / max(1, len(phrase_tokens))
        if overlap:
            signals.append(overlap * 70)
            if overlap >= 0.34:
                reasons.append(f"matches analytics keyword \"{keyword}\"")
    for angle in analytics_context.get("content_angles") or []:
        tokens = set(youtube_research._keyword_tokens(angle))
        overlap = len(phrase_tokens & tokens) / max(1, len(phrase_tokens))
        if overlap >= 0.25:
            signals.append(overlap * 45)
    weak_penalty = 0
    for title in analytics_context.get("weak_titles") or []:
        tokens = set(youtube_research._keyword_tokens(title))
        overlap = len(phrase_tokens & tokens) / max(1, len(phrase_tokens))
        if overlap >= 0.5:
            weak_penalty = max(weak_penalty, 18)
            reasons.append(f"close to weak performer \"{title}\"")
    if not signals:
        return max(0, 35 - weak_penalty), reasons[:3]
    score = max(0, min(100, round(sum(signals[:6]) / max(1, min(len(signals), 6)) + 22 - weak_penalty)))
    return score, reasons[:3]


def _apply_analytics_fit_to_keywords(keywords, analytics_context):
    if not analytics_context:
        return keywords
    adjusted = []
    for row in keywords:
        item = dict(row)
        base = item.get("opportunityScore")
        fit, reasons = _analytics_channel_fit_score(item.get("phrase") or "", analytics_context)
        item["channelFitScore"] = fit
        item["analyticsFitReasons"] = reasons
        if base is not None:
            item["baseOpportunityScore"] = base
            item["opportunityScore"] = round(max(0, min(100, base * 0.72 + fit * 0.28)))
        adjusted.append(item)
    adjusted.sort(key=lambda row: (row.get("opportunityScore") or 0, row.get("channelFitScore") or 0), reverse=True)
    return adjusted


def _seo_research_prompt(*, niche, analytics_context):
    """Render the prompt body for api_youtube_seo_research."""
    import json as _json
    return f"""
Generate launch-grade YouTube SEO keyword candidates for this exact niche/product/topic:
{niche}

OPTIONAL CHANNEL ANALYTICS CONTEXT:
{_json.dumps(analytics_context, indent=2, ensure_ascii=False)[:5000] if analytics_context else "None supplied. Treat this as standalone SEO research."}

Return ONLY valid JSON. No markdown.
Schema:
{{
  "seed": "{niche}",
  "intent_clusters": ["cluster name with searcher job-to-be-done"],
  "phrases": ["search phrase people would type into YouTube"],
  "title_phrases": ["short phrase suitable for a title"],
  "description_phrases": ["natural phrase suitable for a description"],
  "content_angles": ["video angle that could rank for one or more phrases"]
}}

Rules:
- This must work for ANY niche, including products, brands, tools, local businesses, tutorials, entertainment topics, and faceless channel ideas.
- Build phrases from real search intent buckets: problem-aware, beginner/how-to, comparison/alternative, review/proof, mistake/warning, template/example, buyer/product, and trend/news.
- Prioritize phrases that are specific enough to rank, not broad head terms. Prefer "ai sprite animation workflow" over "ai".
- Include Shorts discovery phrases separately from search-friendly tutorial/product phrases when relevant.
- Do not hallucinate fake metrics.
- Do not include broad useless words like "viral", "best", or "tutorial" alone. Use them only inside a specific natural phrase.
- If the niche is a brand/product like "makko.ai", include product-name phrases plus category phrases people might search if they do not know the brand.
- If analytics context is supplied, bridge the user's proven winners to adjacent search demand. Do not blindly copy weak performers.
- Every content angle must pair a phrase with a viewer payoff: what the viewer gets, avoids, learns, or decides.
"""


@research_bp.route("/api/research/youtube/seo", methods=["POST"])
def api_youtube_seo_research():
    data = request.get_json(force=True) or {}
    niche = (data.get("niche") or data.get("keyword") or "").strip()
    if not niche:
        return jsonify({"ok": False, "error": "Type any niche, product, channel topic, or keyword first."}), 400
    model = _resolve_ollama_model(data.get("model"))
    max_phrases = max(5, min(15, int(data.get("max_phrases") or 12)))
    analytics_context = _analytics_context_for_seo(data.get("analytics_context"))

    autocomplete = youtube_research.autocomplete_phrases(niche, limit=14)
    candidates = [niche]
    seo_content_angles = []
    if analytics_context:
        candidates.extend(analytics_context.get("seo_keywords") or [])
        candidates.extend(analytics_context.get("phrases") or [])
    candidates.extend(autocomplete)
    if sg.check_ollama() is not None:
        prompt = _seo_research_prompt(niche=niche, analytics_context=analytics_context)
        try:
            raw = sg.generate(
                model,
                prompt,
                system="You are Phantomline's YouTube SEO keyword strategist. Output strict JSON only. Generate search phrases, not generic tags.",
                label="seo keyword candidates",
                show_progress=False,
                temperature=0.75,
                num_predict=900,
            )
            parsed = _extract_json_object(raw)
            for key in ("phrases", "title_phrases", "description_phrases"):
                values = parsed.get(key) if isinstance(parsed.get(key), list) else []
                candidates.extend(str(v) for v in values if str(v).strip())
            seo_content_angles = [
                str(v).strip()[:220]
                for v in (parsed.get("content_angles") or [])
                if str(v).strip()
            ][:8]
        except Exception:
            pass

    if not youtube_research.api_key_available():
        fallback = youtube_research._dedupe_phrases(candidates, limit=max_phrases)
        keyword_rows = [
            {
                "phrase": phrase,
                "opportunityScore": None,
                "channelFitScore": _analytics_channel_fit_score(phrase, analytics_context)[0] if analytics_context else None,
                "analyticsFitReasons": _analytics_channel_fit_score(phrase, analytics_context)[1] if analytics_context else [],
                "why": "Candidate phrase. Add/connect a YouTube API key for live demand, velocity, and competition scoring.",
            }
            for phrase in fallback
        ]
        return jsonify({
            "ok": True,
            "mode": "candidate_only",
            "niche": niche,
            "api_available": False,
            "analytics_context_used": bool(analytics_context),
            "message": "YouTube API key is not available, so Phantomline generated candidate phrases without live ranking metrics.",
            "keywords": keyword_rows,
            "content_angles": seo_content_angles,
        })

    try:
        scored = youtube_research.score_keyword_opportunities(candidates, max_phrases=max_phrases)
    except youtube_research.YouTubeResearchError as exc:
        status = 429 if exc.code == "QUOTA_EXCEEDED" else 400 if exc.code in ("BAD_INPUT", "BAD_KEY") else 503
        return jsonify({"ok": False, "error": str(exc), "code": exc.code}), status

    keywords = _apply_analytics_fit_to_keywords(scored.get("keywords") or [], analytics_context)
    if not keywords and scored.get("errors"):
        fallback = youtube_research._dedupe_phrases(candidates, limit=max_phrases)
        keyword_rows = []
        for phrase in fallback:
            fit, reasons = _analytics_channel_fit_score(phrase, analytics_context)
            keyword_rows.append({
                "phrase": phrase,
                "opportunityScore": None,
                "channelFitScore": fit if analytics_context else None,
                "analyticsFitReasons": reasons if analytics_context else [],
                "why": "Live YouTube ranking could not run, so this is an analytics/context candidate.",
            })
        keyword_rows.sort(key=lambda row: row.get("channelFitScore") or 0, reverse=True)
        return jsonify({
            "ok": True,
            "mode": "candidate_only",
            "niche": niche,
            "api_available": True,
            "analytics_context_used": bool(analytics_context),
            "analytics_context_summary": {
                "top_titles": analytics_context.get("top_titles") or [],
                "seo_keywords": analytics_context.get("seo_keywords") or [],
            } if analytics_context else None,
            "message": "Live YouTube ranking could not run. Showing the best candidate phrases from autocomplete, Ollama, and analytics context.",
            "keywords": keyword_rows,
            "title_phrases": [row["phrase"] for row in keyword_rows[:5]],
            "description_phrases": [row["phrase"] for row in keyword_rows[:5]],
            "content_angles": seo_content_angles,
            "tags": [row["phrase"] for row in keyword_rows[:12]],
            "hashtags": [],
            "errors": scored.get("errors") or [],
        })
    top = keywords[:10]
    title_phrases = [row["phrase"] for row in top[:5]]
    tag_phrases = [row["phrase"] for row in top[:12]]
    hashtags = []
    for phrase in top[:8]:
        compact = re.sub(r"[^A-Za-z0-9]+", "", phrase["phrase"].title())
        if compact:
            hashtags.append("#" + compact[:40])
    return jsonify({
        "ok": True,
        "mode": "ranked",
        "niche": niche,
        "api_available": True,
        "analytics_context_used": bool(analytics_context),
        "analytics_context_summary": {
            "top_titles": analytics_context.get("top_titles") or [],
            "seo_keywords": analytics_context.get("seo_keywords") or [],
        } if analytics_context else None,
        "candidates_checked": len(keywords),
        "keywords": keywords,
        "title_phrases": title_phrases,
        "description_phrases": title_phrases,
        "content_angles": seo_content_angles,
        "tags": tag_phrases,
        "hashtags": hashtags,
        "errors": scored.get("errors") or [],
    })

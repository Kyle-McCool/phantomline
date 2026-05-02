"""Optimize Library routes.

Reads the connected YouTube channel's existing uploads and proposes
per-video repackaging (title / description / tag adjustments) grounded in
vidIQ Actionable scoring axes plus the user's own channel insights.

Phase 1 is read-only: the response is suggestions, not commits to YouTube."""

from __future__ import annotations

import json

from flask import Blueprint, jsonify, request

import channel_insights
import story_generator as sg
import youtube_publish
import youtube_research

from core import (
    BASE_DIR,
    _extract_json_object,
    _pick_focus_keyword,
    _resolve_ollama_model,
    _save_youtube_connection,
    _youtube_connection,
)
from routes.billing import enforce_tier


optimize_bp = Blueprint("optimize", __name__)


@optimize_bp.before_request
def _gate_optimize_to_pro():
    """Optimize Library is a Pro feature. Block free-tier requests at the
    blueprint boundary so we don't have to sprinkle enforce_tier on every
    handler."""
    ok, err = enforce_tier("pro")
    if not ok:
        return jsonify({"ok": False, "error": err, "code": "UPGRADE"}), 402


def _optimize_analyze_prompt(*, title_text, description_excerpt, tags,
                             stats_block, channel_title, axis_facts_json,
                             focus_keyword, focus_variations, insights_block):
    """Render the prompt body for api_optimize_analyze. Extracted so the
    long inline f-string is reviewable on its own. All Jinja-style
    interpolations use the named parameters above — no implicit closure
    references to the route's local scope."""
    tags_count = len(tags or [])
    return f"""
You are Phantomline's per-video YouTube SEO doctor. Diagnose ONE video against vidIQ's Actionable scoring axes AND the channel's actual data, then propose a tightly-scoped repackaging.

CURRENT VIDEO METADATA:
title: {title_text}
description: |
{description_excerpt}
tags ({tags_count} total): {", ".join(tags) if tags else "(none)"}
stats: {stats_block}
channel: {channel_title}

vidIQ AXIS FACTS (pre-computed from the metadata above — reason against these, do not recompute):
{axis_facts_json}

FOCUS KEYWORD chosen for repackaging: {focus_keyword}
ACCEPTED VARIATIONS: {", ".join(focus_variations) if focus_variations else "(none — use the focus keyword itself)"}

{insights_block or "No channel insights are loaded yet — work from the metadata only and flag this under axes.notes."}

YOUR JOB:
1. Per-axis diagnosis: for each vidIQ Actionable axis, state the current score out of 5, why, and the concrete fix.
2. Propose ONE new title that satisfies the rules below.
3. Propose a 180–400 word description with the focus keyword in the first 8 words and 2–3x throughout naturally.
4. Propose a tag set (15–25 entries) with the focus keyword as the FIRST tag, layered: variations → niche → broader category → channel-topic. Specify which existing tags to KEEP, ADD, and REMOVE.
5. Score how confident you are this change will improve performance (0-100) and list concrete risks.

Return ONLY valid JSON. No markdown. Schema:
{{
  "verdict": "needs_repackaging" | "healthy" | "drift" | "underperformer",
  "focus_keyword": "{focus_keyword}",
  "vidiq_axes": {{
    "keywords_in_title":          {{"current": 0, "max": 5, "reason": "...", "fix": "..."}},
    "tripled_keyword":            {{"current": 0, "max": 5, "in_title": false, "in_description_first_line": false, "in_tags": false, "fix": "..."}},
    "tag_count":                  {{"current": {tags_count}, "max": 5, "tags_present": {tags_count}, "tags_target": 15, "fix": "..."}},
    "tag_volume":                 {{"current": 0, "max": 5, "reason": "...", "fix": "..."}},
    "keywords_in_description":    {{"current": 0, "max": 5, "reason": "...", "fix": "..."}}
  }},
  "diagnosis": "1-3 sentences naming the specific problem(s) with the current packaging in plain language",
  "ranking_keywords": ["keywords this video likely competes for today"],
  "missed_keywords": ["queries from channel insights this video should target but doesn't"],
  "suggestions": {{
    "title": {{"new": "proposed title containing the focus keyword in the first 60 chars", "why": "why this title scores higher on vidIQ axes"}},
    "description": {{"new": "180–400 word proposed description, focus keyword in first 8 words, 2–3 mentions total", "why": "what changed and which axes it lifts"}},
    "tags": {{"keep": ["existing tag worth keeping"], "add": ["new tag with reason in why field"], "remove": ["tag to drop"], "final_list": ["full proposed 15–25 tag list, focus keyword first"], "why": "tag layering rationale"}}
  }},
  "fit_score": 0,
  "risks": ["risk 1 of applying this change"],
  "do_nothing_reason": "if verdict is 'healthy', explain why we should NOT touch this video"
}}

Hard rules:
- The proposed title MUST contain "{focus_keyword}" or an accepted variation. The proposed description MUST place the focus keyword in the first 8 words. The proposed tag list MUST start with the focus keyword exactly.
- "tripled_keyword.current" = 5 only if all three slots (title + description first line + tags) contain the focus keyword. Otherwise it scales: 2 of 3 = 3, 1 of 3 = 1.
- "tag_count.current" maps tag count → score: 0–4 tags = 0, 5–9 = 1, 10–14 = 3, 15+ = 5.
- Use exact keywords from the channel insights block when relevant. Do NOT invent generic SEO tags ("viral", "best", "tutorial" alone).
- Risks must be specific: e.g. "video is 14 days old and ranking — re-titling could trigger algorithm re-evaluation and lose impressions for 2-4 weeks".
- Never recommend clickbait that does not match the video's actual content.
- If the current title is visibly winning (high views, healthy CTR), set verdict=healthy and explain in do_nothing_reason — do NOT propose a new title in that case, but still fill vidiq_axes honestly so the user sees the score.
"""


@optimize_bp.route("/api/optimize/videos")
def api_optimize_videos():
    """List the connected channel's videos with snippet + statistics."""
    connection = _youtube_connection()
    if not connection:
        return jsonify({"ok": False, "error": "Connect a YouTube channel in Publish first."}), 400
    try:
        max_videos = max(10, min(500, int(request.args.get("limit") or 200)))
    except ValueError:
        max_videos = 200
    try:
        videos, updated = youtube_publish.list_channel_videos(BASE_DIR, connection, max_videos=max_videos)
        if updated != connection:
            _save_youtube_connection(updated)
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 502

    # Compute a coarse "needs attention" tier per video so the UI can sort.
    if videos:
        view_counts = sorted([v.get("views") or 0 for v in videos], reverse=True)
        top_quartile = view_counts[max(0, len(view_counts) // 4)] if view_counts else 0
        bottom_quartile = view_counts[max(0, (len(view_counts) * 3) // 4)] if view_counts else 0
        for v in videos:
            views = v.get("views") or 0
            if views >= top_quartile and top_quartile > 0:
                v["tier"] = "winner"
            elif views <= bottom_quartile:
                v["tier"] = "underperformer"
            else:
                v["tier"] = "mid"
    return jsonify({
        "ok": True,
        "channel": (connection.get("channel") or {}),
        "count": len(videos),
        "videos": videos,
    })


@optimize_bp.route("/api/optimize/video/<video_id>")
def api_optimize_video_detail(video_id):
    connection = _youtube_connection()
    if not connection:
        return jsonify({"ok": False, "error": "Connect a YouTube channel in Publish first."}), 400
    try:
        detail, updated = youtube_publish.fetch_video_detail(BASE_DIR, connection, video_id)
        if updated != connection:
            _save_youtube_connection(updated)
        return jsonify({"ok": True, "video": detail})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 502


@optimize_bp.route("/api/optimize/analyze", methods=["POST"])
def api_optimize_analyze():
    """Run a deep single-video analysis with Ollama. Returns a structured
    suggestion package: new title/description/tags + reasoning + risks.

    No quota cost on YouTube side: we only read what was already fetched.
    Phase 1 is read-only — the response is suggestions, not commits."""
    data = request.get_json(force=True) or {}
    video = data.get("video") if isinstance(data.get("video"), dict) else None
    if not video or not video.get("id"):
        return jsonify({"ok": False, "error": "Provide the full video object from /api/optimize/video/<id>."}), 400
    if sg.check_ollama() is None:
        return jsonify({"ok": False, "error": "Ollama is not running on localhost:11434."}), 503
    model = _resolve_ollama_model(data.get("model"))

    insights = channel_insights.load(BASE_DIR)
    insights_block = channel_insights.to_prompt_block(insights)

    # Compress the video object so we don't blow the context window.
    description_excerpt = (video.get("description") or "")[:2000]
    tags = video.get("tags") or []
    title_text = video.get("title") or ""
    channel_title = video.get("channelTitle") or ""
    stats_block = (
        f"views: {video.get('views')}, likes: {video.get('likes')}, "
        f"comments: {video.get('comments')}, published: {video.get('publishedAt')}"
    )

    autocomplete_seeds = youtube_research.autocomplete_phrases(
        title_text or (insights.get("seo_keywords") or [""])[0] or "", limit=10
    ) if (title_text or insights) else []
    focus = _pick_focus_keyword(
        topic=title_text, title=title_text,
        research_keyword="", insights=insights,
        extra_phrases=autocomplete_seeds,
    )
    focus_keyword = focus["focus"]
    focus_variations = focus["variations"]

    # Pre-compute the vidIQ axis facts so Ollama is reasoning over real numbers
    # instead of guessing. Same axes the user sees in the vidIQ panel.
    title_lower = title_text.lower()
    desc_first_line_lower = description_excerpt.split("\n", 1)[0].lower()
    tags_lower = [t.lower() for t in tags]
    keyword_in_title = focus_keyword and focus_keyword in title_lower
    keyword_in_desc_first_line = focus_keyword and focus_keyword in desc_first_line_lower
    keyword_in_tags = focus_keyword and any(focus_keyword == t or focus_keyword in t for t in tags_lower)
    tripled = sum([bool(keyword_in_title), bool(keyword_in_desc_first_line), bool(keyword_in_tags)])
    axis_facts = {
        "tag_count": len(tags),
        "tag_count_target": 15,
        "tags_below_target": max(0, 15 - len(tags)),
        "focus_keyword": focus_keyword,
        "keyword_in_title": bool(keyword_in_title),
        "keyword_in_description_first_line": bool(keyword_in_desc_first_line),
        "keyword_in_tags": bool(keyword_in_tags),
        "tripled_keyword_hits": f"{tripled}/3",
    }
    axis_facts_json = json.dumps(axis_facts, indent=2, ensure_ascii=False)

    prompt = _optimize_analyze_prompt(
        title_text=title_text, description_excerpt=description_excerpt,
        tags=tags, stats_block=stats_block,
        channel_title=channel_title, axis_facts_json=axis_facts_json,
        focus_keyword=focus_keyword, focus_variations=focus_variations,
        insights_block=insights_block,
    )
    raw = sg.generate(
        model,
        prompt,
        system=(
            "You are Phantomline's strict-JSON YouTube SEO doctor. You reason in vidIQ "
            "Actionable scoring axes (keywords-in-title, tripled-keyword, tag-count, "
            "tag-volume, keywords-in-description) and ground every recommendation in "
            "the provided axis facts and channel insights. No generic advice, no "
            "clickbait, no emoji-stuffing. Output strict JSON only."
        ),
        label="optimize analyze",
        show_progress=False,
        temperature=0.35,
        num_predict=2200,
    )
    parsed = _extract_json_object(raw) or {}
    if isinstance(parsed, dict) and not parsed.get("focus_keyword"):
        parsed["focus_keyword"] = focus_keyword

    # Score the proposed title against insights so the UI can also surface a
    # title-fit badge alongside the model's self-reported fit_score.
    proposed_title = ((parsed.get("suggestions") or {}).get("title") or {}).get("new") or ""
    title_fit = channel_insights.title_fit(proposed_title, insights) if proposed_title else None

    return jsonify({
        "ok": True,
        "video_id": video.get("id"),
        "analysis": parsed,
        "title_fit": title_fit,
        "insights_used": bool(insights),
    })

"""Pillar, persona, and listicle page routes.

Each route renders a long-form SEO template. No business logic — just
template dispatch. Grouped here to keep server.py focused on app
factory and middleware."""

from flask import Blueprint, redirect, render_template

pillars_bp = Blueprint("pillars", __name__)


# -- Original category pillars ------------------------------------------------

@pillars_bp.route("/local-ai-video-generator")
def pillar_local_ai_video_generator():
    return render_template("pillar_local_ai.html")


@pillars_bp.route("/faceless-youtube")
def pillar_faceless_youtube():
    return render_template("pillar_faceless_youtube.html")


@pillars_bp.route("/ai-voice-generator")
def pillar_voice_generator():
    return render_template("pillar_voice_generator.html")


@pillars_bp.route("/youtube-scheduler")
def pillar_youtube_scheduler():
    return render_template("pillar_youtube_scheduler.html")


@pillars_bp.route("/youtube-seo-tool")
def pillar_youtube_seo():
    return render_template("pillar_youtube_seo.html")


# -- Original niche use-case pillars -------------------------------------------

@pillars_bp.route("/reddit-stories-video-tool")
def pillar_reddit_stories():
    return render_template("pillar_reddit_stories.html")


@pillars_bp.route("/horror-narration-tool")
def pillar_horror_narration():
    return render_template("pillar_horror_narration.html")


@pillars_bp.route("/mystery-docs-tool")
def pillar_mystery_docs():
    return render_template("pillar_mystery_docs.html")


# -- Phase 1 audience-expansion pillars (2026-05-08) ---------------------------

@pillars_bp.route("/asmr-sleep-story-generator")
def pillar_asmr_sleep():
    return render_template("pillar_asmr_sleep.html")


@pillars_bp.route("/true-crime-video-generator")
def pillar_true_crime():
    return render_template("pillar_true_crime.html")


@pillars_bp.route("/motivational-video-generator")
def pillar_motivational():
    return render_template("pillar_motivational.html")


@pillars_bp.route("/history-video-generator")
def pillar_history():
    return render_template("pillar_history.html")


@pillars_bp.route("/science-explainer-video-generator")
def pillar_science_explainer():
    return render_template("pillar_science_explainer.html")


# -- Phase 2 audience-expansion pillars (2026-05-09) ---------------------------

@pillars_bp.route("/faceless-youtube-niches")
def pillar_faceless_youtube_niches():
    return render_template("pillar_faceless_youtube_niches.html")


@pillars_bp.route("/ai-video-editing")
def pillar_ai_video_editing():
    return render_template("pillar_ai_video_editing.html")


@pillars_bp.route("/text-to-video")
def pillar_text_to_video():
    return render_template("pillar_text_to_video.html")


@pillars_bp.route("/ai-voice-over")
def pillar_ai_voice_over():
    return render_template("pillar_ai_voice_over.html")


@pillars_bp.route("/youtube-automation-tools")
def pillar_youtube_automation():
    return render_template("pillar_youtube_automation.html")


@pillars_bp.route("/faceless-video-production")
def pillar_faceless_video_production():
    return render_template("pillar_faceless_video_production.html")


@pillars_bp.route("/ai-content-creation")
def pillar_ai_content_creation():
    return render_template("pillar_ai_content_creation.html")


@pillars_bp.route("/youtube-growth-strategy")
def pillar_youtube_growth():
    return render_template("pillar_youtube_growth.html")


@pillars_bp.route("/video-monetization")
def pillar_video_monetization():
    return render_template("pillar_video_monetization.html")


@pillars_bp.route("/ai-script-writing")
def pillar_ai_script_writing():
    return render_template("pillar_ai_script_writing.html")


@pillars_bp.route("/short-form-video")
def pillar_short_form_video():
    return render_template("pillar_short_form_video.html")


@pillars_bp.route("/content-repurposing")
def pillar_content_repurposing():
    return render_template("pillar_content_repurposing.html")


# -- BYOK + offline differentiation pillars ------------------------------------

@pillars_bp.route("/bring-your-own-api-key")
def pillar_byok():
    return render_template("pillar_byok.html")


@pillars_bp.route("/ai-video-generator-offline")
def pillar_offline():
    return render_template("pillar_offline.html")


@pillars_bp.route("/ollama-video-generation")
def pillar_ollama():
    return render_template("pillar_ollama.html")


@pillars_bp.route("/ai-youtube-shorts-generator")
def pillar_shorts():
    return render_template("pillar_shorts.html")


@pillars_bp.route("/claude-api-video-generator")
def pillar_claude_api():
    return render_template("pillar_claude_api.html")


@pillars_bp.route("/webgpu-video-generation")
def pillar_webgpu():
    return render_template("pillar_webgpu.html")


# -- Persona pages -------------------------------------------------------------

@pillars_bp.route("/for-solopreneurs")
def persona_solopreneurs():
    return render_template("persona_solopreneurs.html")


@pillars_bp.route("/for-course-creators")
def persona_course_creators():
    return render_template("persona_course_creators.html")


@pillars_bp.route("/for-content-marketers")
def persona_content_marketers():
    return render_template("persona_content_marketers.html")


@pillars_bp.route("/for-content-creators")
def persona_content_creators():
    return render_template("persona_content_creators.html")


@pillars_bp.route("/for-agencies")
def persona_agencies():
    return render_template("persona_agencies.html")


@pillars_bp.route("/for-educators")
def persona_educators():
    return render_template("persona_educators.html")


@pillars_bp.route("/for/<slug>")
def persona_redirect(slug):
    return redirect(f"/for-{slug}", code=301)


# -- Listicle ------------------------------------------------------------------

@pillars_bp.route("/best-faceless-youtube-tools")
def best_faceless_youtube_tools():
    return render_template("best_faceless_youtube_tools.html")

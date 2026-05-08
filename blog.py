"""Blog content registry. Each entry is a dict the blog routes render.

Articles live in this module rather than as standalone Markdown files
because Phantomline's Flask deploy doesn't have a Markdown processor in
production deps and we want the SEO + schema controls Jinja gives us.

To add a new article:
  1. Append to ARTICLES with all required fields.
  2. Pick a unique `slug` — that becomes /blog/<slug>.
  3. Sitemap auto-includes published articles via blog.published_articles().

Drafts live with `published: False` and don't show up in /blog or
sitemap.xml. Useful for staging copy reviews before flipping the switch.
"""
from __future__ import annotations


ARTICLES: list[dict] = [
    {
        "slug": "faceless-youtube-publishing-cadence-2026",
        "published": True,
        "title": "How Often Should a Faceless YouTube Channel Publish in 2026",
        "subtitle": "The publishing cadence question, answered honestly across niches.",
        "meta_description": "How often a faceless YouTube channel should publish in 2026, broken down by niche. Daily Shorts versus weekly long-form versus the hybrid pattern that most successful channels actually run.",
        "published_date": "2026-05-08",
        "primary_pillar": "/faceless-youtube",
        "tags": ["strategy", "cadence", "faceless"],
        "reading_time": "9 min",
    },
    {
        "slug": "ai-narration-voice-selection-faceless",
        "published": True,
        "title": "Picking the Right AI Narrator Voice for Your Faceless Niche",
        "subtitle": "The voice is the channel. A field guide to matching narrator voice to niche.",
        "meta_description": "Picking the right AI narrator voice for your faceless YouTube niche. Horror, sleep, true crime, motivational, science, history — each rewards a specific voice register and pacing pattern.",
        "published_date": "2026-05-08",
        "primary_pillar": "/ai-voice-generator",
        "tags": ["voice", "TTS", "narration", "Kokoro"],
        "reading_time": "11 min",
    },
    {
        "slug": "true-crime-channel-monetization-2026",
        "published": True,
        "title": "True Crime Channel Monetization in 2026: What Actually Works",
        "subtitle": "Editorial responsibility, ad eligibility, and durable monetization for true crime YouTubers.",
        "meta_description": "How true crime YouTube channels actually monetize in 2026: ad eligibility, sponsorship norms, audience-funded models, and the editorial choices that protect long-term channel health.",
        "published_date": "2026-05-08",
        "primary_pillar": "/true-crime-video-generator",
        "tags": ["true-crime", "monetization", "editorial"],
        "reading_time": "12 min",
    },
    {
        "slug": "sleep-channel-watch-time-economics",
        "published": True,
        "title": "Why Sleep Channels Are a Watch-Time Goldmine on YouTube",
        "subtitle": "The strange, durable economics of 90-minute videos people fall asleep to.",
        "meta_description": "Why sleep channels are one of YouTube's most durable niches: the watch-time math, the unskippable mid-roll dynamics, and the production constraints that keep new entrants out.",
        "published_date": "2026-05-08",
        "primary_pillar": "/asmr-sleep-story-generator",
        "tags": ["sleep", "asmr", "watch-time"],
        "reading_time": "10 min",
    },
    {
        "slug": "history-channel-fact-checking-workflow",
        "published": True,
        "title": "A Fact-Checking Workflow for AI-Assisted History Channels",
        "subtitle": "How to use AI in the script process without trading away the editorial bar that history audiences expect.",
        "meta_description": "A practical fact-checking workflow for history YouTube channels using AI script tools. Source citation, claim atomicity, primary-source bias, and corrections.",
        "published_date": "2026-05-08",
        "primary_pillar": "/history-video-generator",
        "tags": ["history", "fact-checking", "workflow"],
        "reading_time": "11 min",
    },
]


ARTICLES_BY_SLUG = {a["slug"]: a for a in ARTICLES}


def published_articles() -> list[dict]:
    """Articles with published=True, newest first by published_date."""
    return sorted(
        [a for a in ARTICLES if a.get("published")],
        key=lambda a: a.get("published_date", ""),
        reverse=True,
    )

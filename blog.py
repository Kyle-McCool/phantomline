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
        "published_date": "2026-04-14",
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
        "published_date": "2026-04-18",
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
        "published_date": "2026-04-22",
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
        "published_date": "2026-04-25",
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
        "published_date": "2026-04-29",
        "primary_pillar": "/history-video-generator",
        "tags": ["history", "fact-checking", "workflow"],
        "reading_time": "11 min",
    },
    {
        "slug": "how-to-start-faceless-youtube-channel-2026",
        "published": True,
        "title": "How to Start a Faceless YouTube Channel in 2026",
        "subtitle": "A practical launch checklist for creators who want to grow a channel without ever showing their face.",
        "meta_description": "How to start a faceless YouTube channel in 2026: niche selection, AI scripting, voiceover setup, B-roll sourcing, thumbnail strategy, and the publishing cadence that actually grows subscribers.",
        "published_date": "2026-05-02",
        "primary_pillar": "/faceless-youtube",
        "tags": ["faceless", "beginner", "strategy"],
        "reading_time": "11 min",
    },
    {
        "slug": "ollama-vs-cloud-apis-video-scripts",
        "published": True,
        "title": "Ollama vs Cloud APIs for Video Scripts: Which Should You Use?",
        "subtitle": "Local LLMs vs frontier cloud models for faceless YouTube script generation. Cost, quality, privacy, and when each wins.",
        "meta_description": "Ollama vs cloud APIs (Claude, GPT) for YouTube video scripts. Comparing cost per script, output quality, privacy, offline capability, and the hybrid approach most serious creators land on.",
        "published_date": "2026-05-05",
        "primary_pillar": "/ollama-video-generation",
        "tags": ["ollama", "cloud", "BYOK", "scripts"],
        "reading_time": "10 min",
    },
    {
        "slug": "youtube-shorts-algorithm-faceless-creators",
        "published": True,
        "title": "YouTube Shorts Algorithm Tips for Faceless Creators in 2026",
        "subtitle": "What the Shorts algorithm actually rewards, and how faceless channels can exploit it.",
        "meta_description": "YouTube Shorts algorithm tips for faceless creators in 2026: hook timing, retention curves, posting frequency, thumbnail strategy, and the metrics that matter for Shorts shelf placement.",
        "published_date": "2026-05-08",
        "primary_pillar": "/ai-youtube-shorts-generator",
        "tags": ["shorts", "algorithm", "faceless", "growth"],
        "reading_time": "9 min",
    },
    {
        "slug": "byok-explained-bring-your-own-api-key",
        "published": True,
        "title": "BYOK Explained: Why Bring Your Own API Key Matters for Creators",
        "subtitle": "The economics and privacy case for using your own Claude or GPT key instead of platform tokens.",
        "meta_description": "BYOK (bring your own API key) explained for video creators: why using your own Claude or GPT key costs 10-100x less than platform tokens, and why your scripts should stay on your machine.",
        "published_date": "2026-05-11",
        "primary_pillar": "/bring-your-own-api-key",
        "tags": ["BYOK", "API", "economics", "privacy"],
        "reading_time": "8 min",
    },
    {
        # 2026-05-15: retired. Cannibalized /faceless-youtube-niches (same
        # primary keyword, the pillar ranked better). 301 in blog_routes.py
        # consolidates the ranking signal. Slug freed for a future
        # "low-competition faceless niches 2026" post.
        "slug": "best-faceless-youtube-niches-passive-income",
        "published": False,
        "title": "Best Faceless YouTube Niches for Passive Income in 2026",
        "subtitle": "The niches where faceless channels actually build durable revenue, ranked by CPM, competition, and automation potential.",
        "meta_description": "Best faceless YouTube niches for passive income in 2026: ranked by CPM, competition, evergreen potential, and how automatable the production pipeline is for each niche.",
        "published_date": "2026-05-14",
        "primary_pillar": "/faceless-youtube-niches",
        "tags": ["niches", "passive-income", "monetization", "faceless"],
        "reading_time": "12 min",
    },
    {
        # Targets "elevenlabs cost", "elevenlabs pricing 2026" — low-comp
        # commercial queries. Internally links to /alternatives/elevenlabs
        # to push that striking-distance page toward page 1.
        "slug": "elevenlabs-cost-2026-real-math",
        "published": True,
        "title": "How Much Does ElevenLabs Actually Cost in 2026? (Real Math for YouTube Creators)",
        "subtitle": "Tier-by-tier cost breakdown, the character math nobody publishes, and where the bill gets uncomfortable.",
        "meta_description": "A real-world cost breakdown of ElevenLabs for faceless YouTube creators in 2026. Tier-by-tier math, hidden character meters, and what a 30-video-per-month channel actually pays.",
        "published_date": "2026-05-17",
        "primary_pillar": "/ai-voice-generator",
        "tags": ["elevenlabs", "pricing", "tts", "alternatives"],
        "reading_time": "9 min",
    },
    {
        # Targets "invideo free trial", "invideo cost", "invideo watermark"
        # — high-intent queries supporting /alternatives/invideo (currently
        # ranked 11.7, one of our strongest striking-distance pages).
        "slug": "invideo-free-trial-real-cost-2026",
        "published": True,
        "title": "InVideo AI Free Trial: What You Actually Get vs What It Costs in 2026",
        "subtitle": "Watermarks, weekly caps, and the real price jump once you hit them.",
        "meta_description": "The real InVideo AI free trial breakdown: watermarks, export caps, character limits, and the price jump once you hit them. What faceless YouTube creators actually need to know in 2026.",
        "published_date": "2026-05-21",
        "primary_pillar": "/faceless-youtube",
        "tags": ["invideo", "pricing", "free-trial", "alternatives"],
        "reading_time": "8 min",
    },
    {
        # Anchors the $79 Founding Lifetime offer and the BYOK + local-first
        # cost story. Targets "start faceless youtube channel cheap", "how
        # much does a faceless channel cost" — top-funnel beginner queries.
        "slug": "start-faceless-youtube-79-dollars-2026",
        "published": True,
        "title": "How to Start a Faceless YouTube Channel for $79 in 2026 (Full Cost Breakdown)",
        "subtitle": "Hardware you already own, AI tools that don't subscribe you to death, and the one-time stack that replaces $80+/month in SaaS bloat.",
        "meta_description": "A realistic 2026 cost breakdown for starting a faceless YouTube channel: hardware you already own, AI tools that don't subscribe you to death, and the actual $79 one-time path that replaces the standard $80+/month SaaS stack.",
        "published_date": "2026-05-25",
        "primary_pillar": "/faceless-youtube",
        "tags": ["beginner", "cost", "byok", "faceless", "founding-lifetime"],
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

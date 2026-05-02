"""Competitor data for the /alternatives/<slug> SEO pages.

Add a new competitor by appending to COMPETITORS. Each entry is a dict
with the fields the template renders verbatim. Keep copy original (≥800
words per page) — Google penalizes thin clones, so do NOT template the
strengths/advantages/faq verbatim across competitors. The shape is the
same; the substance must be different.

The /alternatives hub page lists every entry below as a card.
"""

from __future__ import annotations


COMPETITORS: list[dict] = [
    # -------------------------------------------------------------------
    # Submagic — captioning + short-form clip tool. ~$24/mo standard tier.
    # -------------------------------------------------------------------
    {
        "slug": "submagic",
        "name": "Submagic",
        "tagline": "AI captioning + short-form video tool",
        "primary_keyword": "Submagic alternative",
        "title_tag": "Submagic Alternative for Faceless YouTube Creators | Phantomline",
        "meta_description": "Looking for a Submagic alternative? Phantomline is a local-first faceless YouTube workflow with no subscription lock-in, private rendering, and a built-in publishing pipeline.",
        "h1": "Submagic Alternative for Faceless YouTube Creators",
        "intro": (
            "Submagic built its name on captioning and short-form clip generation, particularly for "
            "creators repurposing long-form content into vertical clips. It works well as a polish "
            "layer — but it doesn't generate the script, the narration, or the final video. You "
            "still need a separate stack to do the actual creation, and Submagic's monthly fee "
            "stacks on top of every other tool you're already paying for."
            "\n\n"
            "Phantomline is a different shape of tool. Instead of optimizing one slice of the "
            "post-production pipeline, it runs the whole faceless YouTube workflow — script, "
            "narration, captions, music, visuals, MP4 export, and YouTube publishing draft — on "
            "your own machine. No per-render API fees. No subscription you have to keep paying to "
            "open your old projects."
        ),
        "competitor_strengths": [
            "Strong out-of-the-box caption styling with creator-friendly templates.",
            "Quick clip generation from existing long-form footage.",
            "Established UX with polished mobile and web apps.",
            "Active template library and frequent style updates.",
        ],
        "phantomline_advantages": [
            "End-to-end faceless YouTube pipeline — Phantomline writes the script, not just the captions.",
            "Local AI inference: no per-render cost, even at faceless-channel volume (30-90 videos/mo).",
            "Private workflow — your scripts, narration, and channel analytics never leave your machine.",
            "One-time Founding Lifetime tier ($79) — pay once instead of forever.",
            "Built-in YouTube research, vidIQ-aware Optimize Library, and channel-insights ingest for SEO repackaging.",
            "Browser-mode PWA on phones for the same workflow without installing anything.",
        ],
        "comparison_rows": [
            ["Tool",                    "Phantomline",                  "Submagic"],
            ["Best for",                "Faceless YouTube end-to-end",  "Captioning + short clip cuts"],
            ["Generates the script",    "Yes (local Llama 3.1)",        "No — bring your own"],
            ["Generates narration",     "Yes (Kokoro local TTS)",       "No"],
            ["Generates music",         "Yes (MusicGen local + bundled pack)", "No"],
            ["Captioning + styling",    "Yes (built into render)",      "Yes (their core feature)"],
            ["Local-first?",            "Yes",                           "Cloud-only"],
            ["Subscription required?",  "Free tier + optional Pro",     "Subscription required"],
            ["One-time lifetime tier?", "Yes ($79 founding, first 200)", "No"],
            ["YouTube publishing draft","Yes",                           "Limited"],
            ["Per-render API costs?",   "No",                            "Bundled, but capped"],
        ],
        "when_competitor_wins": (
            "Submagic is a sharp choice if you already have a stable creation pipeline and just want "
            "fast, branded captions for clips. If you film yourself, edit in CapCut or Premiere, and "
            "only need the captioning layer plus quick repurposing of existing footage, Submagic gets "
            "you 80% of the way there with low setup time. The caption template library is genuinely "
            "good and updates regularly."
            "\n\n"
            "It's also a reasonable choice for creators who don't want to install anything. Submagic "
            "is purely cloud — open the app, paste a video, get captioned output. There's no Python "
            "venv, no Ollama, no local models. The trade-off is the recurring fee and the cap on "
            "monthly minutes."
        ),
        "when_phantomline_wins": (
            "Phantomline is the better fit if you're running a faceless YouTube channel — Reddit "
            "stories, horror narration, mystery docs, listicles, mythology, survival tips — where you "
            "need to generate the script and the voice and the video, not just polish footage you "
            "already have. The faceless niche workflow is fundamentally different from a vlogger or "
            "talking-head creator: there's no source camera footage, the volume is higher (30-90 "
            "videos/month is normal), and per-render cost is the bottleneck, not editing time."
            "\n\n"
            "On that profile, Submagic only solves one slice of the problem. You still need ChatGPT "
            "or Claude for scripts, ElevenLabs or similar for voice, a music license, a stock B-roll "
            "subscription, a thumbnail tool, an SEO research tool, and a scheduler. Phantomline "
            "consolidates all of those into one local-first workflow with no recurring per-render "
            "fees. At 30 videos a month, the math swings hard in favor of paying once for the tool "
            "instead of paying per render across five subscriptions."
            "\n\n"
            "Phantomline is also the better choice if privacy matters — scripts you're researching, "
            "narration you're tweaking, channel analytics you've uploaded — none of it leaves your "
            "machine. Submagic, like every cloud tool, processes your content on their servers."
        ),
        "feature_comparison": [
            ("Script generation", "Local Llama 3.1 (or any Ollama model)", "Not included — bring your own"),
            ("Narration / voice", "Kokoro TTS, 16 voices, fully local", "Not included"),
            ("Captioning + styles", "Built-in, configurable templates", "Strong — their core feature"),
            ("Music", "MusicGen + bundled royalty-free pack", "Not included"),
            ("Visuals / B-roll", "Pexels + optional Forge for AI scenes", "Repurposes your existing footage"),
            ("MP4 export", "ffmpeg local render, no cap", "Cloud render, capped per tier"),
            ("YouTube metadata draft", "Title + description + tags + schedule", "Limited"),
            ("Local / private workflow", "Everything stays on your device", "Cloud-only"),
        ],
        "pricing_comparison_phantomline": (
            "Phantomline is free for up to 5 renders/month. Creator Pro is $15/month or $99/year. "
            "Founding Lifetime is $79 one-time for the first 200 customers — locked-in price for life."
        ),
        "pricing_comparison_competitor": (
            "Submagic uses subscription-based pricing with monthly minute caps that scale with the "
            "tier. Check submagic.co for current pricing."
        ),
        "who_picks_competitor": (
            "Pick Submagic if you primarily need fast, branded captioning for footage you already "
            "have, you don't want to install anything, and the recurring monthly fee is a fair trade "
            "for the polish their template library provides."
        ),
        "who_picks_phantomline": (
            "Pick Phantomline if you're running (or planning to run) a faceless YouTube channel where "
            "you need to generate scripts and narration in addition to captions, you ship enough "
            "videos that per-render fees compound painfully, you want one tool instead of five, and "
            "you'd rather pay $79 once than $24/month forever."
        ),
        "faq": [
            ("Is Phantomline a Submagic alternative?",
             "Yes — but more accurately, Phantomline is a superset for the faceless YouTube use case. "
             "Submagic captions footage; Phantomline generates the script, voice, captions, music, and "
             "visuals from scratch. If you only need captioning, Submagic is purpose-built for that. If "
             "you need the whole faceless pipeline, Phantomline is the bigger tool."),
            ("Is Phantomline better for faceless YouTube?",
             "For faceless workflows, yes. Submagic is shaped for creators who already have footage. "
             "Faceless creators usually don't have any source footage — they generate everything from "
             "a topic prompt. Phantomline starts from the prompt and produces a finished MP4 with no "
             "manual editing required."),
            ("Does Phantomline run locally?",
             "Yes. The desktop install runs Ollama for scripts, Kokoro for TTS, MusicGen for music, "
             "and ffmpeg for video assembly — all on your machine. The browser version (PWA) runs "
             "WebLLM, Web Speech, Web Audio, and ffmpeg.wasm — also all client-side, with no server "
             "round-trip for AI inference."),
            ("Does Phantomline require paid APIs?",
             "No. The default pipeline is fully local. Optional integrations (YouTube Data API for "
             "research, Pexels for stock B-roll) are free with a personal API key, and only used if "
             "you opt in. There are no required SaaS fees."),
            ("Can I use Phantomline for Reddit story videos?",
             "Yes — that's one of the niches the script engine is tuned for. The default genres "
             "include Reddit-style storytime, mystery, horror narration, ancient alien discoveries, "
             "abandoned town docs, lost transmissions, and more. You can also write any custom genre "
             "and the pipeline adapts."),
        ],
    },

    # -------------------------------------------------------------------
    # OpusClip — long-form-to-shorts AI clip generator. ~$15-29/mo.
    # -------------------------------------------------------------------
    {
        "slug": "opus-clip",
        "name": "OpusClip",
        "tagline": "Long-form to short-form AI clip generator",
        "primary_keyword": "OpusClip alternative",
        "title_tag": "OpusClip Alternative for Faceless YouTube Creators | Phantomline",
        "meta_description": "Looking for an OpusClip alternative? Phantomline runs the full faceless YouTube workflow locally — script, narration, captions, and MP4 — with no subscription and no upload caps.",
        "h1": "OpusClip Alternative for Faceless YouTube Creators",
        "intro": (
            "OpusClip is a clip-and-caption tool aimed at long-form creators who need to spin "
            "podcast or vlog footage into vertical shorts. It's a sharp tool for that specific "
            "shape of content — but it depends on you having long-form source footage to begin "
            "with. For faceless creators who generate everything from a script prompt, OpusClip "
            "is solving the wrong half of the problem."
            "\n\n"
            "Phantomline is built for the faceless workflow from the script prompt forward. It "
            "writes the script with a local LLM, narrates with a local TTS, generates ambient "
            "music, layers captions and B-roll, and exports an MP4 ready for YouTube. No source "
            "footage required. No subscription. License keys validate offline."
        ),
        "competitor_strengths": [
            "Strong virality scoring — OpusClip ranks clips by predicted shareability.",
            "Auto-reframing for vertical aspect ratios.",
            "Decent face-tracking that keeps the speaker centered in 9:16.",
            "Caption styling with multiple template options.",
            "Bulk upload and batch processing for podcast hosts.",
        ],
        "phantomline_advantages": [
            "Generates the script and the narration — OpusClip needs source footage to start.",
            "No upload caps. The free tier renders 5 videos/month locally; paid tiers are unlimited.",
            "Local AI inference means scripts and narration never go to a third-party server.",
            "Built for the faceless niche, not the talking-head niche — different copy patterns, different visuals.",
            "Founding Lifetime tier at $79 instead of $15-29/month forever.",
            "Direct YouTube publishing draft (title, description, tags, schedule) integrated into the same render.",
        ],
        "comparison_rows": [
            ["Tool",                       "Phantomline",                          "OpusClip"],
            ["Best for",                   "Faceless YouTube end-to-end",          "Long-form to vertical shorts"],
            ["Needs source footage?",      "No — generates everything",            "Yes — bring long-form video"],
            ["Generates the script",       "Yes",                                   "No"],
            ["Generates narration",        "Yes (local TTS)",                       "Uses speaker's voice from footage"],
            ["Auto-reframe / face track",  "Optional (less needed for faceless)",  "Strong (their core feature)"],
            ["Captions + styling",         "Built into render",                     "Yes"],
            ["Local-first?",               "Yes",                                   "Cloud-only"],
            ["Subscription required?",     "Free tier + optional Pro",             "Subscription required"],
            ["One-time lifetime tier?",    "Yes ($79 founding)",                   "No"],
            ["Render caps",                "5/mo free, unlimited Pro",             "Tiered upload limits"],
        ],
        "when_competitor_wins": (
            "OpusClip is the right tool when you have an existing long-form library — podcast "
            "episodes, gameplay streams, interview footage, talking-head vlogs — and need to "
            "convert it into vertical shorts at scale. Their virality scoring is genuinely useful "
            "for picking the most repostable moments, and their face-tracking handles a single "
            "speaker well in tight 9:16 reframes."
            "\n\n"
            "If your channel runs on talking-head video and you publish 1-3 long-form pieces a "
            "week, OpusClip turning each one into 5-10 short clips is a pure efficiency win. The "
            "subscription pays for itself in saved editor hours."
        ),
        "when_phantomline_wins": (
            "Phantomline wins when you're producing faceless content and there is no source footage "
            "to clip. Reddit stories, horror narration, mythology explainers, listicle channels, "
            "survival guides — none of those niches start with a recorded speaker. They start with "
            "a topic prompt that needs to become a script, voice, captions, music bed, and MP4."
            "\n\n"
            "OpusClip cannot do any of that — it needs you to record yourself first, then it cuts "
            "the recording into shorter clips. Phantomline starts where OpusClip can't: with no "
            "footage at all. A topic, a tone, a target length, and the local LLM writes the script, "
            "Kokoro narrates it, MusicGen scores it, and ffmpeg renders the MP4."
            "\n\n"
            "Phantomline is also the better economics for high-volume faceless channels. OpusClip's "
            "subscription tiers cap monthly upload minutes — fine for a podcast clipping 5 hours a "
            "week, expensive for a channel publishing daily. Local rendering has no per-render fee, "
            "so the math holds at any volume."
        ),
        "feature_comparison": [
            ("Script generation", "Yes — local Llama 3.1, custom genres", "Not the use case"),
            ("Narration", "Kokoro TTS, 16 voices, fully local", "Uses speaker's voice from source"),
            ("Auto-reframe / face tracking", "Optional", "Yes — their core feature"),
            ("Captioning", "Built into the render pipeline", "Strong, virality-scored"),
            ("Music", "MusicGen + bundled royalty-free pack", "Not generated"),
            ("Visuals / B-roll", "Pexels + Forge integration", "Reframes existing footage"),
            ("MP4 export", "Local ffmpeg, unlimited on Pro", "Cloud, tier-capped"),
            ("YouTube metadata draft", "Yes — title, description, tags, schedule", "Limited"),
            ("Local / private workflow", "Yes", "Cloud-only"),
        ],
        "pricing_comparison_phantomline": (
            "Free tier (5 renders/month). Creator Pro $15/month or $99/year. Founding Lifetime "
            "$79 one-time for the first 200 customers."
        ),
        "pricing_comparison_competitor": (
            "OpusClip uses subscription-based pricing with tiered upload-minute caps. Check "
            "opus.pro for current pricing."
        ),
        "who_picks_competitor": (
            "Pick OpusClip if you have a long-form video library and need to mass-produce vertical "
            "shorts from existing speaker footage. Their virality scoring and face-tracking are "
            "genuinely the strongest in this category."
        ),
        "who_picks_phantomline": (
            "Pick Phantomline if you're running a faceless YouTube channel where there is no "
            "speaker on camera, you need to generate scripts and narration from prompts, and you "
            "ship enough volume that subscription caps would bottleneck you. Phantomline starts "
            "from the prompt; OpusClip starts from the footage."
        ),
        "faq": [
            ("Is Phantomline an OpusClip alternative?",
             "Yes, for the faceless YouTube use case. OpusClip clips long-form footage into vertical "
             "shorts. Phantomline generates the long-form-equivalent script, the voice, the captions, "
             "and the MP4 from scratch. They solve adjacent problems."),
            ("Can Phantomline turn long-form footage into shorts?",
             "Phantomline's primary workflow is generating from a prompt, not clipping source footage. "
             "If your goal is specifically reformatting existing recordings into vertical shorts with "
             "auto-reframing, OpusClip is better tooled for that. If your goal is generating faceless "
             "content from scratch, Phantomline is the right pick."),
            ("Does Phantomline run locally?",
             "Yes. Desktop install runs Ollama, Kokoro, MusicGen, and ffmpeg locally. The browser "
             "version runs WebLLM, Web Speech, Web Audio, and ffmpeg.wasm in your browser. No server "
             "inference round-trips for the AI work."),
            ("Does Phantomline have upload or render caps?",
             "Free tier: 5 renders/month. Creator Pro and Founding Lifetime: unlimited. Because "
             "rendering happens on your hardware, the cap on Pro is just whatever your machine can "
             "produce — there's no per-render fee on our end to throttle."),
            ("Can I use Phantomline for Reddit story videos?",
             "Yes. The script engine has built-in support for Reddit storytime, mystery, horror, "
             "ancient alien discoveries, abandoned towns, lost transmissions, and other faceless "
             "niches. You can also pass any custom genre and tone."),
        ],
    },
]


# Quick lookup by slug for the route handler.
COMPETITORS_BY_SLUG = {c["slug"]: c for c in COMPETITORS}

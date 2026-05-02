"""
Channel Insights — persistent analytics + SEO state for Phantomline.

Bridges YouTube Analytics (what's working) with SEO research (what people
search for) so every idea, title, and script Phantomline generates is informed
by the user's actual channel data instead of generic niche prompts.

Stored at output/insights/channel_insights.json. Atomic writes.

Schema:
{
  "updated_at": <epoch>,
  "summary": {                     # high-level diagnostic numbers
    "total_videos": int,
    "avg_ctr": float, "avg_avd_pct": float,
    "search_share_pct": float, "browse_share_pct": float,
    "suggested_share_pct": float, "external_share_pct": float
  },
  "videos": [...],                 # per-video metrics from overview CSV
  "winning_titles": [...],         # top videos by views (titles only)
  "weak_titles": [...],            # bottom-quartile titles
  "seo_assets": [                  # videos pulling search traffic
    {"title": "...", "search_share_pct": 41.2, "views": 12000, "subs_per_1k": 3.4}
  ],
  "search_terms": [                # queries that brought views
    {"query": "lost city in amazon", "views": 412, "videos": ["..."]}
  ],
  "gap_keywords": [...],           # search terms NOT in any current title
  "seo_keywords": [...],           # keywords the channel should target
  "content_angles": [...],         # video ideas anchored to data
  "next_video_rules": [...],       # rules Phantomline should follow when generating
  "hook_guidance": [...],          # hook patterns, with examples
  "title_guidance": [...],         # title patterns, with rewrites
}
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path


_lock = threading.Lock()
INSIGHTS_FILENAME = "channel_insights.json"


def _path(base_dir):
    p = Path(base_dir) / "insights"
    p.mkdir(parents=True, exist_ok=True)
    return p / INSIGHTS_FILENAME


def load(base_dir):
    """Return current insights dict, or {} if none yet. Never raises."""
    p = _path(base_dir)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8")) or {}
    except (OSError, json.JSONDecodeError):
        return {}


def save(base_dir, insights):
    """Atomic write."""
    p = _path(base_dir)
    with _lock:
        tmp = p.with_suffix(".tmp")
        payload = dict(insights or {})
        payload["updated_at"] = time.time()
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, p)
    return payload


def clear(base_dir):
    p = _path(base_dir)
    if p.exists():
        try: p.unlink()
        except OSError: pass


def merge(base_dir, partial):
    """Merge a partial update into the persisted insights, preserving prior fields."""
    current = load(base_dir)
    merged = {**current, **(partial or {})}
    return save(base_dir, merged)


# ---------------------------------------------------------------------------
# Tokenization helpers (kept simple to avoid yet another dep)
# ---------------------------------------------------------------------------

_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "of", "to", "in", "on", "for", "with",
    "is", "are", "was", "were", "be", "by", "at", "from", "this", "that", "it",
    "you", "your", "i", "me", "my", "we", "our", "they", "them", "their",
    "what", "why", "how", "when", "where", "who", "which", "do", "does", "did",
    "can", "could", "will", "would", "should", "have", "has", "had", "as", "if",
    "then", "than", "so", "into", "out", "up", "down", "vs", "via",
}


def _tokens(text, *, min_len=2):
    text = (text or "").lower()
    parts = re.findall(r"[a-z0-9][a-z0-9\-']*", text)
    return [t for t in parts if len(t) >= min_len and t not in _STOPWORDS]


def title_tokens(text):
    return set(_tokens(text))


# ---------------------------------------------------------------------------
# Title-fit scoring
# ---------------------------------------------------------------------------

def title_fit(title, insights):
    """
    Score a candidate title against the channel's persisted insights.

    Returns:
      {
        "score": int 0..100,
        "verdict": "strong_fit" | "stretch" | "risky" | "neutral",
        "reasons": ["matches search term 'lost city'", ...],
        "matched_search_terms": [...],
        "matched_assets": [...],
        "warnings": [...]
      }
    """
    title = (title or "").strip()
    out = {
        "score": 50,
        "verdict": "neutral",
        "reasons": [],
        "matched_search_terms": [],
        "matched_assets": [],
        "warnings": [],
    }
    if not title or not insights:
        return out

    t_tokens = title_tokens(title)
    if not t_tokens:
        return out

    score = 30
    reasons = []

    # +25 if title overlaps with any actual search query that brought traffic
    matched_terms = []
    for entry in insights.get("search_terms") or []:
        q = entry.get("query") or ""
        q_tokens = title_tokens(q)
        if not q_tokens:
            continue
        overlap = len(t_tokens & q_tokens) / max(1, len(q_tokens))
        if overlap >= 0.5:
            matched_terms.append(q)
    if matched_terms:
        score += min(35, 12 * len(matched_terms))
        reasons.append(f"matches search terms that already pull traffic: " + ", ".join(f'"{m}"' for m in matched_terms[:3]))
        out["matched_search_terms"] = matched_terms[:8]

    # +20 if title overlaps with current SEO asset video titles
    matched_assets = []
    for asset in insights.get("seo_assets") or []:
        a_title = asset.get("title") or ""
        a_tokens = title_tokens(a_title)
        if not a_tokens:
            continue
        overlap = len(t_tokens & a_tokens) / max(1, len(a_tokens))
        if overlap >= 0.4:
            matched_assets.append(a_title)
    if matched_assets:
        score += min(25, 8 * len(matched_assets))
        reasons.append(f"adjacent to existing SEO assets: {len(matched_assets)} similar video(s) ranking now")
        out["matched_assets"] = matched_assets[:5]

    # +10 if title hits curated SEO keywords from prior analysis
    matched_kw = []
    for kw in insights.get("seo_keywords") or []:
        kw_tokens = title_tokens(kw)
        if not kw_tokens:
            continue
        if len(t_tokens & kw_tokens) / max(1, len(kw_tokens)) >= 0.5:
            matched_kw.append(kw)
    if matched_kw:
        score += min(15, 5 * len(matched_kw))
        reasons.append(f"matches curated SEO target(s): " + ", ".join(f'"{m}"' for m in matched_kw[:3]))

    # -25 if title looks like an attempted-but-failed pattern
    weak_tokens = set()
    for weak in insights.get("weak_titles") or []:
        weak_tokens |= title_tokens(weak)
    if weak_tokens and t_tokens:
        weak_overlap = len(t_tokens & weak_tokens) / max(1, len(t_tokens))
        if weak_overlap >= 0.6:
            score -= 25
            out["warnings"].append("similar wording to videos that underperformed for this channel")

    score = max(0, min(100, score))
    out["score"] = score
    out["reasons"] = reasons

    if score >= 75:
        out["verdict"] = "strong_fit"
    elif score >= 55:
        out["verdict"] = "good_fit"
    elif score >= 35:
        out["verdict"] = "stretch"
    else:
        out["verdict"] = "risky"
    return out


# ---------------------------------------------------------------------------
# Prompt-block helper for idea/title generation
# ---------------------------------------------------------------------------

def to_prompt_block(insights, *, max_assets=5, max_terms=8, max_keywords=8, max_rules=5):
    """
    Turn persisted insights into a compact prompt block that can be injected
    into idea/title/script generation prompts. Returns "" if no insights.
    """
    if not insights:
        return ""

    parts = [
        "This channel's current data signals (treat as operating constraints, not background trivia):",
        "- Use these signals to choose topics, hooks, titles, descriptions, captions, and posting tests.",
        "- Model the pattern behind winners, not the exact wording.",
        "- Avoid repeating weak titles unless the angle is substantially changed.",
    ]

    assets = (insights.get("seo_assets") or [])[:max_assets]
    if assets:
        parts.append("Top SEO assets (videos already pulling search traffic):")
        for a in assets:
            line = f"- \"{a.get('title','').strip()}\""
            views = a.get("views")
            if views:
                line += f" ({int(views):,} views"
                share = a.get("search_share_pct")
                if share:
                    line += f", {share:.0f}% search"
                line += ")"
            parts.append(line)

    winning = (insights.get("winning_titles") or [])[:max_assets]
    if winning and not assets:
        parts.append("Best-performing titles to model (style and angle, not wording):")
        for t in winning:
            parts.append(f"- \"{str(t).strip()}\"")

    terms = (insights.get("search_terms") or [])[:max_terms]
    if terms:
        parts.append("Real search queries that brought viewers (target adjacent demand):")
        for t in terms:
            q = (t.get("query") or "").strip()
            views = t.get("views")
            if q:
                parts.append(f"- \"{q}\"" + (f" ({int(views)} views)" if views else ""))

    gap = (insights.get("gap_keywords") or [])[:max_terms]
    if gap:
        parts.append("Untapped queries (channel has search demand here but no dedicated video):")
        for g in gap:
            parts.append(f"- \"{str(g).strip()}\"")

    keywords = (insights.get("seo_keywords") or [])[:max_keywords]
    if keywords:
        parts.append("Curated SEO keywords this channel should target:")
        for k in keywords:
            parts.append(f"- \"{str(k).strip()}\"")

    rules = (insights.get("next_video_rules") or [])[:max_rules]
    if rules:
        parts.append("Rules from prior performance analysis (must follow unless the user overrides them):")
        for r in rules:
            parts.append(f"- {str(r).strip()}")

    hook_rules = (insights.get("hook_guidance") or [])[:max_rules]
    if hook_rules:
        parts.append("Hook patterns to use more often:")
        for h in hook_rules:
            parts.append(f"- {str(h).strip()}")

    title_rules = (insights.get("title_guidance") or [])[:max_rules]
    if title_rules:
        parts.append("Title patterns to use more often:")
        for h in title_rules:
            parts.append(f"- {str(h).strip()}")

    weak = (insights.get("weak_titles") or [])[:3]
    if weak:
        parts.append("Underperforming titles on this channel — DO NOT generate similar wording:")
        for w in weak:
            parts.append(f"- \"{str(w).strip()}\"")

    if len(parts) <= 1:
        return ""
    return "\n".join(parts) + "\n"


# ---------------------------------------------------------------------------
# CSV parsers — tolerant of YouTube Studio's various export formats
# ---------------------------------------------------------------------------

def parse_traffic_sources_rows(rows):
    """Aggregate Traffic Source CSV rows into share-percentages.

    Accepts the YouTube Studio Traffic Source export. Looks for columns:
      "Traffic source type" or "Traffic source", "Views", "Watch time"
    """
    if not rows:
        return {"shares": {}, "total_views": 0}
    counts = {}
    total = 0
    for row in rows:
        source = ""
        for k in ("Traffic source type", "Traffic source", "Source", "Type"):
            if row.get(k):
                source = str(row[k]).strip()
                break
        if not source:
            continue
        views = _safe_int(row.get("Views") or row.get("views") or 0)
        counts[_classify_traffic_source(source)] = counts.get(_classify_traffic_source(source), 0) + views
        total += views
    if total <= 0:
        return {"shares": {}, "total_views": 0}
    shares = {k: round(v * 100.0 / total, 1) for k, v in counts.items()}
    return {"shares": shares, "total_views": total}


def parse_search_terms_rows(rows):
    """Reduce Search Terms CSV rows into [{query, views}] sorted desc."""
    out = []
    for row in rows:
        q = ""
        for k in ("Search term", "search term", "Query", "Search query", "Term"):
            if row.get(k):
                q = str(row[k]).strip()
                break
        if not q:
            continue
        views = _safe_int(row.get("Views") or row.get("views") or row.get("View count") or 0)
        out.append({"query": q, "views": views})
    out.sort(key=lambda r: -r["views"])
    return out[:50]


def compute_gap_keywords(search_terms, video_titles, limit=15):
    """A search query is a 'gap' if it brought traffic but no current video
    title contains all the meaningful tokens of the query."""
    title_token_sets = [title_tokens(t) for t in (video_titles or [])]
    if not title_token_sets:
        return [s["query"] for s in (search_terms or [])][:limit]
    gaps = []
    for entry in search_terms or []:
        q = entry.get("query") or ""
        q_tokens = title_tokens(q)
        if not q_tokens:
            continue
        # Covered if any title contains 70%+ of query tokens.
        covered = any(
            len(q_tokens & ts) / max(1, len(q_tokens)) >= 0.7
            for ts in title_token_sets
        )
        if not covered:
            gaps.append(q)
    return gaps[:limit]


def compute_seo_assets(videos, traffic_sources_per_video=None, limit=5):
    """Pick the top N videos by 'SEO asset score' = function of search share
    and view volume. If we have per-video traffic-source data, use it; otherwise
    fall back to view ranking."""
    rows = []
    for v in videos or []:
        title = v.get("title") or ""
        views = _safe_float(v.get("views") or 0)
        if not title or views <= 0:
            continue
        share = None
        if traffic_sources_per_video and title in traffic_sources_per_video:
            share = traffic_sources_per_video[title].get("search_share_pct")
        # Score: log(views) weighted by search share if known.
        import math
        score = math.log10(views + 10)
        if share is not None:
            score *= (0.5 + (share / 100.0))
        rows.append({
            "title": title,
            "views": int(views),
            "search_share_pct": share,
            "_score": round(score, 3),
        })
    rows.sort(key=lambda r: -r["_score"])
    for r in rows:
        r.pop("_score", None)
    return rows[:limit]


def _classify_traffic_source(s):
    s = (s or "").lower()
    if "search" in s:
        return "search"
    if "browse" in s or "home" in s:
        return "browse"
    if "suggested" in s or "related" in s or "end screen" in s:
        return "suggested"
    if "external" in s:
        return "external"
    if "channel" in s:
        return "channel"
    if "playlist" in s:
        return "playlist"
    if "shorts feed" in s or "shorts" in s:
        return "shorts_feed"
    return "other"


def _safe_int(value):
    try:
        return int(re.sub(r"[^0-9\-]", "", str(value)) or 0)
    except (ValueError, TypeError):
        return 0


def _safe_float(value):
    try:
        return float(re.sub(r"[^0-9.\-]", "", str(value)) or 0)
    except (ValueError, TypeError):
        return 0.0

"""
YouTube research helpers for Ghostline.

Ports the useful backend pieces from the local youtube-niche-tool without
exposing or printing the user's API key. The module is intentionally small:
it gives Ghostline market signals that Ollama can use for ideas, hooks,
titles, descriptions, and packaging.
"""

import os
import re
import time
import math
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import requests


YT_BASE = "https://www.googleapis.com/youtube/v3"
NICHE_TOOL_ENV = Path(r"C:\Users\kylem\Downloads\Claude\youtube-niche-tool\.env")
GHOSTLINE_ENV = Path(__file__).resolve().parent / ".env"

# Per-key cooldown table. When a key returns quotaExceeded we park it for
# this many seconds and rotate to the next key. YouTube quotas reset at
# midnight Pacific so 6h is conservative, will retry sooner if all keys are
# parked simultaneously.
_KEY_COOLDOWN = {}
_KEY_COOLDOWN_SECONDS = 6 * 3600

RPM_BENCHMARKS = [
    {"category": "Finance/Investing", "low": 12, "high": 45, "keywords": ["finance", "invest", "stock", "crypto", "money", "trading", "wealth"]},
    {"category": "SaaS/Software", "low": 10, "high": 35, "keywords": ["saas", "software", "app", "tech", "developer", "coding", "programming", "ai tools"]},
    {"category": "Legal", "low": 8, "high": 30, "keywords": ["legal", "law", "attorney", "lawyer"]},
    {"category": "Health/Medical", "low": 8, "high": 25, "keywords": ["health", "medical", "doctor", "fitness", "wellness", "nutrition"]},
    {"category": "Real Estate", "low": 7, "high": 20, "keywords": ["real estate", "property", "realtor", "mortgage"]},
    {"category": "B2B/Marketing", "low": 7, "high": 22, "keywords": ["marketing", "b2b", "sales", "agency", "seo"]},
    {"category": "Education", "low": 5, "high": 15, "keywords": ["education", "tutorial", "learn", "course", "study", "science", "history", "facts"]},
    {"category": "Tech Reviews", "low": 4, "high": 14, "keywords": ["review", "unboxing", "gadget", "tech review", "iphone"]},
    {"category": "Automotive", "low": 4, "high": 12, "keywords": ["car", "auto", "vehicle", "motor", "supercar"]},
    {"category": "Travel", "low": 3, "high": 10, "keywords": ["travel", "tourism", "vacation", "destination"]},
    {"category": "News", "low": 3, "high": 10, "keywords": ["news", "politics", "current events", "true crime", "mystery"]},
    {"category": "Cooking/Food", "low": 3, "high": 9, "keywords": ["cooking", "food", "recipe", "chef", "baking"]},
    {"category": "Faceless/Automation", "low": 3, "high": 8, "keywords": ["faceless", "automation", "compilation", "reddit story", "storytime"]},
    {"category": "Gaming", "low": 2, "high": 8, "keywords": ["gaming", "game", "gameplay", "minecraft", "fortnite", "roblox"]},
    {"category": "Sports", "low": 2, "high": 6, "keywords": ["sports", "football", "basketball", "soccer", "nba", "nfl", "ufc", "f1"]},
    {"category": "Entertainment/Vlogging", "low": 2, "high": 6, "keywords": ["vlog", "entertainment", "lifestyle", "comedy", "celebrity", "movie"]},
    {"category": "Animation", "low": 1, "high": 5, "keywords": ["animation", "animated", "cartoon"]},
]

CURATED_SHORTS_NICHES = [
    {"keyword": "reddit story narration", "category": "Faceless/Automation", "competition": "HIGH", "viralPotential": "HIGH", "shortsRpm": 0.05, "sourceMethod": "VOICEOVER_STOCK", "why": "Classic faceless format with proven retention when hooks and captions are strong."},
    {"keyword": "unsolved mysteries narration", "category": "News", "competition": "MEDIUM", "viralPotential": "HIGH", "shortsRpm": 0.06, "sourceMethod": "VOICEOVER_STOCK", "why": "Bingeable curiosity and easy visual atmosphere."},
    {"keyword": "space facts narration", "category": "Education", "competition": "MEDIUM", "viralPotential": "HIGH", "shortsRpm": 0.08, "sourceMethod": "VOICEOVER_STOCK", "why": "Free public-domain style visuals and evergreen curiosity."},
    {"keyword": "business podcast clips", "category": "Finance/Investing", "competition": "MEDIUM", "viralPotential": "HIGH", "shortsRpm": 0.12, "sourceMethod": "CLIP_REMIX", "why": "High-value audience and strong insight hooks."},
    {"keyword": "science podcast clips", "category": "Education", "competition": "LOW", "viralPotential": "MEDIUM", "shortsRpm": 0.10, "sourceMethod": "CLIP_REMIX", "why": "Higher CPM intellectual audience with less crowding."},
    {"keyword": "tech keynote breakdowns", "category": "Tech Reviews", "competition": "LOW", "viralPotential": "HIGH", "shortsRpm": 0.09, "sourceMethod": "CLIP_REMIX", "why": "Event spikes create predictable search demand."},
    {"keyword": "finance tips shorts", "category": "Finance/Investing", "competition": "HIGH", "viralPotential": "MEDIUM", "shortsRpm": 0.15, "sourceMethod": "YOU_FILM", "why": "High monetization if the content provides concrete value."},
    {"keyword": "AI tools showcase shorts", "category": "SaaS/Software", "competition": "HIGH", "viralPotential": "HIGH", "shortsRpm": 0.10, "sourceMethod": "YOU_FILM", "why": "Trending search topic with strong software/creator audience."},
    {"keyword": "wildlife clips narration", "category": "Education", "competition": "MEDIUM", "viralPotential": "HIGH", "shortsRpm": 0.06, "sourceMethod": "CLIP_REMIX", "why": "Universal visual appeal plus educational packaging."},
    {"keyword": "pickleball viral moments", "category": "Sports", "competition": "LOW", "viralPotential": "HIGH", "shortsRpm": 0.05, "sourceMethod": "CLIP_REMIX", "why": "Fast-growing sport with less creator saturation."},
]

_CACHE = {}
TTL_CHANNEL = 30 * 60
TTL_SEARCH = 60 * 60
_quota_used = 0


class YouTubeResearchError(RuntimeError):
    def __init__(self, message, code="API_ERROR"):
        super().__init__(message)
        self.code = code


def _load_env_value(path, key):
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k.strip() == key:
                return v.strip().strip('"').strip("'")
    except OSError:
        return None
    return None


def api_key_available():
    return bool(_available_keys())


def _api_key():
    """Backward-compatible single-key accessor (returns the first available)."""
    keys = _available_keys()
    return keys[0] if keys else None


def _api_keys():
    """All configured YouTube API keys, in priority order, deduped.

    Sources, in order:
      1. env YOUTUBE_API_KEY                  (legacy single key)
      2. env YOUTUBE_API_KEY_2 / _3 / _4 / _5 (numbered fallbacks)
      3. env YOUTUBE_API_KEYS                 (comma-separated bulk)
      4. bedtime-story-gen/.env               (any of the above)
      5. youtube-niche-tool/.env              (any of the above, kept for compat)
    """
    found = []
    seen = set()

    def push(value):
        if not value:
            return
        v = value.strip()
        if not v or v in seen:
            return
        seen.add(v)
        found.append(v)

    sources = [
        ("YOUTUBE_API_KEY", os.environ),
        *[(f"YOUTUBE_API_KEY_{i}", os.environ) for i in range(2, 6)],
        ("YOUTUBE_API_KEYS", os.environ),
    ]
    for name, env in sources:
        push(env.get(name))

    for env_path in (GHOSTLINE_ENV, NICHE_TOOL_ENV):
        for name in ("YOUTUBE_API_KEY", "YOUTUBE_API_KEY_2", "YOUTUBE_API_KEY_3",
                     "YOUTUBE_API_KEY_4", "YOUTUBE_API_KEY_5", "YOUTUBE_API_KEYS"):
            push(_load_env_value(env_path, name))

    # Expand any comma-separated entries.
    expanded = []
    expanded_seen = set()
    for v in found:
        for part in v.split(","):
            p = part.strip()
            if p and p not in expanded_seen:
                expanded_seen.add(p)
                expanded.append(p)
    return expanded


def _available_keys():
    """Configured keys whose cooldown has expired."""
    now = time.time()
    return [k for k in _api_keys() if _KEY_COOLDOWN.get(k, 0) <= now]


def _redact_key(key):
    if not key or len(key) < 12:
        return "***"
    return key[:8] + "..." + key[-4:]


def _key_status():
    """Per-key status (redacted), for the health endpoint."""
    now = time.time()
    rows = []
    for k in _api_keys():
        cooldown_until = _KEY_COOLDOWN.get(k, 0)
        rows.append({
            "key": _redact_key(k),
            "available": cooldown_until <= now,
            "cooldown_until": cooldown_until or None,
            "cooldown_remaining_sec": max(0, int(cooldown_until - now)) if cooldown_until else 0,
        })
    return rows


def _cache_get(key):
    entry = _CACHE.get(key)
    if not entry:
        return None
    if time.time() > entry["expires_at"]:
        _CACHE.pop(key, None)
        return None
    return entry["value"]


def _cache_set(key, value, ttl):
    _CACHE[key] = {"value": value, "expires_at": time.time() + ttl, "cached_at": time.time()}


def _api_request(endpoint, params, quota_cost=1):
    """
    Call the YouTube Data API.

    Rotates through every configured key on quotaExceeded/keyInvalid: parks
    the bad key in a cooldown table and retries the request with the next
    key. Per-key network errors retry up to 3 times with exponential backoff
    before falling through to the next key.
    """
    global _quota_used
    keys = _available_keys()
    if not keys:
        # Either no keys configured or every key is in cooldown.
        configured = _api_keys()
        if configured:
            raise YouTubeResearchError(
                "All configured YouTube API keys hit their daily quota. "
                "Add another key in .env (YOUTUBE_API_KEY_2=...) or wait until "
                "midnight Pacific for Google to reset the daily limit.",
                "QUOTA_EXCEEDED",
            )
        raise YouTubeResearchError("YouTube API key is not configured.", "BAD_KEY")

    url = f"{YT_BASE}/{endpoint}"
    last_err = None

    for key in keys:
        query = dict(params)
        query["key"] = key
        rotate_to_next_key = False
        for attempt in range(3):
            try:
                resp = requests.get(url, params=query, timeout=20)
                data = resp.json() if resp.text else {}
                if not resp.ok:
                    err_body = data.get("error") if isinstance(data, dict) else {}
                    reason = ((err_body.get("errors") or [{}])[0].get("reason") or "") if isinstance(err_body, dict) else ""
                    message = err_body.get("message") if isinstance(err_body, dict) else f"HTTP {resp.status_code}"
                    if reason in ("quotaExceeded", "dailyLimitExceeded"):
                        # Park this key, rotate to the next.
                        _KEY_COOLDOWN[key] = time.time() + _KEY_COOLDOWN_SECONDS
                        last_err = YouTubeResearchError("YouTube API quota exceeded.", "QUOTA_EXCEEDED")
                        rotate_to_next_key = True
                        break
                    if reason in ("keyInvalid", "forbidden") or resp.status_code in (400, 401):
                        # Bad key. Park it long and rotate.
                        _KEY_COOLDOWN[key] = time.time() + _KEY_COOLDOWN_SECONDS
                        last_err = YouTubeResearchError("YouTube API key is missing or invalid.", "BAD_KEY")
                        rotate_to_next_key = True
                        break
                    if resp.status_code == 404:
                        # Real "not found" — don't rotate, surface immediately.
                        raise YouTubeResearchError("YouTube resource not found.", "NOT_FOUND")
                    if resp.status_code >= 500 or resp.status_code == 429:
                        last_err = YouTubeResearchError(message, "NETWORK_ERROR")
                        time.sleep(0.25 * (2 ** attempt))
                        continue
                    # Unknown error — try next key, but don't loop forever.
                    last_err = YouTubeResearchError(message, "API_ERROR")
                    rotate_to_next_key = True
                    break
                _quota_used += quota_cost
                return data
            except requests.RequestException as exc:
                last_err = exc
                time.sleep(0.25 * (2 ** attempt))
        # If this key was poisoned (quota / bad key / API error), continue to next.
        # If it just had network noise, also try next key once before giving up.
        if not rotate_to_next_key and isinstance(last_err, requests.RequestException):
            continue
        if not rotate_to_next_key:
            break

    if isinstance(last_err, YouTubeResearchError):
        raise last_err
    raise YouTubeResearchError(str(last_err or "Network error reaching YouTube."), "NETWORK_ERROR")


def health():
    available = _available_keys()
    configured = _api_keys()
    return {
        "available": bool(available),
        "configured_keys": len(configured),
        "available_keys": len(available),
        "keys": _key_status(),
        "quota_used_session": _quota_used,
        "cache_size": len(_CACHE),
    }


def reset_key_cooldowns():
    """Clear the per-key cooldown table. Useful when keys recover early."""
    _KEY_COOLDOWN.clear()


def classify_category(name="", description=""):
    text = f"{name} {description}".lower()
    best = next((b for b in RPM_BENCHMARKS if b["category"] == "Entertainment/Vlogging"), RPM_BENCHMARKS[0])
    best_score = 0
    for benchmark in RPM_BENCHMARKS:
        score = sum(1 for kw in benchmark["keywords"] if kw in text)
        if score > best_score:
            best = benchmark
            best_score = score
    return best


def parse_duration(iso):
    if not iso:
        return 0
    match = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso)
    if not match:
        return 0
    h, m, s = [int(part or 0) for part in match.groups()]
    return h * 3600 + m * 60 + s


def uploads_per_month(published_dates):
    if not published_dates or len(published_dates) < 2:
        return 0.0
    times = sorted(_parse_time(d) for d in published_dates if _parse_time(d))
    if len(times) < 2:
        return 0.0
    months = max(1.0, (times[-1] - times[0]) / (60 * 60 * 24 * 30))
    return len(times) / months


def _parse_time(value):
    try:
        from datetime import datetime
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def estimate_monthly_revenue(avg_views, uploads_per_month_value, rpm_range):
    monthly_views = avg_views * uploads_per_month_value
    monetized_low = monthly_views * 0.4
    monetized_high = monthly_views * 0.6
    return {
        "low": round((monetized_low / 1000) * rpm_range["low"]),
        "mid": round((((monetized_low + monetized_high) / 2) / 1000) * ((rpm_range["low"] + rpm_range["high"]) / 2)),
        "high": round((monetized_high / 1000) * rpm_range["high"]),
    }


def fetch_uploads_playlist(playlist_id, max_items=50):
    items = []
    page_token = None
    while len(items) < max_items:
        data = _api_request("playlistItems", {
            "part": "snippet,contentDetails",
            "playlistId": playlist_id,
            "maxResults": min(50, max_items - len(items)),
            "pageToken": page_token,
        }, quota_cost=1)
        items.extend(data.get("items") or [])
        page_token = data.get("nextPageToken")
        if not page_token:
            break
    return items[:max_items]


def fetch_videos(video_ids):
    all_items = []
    for idx in range(0, len(video_ids), 50):
        chunk = video_ids[idx:idx + 50]
        data = _api_request("videos", {
            "part": "snippet,statistics,contentDetails",
            "id": ",".join(chunk),
        }, quota_cost=1)
        all_items.extend(data.get("items") or [])
    return all_items


def _median(values):
    values = sorted(v for v in values if v is not None)
    if not values:
        return 0
    mid = len(values) // 2
    if len(values) % 2:
        return values[mid]
    return (values[mid - 1] + values[mid]) / 2


def _log_score(value, low=2.0, high=7.0):
    if value <= 0:
        return 0
    raw = math.log10(max(1, value))
    return max(0, min(100, ((raw - low) / max(0.1, high - low)) * 100))


def _keyword_tokens(text):
    return [t for t in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(t) > 1]


def _dedupe_phrases(phrases, limit=30):
    seen = set()
    out = []
    for phrase in phrases:
        clean = re.sub(r"\s+", " ", str(phrase or "").strip().lower())
        clean = re.sub(r"[^\w\s#.-]", "", clean).strip()
        if len(clean) < 3 or clean in seen:
            continue
        seen.add(clean)
        out.append(clean[:90])
        if len(out) >= limit:
            break
    return out


def autocomplete_phrases(seed, limit=12):
    seed = (seed or "").strip()
    if not seed:
        return []
    cache_key = f"suggest:{seed.lower()}"
    cached = _cache_get(cache_key)
    if cached:
        return cached
    phrases = []
    stems = [
        seed,
        f"{seed} tutorial",
        f"{seed} review",
        f"{seed} alternative",
        f"best {seed}",
        f"how to use {seed}",
        f"{seed} for beginners",
    ]
    for stem in stems:
        try:
            resp = requests.get(
                "https://suggestqueries.google.com/complete/search",
                params={"client": "firefox", "ds": "yt", "q": stem},
                timeout=8,
            )
            if resp.ok:
                data = resp.json()
                phrases.extend(data[1] if isinstance(data, list) and len(data) > 1 else [])
        except Exception:
            continue
    result = _dedupe_phrases(phrases, limit=limit)
    _cache_set(cache_key, result, TTL_SEARCH)
    return result


def score_keyword_phrase(phrase, max_results=20):
    phrase = (phrase or "").strip()
    if not phrase:
        raise YouTubeResearchError("Keyword phrase required.", "BAD_INPUT")
    cache_key = f"kwscore:{phrase.lower()}:{max_results}"
    cached = _cache_get(cache_key)
    if cached:
        return cached

    search_data = _api_request("search", {
        "part": "snippet",
        "q": phrase,
        "type": "video",
        "maxResults": max(5, min(50, max_results)),
        "order": "relevance",
        "regionCode": "US",
        "relevanceLanguage": "en",
    }, quota_cost=100)
    search_items = search_data.get("items") or []
    video_ids = [((item.get("id") or {}).get("videoId")) for item in search_items]
    video_ids = [vid for vid in video_ids if vid]
    videos = fetch_videos(video_ids) if video_ids else []
    channel_ids = []
    for video in videos:
        cid = ((video.get("snippet") or {}).get("channelId") or "").strip()
        if cid and cid not in channel_ids:
            channel_ids.append(cid)
    channel_subs = {}
    if channel_ids:
        channel_data = _api_request("channels", {
            "part": "statistics",
            "id": ",".join(channel_ids[:50]),
        }, quota_cost=1)
        for channel in channel_data.get("items") or []:
            channel_subs[channel.get("id")] = int((channel.get("statistics") or {}).get("subscriberCount") or 0)

    now = time.time()
    rows = []
    phrase_tokens = set(_keyword_tokens(phrase))
    for video in videos:
        snippet = video.get("snippet") or {}
        stats = video.get("statistics") or {}
        duration = parse_duration((video.get("contentDetails") or {}).get("duration"))
        published = _parse_time(snippet.get("publishedAt"))
        age_days = max(1, (now - published) / 86400) if published else 365
        views = int(stats.get("viewCount") or 0)
        likes = int(stats.get("likeCount") or 0)
        comments = int(stats.get("commentCount") or 0)
        cid = snippet.get("channelId")
        subs = channel_subs.get(cid, 0)
        title = snippet.get("title") or ""
        title_tokens = set(_keyword_tokens(title))
        overlap = len(phrase_tokens & title_tokens) / max(1, len(phrase_tokens))
        rows.append({
            "id": video.get("id"),
            "title": title,
            "channel": snippet.get("channelTitle"),
            "views": views,
            "likes": likes,
            "comments": comments,
            "subs": subs,
            "durationSec": duration,
            "publishedAt": snippet.get("publishedAt"),
            "ageDays": round(age_days, 1),
            "viewsPerDay": round(views / age_days, 1),
            "titleMatch": round(overlap, 2),
            "isShort": duration > 0 and duration <= 75,
            "smallChannelOutlier": bool(subs and subs < 100000 and views >= max(25000, subs * 2)),
        })

    views = [r["views"] for r in rows]
    vpd = [r["viewsPerDay"] for r in rows]
    shorts_ratio = sum(1 for r in rows if r["isShort"]) / max(1, len(rows))
    exactness = sum(r["titleMatch"] for r in rows[:10]) / max(1, min(10, len(rows)))
    outlier_ratio = sum(1 for r in rows if r["smallChannelOutlier"]) / max(1, len(rows))
    huge_channel_ratio = sum(1 for r in rows[:10] if r["subs"] >= 500000) / max(1, min(10, len(rows)))
    median_views = _median(views)
    median_vpd = _median(vpd)
    demand_score = _log_score(median_views, low=3.5, high=6.4)
    velocity_score = _log_score(median_vpd, low=1.0, high=4.5)
    shorts_score = shorts_ratio * 100
    relevance_score = exactness * 100
    outlier_score = outlier_ratio * 100
    competition_penalty = huge_channel_ratio * 55
    opportunity_score = round(max(0, min(100,
        demand_score * 0.26 +
        velocity_score * 0.25 +
        relevance_score * 0.18 +
        outlier_score * 0.20 +
        shorts_score * 0.11 -
        competition_penalty * 0.35
    )))
    result = {
        "phrase": phrase,
        "opportunityScore": opportunity_score,
        "demandScore": round(demand_score),
        "velocityScore": round(velocity_score),
        "shortsFitScore": round(shorts_score),
        "relevanceScore": round(relevance_score),
        "outlierScore": round(outlier_score),
        "competitionPenalty": round(competition_penalty),
        "medianViews": round(median_views),
        "medianViewsPerDay": round(median_vpd, 1),
        "shortsRatio": round(shorts_ratio, 2),
        "sampleSize": len(rows),
        "topVideos": sorted(rows, key=lambda r: r["viewsPerDay"], reverse=True)[:6],
    }
    _cache_set(cache_key, result, TTL_SEARCH)
    return result


def score_keyword_opportunities(phrases, max_phrases=12):
    scored = []
    errors = []
    for phrase in _dedupe_phrases(phrases, limit=max_phrases):
        try:
            scored.append(score_keyword_phrase(phrase))
        except YouTubeResearchError as exc:
            errors.append({"phrase": phrase, "error": str(exc), "code": exc.code})
    scored.sort(key=lambda row: row.get("opportunityScore", 0), reverse=True)
    return {"keywords": scored, "errors": errors}


def build_niche_search(keyword):
    keyword = (keyword or "").strip()
    if not keyword:
        raise YouTubeResearchError("Keyword required.", "BAD_INPUT")
    cache_key = f"niche:{keyword.lower()}"
    cached = _cache_get(cache_key)
    if cached:
        return cached

    search_data = _api_request("search", {
        "part": "snippet",
        "q": keyword,
        "type": "channel",
        "maxResults": 25,
        "order": "relevance",
    }, quota_cost=100)
    channel_ids = [
        (item.get("snippet") or {}).get("channelId") or (item.get("id") or {}).get("channelId")
        for item in search_data.get("items") or []
    ]
    channel_ids = [cid for cid in channel_ids if cid]
    if not channel_ids:
        cat = classify_category(keyword)
        result = {
            "keyword": keyword,
            "channels": [],
            "saturation": 0,
            "opportunity": "HIGH",
            "opportunityScore": 100,
            "category": cat["category"],
            "rpmRange": {"low": cat["low"], "high": cat["high"]},
        }
        _cache_set(cache_key, result, TTL_SEARCH)
        return result

    channel_data = _api_request("channels", {
        "part": "snippet,statistics,contentDetails",
        "id": ",".join(channel_ids[:50]),
    }, quota_cost=3)
    top = sorted(
        channel_data.get("items") or [],
        key=lambda c: int((c.get("statistics") or {}).get("subscriberCount") or 0),
        reverse=True,
    )[:12]

    channels = []
    for channel in top:
        uploads_id = ((channel.get("contentDetails") or {}).get("relatedPlaylists") or {}).get("uploads")
        upm = 0.0
        recent_avg = 0
        sample_titles = []
        if uploads_id:
            try:
                items = fetch_uploads_playlist(uploads_id, max_items=8)
                video_ids = [(it.get("contentDetails") or {}).get("videoId") for it in items]
                video_ids = [vid for vid in video_ids if vid]
                videos = fetch_videos(video_ids) if video_ids else []
                dates = [(v.get("snippet") or {}).get("publishedAt") for v in videos]
                upm = uploads_per_month([d for d in dates if d])
                views = [int((v.get("statistics") or {}).get("viewCount") or 0) for v in videos]
                recent_avg = round(sum(views) / len(views)) if views else 0
                sample_titles = [(v.get("snippet") or {}).get("title") for v in videos[:5]]
            except YouTubeResearchError:
                pass
        stats = channel.get("statistics") or {}
        snippet = channel.get("snippet") or {}
        subs = int(stats.get("subscriberCount") or 0)
        total_views = int(stats.get("viewCount") or 0)
        video_count = int(stats.get("videoCount") or 0)
        avg_views = round(total_views / video_count) if video_count else 0
        if not recent_avg:
            recent_avg = avg_views
        cat = classify_category(snippet.get("title", ""), snippet.get("description", ""))
        channels.append({
            "id": channel.get("id"),
            "name": snippet.get("title"),
            "description": (snippet.get("description") or "")[:200],
            "subs": subs,
            "totalViews": total_views,
            "videoCount": video_count,
            "avgViews": avg_views,
            "recentAvgViews": recent_avg,
            "uploadsPerMonth": round(upm, 2),
            "category": cat["category"],
            "sampleTitles": [t for t in sample_titles if t],
            "revenue": estimate_monthly_revenue(recent_avg, upm, cat),
        })

    big = len([c for c in channels if c["subs"] >= 100000])
    avg_upm = sum(c["uploadsPerMonth"] for c in channels) / len(channels) if channels else 0
    ratios = [c["recentAvgViews"] / c["subs"] for c in channels if c["subs"] > 0]
    avg_vts = sum(ratios) / len(ratios) if ratios else 0
    saturation = min(100, max(0, round(min(50, big * 4) + min(30, avg_upm * 3) + max(0, 20 - min(20, avg_vts * 100)))))
    cat = classify_category(keyword)
    rpm_mid = (cat["low"] + cat["high"]) / 2
    opportunity_score = min(100, max(0, round(100 - saturation + min(20, rpm_mid))))
    opportunity = "HIGH" if opportunity_score >= 70 else "LOW" if opportunity_score < 40 else "MEDIUM"
    result = {
        "keyword": keyword,
        "category": cat["category"],
        "rpmRange": {"low": cat["low"], "high": cat["high"]},
        "channels": channels,
        "saturation": saturation,
        "opportunity": opportunity,
        "opportunityScore": opportunity_score,
        "cachedAt": time.time(),
    }
    _cache_set(cache_key, result, TTL_SEARCH)
    return result


def normalize_channel_input(value):
    value = (value or "").strip()
    if not value:
        raise YouTubeResearchError("Channel input required.", "BAD_INPUT")
    if re.match(r"^UC[A-Za-z0-9_-]{20,}$", value):
        return value
    parsed = urlparse(value)
    if parsed.netloc and "youtu" in parsed.netloc:
        parts = [p for p in parsed.path.split("/") if p]
        if parts and parts[0] == "channel" and len(parts) > 1:
            return parts[1]
        if parts and parts[0].startswith("@"):
            return resolve_by_handle(parts[0])
        if parsed.path.startswith("/watch"):
            video_id = (parse_qs(parsed.query).get("v") or [""])[0]
            if video_id:
                return resolve_by_video_id(video_id)
        if "youtu.be" in parsed.netloc and parts:
            return resolve_by_video_id(parts[0])
    return resolve_by_handle(value if value.startswith("@") else "@" + value.replace("@", ""))


def resolve_by_handle(handle):
    data = _api_request("channels", {"part": "id", "forHandle": handle, "maxResults": 1}, quota_cost=1)
    item = (data.get("items") or [None])[0]
    if item and item.get("id"):
        return item["id"]
    search = _api_request("search", {"part": "snippet", "q": handle, "type": "channel", "maxResults": 1}, quota_cost=100)
    item = (search.get("items") or [None])[0]
    channel_id = ((item or {}).get("snippet") or {}).get("channelId") or ((item or {}).get("id") or {}).get("channelId")
    if not channel_id:
        raise YouTubeResearchError(f"Channel not found: {handle}", "NOT_FOUND")
    return channel_id


def resolve_by_video_id(video_id):
    data = _api_request("videos", {"part": "snippet", "id": video_id}, quota_cost=1)
    item = (data.get("items") or [None])[0]
    channel_id = ((item or {}).get("snippet") or {}).get("channelId")
    if not channel_id:
        raise YouTubeResearchError("Could not resolve channel from video.", "NOT_FOUND")
    return channel_id


def build_outliers(channel_input):
    channel_id = normalize_channel_input(channel_input)
    cache_key = f"outliers:{channel_id}"
    cached = _cache_get(cache_key)
    if cached:
        return cached
    channel = _api_request("channels", {
        "part": "snippet,statistics,contentDetails",
        "id": channel_id,
    }, quota_cost=3)
    channel_item = (channel.get("items") or [None])[0]
    if not channel_item:
        raise YouTubeResearchError("Channel not found.", "NOT_FOUND")
    uploads_id = ((channel_item.get("contentDetails") or {}).get("relatedPlaylists") or {}).get("uploads")
    if not uploads_id:
        raise YouTubeResearchError("No uploads playlist found.", "NOT_FOUND")
    playlist_items = fetch_uploads_playlist(uploads_id, max_items=50)
    video_ids = [(item.get("contentDetails") or {}).get("videoId") for item in playlist_items]
    videos = fetch_videos([vid for vid in video_ids if vid])
    rows = []
    for video in videos:
        snippet = video.get("snippet") or {}
        stats = video.get("statistics") or {}
        rows.append({
            "id": video.get("id"),
            "title": snippet.get("title"),
            "views": int(stats.get("viewCount") or 0),
            "likes": int(stats.get("likeCount") or 0),
            "comments": int(stats.get("commentCount") or 0),
            "publishedAt": snippet.get("publishedAt"),
            "durationSec": parse_duration((video.get("contentDetails") or {}).get("duration")),
        })
    baseline = round(sum(row["views"] for row in rows) / len(rows)) if rows else 0
    for row in rows:
        row["multiplier"] = round(row["views"] / baseline, 2) if baseline else 0
        row["isOutlier"] = row["multiplier"] >= 3
    rows.sort(key=lambda row: row["multiplier"], reverse=True)
    snippet = channel_item.get("snippet") or {}
    result = {
        "channel": {"id": channel_item.get("id"), "name": snippet.get("title")},
        "baseline": baseline,
        "sampleSize": len(rows),
        "videos": rows,
        "cachedAt": time.time(),
    }
    _cache_set(cache_key, result, TTL_CHANNEL)
    return result


def curated_niches(count=9):
    def enrich(item):
        rpm = classify_category(item["category"])
        viral_weight = 5 if item["viralPotential"] == "HIGH" else 2 if item["viralPotential"] == "MEDIUM" else 0.5
        return {
            **item,
            "rpmLow": rpm["low"],
            "rpmHigh": rpm["high"],
            "score": round(item["shortsRpm"] * viral_weight, 3),
        }
    picks = sorted((enrich(item) for item in CURATED_SHORTS_NICHES), key=lambda item: item["score"], reverse=True)
    return picks[:max(1, min(count, len(picks)))]


def suggest_research_keyword(recipe="", niche="", topic="", video_format=""):
    text = " ".join(part for part in [recipe, niche, topic, video_format] if part).lower()
    if "reddit" in text or "viral-story" in text or "story" in text:
        return "reddit story narration"
    if "space" in text or "alien" in text:
        return "space facts narration"
    if "mystery" in text or "horror" in text:
        return "unsolved mysteries narration"
    if "true-crime" in text or "true crime" in text:
        return "true crime documentary"
    if "finance" in text or "money" in text:
        return "finance tips shorts"
    if "tech" in text or "ai" in text:
        return "AI tools showcase shorts"
    if "history" in text:
        return "history facts shorts"
    if "survival" in text:
        return "survival tips shorts"
    if "asmr" in text or "sleep" in text:
        return "sleep narration ASMR"
    if "science" in text:
        return "science explained shorts"
    if "conspiracy" in text or "what if" in text:
        return "conspiracy theory documentary"
    if "top" in text and ("list" in text or "10" in text or "ranked" in text):
        return "top 10 facts shorts"
    if "travel" in text or "geography" in text:
        return "travel documentary narration"
    if "philosophy" in text:
        return "philosophy explained shorts"
    if "urban legend" in text or "folklore" in text:
        return "urban legend narration"
    if "news" in text or "recap" in text:
        return "news recap shorts"
    if "product review" in text or "review" in text:
        return "product review shorts"
    if "motivat" in text:
        return "motivational shorts narration"
    return (niche or topic or "faceless YouTube shorts").strip()[:80]


def research_context(keyword, max_channels=5, max_titles=12):
    if not api_key_available():
        return ""
    try:
        data = build_niche_search(keyword)
    except YouTubeResearchError:
        return ""
    lines = [
        "YOUTUBE MARKET SIGNALS:",
        f"- Keyword validated: {data.get('keyword')}",
        f"- Category: {data.get('category')} | RPM range: ${data.get('rpmRange', {}).get('low')}-${data.get('rpmRange', {}).get('high')} long-form benchmark",
        f"- Opportunity: {data.get('opportunity')} ({data.get('opportunityScore')}/100) | Saturation: {data.get('saturation')}/100",
    ]
    titles = []
    for channel in (data.get("channels") or [])[:max_channels]:
        lines.append(
            f"- Competitor: {channel.get('name')} | subs {channel.get('subs')} | recent avg views {channel.get('recentAvgViews')} | uploads/mo {channel.get('uploadsPerMonth')}"
        )
        titles.extend(channel.get("sampleTitles") or [])
    clean_titles = []
    seen = set()
    for title in titles:
        t = re.sub(r"\s+", " ", (title or "")).strip()
        if t and t.lower() not in seen:
            clean_titles.append(t[:120])
            seen.add(t.lower())
        if len(clean_titles) >= max_titles:
            break
    if clean_titles:
        lines.append("- Recent competitor titles to learn from, not copy:")
        lines.extend(f"  - {title}" for title in clean_titles)
    lines.append("Use these signals to create differentiated ideas. Do not copy competitor titles verbatim.")
    return "\n".join(lines)

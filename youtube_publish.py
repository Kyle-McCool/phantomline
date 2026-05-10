"""
YouTube publishing helpers ported from Cadence into Ghostline.

Keeps the useful backend pieces small and Flask-friendly:
OAuth, token refresh, playlist lookup, and resumable video upload.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests

import youtube_research


SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/youtube",
]

YT_API = "https://www.googleapis.com/youtube/v3"


def _is_quota_exceeded(response: requests.Response) -> bool:
    if response.status_code != 403:
        return False
    try:
        body = response.json()
    except ValueError:
        return "quotaexceeded" in response.text.lower()
    err = body.get("error") if isinstance(body, dict) else {}
    if not isinstance(err, dict):
        return False
    reasons = [(e or {}).get("reason", "") for e in (err.get("errors") or [])]
    if any(r in ("quotaExceeded", "dailyLimitExceeded") for r in reasons):
        return True
    return "quotaexceeded" in (err.get("message") or "").lower()


def _api_get_with_fallback(
    token: str,
    endpoint: str,
    oauth_params: dict[str, Any],
    fallback_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """GET a YouTube Data API endpoint via OAuth, falling back to the rotated
    API-key pool in youtube_research on quotaExceeded.

    The OAuth project and the API-key project have separate daily quotas.
    Read-only endpoints (channels.list, playlistItems.list, videos.list) accept
    API keys when called with `id=` instead of `mine=true`, so callers should
    pass `fallback_params` with the explicit channel/video id substituted in.
    If `fallback_params` is None, `oauth_params` is reused as-is."""
    res = requests.get(
        f"{YT_API}/{endpoint}",
        params=oauth_params,
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    if res.ok:
        try:
            return res.json()
        except ValueError as exc:
            raise RuntimeError(f"YouTube {endpoint} returned non-JSON: {res.text[:200]}") from exc
    if not _is_quota_exceeded(res):
        raise RuntimeError(f"YouTube {endpoint} failed: {res.text}")

    params = dict(fallback_params if fallback_params is not None else oauth_params)
    if params.pop("mine", None) and "id" not in params:
        raise RuntimeError(
            f"YouTube OAuth quota exceeded for {endpoint}, and no channel id was "
            f"available for the API-key fallback. Reconnect the channel in Publish."
        )
    try:
        return youtube_research._api_request(endpoint, params)
    except Exception as exc:
        raise RuntimeError(
            f"YouTube OAuth quota exceeded for {endpoint}, and API-key fallback "
            f"failed: {exc}"
        ) from exc


def _env_files(base_dir: Path) -> list[Path]:
    return [base_dir.parent / "cadence" / ".env", base_dir / ".env"]


def _read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    except OSError:
        pass
    return values


def config(base_dir: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for path in _env_files(base_dir):
        values.update(_read_env(path))
    return {
        "client_id": os.environ.get("YOUTUBE_CLIENT_ID") or values.get("YOUTUBE_CLIENT_ID", ""),
        "client_secret": os.environ.get("YOUTUBE_CLIENT_SECRET") or values.get("YOUTUBE_CLIENT_SECRET", ""),
        "redirect_uri": (
            os.environ.get("YOUTUBE_REDIRECT_URI")
            or values.get("YOUTUBE_REDIRECT_URI", "")
            or "http://127.0.0.1:5000/api/youtube/callback"
        ),
    }


def configured(base_dir: Path) -> bool:
    cfg = config(base_dir)
    return bool(cfg["client_id"] and cfg["client_secret"])


def auth_url(base_dir: Path) -> str:
    cfg = config(base_dir)
    if not cfg["client_id"] or not cfg["client_secret"]:
        raise RuntimeError("Missing YOUTUBE_CLIENT_ID or YOUTUBE_CLIENT_SECRET.")
    params = {
        "client_id": cfg["client_id"],
        "redirect_uri": cfg["redirect_uri"],
        "response_type": "code",
        "access_type": "offline",
        "prompt": "consent",
        "scope": " ".join(SCOPES),
    }
    return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params)


def exchange_code(base_dir: Path, code: str) -> dict[str, Any]:
    cfg = config(base_dir)
    res = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "code": code,
            "client_id": cfg["client_id"],
            "client_secret": cfg["client_secret"],
            "redirect_uri": cfg["redirect_uri"],
            "grant_type": "authorization_code",
        },
        timeout=30,
    )
    if not res.ok:
        raise RuntimeError(f"YouTube token exchange failed: {res.text}")
    tokens = res.json()
    channel = channel_info(tokens["access_token"])
    return {
        "external_id": channel["id"],
        "display_name": channel["title"],
        "access_token": tokens["access_token"],
        "refresh_token": tokens.get("refresh_token"),
        "expires_at": int(time.time()) + int(tokens.get("expires_in", 3600)),
        "scope": tokens.get("scope", ""),
        "channel": channel,
    }


def channel_info(access_token: str) -> dict[str, str]:
    res = requests.get(
        f"{YT_API}/channels",
        params={"part": "snippet", "mine": "true"},
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=30,
    )
    if not res.ok:
        raise RuntimeError(f"YouTube channel lookup failed: {res.text}")
    items = (res.json().get("items") or [])
    if not items:
        raise RuntimeError("No YouTube channel found for this Google account.")
    item = items[0]
    return {"id": item["id"], "title": item.get("snippet", {}).get("title", "YouTube channel")}


def refresh_if_needed(base_dir: Path, connection: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    if int(connection.get("expires_at") or 0) > int(time.time()) + 60:
        return str(connection["access_token"]), connection
    refresh_token = connection.get("refresh_token")
    if not refresh_token:
        return str(connection["access_token"]), connection
    cfg = config(base_dir)
    res = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "refresh_token": refresh_token,
            "client_id": cfg["client_id"],
            "client_secret": cfg["client_secret"],
            "grant_type": "refresh_token",
        },
        timeout=30,
    )
    if not res.ok:
        raise RuntimeError(f"YouTube refresh failed: {res.text}")
    data = res.json()
    updated = dict(connection)
    updated["access_token"] = data["access_token"]
    updated["expires_at"] = int(time.time()) + int(data.get("expires_in", 3600))
    return str(updated["access_token"]), updated


def fetch_playlists(base_dir: Path, connection: dict[str, Any]) -> tuple[list[dict[str, str]], dict[str, Any]]:
    token, updated = refresh_if_needed(base_dir, connection)
    res = requests.get(
        f"{YT_API}/playlists",
        params={"part": "snippet", "mine": "true", "maxResults": 50},
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    if not res.ok:
        raise RuntimeError(f"YouTube playlists failed: {res.text}")
    playlists = [
        {"id": item["id"], "title": item.get("snippet", {}).get("title", "Playlist")}
        for item in (res.json().get("items") or [])
    ]
    return playlists, updated


def _uploads_playlist_id(token: str, channel_id: str | None = None) -> str:
    """Return the connected channel's uploads playlist ID. Cheap (1 quota unit).

    Falls back to the API-key pool when OAuth quota is exhausted, provided the
    channel id is known (it normally is — stored as connection.external_id)."""
    oauth_params = {"part": "contentDetails", "mine": "true"}
    fallback_params = (
        {"part": "contentDetails", "id": channel_id} if channel_id else None
    )
    data = _api_get_with_fallback(token, "channels", oauth_params, fallback_params)
    items = data.get("items") or []
    if not items:
        raise RuntimeError("No YouTube channel found for this Google account.")
    return ((items[0].get("contentDetails") or {}).get("relatedPlaylists") or {}).get("uploads") or ""


def list_channel_videos(
    base_dir: Path,
    connection: dict[str, Any],
    *,
    max_videos: int = 200,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    Fetch the connected channel's uploaded videos with snippet + statistics.

    Cheap on quota: ~1 unit per 50 playlist items + 1 unit per 50 video detail
    fetches. A 200-video pull costs ~8 quota units total.
    """
    token, updated = refresh_if_needed(base_dir, connection)
    channel_id = (connection.get("external_id") or "").strip() or None
    uploads_id = _uploads_playlist_id(token, channel_id=channel_id)
    if not uploads_id:
        return [], updated

    video_ids: list[str] = []
    page_token = ""
    while len(video_ids) < max_videos:
        params = {"part": "contentDetails", "playlistId": uploads_id, "maxResults": 50}
        if page_token:
            params["pageToken"] = page_token
        data = _api_get_with_fallback(token, "playlistItems", params)
        for item in data.get("items") or []:
            vid = ((item.get("contentDetails") or {}).get("videoId") or "").strip()
            if vid:
                video_ids.append(vid)
        page_token = data.get("nextPageToken") or ""
        if not page_token:
            break
    video_ids = video_ids[:max_videos]
    if not video_ids:
        return [], updated

    videos: list[dict[str, Any]] = []
    for batch_start in range(0, len(video_ids), 50):
        batch = video_ids[batch_start:batch_start + 50]
        params = {"part": "snippet,statistics,contentDetails,status", "id": ",".join(batch)}
        data = _api_get_with_fallback(token, "videos", params)
        for v in data.get("items") or []:
            snip = v.get("snippet") or {}
            stats = v.get("statistics") or {}
            content = v.get("contentDetails") or {}
            status = v.get("status") or {}
            thumbs = (snip.get("thumbnails") or {})
            thumb_url = (thumbs.get("medium") or thumbs.get("default") or thumbs.get("high") or {}).get("url", "")
            videos.append({
                "id": v.get("id"),
                "title": snip.get("title") or "",
                "description": snip.get("description") or "",
                "tags": snip.get("tags") or [],
                "publishedAt": snip.get("publishedAt"),
                "channelTitle": snip.get("channelTitle"),
                "thumbnail": thumb_url,
                "duration": content.get("duration"),
                "categoryId": snip.get("categoryId"),
                "defaultLanguage": snip.get("defaultLanguage") or snip.get("defaultAudioLanguage"),
                "privacyStatus": status.get("privacyStatus"),
                "views": int(stats.get("viewCount") or 0),
                "likes": int(stats.get("likeCount") or 0),
                "comments": int(stats.get("commentCount") or 0),
            })
    videos.sort(key=lambda v: -(v.get("views") or 0))
    return videos, updated


def fetch_video_detail(
    base_dir: Path,
    connection: dict[str, Any],
    video_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Fetch full snippet + statistics + topicDetails for one video."""
    token, updated = refresh_if_needed(base_dir, connection)
    params = {"part": "snippet,statistics,contentDetails,topicDetails,status", "id": video_id}
    data = _api_get_with_fallback(token, "videos", params)
    items = data.get("items") or []
    if not items:
        raise RuntimeError("Video not found.")
    v = items[0]
    snip = v.get("snippet") or {}
    stats = v.get("statistics") or {}
    content = v.get("contentDetails") or {}
    topic = v.get("topicDetails") or {}
    status = v.get("status") or {}
    thumbs = snip.get("thumbnails") or {}
    detail = {
        "id": v.get("id"),
        "title": snip.get("title") or "",
        "description": snip.get("description") or "",
        "tags": snip.get("tags") or [],
        "publishedAt": snip.get("publishedAt"),
        "channelTitle": snip.get("channelTitle"),
        "thumbnail": (thumbs.get("medium") or thumbs.get("default") or thumbs.get("high") or {}).get("url", ""),
        "duration": content.get("duration"),
        "categoryId": snip.get("categoryId"),
        "defaultLanguage": snip.get("defaultLanguage") or snip.get("defaultAudioLanguage"),
        "privacyStatus": status.get("privacyStatus"),
        "topicCategories": topic.get("topicCategories") or [],
        "views": int(stats.get("viewCount") or 0),
        "likes": int(stats.get("likeCount") or 0),
        "comments": int(stats.get("commentCount") or 0),
    }
    return detail, updated


def normalize_tags(raw: Any) -> list[str]:
    if isinstance(raw, str):
        tags = raw.replace(",", " ").split()
    elif isinstance(raw, list):
        tags = raw
    else:
        tags = []
    cleaned = []
    seen = set()
    for tag in tags:
        value = str(tag).strip().lstrip("#")
        if not value:
            continue
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(value[:50])
    return cleaned[:20]


def upload_video(
    base_dir: Path,
    connection: dict[str, Any],
    video_path: Path,
    metadata: dict[str, Any],
) -> tuple[dict[str, str], dict[str, Any]]:
    token, updated = refresh_if_needed(base_dir, connection)
    title = (metadata.get("title") or video_path.stem or "Phantomline video").strip()[:50]
    description = (metadata.get("description") or "").strip()
    privacy = metadata.get("privacy") or "private"
    if privacy not in {"private", "unlisted", "public"}:
        privacy = "private"
    tags = normalize_tags(metadata.get("tags") or metadata.get("hashtags"))
    size = video_path.stat().st_size
    body: dict[str, Any] = {
        "snippet": {
            "title": title,
            "description": description,
            "categoryId": str(metadata.get("categoryId") or "24"),
            "defaultLanguage": metadata.get("defaultLanguage") or "en",
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": bool(metadata.get("madeForKids", False)),
            "embeddable": bool(metadata.get("embeddable", True)),
            "containsSyntheticMedia": bool(metadata.get("syntheticMedia", True)),
        },
    }
    if tags:
        body["snippet"]["tags"] = tags
    init = requests.post(
        "https://www.googleapis.com/upload/youtube/v3/videos",
        params={"uploadType": "resumable", "part": "snippet,status"},
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=UTF-8",
            "X-Upload-Content-Type": "video/mp4",
            "X-Upload-Content-Length": str(size),
        },
        json=body,
        timeout=60,
    )
    if not init.ok:
        raise RuntimeError(f"YouTube upload init failed: {init.text}")
    upload_url = init.headers.get("location")
    if not upload_url:
        raise RuntimeError("YouTube did not return an upload URL.")
    with video_path.open("rb") as fh:
        put = requests.put(
            upload_url,
            headers={"Content-Type": "video/mp4", "Content-Length": str(size)},
            data=fh,
            timeout=1800,
        )
    if not put.ok:
        raise RuntimeError(f"YouTube upload failed: {put.text}")
    video_id = put.json()["id"]
    for playlist_id in metadata.get("playlistIds") or []:
        try:
            requests.post(
                f"{YT_API}/playlistItems",
                params={"part": "snippet"},
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json={
                    "snippet": {
                        "playlistId": playlist_id,
                        "resourceId": {"kind": "youtube#video", "videoId": video_id},
                    }
                },
                timeout=30,
            )
        except requests.RequestException:
            pass
    return {"externalPostId": video_id, "externalUrl": f"https://youtube.com/shorts/{video_id}"}, updated

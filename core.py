"""Shared module-level state for Phantomline.

server.py and the route Blueprints under routes/ all import from here.
This module imports nothing from the rest of the project, so the import
graph stays acyclic:

    core         <- (no project imports)
    server.py    <- core, project modules, blueprints
    routes/*.py  <- core, flask, project modules

Anything that two or more route groups need to share should live here:
output paths, the persistent ProjectStore singleton, JSON read/write
helpers. Domain-specific state (job tables, locks, prompt builders) stays
in the route module that owns it.
"""

from __future__ import annotations

import contextvars
import csv
import functools
import json
import os
import re
import threading
from io import StringIO
from pathlib import Path

import projects as project_store
import story_generator as sg


BASE_DIR: Path = Path(__file__).resolve().parent
OUTPUT_DIR: Path = BASE_DIR / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Project store proxy.
#
# The studio has 60+ call sites that touch `PROJECTS`. Refactoring every one
# to thread `user_id` through manually would be a multi-day rewrite and would
# break the desktop install (which doesn't have a user_id concept since it's
# local-only).
#
# Instead: PROJECTS is a thin proxy that picks the active backend per-call:
#   - Local install (or Supabase env vars unset): falls through to the
#     file-based ProjectStore, no user scoping.
#   - Hosted with PHANTOMLINE_USE_SUPABASE_PROJECTS=true: routes to the
#     SupabaseProjectStore, scoped to the user_id stored in a context var
#     that's set per-request by a Flask before_request hook.
#
# Background render threads call `with_user_context(user_id)` to re-bind the
# context var when they spawn from a request handler, so a user's renders
# always write back to their own row.
# ---------------------------------------------------------------------------

_USER_ID_VAR: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "phantomline_user_id", default=None
)
# Companion contextvar for the user's Supabase JWT (access_token).
# Set alongside _USER_ID_VAR by the Flask before_request hook so
# SupabaseProjectStore can make user-scoped REST calls. RLS then
# enforces row-level ownership instead of relying on service_role.
# spawn_user_thread() copies contextvars so background render threads
# inherit both — they keep working with the user's own JWT instead of
# crashing or silently writing to the wrong row.
_USER_JWT_VAR: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "phantomline_user_jwt", default=None
)


def _supabase_projects_enabled() -> bool:
    """Feature flag. Default OFF so the existing local store stays the
    canonical backend until we explicitly opt a deploy in."""
    flag = os.environ.get("PHANTOMLINE_USE_SUPABASE_PROJECTS", "").lower()
    return flag in ("1", "true", "yes")


def set_request_user(user_id: str | None,
                     jwt: str | None = None):
    """Bind the signed-in user's ID + JWT to the current context.
    Returns a tuple of (user_token, jwt_token) which the caller MUST
    pass to `reset_request_user` once the scope ends.

    `jwt` is optional for backward compatibility. When None, the
    project store falls back to service_role (Render hosted pattern).
    On local installs the JWT is required for cloud sync to work."""
    user_token = _USER_ID_VAR.set(user_id)
    jwt_token = _USER_JWT_VAR.set(jwt)
    return (user_token, jwt_token)


def reset_request_user(token) -> None:
    """Restore the previous user-id + JWT bindings. Accepts either the
    new (user_token, jwt_token) tuple or a legacy single-token value
    for backward compat with older callers."""
    if isinstance(token, tuple) and len(token) == 2:
        user_token, jwt_token = token
        try:
            _USER_ID_VAR.reset(user_token)
        except (LookupError, ValueError):
            pass
        try:
            _USER_JWT_VAR.reset(jwt_token)
        except (LookupError, ValueError):
            pass
    else:
        # Legacy single-token path — older callers that only set user_id.
        try:
            _USER_ID_VAR.reset(token)
        except (LookupError, ValueError):
            pass


def current_user_id() -> str | None:
    """The user_id bound to the current execution context, or None if
    we're outside a request (e.g. a background render started before
    sign-in support shipped)."""
    return _USER_ID_VAR.get()


def current_user_jwt() -> str | None:
    """The Supabase access_token (JWT) bound to the current execution
    context, or None if not signed in / not running under a hooked
    request. Used by SupabaseProjectStore to make user-scoped REST
    calls so RLS enforces ownership."""
    return _USER_JWT_VAR.get()


def run_with_user_context(user_id: str | None, fn, *args, **kwargs):
    """Run `fn(*args, **kwargs)` with `user_id` as the active context.
    Restores the prior context on exit. Use this for spawned threads:

        threading.Thread(
            target=lambda: run_with_user_context(uid, worker, job_id),
            daemon=True,
        ).start()
    """
    token = set_request_user(user_id)
    try:
        return fn(*args, **kwargs)
    finally:
        reset_request_user(token)


def spawn_user_thread(target, *args, daemon=True, name=None, **kwargs) -> threading.Thread:
    """threading.Thread() replacement that captures the current
    contextvar bindings (including the signed-in user_id set by the
    Flask before_request hook) and re-binds them inside the worker.

    Without this, a render thread spawned mid-request loses the
    user_id when it tries to write back to PROJECTS, and SupabaseProjectStore
    raises PermissionError because no user is on the context.
    """
    ctx = contextvars.copy_context()
    thread = threading.Thread(
        target=lambda: ctx.run(target, *args, **kwargs),
        daemon=daemon,
        name=name,
    )
    return thread


class _RequestScopedProjectStore:
    """Proxy that delegates to either the local file-based store or the
    Supabase-backed store, transparently injecting `user_id` from the
    context var when the Supabase backend is active.

    Only the methods the rest of the app actually calls are surfaced;
    anything else falls through via __getattr__ so the local store keeps
    working unchanged for any obscure call sites."""

    def __init__(self, local_store):
        self._local = local_store
        self._supabase_lock = threading.Lock()
        self._supabase = None  # built lazily so import order doesn't matter

    def _supabase_store(self):
        """The shared base SupabaseProjectStore. Per-request user-scoped
        clones are created via .with_jwt() inside _route(). Building the
        base once lets us amortize the env-var validation + cache_dir
        setup across requests."""
        if self._supabase is not None:
            return self._supabase
        with self._supabase_lock:
            if self._supabase is None:
                from supabase_projects import SupabaseProjectStore  # noqa: WPS433
                # Construct WITHOUT a JWT so the env-var check picks the
                # right path: hosted with service_role works; local with
                # only URL+anon would fail here, but local installs hit
                # this code path only after a sign-in (JWT-mode), so the
                # base store stays unused on local.
                try:
                    self._supabase = SupabaseProjectStore(self._local.output_dir)
                except RuntimeError:
                    # Local install with no service_role — that's fine,
                    # we'll build per-request JWT-scoped stores instead.
                    # Use a minimal placeholder we can call .with_jwt() on.
                    self._supabase = SupabaseProjectStore.__new__(SupabaseProjectStore)
                    from pathlib import Path as _P
                    import tempfile as _t
                    self._supabase.cache_dir = (
                        _P(self._local.output_dir) / "phantomline_dl_cache"
                        if hasattr(self._local, "output_dir") else
                        _P(_t.gettempdir()) / "phantomline_dl_cache"
                    )
                    self._supabase.cache_dir.mkdir(parents=True, exist_ok=True)
                    self._supabase._jwt = None
            return self._supabase

    def _route(self):
        """Return (active_store, user_id) for this call. user_id is None
        when we're using the local file store.

        New for Phase 2B: when a user JWT is bound to the context, we
        return a per-request user-scoped SupabaseProjectStore clone so
        the JWT travels through every Postgrest + Storage call. RLS
        enforces ownership instead of relying on service_role.

        The legacy service_role path (no JWT, hosted) still works for
        any background admin use cases on Render — but for normal user
        traffic the JWT path is now the default."""
        if _supabase_projects_enabled():
            user_id = current_user_id()
            if user_id:
                jwt = current_user_jwt()
                base = self._supabase_store()
                store = base.with_jwt(jwt) if jwt else base
                return store, user_id
            # Hosted env enabled but no user_id on the context — refuse.
            raise PermissionError(
                "Supabase project store active but no user is signed in. "
                "Ensure the route is JWT-gated."
            )
        return self._local, None

    # ----- proxied methods -----

    def all(self):
        store, uid = self._route()
        return store.all(user_id=uid) if uid else store.all()

    def get(self, project_id):
        store, uid = self._route()
        return store.get(project_id, user_id=uid) if uid else store.get(project_id)

    def create(self, kind, title, params=None):
        store, uid = self._route()
        if uid:
            return store.create(user_id=uid, kind=kind, title=title, params=params)
        return store.create(kind=kind, title=title, params=params)

    def update(self, project_id, **fields):
        store, uid = self._route()
        if uid:
            return store.update(project_id, user_id=uid, **fields)
        return store.update(project_id, **fields)

    def delete(self, project_id):
        store, uid = self._route()
        if uid:
            return store.delete(project_id, user_id=uid)
        return store.delete(project_id)

    def attach_file(self, project_id, role, src_path, copy=False):
        store, uid = self._route()
        if uid:
            return store.attach_file(project_id, role, src_path,
                                     user_id=uid, copy=copy)
        return store.attach_file(project_id, role, src_path, copy=copy)

    def file_path(self, project_id, role):
        store, uid = self._route()
        if uid:
            return store.file_path(project_id, role, user_id=uid)
        return store.file_path(project_id, role)

    def file_signed_url(self, project_id, role, expires_in=3600):
        """Only available on the Supabase backend. Local store returns
        None so the caller can fall back to streaming via Flask."""
        store, uid = self._route()
        if uid and hasattr(store, "file_signed_url"):
            return store.file_signed_url(project_id, role,
                                         user_id=uid, expires_in=expires_in)
        return None

    def create_bundle(self, title, params=None, members=None):
        store, uid = self._route()
        if uid:
            return store.create_bundle(user_id=uid, title=title,
                                       params=params, members=members)
        return store.create_bundle(title=title, params=params, members=members)

    def attach_bundle_member(self, bundle_id, role, project_id):
        store, uid = self._route()
        if uid:
            return store.attach_bundle_member(bundle_id, role, project_id,
                                              user_id=uid)
        return store.attach_bundle_member(bundle_id, role, project_id)

    def bundle_for(self, project_id):
        store, uid = self._route()
        if uid:
            return store.bundle_for(project_id, user_id=uid)
        return store.bundle_for(project_id)

    def expand_bundle(self, bundle_id):
        store, uid = self._route()
        if uid:
            return store.expand_bundle(bundle_id, user_id=uid)
        return store.expand_bundle(bundle_id)

    # Fall-through for anything we missed. Routes the call to the local
    # store unconditionally — this is fine because anything not surfaced
    # above isn't user-scoped (e.g. internal cache helpers).
    def __getattr__(self, name):
        return getattr(self._local, name)


# Persistent project store. Survives server restarts.
# In local desktop mode, this is the file-based store. On hosted with the
# Supabase flag enabled, every call routes through the user-scoped Supabase
# backend. Call sites stay identical thanks to the proxy.
PROJECTS = _RequestScopedProjectStore(project_store.ProjectStore(OUTPUT_DIR))


def _read_json_file(path: Path, fallback):
    """Best-effort read of a JSON file. Returns `fallback` if missing or
    corrupt. Never raises."""
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass
    return fallback


def _write_json_file(path: Path, payload) -> None:
    """Atomic JSON write via tmp + rename. Creates parent dirs as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _parse_analytics_upload(file_storage):
    """Parse a YouTube Studio CSV/TSV export into a list of normalized dict
    rows. Tolerant of varying delimiters, BOMs, and pre-header garbage.
    Used by the channel insights import routes and the analytics analyzer."""
    raw = file_storage.read()
    text = raw.decode("utf-8-sig", errors="replace")
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;")
    except csv.Error:
        dialect = csv.excel
    raw_rows = list(csv.reader(StringIO(text), dialect=dialect))
    header_index = 0
    for idx, row in enumerate(raw_rows[:40]):
        normalized = [re.sub(r"\s+", " ", str(cell or "")).strip().lower() for cell in row]
        joined = " | ".join(normalized)
        if "video title" in normalized or "title" in normalized or ("video title" in joined and "views" in joined):
            header_index = idx
            break
    if not raw_rows:
        return []
    headers = [
        re.sub(r"\s+", " ", str(cell or "").strip())
        for cell in raw_rows[header_index]
    ]
    rows = []
    for raw_row in raw_rows[header_index + 1:]:
        if not any(str(cell or "").strip() for cell in raw_row):
            continue
        row = {}
        for i, header in enumerate(headers):
            if not header:
                continue
            row[header] = raw_row[i] if i < len(raw_row) else ""
        rows.append(row)
    cleaned = []
    for row in rows[:5000]:
        item = {}
        for key, value in row.items():
            if not key:
                continue
            k = re.sub(r"\s+", " ", str(key)).strip()
            v = str(value or "").strip()
            if k and v:
                item[k] = v
        if item:
            cleaned.append(item)
    return cleaned


# ---------------------------------------------------------------------------
# YouTube OAuth connection — used by both the publish flow and the
# optimize-library flow. The on-disk JSON sits next to the publish state
# files. Stored in core.py so multiple blueprints can read without coupling
# to publish.py.
# ---------------------------------------------------------------------------

PUBLISH_DIR: Path = OUTPUT_DIR / "publishing"
PUBLISH_DIR.mkdir(parents=True, exist_ok=True)
YOUTUBE_CONNECTION_PATH: Path = PUBLISH_DIR / "youtube_connection.json"


def _youtube_connection():
    data = _read_json_file(YOUTUBE_CONNECTION_PATH, None)
    return data if isinstance(data, dict) else None


def _save_youtube_connection(connection):
    _write_json_file(YOUTUBE_CONNECTION_PATH, connection)


# ---------------------------------------------------------------------------
# LLM helpers used across multiple route blueprints.
# ---------------------------------------------------------------------------

def _resolve_ollama_model(model):
    """Return an installed Ollama model tag when the UI sends a short alias."""
    requested = (model or sg.DEFAULT_MODEL).strip() or sg.DEFAULT_MODEL
    models = sg.check_ollama()
    if models is None:
        return requested
    if requested in models:
        return requested
    if ":" not in requested:
        latest = f"{requested}:latest"
        if latest in models:
            return latest
    base = requested.split(":", 1)[0]
    for installed in models:
        if installed.split(":", 1)[0] == base:
            return installed
    return requested


def _extract_json_array(text):
    """Best-effort JSON array extraction from local model output."""
    if not text:
        return []
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?", "", cleaned, flags=re.I).strip()
    cleaned = re.sub(r"```$", "", cleaned).strip()
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("[")
        end = cleaned.rfind("]")
        if start == -1 or end == -1 or end <= start:
            return []
        try:
            parsed = json.loads(cleaned[start:end + 1])
        except json.JSONDecodeError:
            return []
    return parsed if isinstance(parsed, list) else []


def _extract_json_object(text):
    """Best-effort JSON object extraction from local model output."""
    if not text:
        return {}
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?", "", cleaned, flags=re.I).strip()
    cleaned = re.sub(r"```$", "", cleaned).strip()
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return {}
        try:
            parsed = json.loads(cleaned[start:end + 1])
        except json.JSONDecodeError:
            return {}
    return parsed if isinstance(parsed, dict) else {}


def _pick_focus_keyword(*, topic="", title="", niche="", research_keyword="",
                        insights=None, extra_phrases=None):
    """Pick ONE focus keyword + variations to thread through title/description/tags.

    vidIQ rewards a single keyword that appears verbatim in the title, the
    first line of the description, and the tag list (the "tripled keyword").
    This helper picks that anchor by preferring channel-insight phrases that
    overlap the current topic, then research keyword, then any phrase pool
    we have, then the niche/topic itself."""
    insights = insights or {}
    extra_phrases = extra_phrases or []
    seed_text = " ".join(filter(None, [str(topic or ""), str(title or "")])).lower()
    seed_words = set(re.findall(r"[a-z0-9']+", seed_text))

    def overlap(phrase):
        words = set(re.findall(r"[a-z0-9']+", (phrase or "").lower()))
        if not words:
            return 0
        return len(words & seed_words)

    insight_pool = []
    for g in (insights.get("gap_keywords") or []):
        if str(g).strip():
            insight_pool.append(str(g).strip())
    for k in (insights.get("seo_keywords") or []):
        if str(k).strip():
            insight_pool.append(str(k).strip())
    for term in (insights.get("search_terms") or []):
        q = term.get("query") if isinstance(term, dict) else term
        if q and str(q).strip():
            insight_pool.append(str(q).strip())

    sorted_insights = sorted(insight_pool, key=lambda p: -overlap(p)) if seed_words else list(insight_pool)
    best_insight = next((p for p in sorted_insights if seed_words and overlap(p) > 0), "")

    topic_head = (topic or title or "").strip().split(".")[0].strip()
    topic_short = " ".join(topic_head.split()[:4]) if topic_head else ""
    focus = (
        best_insight
        or (research_keyword or "").strip()
        or (sorted_insights[0] if sorted_insights else "")
        or next((p.strip() for p in extra_phrases if str(p).strip()), "")
        or topic_short
        or (niche or "").strip()
    )
    focus = re.sub(r"\s+", " ", focus).strip().lower()[:60]

    variations = []
    seen = {focus}
    pool = list(sorted_insights) + list(extra_phrases)
    for raw in pool:
        v = re.sub(r"\s+", " ", str(raw or "")).strip().lower()
        if not v or v in seen or len(v) > 60:
            continue
        seen.add(v)
        variations.append(v)
        if len(variations) >= 6:
            break
    return {"focus": focus, "variations": variations}

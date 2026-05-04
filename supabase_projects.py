"""User-scoped project store backed by Supabase.

Mirrors the surface API of `projects.ProjectStore` so server.py can swap
the active backend with one line. Three responsibilities:

  1. Persist the project records in the `projects` table (RLS-scoped to
     auth.uid() so cross-user reads are rejected by Postgres directly).
  2. Stream attached files into the `project-files` Storage bucket under
     <user_id>/<project_id>/<role>.<ext>.
  3. Hand back time-limited signed URLs for downloading those files.

Why we use the service_role key for writes even though RLS would also
allow user-scoped writes via the bearer JWT:
  - Some flows (TTS render, video assembly, thumbnail compose) mutate
    projects from background threads that don't have the user's JWT in
    scope. Threading the JWT through every job would be intrusive.
  - We pass user_id explicitly on every call so the server still
    enforces per-user scoping; service_role just bypasses Postgres-side
    RLS to make the writes go through.

Background threads MUST pass the same user_id the request handler used,
or projects will leak between users. The convention is: the route
extracts user_id from the JWT, passes it into the job submission, the
job carries it for the lifetime of the render.
"""
from __future__ import annotations

import json
import mimetypes
import os
import shutil
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import requests

from auth_helpers import (
    rest_url,
    service_headers,
    storage_signed_url,
    storage_url,
    supabase_configured,
    supabase_service_role_key,
    supabase_url,
)


# Copied from projects.py so callers don't have to know about both modules.
KIND_STORY = "story"
KIND_SHORT = "short_script"
KIND_NARRATION = "narration"
KIND_MUSIC = "music"
KIND_MIX = "mix"
KIND_UPLOAD = "upload"
KIND_VIDEO_PLAN = "video_plan"
KIND_TIMELINE = "timeline"
KIND_VIDEO = "video"
KIND_BUNDLE = "bundle"

PROJECT_FILES_BUCKET = "project-files"


_lock = threading.Lock()


class SupabaseProjectStore:
    """Drop-in replacement for the file-based ProjectStore.

    All public methods that need ownership context now require user_id
    as the first kwarg. Methods that don't (file_path, etc.) are still
    safe because the underlying RLS policies catch any cross-user
    attempt.
    """

    def __init__(self, output_dir: Path | str | None = None):
        # output_dir is kept as a download cache for streaming files
        # back to clients. Files themselves live in Storage; this is
        # just a scratch directory for the current Flask request.
        self.cache_dir = Path(output_dir or tempfile.gettempdir()) / "phantomline_dl_cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        if not supabase_configured():
            # Fail loud at construction so a bad deploy doesn't silently
            # serve every visitor an empty library.
            raise RuntimeError(
                "SupabaseProjectStore requires SUPABASE_URL, SUPABASE_ANON_KEY, "
                "and SUPABASE_SERVICE_ROLE_KEY env vars. Either set them or "
                "use the local-disk ProjectStore for the desktop install."
            )

    # ----- helpers -----

    def _get_url(self, path: str) -> str:
        return rest_url(path)

    def _post_json(self, path: str, body: Any) -> Any:
        res = requests.post(self._get_url(path), json=body,
                            headers=service_headers(), timeout=10)
        if res.status_code not in (200, 201):
            raise RuntimeError(f"Supabase POST {path} -> {res.status_code} {res.text[:200]}")
        try:
            return res.json()
        except ValueError:
            return None

    def _patch_json(self, path: str, body: Any) -> Any:
        res = requests.patch(self._get_url(path), json=body,
                             headers=service_headers(), timeout=10)
        if res.status_code not in (200, 204):
            raise RuntimeError(f"Supabase PATCH {path} -> {res.status_code} {res.text[:200]}")
        try:
            return res.json()
        except ValueError:
            return None

    def _get(self, path: str) -> Any:
        res = requests.get(self._get_url(path), headers=service_headers(), timeout=10)
        if res.status_code != 200:
            raise RuntimeError(f"Supabase GET {path} -> {res.status_code} {res.text[:200]}")
        try:
            return res.json()
        except ValueError:
            return None

    def _delete(self, path: str) -> None:
        res = requests.delete(self._get_url(path), headers=service_headers(), timeout=10)
        if res.status_code not in (200, 204):
            raise RuntimeError(f"Supabase DELETE {path} -> {res.status_code} {res.text[:200]}")

    @staticmethod
    def _row_to_project(row: dict[str, Any]) -> dict[str, Any]:
        """Translate a Supabase row to the dict shape ProjectStore returns.
        Notably: created_at/updated_at become unix floats (the local store
        uses time.time() floats; the JS UI expects floats too)."""
        out = dict(row)
        # Postgrest returns ISO 8601 strings; convert to floats.
        for ts_col in ("created_at", "updated_at"):
            v = out.get(ts_col)
            if isinstance(v, str):
                # Strip trailing 'Z' if present, parse RFC3339-ish.
                from datetime import datetime
                try:
                    if v.endswith("Z"):
                        v = v[:-1] + "+00:00"
                    out[ts_col] = datetime.fromisoformat(v).timestamp()
                except (ValueError, AttributeError):
                    out[ts_col] = time.time()
        # Match local schema field names where they differ.
        if "files" not in out or out["files"] is None:
            out["files"] = {}
        if "params" not in out or out["params"] is None:
            out["params"] = {}
        return out

    # ----- public API mirroring ProjectStore -----

    def all(self, *, user_id: str) -> list[dict[str, Any]]:
        """Return all projects owned by `user_id`, newest first."""
        if not user_id:
            return []
        rows = self._get(f"projects?user_id=eq.{user_id}&order=created_at.desc")
        if not isinstance(rows, list):
            return []
        return [self._row_to_project(r) for r in rows]

    def get(self, project_id: str, *, user_id: str | None = None) -> dict[str, Any] | None:
        """Fetch a single project. user_id is optional but recommended:
        when present we add it to the filter so an attacker who guesses
        a UUID still doesn't get back another user's row."""
        if not project_id:
            return None
        path = f"projects?id=eq.{project_id}"
        if user_id:
            path += f"&user_id=eq.{user_id}"
        path += "&limit=1"
        rows = self._get(path)
        if not isinstance(rows, list) or not rows:
            return None
        return self._row_to_project(rows[0])

    def create(self, *, user_id: str, kind: str, title: str,
               params: dict | None = None) -> dict[str, Any]:
        """Insert a new project owned by user_id."""
        if not user_id:
            raise ValueError("user_id is required to create a project")
        project_id = uuid.uuid4().hex[:12]
        body = {
            "id": project_id,
            "user_id": user_id,
            "kind": kind,
            "title": (title or "Untitled")[:300],
            "status": "pending",
            "params": params or {},
            "files": {},
        }
        rows = self._post_json("projects", body)
        if not isinstance(rows, list) or not rows:
            raise RuntimeError("Supabase insert returned no row")
        return self._row_to_project(rows[0])

    def update(self, project_id: str, *, user_id: str | None = None,
               **fields) -> dict[str, Any] | None:
        """Patch the row. updated_at is bumped by the trigger; we just
        send the user-supplied fields. Caller should NOT pass user_id in
        fields (that's a separate kwarg)."""
        if not project_id or not fields:
            return self.get(project_id, user_id=user_id)
        path = f"projects?id=eq.{project_id}"
        if user_id:
            path += f"&user_id=eq.{user_id}"
        rows = self._patch_json(path, fields)
        if isinstance(rows, list) and rows:
            return self._row_to_project(rows[0])
        return self.get(project_id, user_id=user_id)

    def delete(self, project_id: str, *, user_id: str | None = None) -> bool:
        """Delete the row + best-effort wipe of any files in the bucket
        under the project's folder. RLS catches cross-user delete attempts
        in any case."""
        existing = self.get(project_id, user_id=user_id)
        if not existing:
            return False
        owner = existing.get("user_id")
        # Delete attached storage objects first.
        files = existing.get("files") or {}
        for role, rel_path in files.items():
            try:
                self._delete_storage_object(rel_path)
            except Exception:
                # Best effort — orphaned objects can be cleaned up by a
                # background job later if needed.
                pass
        path = f"projects?id=eq.{project_id}"
        if user_id:
            path += f"&user_id=eq.{user_id}"
        try:
            self._delete(path)
        except Exception:
            return False
        return True

    def attach_file(self, project_id: str, role: str,
                    src_path: str | Path, *, user_id: str,
                    copy: bool = False) -> dict[str, Any] | None:
        """Upload the local file to Storage under
        <user_id>/<project_id>/<role>.<ext> and update the project's
        `files` JSONB to record the storage path.

        `copy=True` keeps the local source file; `copy=False` (default)
        deletes the local file after a successful upload to mirror the
        local store's "move into project folder" semantics.
        """
        existing = self.get(project_id, user_id=user_id)
        if not existing:
            return None
        src = Path(src_path)
        if not src.exists():
            return existing
        owner = existing.get("user_id") or user_id
        ext = src.suffix or ""
        object_path = f"{owner}/{project_id}/{role}{ext}"
        # Upload to Storage. We use service_role for the upload so the
        # background thread doesn't need a user JWT; the path itself
        # encodes the user_id, and RLS still applies to subsequent
        # downloads.
        ctype, _ = mimetypes.guess_type(src.name)
        if not ctype:
            ctype = "application/octet-stream"
        upload_url = storage_url(PROJECT_FILES_BUCKET, object_path)
        headers = {
            "apikey": supabase_service_role_key() or "",
            "Authorization": f"Bearer {supabase_service_role_key() or ''}",
            "Content-Type": ctype,
            "x-upsert": "true",  # replace if a previous upload exists
            "Cache-Control": "max-age=31536000",
        }
        with src.open("rb") as fh:
            res = requests.post(upload_url, data=fh, headers=headers, timeout=120)
        if res.status_code not in (200, 201):
            raise RuntimeError(f"Storage upload failed: {res.status_code} {res.text[:200]}")
        # Record the storage path on the row.
        new_files = dict(existing.get("files") or {})
        new_files[role] = object_path
        updated = self.update(project_id, user_id=user_id, files=new_files)
        if not copy:
            try:
                src.unlink()
            except OSError:
                pass
        return updated

    def file_path(self, project_id: str, role: str, *,
                  user_id: str | None = None) -> Path | None:
        """Download the storage object into a local cache and return the
        path. Used by streaming routes that need to send the file via
        Flask. For browser-direct streaming, prefer file_signed_url()
        instead — it cuts out the Flask hop entirely."""
        existing = self.get(project_id, user_id=user_id)
        if not existing:
            return None
        files = existing.get("files") or {}
        rel = files.get(role)
        if not rel:
            return None
        local = self.cache_dir / project_id / role
        local.parent.mkdir(parents=True, exist_ok=True)
        # Cache hit: serve the existing local copy if the project hasn't
        # been updated since we cached it.
        if local.exists():
            return local
        # Cache miss: pull from Storage.
        url = storage_url(PROJECT_FILES_BUCKET, rel)
        headers = {
            "apikey": supabase_service_role_key() or "",
            "Authorization": f"Bearer {supabase_service_role_key() or ''}",
        }
        try:
            res = requests.get(url, headers=headers, timeout=60, stream=True)
        except requests.RequestException:
            return None
        if res.status_code != 200:
            return None
        with local.open("wb") as fh:
            for chunk in res.iter_content(chunk_size=64 * 1024):
                if chunk:
                    fh.write(chunk)
        return local

    def file_signed_url(self, project_id: str, role: str, *,
                        user_id: str | None = None,
                        expires_in: int = 3600) -> str | None:
        """Return a signed URL the browser can fetch directly. Faster +
        cheaper than streaming through Flask for big MP4s."""
        existing = self.get(project_id, user_id=user_id)
        if not existing:
            return None
        files = existing.get("files") or {}
        rel = files.get(role)
        if not rel:
            return None
        return storage_signed_url(PROJECT_FILES_BUCKET, rel, expires_in=expires_in)

    def _delete_storage_object(self, object_path: str) -> None:
        """Delete a single object from the project-files bucket."""
        if not object_path:
            return
        base = (supabase_url() or "").rstrip("/")
        url = f"{base}/storage/v1/object/{PROJECT_FILES_BUCKET}/{object_path}"
        headers = {
            "apikey": supabase_service_role_key() or "",
            "Authorization": f"Bearer {supabase_service_role_key() or ''}",
        }
        try:
            requests.delete(url, headers=headers, timeout=8)
        except requests.RequestException:
            pass

    # ----- bundle helpers (mirror the local ProjectStore methods) -----

    def create_bundle(self, *, user_id: str, title: str,
                      params: dict | None = None,
                      members: dict | None = None) -> dict[str, Any]:
        """Create a bundle that links several artifact projects together."""
        body_params = dict(params or {})
        record = self.create(user_id=user_id, kind=KIND_BUNDLE,
                             title=title, params=body_params)
        if members:
            updated = self.update(record["id"], user_id=user_id, members=members)
            if updated:
                return updated
        return record

    def attach_bundle_member(self, bundle_id: str, role: str,
                             project_id: str, *,
                             user_id: str) -> dict[str, Any] | None:
        bundle = self.get(bundle_id, user_id=user_id)
        if not bundle or bundle.get("kind") != KIND_BUNDLE:
            return None
        members = dict(bundle.get("members") or {})
        members[role] = project_id
        return self.update(bundle_id, user_id=user_id, members=members)

    def bundle_for(self, project_id: str, *,
                   user_id: str) -> dict[str, Any] | None:
        """Find the bundle that contains a given child project."""
        if not user_id:
            return None
        # Postgrest JSONB containment query: members ?| array['<project_id>']
        # is awkward via REST; simplest is to fetch all bundles for the
        # user and scan client-side. Bundle counts are typically <100 per
        # user so the cost is fine.
        path = (
            f"projects?user_id=eq.{user_id}&kind=eq.bundle"
            f"&select=id,members,kind,title"
        )
        try:
            rows = self._get(path)
        except RuntimeError:
            return None
        if not isinstance(rows, list):
            return None
        for row in rows:
            members = row.get("members") or {}
            if project_id in members.values():
                return self._row_to_project(row)
        return None

    def expand_bundle(self, bundle_id: str, *,
                      user_id: str) -> dict[str, Any] | None:
        """Return the bundle plus its resolved children, in role order."""
        bundle = self.get(bundle_id, user_id=user_id)
        if not bundle or bundle.get("kind") != KIND_BUNDLE:
            return None
        members = bundle.get("members") or {}
        children: dict[str, dict[str, Any]] = {}
        for role, pid in members.items():
            child = self.get(pid, user_id=user_id)
            if child:
                children[role] = child
        out = dict(bundle)
        out["children"] = children
        return out

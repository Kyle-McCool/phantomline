"""
Persistent project store backed by output/projects.json.

A "project" is one user-visible artifact: a generated story, a short script,
a narration, a music bed, or a final mixdown. Each project lives in its own
folder under output/projects/<id>/ with a meta.json and the produced files.

The store survives server restarts. Atomic writes (tmp + rename) so concurrent
threads don't corrupt the index.
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any


# Project kinds - used for filtering in the Library UI.
KIND_STORY = "story"            # long-form story (.txt)
KIND_SHORT = "short_script"     # 30s–10min one-shot script (.txt)
KIND_NARRATION = "narration"    # Kokoro TTS audio
KIND_MUSIC = "music"            # MusicGen background bed
KIND_MIX = "mix"                # narration + music final
KIND_UPLOAD = "upload"          # user-uploaded narration audio
KIND_VIDEO_PLAN = "video_plan"  # scene breakdown + video prompts
KIND_TIMELINE = "timeline"      # narration-aligned video assembly plan
KIND_VIDEO = "video"            # rendered MP4 draft/final video
KIND_BUNDLE = "bundle"           # one Make Video session: idea + script + audio + visuals + render + publish draft, all linked


# Kinds that count as raw artifacts inside a bundle. Library uses this to
# default-hide them when bundle view is on.
ARTIFACT_KINDS = {
    KIND_STORY, KIND_SHORT, KIND_NARRATION, KIND_MUSIC, KIND_MIX,
    KIND_UPLOAD, KIND_VIDEO_PLAN, KIND_TIMELINE, KIND_VIDEO,
}


_lock = threading.Lock()


class ProjectStore:
    """Singleton-ish wrapper around a JSON-backed list of project dicts."""

    def __init__(self, output_dir: Path):
        self.output_dir = Path(output_dir)
        self.projects_dir = self.output_dir / "projects"
        self.index_path = self.output_dir / "projects.json"
        self.projects_dir.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, dict] = {}
        self._load()

    # ----- load / save -----

    def _load(self):
        if not self.index_path.exists():
            self._cache = {}
            return
        try:
            data = json.loads(self.index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            # Corrupt index - start fresh and back up the broken file.
            try:
                self.index_path.rename(self.index_path.with_suffix(".broken.json"))
            except OSError:
                pass
            self._cache = {}
            return
        if isinstance(data, list):
            self._cache = {p["id"]: p for p in data if "id" in p}
        elif isinstance(data, dict):
            self._cache = data
        else:
            self._cache = {}

    def _save(self):
        # Atomic write: tmp + rename.
        tmp = self.index_path.with_suffix(".tmp")
        payload = list(self._cache.values())
        payload.sort(key=lambda p: p.get("created_at", 0), reverse=True)
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                       encoding="utf-8")
        os.replace(tmp, self.index_path)

    # ----- public API -----

    def all(self) -> list[dict]:
        with _lock:
            return sorted(self._cache.values(),
                          key=lambda p: p.get("created_at", 0),
                          reverse=True)

    def get(self, project_id: str) -> dict | None:
        with _lock:
            p = self._cache.get(project_id)
            return dict(p) if p else None

    def create(self, kind: str, title: str, params: dict | None = None) -> dict:
        project_id = uuid.uuid4().hex[:12]
        folder = self.projects_dir / project_id
        folder.mkdir(parents=True, exist_ok=True)
        record = {
            "id": project_id,
            "kind": kind,
            "title": title or "Untitled",
            "created_at": time.time(),
            "updated_at": time.time(),
            "status": "pending",
            "params": params or {},
            "files": {},          # role -> relative path under folder
            "error": None,
            "duration_seconds": None,
            "word_count": None,
        }
        with _lock:
            self._cache[project_id] = record
            self._save()
        return dict(record)

    def update(self, project_id: str, **fields) -> dict | None:
        with _lock:
            p = self._cache.get(project_id)
            if not p:
                return None
            p.update(fields)
            p["updated_at"] = time.time()
            self._save()
            return dict(p)

    def attach_file(self, project_id: str, role: str,
                    src_path: str | Path, copy: bool = False) -> dict | None:
        """Register a file with the project. By default the file is moved into
        the project folder. Pass copy=True to copy instead (e.g. uploads).
        Returns the updated record."""
        src = Path(src_path)
        if not src.exists():
            return None
        with _lock:
            p = self._cache.get(project_id)
            if not p:
                return None
            folder = self.projects_dir / project_id
            folder.mkdir(parents=True, exist_ok=True)
            dst = folder / f"{role}{src.suffix}"
            try:
                if copy:
                    shutil.copy2(src, dst)
                else:
                    if src.resolve() != dst.resolve():
                        if dst.exists():
                            dst.unlink()
                        shutil.move(str(src), str(dst))
            except OSError:
                return None
            p.setdefault("files", {})[role] = dst.name
            p["updated_at"] = time.time()
            self._save()
            return dict(p)

    def delete(self, project_id: str) -> bool:
        with _lock:
            p = self._cache.pop(project_id, None)
            if not p:
                return False
            folder = self.projects_dir / project_id
            if folder.exists():
                shutil.rmtree(folder, ignore_errors=True)
            self._save()
            return True

    def file_path(self, project_id: str, role: str) -> Path | None:
        """Resolve a file role to an absolute path on disk, or None."""
        with _lock:
            p = self._cache.get(project_id)
            if not p:
                return None
            rel = (p.get("files") or {}).get(role)
            if not rel:
                return None
            full = self.projects_dir / project_id / rel
            return full if full.exists() else None

    # ----- Bundles: one Make Video session as a navigable record -----

    def create_bundle(self, title: str, params: dict | None = None,
                      members: dict[str, str] | None = None) -> dict:
        """Create a bundle project that links several artifact projects.

        `members` maps role -> child project_id, e.g.
        {"script": "abc...", "narration": "def...", "video": "ghi..."}.
        Bundles are first-class projects with kind=KIND_BUNDLE; they have no
        files of their own — child file paths are resolved through the
        artifact projects."""
        record = self.create(kind=KIND_BUNDLE, title=title, params=params or {})
        if members:
            self.update(record["id"], members=dict(members))
            record = self.get(record["id"]) or record
        return record

    def attach_bundle_member(self, bundle_id: str, role: str, project_id: str) -> dict | None:
        """Add or replace a member of a bundle. Bundle stores a `members`
        dict (role -> child_project_id)."""
        with _lock:
            b = self._cache.get(bundle_id)
            if not b or b.get("kind") != KIND_BUNDLE:
                return None
            members = dict(b.get("members") or {})
            members[role] = project_id
            b["members"] = members
            b["updated_at"] = time.time()
            self._save()
            return dict(b)

    def bundle_for(self, project_id: str) -> dict | None:
        """Find the bundle that contains a given child project, if any."""
        with _lock:
            for b in self._cache.values():
                if b.get("kind") != KIND_BUNDLE:
                    continue
                members = b.get("members") or {}
                if project_id in members.values():
                    return dict(b)
        return None

    def expand_bundle(self, bundle_id: str) -> dict | None:
        """Return the bundle plus its resolved child records, in role order."""
        with _lock:
            b = self._cache.get(bundle_id)
            if not b or b.get("kind") != KIND_BUNDLE:
                return None
            members = b.get("members") or {}
            children = {}
            for role, pid in members.items():
                child = self._cache.get(pid)
                if child:
                    children[role] = dict(child)
        out = dict(b)
        out["children"] = children
        return out

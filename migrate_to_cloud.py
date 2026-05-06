"""Migrate local Phantomline projects to Supabase cloud sync.

Reads `output/projects.json` + every file under `output/projects/<id>/`,
uploads each row to Supabase under the signed-in user's account, and
uploads attached files to the `project-files` Storage bucket. After a
successful run, renames `output/projects.json` to
`output/projects.json.migrated` so it doesn't run twice.

Idempotent: if you re-run with a fresh `projects.json` (e.g. after
new local renders that landed before sync was on), already-uploaded
project IDs are skipped via Postgrest 409 detection.

Usage:
    # 1. Make sure your local install has these env vars set in .env:
    #    SUPABASE_URL=https://vdzydhrgazqeyaalguuy.supabase.co
    #    SUPABASE_ANON_KEY=<the publishable anon key>
    #
    # 2. Sign in once via the studio: open localhost:5000/account in
    #    a browser, "Continue with Google" — that lands a session in
    #    your browser's localStorage. You don't actually need to
    #    extract it for this script; instead the script asks Supabase
    #    Auth directly using a refresh token you paste below.
    #
    # 3. Run:
    #    python migrate_to_cloud.py
    #
    #    The script will prompt you for your Supabase access_token
    #    (one easy way: open localhost:5000/account in your browser,
    #    open DevTools, run the snippet it tells you to, paste the
    #    result back here).

The script intentionally does NOT read the browser's localStorage —
LocalStorage isn't accessible from Python without browser automation,
and a one-time copy-paste is simpler than wiring up Selenium.

Why this script exists: the Phase 2B JWT-mode SupabaseProjectStore
makes new renders sync automatically once you sign in. But projects
created BEFORE you turned on cloud sync stay marooned on disk. This
script catches them up so the new cloud-synced library matches what
you have locally.
"""

from __future__ import annotations

import json
import mimetypes
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# Config — read from env so the script honors whatever .env you have.
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "output"
PROJECTS_JSON = OUTPUT_DIR / "projects.json"
PROJECTS_DIR = OUTPUT_DIR / "projects"
MIGRATED_MARKER = OUTPUT_DIR / "projects.json.migrated"

PROJECT_FILES_BUCKET = "project-files"


def _env(name: str) -> str:
    v = (os.environ.get(name) or "").strip()
    if not v:
        # Try .env file in case the script wasn't launched via a Flask app
        env_file = BASE_DIR / ".env"
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("#") or "=" not in line:
                    continue
                k, _, val = line.partition("=")
                if k.strip() == name:
                    return val.strip().strip('"').strip("'")
    return v


SUPABASE_URL = _env("SUPABASE_URL").rstrip("/")
SUPABASE_ANON_KEY = _env("SUPABASE_ANON_KEY")


def fail(msg: str, code: int = 1) -> "None":
    print(f"\n[migrate_to_cloud] ERROR: {msg}\n", file=sys.stderr)
    sys.exit(code)


def info(msg: str) -> None:
    print(f"[migrate_to_cloud] {msg}")


def prompt_for_jwt() -> str:
    """Walk the user through extracting a JWT from their browser session.

    The user already signed in via /account in the browser. Their JWT
    lives in localStorage at a key like `sb-<projectref>-auth-token`.
    Easiest copy-out path: paste a one-liner into DevTools console.
    """
    sys.stdout.write(
        "\n"
        "To migrate, I need your Supabase access token (JWT). It's already\n"
        "in your browser if you signed in at localhost:5000/account.\n"
        "\n"
        "Easiest extraction:\n"
        "  1. Open http://localhost:5000/account in your browser\n"
        "  2. Press F12 (DevTools)\n"
        "  3. Console tab. Paste this and press Enter:\n"
        "\n"
        "     copy(JSON.parse(Object.entries(localStorage)\n"
        "       .find(([k])=>k.startsWith('sb-')&&k.includes('-auth-token'))[1]\n"
        "       ).access_token)\n"
        "\n"
        "  4. The JWT is now in your clipboard. Paste it here and Enter:\n"
        "\n"
        "JWT (paste, then Enter): "
    )
    sys.stdout.flush()
    jwt = sys.stdin.readline().strip()
    if not jwt:
        fail("No JWT entered. Aborting.")
    if "." not in jwt or jwt.count(".") < 2:
        fail("That doesn't look like a JWT (expected three dot-separated parts).")
    return jwt


def get_user_id_from_jwt(jwt: str) -> str:
    """Validate the JWT against Supabase /auth/v1/user and return the user id."""
    r = requests.get(
        f"{SUPABASE_URL}/auth/v1/user",
        headers={"apikey": SUPABASE_ANON_KEY, "Authorization": f"Bearer {jwt}"},
        timeout=10,
    )
    if r.status_code != 200:
        fail(
            f"Supabase rejected the JWT ({r.status_code}). "
            "Sign in again at localhost:5000/account and re-copy the token."
        )
    user = r.json() or {}
    user_id = user.get("id")
    if not user_id:
        fail("JWT was accepted but no user id came back. Aborting.")
    info(f"Authenticated as {user.get('email', '(no email)')} — user id {user_id}")
    return user_id


def _user_headers(jwt: str) -> dict[str, str]:
    return {
        "apikey": SUPABASE_ANON_KEY,
        "Authorization": f"Bearer {jwt}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }


def upsert_project(jwt: str, user_id: str, record: dict) -> tuple[bool, str]:
    """POST the project row to Supabase. Returns (inserted, message).

    `inserted` is False if the row already exists (skipped, not an error).
    The local project ID is preserved so re-runs are idempotent.
    """
    body = {
        "id": record["id"],
        "user_id": user_id,
        "kind": record.get("kind") or "story",
        "title": (record.get("title") or "Untitled")[:300],
        "status": record.get("status") or "complete",
        "params": record.get("params") or {},
        "files": {},  # we'll patch this after uploads
    }
    # Honor the original timestamps if Postgrest accepts them (created_at
    # has a default but we want to preserve history).
    for ts_field in ("created_at", "updated_at"):
        v = record.get(ts_field)
        if isinstance(v, (int, float)):
            body[ts_field] = datetime.fromtimestamp(v, tz=timezone.utc).isoformat()

    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/projects",
        headers=_user_headers(jwt),
        json=body,
        timeout=15,
    )
    if r.status_code == 409:
        return False, f"already exists (skipped): {record['id']}"
    if r.status_code not in (200, 201):
        return False, f"failed ({r.status_code}): {r.text[:200]}"
    return True, f"inserted {record['id']}"


def upload_file(jwt: str, user_id: str, project_id: str,
                role: str, src: Path) -> str | None:
    """Upload a single file to Storage. Returns the object path on success
    or None on failure."""
    ext = src.suffix or ""
    object_path = f"{user_id}/{project_id}/{role}{ext}"
    ctype, _ = mimetypes.guess_type(src.name)
    if not ctype:
        ctype = "application/octet-stream"
    headers = {
        "apikey": SUPABASE_ANON_KEY,
        "Authorization": f"Bearer {jwt}",
        "Content-Type": ctype,
        "x-upsert": "true",
        "Cache-Control": "max-age=31536000",
    }
    upload_url = f"{SUPABASE_URL}/storage/v1/object/{PROJECT_FILES_BUCKET}/{object_path}"
    try:
        with src.open("rb") as fh:
            r = requests.post(upload_url, data=fh, headers=headers, timeout=120)
    except requests.RequestException as e:
        info(f"  upload failed: {role}: {e}")
        return None
    if r.status_code not in (200, 201):
        info(f"  upload failed: {role}: {r.status_code} {r.text[:120]}")
        return None
    return object_path


def patch_files_field(jwt: str, project_id: str,
                      files: dict[str, str]) -> bool:
    """Update the project row's `files` JSONB to record uploaded paths."""
    r = requests.patch(
        f"{SUPABASE_URL}/rest/v1/projects?id=eq.{project_id}",
        headers=_user_headers(jwt),
        json={"files": files},
        timeout=15,
    )
    if r.status_code not in (200, 204):
        info(f"  PATCH files field failed: {r.status_code} {r.text[:120]}")
        return False
    return True


def main() -> int:
    print()
    info("Phantomline → Supabase project migration")
    print()

    if not PROJECTS_JSON.exists():
        if MIGRATED_MARKER.exists():
            info(
                "Looks like you've already migrated — projects.json was renamed "
                "to projects.json.migrated. Nothing to do."
            )
            return 0
        info(f"No {PROJECTS_JSON} found. Nothing to migrate.")
        return 0

    if not SUPABASE_URL or not SUPABASE_ANON_KEY:
        fail(
            "SUPABASE_URL or SUPABASE_ANON_KEY missing. Set them in .env "
            "(see .env.example for instructions)."
        )

    try:
        rows = json.loads(PROJECTS_JSON.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        fail(f"Couldn't read {PROJECTS_JSON}: {e}")

    if not isinstance(rows, list) or not rows:
        info(f"{PROJECTS_JSON} is empty. Nothing to migrate.")
        return 0

    info(f"Found {len(rows)} local project(s) in {PROJECTS_JSON}.")

    jwt = prompt_for_jwt()
    user_id = get_user_id_from_jwt(jwt)

    print()
    info("Uploading projects (idempotent — already-existing rows are skipped):")
    print()

    inserted = skipped = failed = 0
    file_count = 0

    for row in rows:
        pid = row.get("id")
        if not pid:
            failed += 1
            continue

        ok, msg = upsert_project(jwt, user_id, row)
        if not ok and "already exists" in msg:
            skipped += 1
        elif not ok:
            failed += 1
            info(f"  ✗ {msg}")
            continue
        else:
            inserted += 1
            info(f"  ✓ {msg} — {row.get('title') or '(untitled)'}")

        # Upload attached files (works for both inserted + skipped — handles
        # the case where the row was created but file uploads were
        # interrupted on a previous run).
        files_local = row.get("files") or {}
        if not files_local:
            continue
        project_folder = PROJECTS_DIR / pid
        if not project_folder.is_dir():
            continue

        new_files: dict[str, str] = {}
        for role, rel in files_local.items():
            local_path = project_folder / rel
            if not local_path.is_file():
                info(f"  skipping {role}: file not found at {local_path}")
                continue
            uploaded_at = upload_file(jwt, user_id, pid, role, local_path)
            if uploaded_at:
                new_files[role] = uploaded_at
                file_count += 1
                info(f"    ↑ {role}: {local_path.name} ({local_path.stat().st_size // 1024} KB)")

        if new_files:
            patch_files_field(jwt, pid, new_files)

        # Light pacing so we don't overwhelm Supabase free-tier rate limits.
        time.sleep(0.05)

    print()
    info(
        f"Done. Inserted {inserted}, skipped {skipped}, failed {failed}. "
        f"Uploaded {file_count} file(s)."
    )

    if failed == 0:
        try:
            PROJECTS_JSON.rename(MIGRATED_MARKER)
            info(
                f"Renamed {PROJECTS_JSON.name} → {MIGRATED_MARKER.name} so this "
                "won't run again. (Delete the .migrated file if you want a re-run.)"
            )
        except OSError as e:
            info(f"Couldn't rename {PROJECTS_JSON}: {e}")
    else:
        info(
            f"{failed} project(s) failed — leaving {PROJECTS_JSON} in place "
            "so you can fix and re-run."
        )

    return 0 if failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())

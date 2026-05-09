"""Phantomline auto-updater (called by the launcher scripts).

Design: this script is the brains of the auto-update flow. The launcher
scripts (start-phantomline.bat / .command / .sh) just call:

    python _updater.py && python server.py

That keeps the launchers as thin shims that never need to change as the
updater logic evolves. All the version-comparison + download + extract +
file-replacement work happens here.

Update flow:

    1. Read local VERSION → "1.2.0"
    2. GET phantomline.xyz/api/system/version → "1.3.0"
    3. If equal or local newer: print "up to date" and exit 0
    4. If hosted newer:
         a. Download the source zip to a temp dir
         b. Extract to temp/extracted/phantomline/
         c. Walk the extracted tree, copy each file into the install
            EXCEPT for paths in EXCLUDE_FROM_REPLACE (user data, secrets,
            launchers themselves, etc.)
         d. Print "updated to X.Y.Z" and exit 0
    5. On any error (network, disk, zip corruption): print warning and
       exit 0 anyway. Failed updates must NOT block the server from
       starting; the user gets stuck-on-old-version, not a broken install.

What we deliberately DON'T do:

  - Prompt the user. The launcher is opinionated — if you ran it, you
    want updates. Power users who want manual control run `python
    server.py` directly and use the in-studio update banner instead.
  - Modify the running launcher script. The launchers are exempt from
    the file copy because Windows doesn't reliably allow rewriting a
    .bat that's currently executing. The launchers are intentionally
    minimal so they don't need to change often anyway.
  - Touch user data. output/, .env, .venv, .git, and similar are skipped
    so an update preserves projects, license cache, and secrets.

Run standalone for testing:

    python _updater.py            # check + apply if newer
    python _updater.py --check    # check only, never download
    python _updater.py --force    # apply even if current >= hosted
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path


VERSION_URL = "https://phantomline.xyz/api/system/version"
DOWNLOAD_URL = "https://phantomline.xyz/download/phantomline-source.zip"
INSTALL_DIR = Path(__file__).resolve().parent
LOCAL_VERSION_FILE = INSTALL_DIR / "VERSION"

# Paths whose contents are NEVER overwritten by an update. Match by any
# component of the relative path so e.g. "output/projects/foo.json" is
# skipped because the first component is "output".
EXCLUDE_DIR_PARTS = {
    "output",          # user-generated content
    ".venv", ".venv312", "venv",  # virtual environments
    ".git", ".github",  # git metadata (shouldn't be in the zip anyway)
    "node_modules",
    "__pycache__",
}

# Specific filenames that get skipped wherever they appear.
EXCLUDE_FILENAMES = {
    ".env", ".env.local", ".env.production", ".env.development",
    # Don't replace the running launcher script under any circumstances —
    # Windows in particular will misbehave if a .bat is rewritten while it's
    # executing. Launchers are intentionally minimal so they rarely change.
    "start-phantomline.bat",
    "start-phantomline.command",
    "start-phantomline.sh",
}


def log(msg: str) -> None:
    """Single print path so launcher scripts can grep / pipe output."""
    print(f"[updater] {msg}", flush=True)


def read_local_version() -> str:
    try:
        v = LOCAL_VERSION_FILE.read_text(encoding="utf-8").strip()
        return v if v else "0.0.0"
    except OSError:
        return "0.0.0"


def semver_tuple(v: str) -> tuple[int, int, int]:
    """Parse semver strings tolerantly. Returns (0,0,0) for unparseable
    input so an unknown version always looks 'older' than a real one."""
    if not v or not isinstance(v, str):
        return (0, 0, 0)
    s = v.strip().lstrip("vV")
    out: list[int] = []
    for p in s.split(".")[:3]:
        try:
            out.append(int(p.split("-")[0].split("+")[0]))
        except (ValueError, IndexError):
            out.append(0)
    while len(out) < 3:
        out.append(0)
    return (out[0], out[1], out[2])


def fetch_hosted_version(timeout: int = 4) -> tuple[str | None, str | None]:
    """Returns (latest_version, error_message). Either may be None.
    Capped at 4s so the launcher doesn't hang when offline or when
    phantomline.xyz is having a bad day."""
    try:
        req = urllib.request.Request(
            VERSION_URL,
            headers={"User-Agent": "phantomline-launcher-updater"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = json.loads(r.read().decode("utf-8"))
        latest = (body.get("version") or "").strip()
        if not latest:
            return None, "Hosted version endpoint returned empty version"
        return latest, None
    except urllib.error.HTTPError as exc:
        return None, f"HTTP {exc.code} from version endpoint"
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return None, f"Network error: {exc}"
    except (ValueError, json.JSONDecodeError) as exc:
        return None, f"Bad response from version endpoint: {exc}"


def download_zip(dest: Path, timeout: int = 90) -> tuple[bool, str | None]:
    """Stream the source zip to dest. Returns (success, error_message).
    Larger timeout because the zip can be ~5-10 MB on a slow connection."""
    try:
        req = urllib.request.Request(
            DOWNLOAD_URL,
            headers={"User-Agent": "phantomline-launcher-updater"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            with open(dest, "wb") as f:
                shutil.copyfileobj(r, f, length=64 * 1024)
        if not dest.exists() or dest.stat().st_size < 1024:
            return False, "Downloaded zip is implausibly small"
        return True, None
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code} downloading source zip"
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return False, f"Network error downloading source zip: {exc}"


def should_skip(rel_path: Path) -> bool:
    """Decide whether a file from the new zip should NOT be copied over
    the install. The rule: skip if any component is in EXCLUDE_DIR_PARTS,
    or if the filename matches an EXCLUDE_FILENAMES entry."""
    for part in rel_path.parts:
        if part in EXCLUDE_DIR_PARTS:
            return True
    if rel_path.name in EXCLUDE_FILENAMES:
        return True
    return False


def apply_update(zip_path: Path) -> tuple[int, str | None]:
    """Extract the zip and copy files over the install. Returns
    (files_copied, error_message). On error returns the partial count
    plus the error so the caller can decide whether to roll back."""
    extract_dir = zip_path.parent / "extracted"
    extract_dir.mkdir(exist_ok=True)
    try:
        with zipfile.ZipFile(zip_path, "r") as z:
            z.extractall(extract_dir)
    except (zipfile.BadZipFile, OSError) as exc:
        return 0, f"Failed to extract zip: {exc}"

    # The source zip's top-level directory is `phantomline/` — that's the
    # actual install root. Confirm it exists; older zips (or a re-zipped
    # source) might lay things out differently.
    src_root = extract_dir / "phantomline"
    if not src_root.exists() or not src_root.is_dir():
        # Some zips might not have the top wrapper. Use extract_dir directly.
        candidate_files = list(extract_dir.glob("*.py"))
        if candidate_files:
            src_root = extract_dir
        else:
            return 0, "Extracted zip layout unrecognized — no phantomline/ root"

    copied = 0
    for src_file in src_root.rglob("*"):
        if src_file.is_dir():
            continue
        rel = src_file.relative_to(src_root)
        if should_skip(rel):
            continue
        dst = INSTALL_DIR / rel
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_file, dst)
            copied += 1
        except (IOError, OSError) as exc:
            return copied, f"Failed to copy {rel}: {exc}"
    return copied, None


def main(argv: list[str]) -> int:
    check_only = "--check" in argv
    force = "--force" in argv

    current = read_local_version()
    log(f"current version: {current}")

    latest, err = fetch_hosted_version()
    if err:
        log(f"couldn't check for updates ({err}) — continuing with current version")
        return 0
    log(f"latest published: {latest}")

    if not force and semver_tuple(latest) <= semver_tuple(current):
        log("you're on the latest version")
        return 0

    if check_only:
        log(f"update available: {current} → {latest} (run without --check to apply)")
        return 0

    log(f"update available: {current} → {latest}")
    log("downloading...")
    tmp = Path(tempfile.mkdtemp(prefix="phantomline-update-"))
    zip_path = tmp / "phantomline-source.zip"
    ok, err = download_zip(zip_path)
    if not ok:
        log(f"download failed: {err} — continuing with current version")
        try:
            shutil.rmtree(tmp, ignore_errors=True)
        except OSError:
            pass
        return 0  # Never block server start because of an update failure.

    log("applying update...")
    copied, err = apply_update(zip_path)

    # Clean up temp regardless of outcome.
    try:
        shutil.rmtree(tmp, ignore_errors=True)
    except OSError:
        pass

    if err:
        log(f"update partially applied ({copied} files) before error: {err}")
        log("the install may be in an inconsistent state — re-download from")
        log("https://phantomline.xyz/download if anything looks off")
        return 0  # Still don't block server start; user can roll back manually.

    log(f"updated {copied} files")
    log(f"now running version {read_local_version()}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

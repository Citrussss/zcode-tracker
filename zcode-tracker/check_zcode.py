#!/usr/bin/env python3
"""
ZCode Windows installer tracker.

Fetches the ZCode download page (https://zcode.z.ai/cn), extracts every
Windows x64 .exe download URL embedded in the server-rendered HTML, picks the
newest by semantic version, and (when a new version is detected) downloads the
installer, verifies it against the official SHA512 published in the matching
latest.yml, records the release in catalog.json, and updates state.json.

Pure standard library — no third-party dependencies, so it runs both locally
and inside a vanilla GitHub Actions Python setup.

Exit codes:
    0  completed (new version downloaded & recorded, or --check-only ran)
    2  no new version (state already up to date)
    3  check-only mode and a new version IS available (not yet downloaded)
    1  hard error (network/parse/verification failure)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

PAGE_URL = "https://zcode.z.ai/cn"
CDN_BASE = "https://cdn-zcode.z.ai/zcode/electron/releases"

# The download page embeds two different URL shapes for the win-x64 installer:
#   * current/leading release:  .../{ver}/windows-x64/ZCode-{ver}-win-x64.exe
#   * archived older releases:  .../{ver}/ZCode-{ver}-win-x64.exe
# Both are captured so the newest version is always found regardless of which
# bucket it happens to live in.
URL_PATTERNS = [
    re.compile(r"releases/(\d+\.\d+\.\d+)/windows-x64/ZCode-\d+\.\d+\.\d+-win-x64\.exe"),
    re.compile(r"releases/(\d+\.\d+\.\d+)/ZCode-\d+\.\d+\.\d+-win-x64\.exe"),
]

# Where state.json / catalog.json live (next to this script by default).
# Overridable via --state-file / --catalog-file (e.g. for NAS scheduled tasks
# that keep state in a fixed location outside the repo).
HERE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(HERE, "state.json")
CATALOG_FILE = os.path.join(HERE, "catalog.json")

HTTP_TIMEOUT = 60
USER_AGENT = "zcode-tracker/1.0 (+github-actions)"

# Exit codes
EXIT_OK = 0
EXIT_NO_CHANGE = 2
EXIT_NEW_AVAILABLE_CHECK_ONLY = 3
EXIT_ERROR = 1


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #

def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(msg: str) -> None:
    print(f"[zcode-tracker] {msg}", flush=True)


def semver_key(version: str):
    """Sort key for 'X.Y.Z' strings. Non-numeric segments fall back to 0."""
    parts = []
    for p in version.split("."):
        try:
            parts.append(int(p))
        except ValueError:
            parts.append(0)
    # pad/truncate to 3 components
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def http_get(url: str, *, headers=None, timeout=HTTP_TIMEOUT):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    return urllib.request.urlopen(req, timeout=timeout)


def http_head_ok(url: str) -> bool:
    """Return True if a HEAD (falling back to GET) returns 2xx for the URL."""
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            return 200 <= resp.status < 300
    except (urllib.error.HTTPError, urllib.error.URLError, ValueError):
        # Some servers reject HEAD; fall back to a ranged GET of 1 byte.
        try:
            req = urllib.request.Request(
                url,
                method="GET",
                headers={"User-Agent": USER_AGENT, "Range": "bytes=0-0"},
            )
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                return 200 <= resp.status < 300
        except Exception:
            return False


def read_json(path: str, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_json(path: str, data) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def set_github_output(key: str, value: str) -> None:
    """Write a key=value line to $GITHUB_OUTPUT if running in Actions."""
    gh_output = os.environ.get("GITHUB_OUTPUT")
    if not gh_output:
        return
    with open(gh_output, "a", encoding="utf-8") as f:
        # Use the heredoc form so values may contain anything.
        delimiter = f"EOF_{hash(key) & 0xFFFFFFFF:08x}"
        f.write(f"{key}<<{delimiter}\n{value}\n{delimiter}\n")


# --------------------------------------------------------------------------- #
# Core steps
# --------------------------------------------------------------------------- #

def fetch_page_versions() -> list[str]:
    """Download the page HTML and return every win-x64 version string found."""
    log(f"Fetching {PAGE_URL} ...")
    with http_get(PAGE_URL) as resp:
        if resp.status != 200:
            raise RuntimeError(f"page returned HTTP {resp.status}")
        html = resp.read().decode("utf-8", errors="replace")

    versions: set[str] = set()
    for pat in URL_PATTERNS:
        versions.update(pat.findall(html))

    if not versions:
        raise RuntimeError(
            "no win-x64 version URLs found in page HTML — the page layout may "
            "have changed; please inspect check_zcode.py URL_PATTERNS"
        )
    found = sorted(versions, key=semver_key)
    log(f"Found {len(found)} win-x64 version(s); newest = {found[-1]}")
    return found


def pick_latest(versions: list[str]) -> str:
    return versions[-1]


def resolve_urls(version: str) -> tuple[str, str, str]:
    """
    Return (exe_url, latest_yml_url, url_format) for a given version.

    Tries the new (arch-subdir) format first; falls back to the legacy flat
    format if the new one is not reachable.
    """
    new_exe = f"{CDN_BASE}/{version}/windows-x64/ZCode-{version}-win-x64.exe"
    legacy_exe = f"{CDN_BASE}/{version}/ZCode-{version}-win-x64.exe"

    if http_head_ok(new_exe):
        yml = f"{CDN_BASE}/{version}/windows-x64/latest.yml"
        return new_exe, yml, "new"

    if http_head_ok(legacy_exe):
        # Legacy releases publish latest.yml at the version root.
        yml = f"{CDN_BASE}/{version}/latest.yml"
        return legacy_exe, yml, "legacy"

    raise RuntimeError(
        f"neither new nor legacy exe URL is reachable for version {version}"
    )


def parse_latest_yml(yml_text: str) -> dict:
    """
    Minimal parser for an electron-builder latest.yml. Extracts sha512, size,
    version, and releaseDate.
    """
    sha512 = re.search(r"(?m)^\s*sha512:\s*(\S+)", yml_text)
    size = re.search(r"(?m)^\s*size:\s*(\d+)", yml_text)
    version = re.search(r"(?m)^version:\s*(\S+)", yml_text)
    release_date = re.search(r"(?m)^releaseDate:\s*['\"]?([^'\"\n]+)", yml_text)
    if not (sha512 and size and version):
        raise RuntimeError("latest.yml missing required fields (sha512/size/version)")
    return {
        "sha512": sha512.group(1),
        "size": int(size.group(1)),
        "version": version.group(1),
        "releaseDate": release_date.group(1).strip() if release_date else None,
    }


def download_with_progress(url: str, dest: str, expected_size: int | None = None) -> None:
    """Stream a URL to disk with a periodic progress log."""
    tmp = dest + ".part"
    with http_get(url) as resp:
        if resp.status != 200:
            raise RuntimeError(f"download HTTP {resp.status} for {url}")
        total = int(resp.headers.get("Content-Length") or expected_size or 0)
        done = 0
        last_log = 0.0
        with open(tmp, "wb") as f:
            while True:
                chunk = resp.read(1024 * 256)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                now = time.time()
                if now - last_log >= 5:
                    if total:
                        pct = done * 100 / total
                        log(f"  downloaded {done:,}/{total:,} bytes ({pct:.1f}%)")
                    else:
                        log(f"  downloaded {done:,} bytes")
                    last_log = now
    os.replace(tmp, dest)
    log(f"  saved -> {dest} ({done:,} bytes)")


def verify_file(path: str, expected_sha512: str, expected_size: int) -> None:
    actual_size = os.path.getsize(path)
    if actual_size != expected_size:
        raise RuntimeError(
            f"size mismatch: file={actual_size} expected={expected_size}"
        )
    h = hashlib.sha512()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    digest = base64_encode(h.digest())
    if digest != expected_sha512:
        raise RuntimeError(
            f"sha512 mismatch:\n  actual   = {digest}\n  expected = {expected_sha512}"
        )
    log(f"  sha512 OK ({digest[:16]}...)")


def base64_encode(digest_bytes: bytes) -> str:
    import base64
    return base64.b64encode(digest_bytes).decode("ascii")


# --------------------------------------------------------------------------- #
# Main flow
# --------------------------------------------------------------------------- #

def load_state() -> dict:
    return read_json(STATE_FILE, {"last_seen_version": None, "last_seen_at": None,
                                 "last_checked_at": None})


def load_catalog() -> dict:
    return read_json(CATALOG_FILE, {"releases": {}})


def main(argv: list[str]) -> int:
    # module-level globals so the override is visible to load_state/load_catalog
    global STATE_FILE, CATALOG_FILE

    parser = argparse.ArgumentParser(description="Track ZCode Windows x64 releases.")
    parser.add_argument("--check-only", action="store_true",
                        help="only detect & compare; do not download")
    parser.add_argument("--download-dir", default="downloads",
                        help="directory for downloaded installers (default: downloads)")
    parser.add_argument("--state-file", default=None,
                        help="path to state.json (default: next to this script). "
                             "Use a fixed absolute path when running outside the repo, "
                             "e.g. on a NAS scheduled task.")
    parser.add_argument("--catalog-file", default=None,
                        help="path to catalog.json (default: next to this script)")
    parser.add_argument("--force", action="store_true",
                        help="download even if state is already current")
    parser.add_argument("--page-url", default=PAGE_URL,
                        help=argparse.SUPPRESS)  # override for testing
    args = parser.parse_args(argv)

    if args.state_file:
        STATE_FILE = os.path.abspath(args.state_file)
    if args.catalog_file:
        CATALOG_FILE = os.path.abspath(args.catalog_file)

    try:
        versions = fetch_page_versions() if args.page_url == PAGE_URL else _fetch_versions_from(args.page_url)
        latest = pick_latest(versions)
    except Exception as e:
        log(f"ERROR detecting version: {e}")
        return EXIT_ERROR

    state = load_state()
    state["last_checked_at"] = now_iso()
    last_seen = state.get("last_seen_version")

    log(f"latest on site  = {latest}")
    log(f"last seen       = {last_seen or '(none — first run)'}")

    is_new = (last_seen is None) or (semver_key(latest) > semver_key(last_seen))

    if not is_new and not args.force:
        log("No new version. Up to date.")
        set_github_output("changed", "false")
        set_github_output("version", str(last_seen))
        # still persist the last_checked_at timestamp
        write_json(STATE_FILE, state)
        return EXIT_NO_CHANGE

    if args.check_only:
        log(f"NEW version available: {latest} (check-only, not downloading).")
        set_github_output("changed", "true")
        set_github_output("version", latest)
        write_json(STATE_FILE, state)
        return EXIT_NEW_AVAILABLE_CHECK_ONLY

    # ---- Full run: resolve URL, fetch manifest, download, verify ----
    try:
        exe_url, yml_url, url_format = resolve_urls(latest)
        log(f"exe url  ({url_format}) = {exe_url}")
        log(f"manifest url            = {yml_url}")

        with http_get(yml_url) as resp:
            yml_text = resp.read().decode("utf-8", errors="replace")
        manifest = parse_latest_yml(yml_text)
        if manifest["version"] != latest:
            log(f"WARNING: manifest version {manifest['version']} != page version {latest}")

        os.makedirs(args.download_dir, exist_ok=True)
        filename = f"ZCode-{latest}-win-x64.exe"
        dest = os.path.join(args.download_dir, filename)

        log(f"Downloading {latest} ...")
        download_with_progress(exe_url, dest, expected_size=manifest["size"])

        log("Verifying integrity ...")
        verify_file(dest, manifest["sha512"], manifest["size"])
    except Exception as e:
        log(f"ERROR during download/verify: {e}")
        return EXIT_ERROR

    # ---- Record release in catalog ----
    catalog = load_catalog()
    catalog["releases"][latest] = {
        "version": latest,
        "platform": "windows-x64",
        "url": exe_url,
        "url_format": url_format,
        "sha512": manifest["sha512"],
        "size": manifest["size"],
        "release_date": manifest["releaseDate"],
        "detected_at": now_iso(),
        "filename": filename,
        "artifact_dir": args.download_dir,
    }
    # keep catalog sorted by version
    ordered = {k: catalog["releases"][k] for k in sorted(catalog["releases"], key=semver_key)}
    catalog["releases"] = ordered
    write_json(CATALOG_FILE, catalog)

    # ---- Update state ----
    state["last_seen_version"] = latest
    state["last_seen_at"] = now_iso()
    write_json(STATE_FILE, state)

    # ---- Emit outputs for the workflow ----
    set_github_output("changed", "true")
    set_github_output("version", latest)
    set_github_output("filename", filename)
    set_github_output("artifact_path", args.download_dir)

    log(f"Done. New version {latest} downloaded, verified and recorded.")
    return EXIT_OK


def _fetch_versions_from(page_url: str) -> list[str]:
    log(f"Fetching {page_url} ...")
    with http_get(page_url) as resp:
        html = resp.read().decode("utf-8", errors="replace")
    versions: set[str] = set()
    for pat in URL_PATTERNS:
        versions.update(pat.findall(html))
    if not versions:
        raise RuntimeError("no win-x64 versions found on overridden page URL")
    found = sorted(versions, key=semver_key)
    log(f"Found {len(found)} win-x64 version(s); newest = {found[-1]}")
    return found


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

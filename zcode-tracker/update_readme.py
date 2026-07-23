#!/usr/bin/env python3
"""
Regenerate the auto-managed sections of the repo-root README.md.

Two sections are rewritten in place (everything else is left untouched):
  - <!-- ZCODE:latest -->  ...  <!-- /ZCODE:latest -->
        a badge + line showing the newest version detected on the site
  - <!-- ZCODE:history --> ...  <!-- /ZCODE:history -->
        a table of every release recorded in catalog.json, newest first,
        with its release date and installer size

The "latest" block reflects a live probe of the download page (so it is
accurate even before a version lands in catalog.json). The "history" table
is built purely from catalog.json (the persisted record).

Pure standard library. Run it from anywhere; it locates the repo root by
walking up for a directory containing a `.github` folder.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone

# Reuse the detector so "latest" stays consistent with check_zcode.py.
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import check_zcode as tracker  # noqa: E402

CATALOG_FILE = os.path.join(HERE, "catalog.json")

LATEST_OPEN = "<!-- ZCODE:latest -->"
LATEST_CLOSE = "<!-- /ZCODE:latest -->"
HISTORY_OPEN = "<!-- ZCODE:history -->"
HISTORY_CLOSE = "<!-- /ZCODE:history -->"


def log(msg: str) -> None:
    print(f"[update-readme] {msg}", flush=True)


def find_repo_root(start: str = HERE) -> str:
    """Walk up until we find a dir containing a .github folder (the repo root)."""
    cur = os.path.abspath(start)
    for _ in range(10):
        if os.path.isdir(os.path.join(cur, ".github")):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    # Fallback: assume the parent of zcode-tracker/ is the root.
    return os.path.dirname(HERE)


def load_catalog() -> dict:
    try:
        with open(CATALOG_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"releases": {}}


def detect_latest() -> str | None:
    """Live-probe the download page for the newest version. Returns None on failure."""
    try:
        versions = tracker.fetch_page_versions()
        return tracker.pick_latest(versions)
    except Exception as e:
        log(f"WARNING: could not probe latest version ({e}); using catalog only")
        return None


def fmt_date(iso: str | None) -> str:
    """Render an ISO date string as YYYY-MM-DD, or '—' if missing/unparseable."""
    if not iso:
        return "—"
    # tolerate trailing Z / fractional seconds
    s = iso.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%d")
    except ValueError:
        return iso[:10] if len(iso) >= 10 else "—"


def fmt_size(n: int | None) -> str:
    if not n:
        return "—"
    mb = n / 1048576
    return f"{mb:.1f} MB"


def render_latest(latest_version: str | None, catalog: dict) -> str:
    lines = [LATEST_OPEN, ""]
    if latest_version:
        v = latest_version
        lines.append(
            f"> 🟢 **Latest version: `ZCode v{v}`** (Windows x64)"
        )
        # If the catalog has a date for it, show it.
        rel = catalog.get("releases", {}).get(v)
        if rel and rel.get("release_date"):
            lines.append(f"> released `{fmt_date(rel['release_date'])}`")
        lines.append("")
        lines.append(
            f"[![version](https://img.shields.io/badge/ZCode-{v}-blue)]"
            f"(https://zcode.z.ai/cn#all-downloads)"
        )
    else:
        # Fall back to newest entry in catalog.
        rels = catalog.get("releases", {})
        if rels:
            newest = sorted(rels, key=tracker.semver_key)[-1]
            lines.append(
                f"> 🟢 **Latest tracked version: `ZCode v{newest}`** (Windows x64)"
            )
        else:
            lines.append("> _No version tracked yet._")
    lines += ["", LATEST_CLOSE]
    return "\n".join(lines)


def render_history(catalog: dict) -> str:
    rels = catalog.get("releases", {})
    lines = [
        HISTORY_OPEN,
        "",
        "| Version | Release date | Size (win-x64) | Filename |",
        "|:--------|:-------------|---------------:|:---------|",
    ]
    if rels:
        # newest first
        for v in sorted(rels, key=tracker.semver_key, reverse=True):
            info = rels[v]
            lines.append(
                f"| `{v}` | {fmt_date(info.get('release_date'))} | "
                f"{fmt_size(info.get('size'))} | "
                f"`{info.get('filename', '—')}` |"
            )
    else:
        lines.append("| _none yet_ | — | — | — |")
    lines += ["", HISTORY_CLOSE]
    return "\n".join(lines)


def replace_block(text: str, open_tag: str, close_tag: str, new_block: str) -> str:
    pattern = re.compile(
        re.escape(open_tag) + r".*?" + re.escape(close_tag), re.DOTALL
    )
    if not pattern.search(text):
        # No markers yet — append the block at the end.
        log(f"NOTE: markers {open_tag!r} not found; appending block at end.")
        return text.rstrip() + "\n\n" + new_block + "\n"
    return pattern.sub(lambda m: new_block, text)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Refresh auto-managed README sections.")
    parser.add_argument("--readme", default=None,
                        help="path to README.md (default: <repo-root>/README.md)")
    parser.add_argument("--no-probe", action="store_true",
                        help="skip live probing; use catalog only")
    args = parser.parse_args(argv)

    repo_root = find_repo_root()
    readme_path = args.readme or os.path.join(repo_root, "README.md")
    log(f"repo root  = {repo_root}")
    log(f"README     = {readme_path}")

    catalog = load_catalog()
    log(f"catalog has {len(catalog.get('releases', {}))} release(s)")

    latest = None if args.no_probe else detect_latest()

    try:
        with open(readme_path, encoding="utf-8") as f:
            text = f.read()
    except FileNotFoundError:
        log(f"README not found at {readme_path}; creating a minimal one.")
        text = "# ZCode Tracker\n"

    before = text
    text = replace_block(text, LATEST_OPEN, LATEST_CLOSE, render_latest(latest, catalog))
    text = replace_block(text, HISTORY_OPEN, HISTORY_CLOSE, render_history(catalog))

    if text == before:
        log("README unchanged.")
        return 0

    with open(readme_path, "w", encoding="utf-8") as f:
        f.write(text)
    log("README updated.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

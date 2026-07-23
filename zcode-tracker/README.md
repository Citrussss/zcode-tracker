# ZCode Tracker

Automatically detects new **ZCode** Windows x64 installer releases, downloads
the `.exe`, verifies its integrity against the publisher's SHA512, uploads it
as a versioned GitHub Actions **build artifact**, and keeps a versioned
catalog of everything seen so far.

It runs on a daily schedule (and can be triggered manually) via GitHub Actions.

---

## How it works

1. **Fetch the download page** — `https://zcode.z.ai/cn` is server-side
   rendered, so the HTML already contains every release's download links
   (no JavaScript execution needed).

2. **Extract versions** — two URL shapes coexist on the page, both captured:
   - current release: `…/releases/{ver}/windows-x64/ZCode-{ver}-win-x64.exe`
   - archived releases: `…/releases/{ver}/ZCode-{ver}-win-x64.exe`

   All matched `{ver}` values are sorted by semantic version and the newest
   is taken as "latest".

3. **Compare to remembered state** — `zcode-tracker/state.json` holds the last
   version we already handled. If the page's latest is newer, we proceed;
   otherwise the run ends with "no change".

4. **Download & verify** — the installer is streamed to
   `zcode-tracker/downloads/`, then checked against the official
   `latest.yml` manifest (`sha512` + `size`) for the same version. A mismatch
   aborts the run.

5. **Record & publish** — the release is appended to
   `zcode-tracker/catalog.json` (keyed + sorted by version), `state.json` is
   updated, both are committed back to the repo, and the `.exe` is uploaded as
   a GitHub Actions artifact named `ZCode-{ver}-win-x64` (retained 90 days).

---

## Files

| Path | Purpose |
|------|---------|
| `zcode-tracker/check_zcode.py` | The tracker script (Python stdlib only). |
| `zcode-tracker/state.json` | Remembers the last version handled (the dedup memory). |
| `zcode-tracker/catalog.json` | Versioned archive of every release seen, with URLs/hashes/metadata. |
| `.github/workflows/zcode-tracker.yml` | The GitHub Actions workflow. |

---

## Run locally

```bash
cd zcode-tracker

# Detect & report only — no download, no state change beyond a timestamp.
# Prints the newest version and exits 3 if a new version is available,
# 2 if state is already up to date.
python check_zcode.py --check-only

# Actually download + verify the newest version (writes to ./downloads).
# First run always downloads because state.json starts at null.
python check_zcode.py

# Force re-download of the current version even if already seen (debug).
python check_zcode.py --force
```

### Exit codes

| Code | Meaning |
|------|---------|
| `0` | Completed — new version downloaded, verified and recorded. |
| `1` | Hard error (network / parse / verification failure). |
| `2` | No new version; state already current. |
| `3` | `--check-only`: a new version **is** available but was not downloaded. |

---

## GitHub Actions

The workflow (`.github/workflows/zcode-tracker.yml`):

- **Triggers**
  - `schedule: 0 2 * * *` — daily at 02:00 UTC (~10:00 Beijing time).
  - `workflow_dispatch` — manual run from the Actions tab, with an optional
    `force` checkbox to re-download the current version.
- **Permissions**: `contents: write` (to commit the updated state/catalog).
- **Concurrency**: one run at a time (`zcode-tracker` group), queued not
  cancelled, so state writes never race.

Artifacts appear under the run's **Artifacts** section, named
`ZCode-{version}-win-x64`.

---

## ⚠️ Security note before pushing to GitHub

This repository's root (`C:\Users\29283\Documents\nas`) contains **hardcoded
credentials** in unrelated files (NAS passwords, the PHP portal password in
`web/config.php`, etc.). Those were **not** created by this tool, but if you
`git init` + push the whole directory to GitHub to run Actions, those secrets
become public.

Recommended options:

1. **Put this tool in its own separate git repo** containing only
   `zcode-tracker/` and `.github/`. (Cleanest.)
2. Or, if using one repo, add a strict `.gitignore` that excludes
   `deploy.py`, `ssh_exec*.py`, `put_php.py`, `web/`, and any `*-result*.txt`
   / `diag*.ps1` files, **and** audit the staged files with
   `git diff --cached` before the first push.

Never push the credentials to a public repository.

---

## Maintenance / troubleshooting

- **"no win-x64 version URLs found in page HTML"** — the download page layout
  changed. Inspect the live HTML and update `URL_PATTERNS` in `check_zcode.py`.
- **SHA512 mismatch** — either the download was truncated (re-run) or the
  publisher republished the version. Re-running resolves the former; the latter
  is reported and the bad file is not recorded.
- **Want ARM64 too?** The script is x64-only by design; add arm64 patterns and
  a second `--arch` branch (the page exposes `windows-arm64` URLs the same way).

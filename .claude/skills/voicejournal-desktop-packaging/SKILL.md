---
name: voicejournal-desktop-packaging
description: 'Use for standalone executable packaging, PyInstaller, dependency isolation, per-user app-data storage, build environment setup, model download placement, and release hardening for VoiceJournal.'
---

# VoiceJournal Desktop Packaging

## When to Use

- Packaging the app as a standalone desktop executable.
- Making decisions about Python dependency isolation.
- Working on build environments, PyInstaller, or asset bundling.
- Defining where models, database files, photos, logs, and caches live.
- Hardening release behavior across macOS, Windows, and Linux.

## Project Rules

- Build from `.venv-build`, not from the system Python.
- Use Python 3.10 unless the project docs are explicitly changed.
- Bundle the Python interpreter and Python dependencies into the packaged app.
- Do not run `pip` on the end user’s machine at runtime.
- Do not write to global `site-packages`.
- Treat models, database, photos, logs, and caches as app data.
- Use `platformdirs` for per-user data locations.

## Packaging Checklist

1. Verify bundled assets work in dev and packaged builds.
2. Verify PyInstaller includes all required assets and runtime files.
3. Verify the app launches without depending on system Python.
4. Verify writable data goes only to per-user app-data directories.
5. Verify model downloads and local data stay outside the app bundle.

## Output Requirements

- State whether the task affects build-time artifacts, runtime data placement, or both.
- State which path is bundled and which path is user-writable.
- Call out any packaging assumption that still needs a spike or release verification.
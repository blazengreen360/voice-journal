# VoiceJournal

VoiceJournal is a local-first desktop journaling app for macOS, Windows, and Linux.
The product is designed around a voice-first flow: the user speaks, the app asks follow-up questions in a local voice, then generates a polished journal entry for review and save.

## Current Status

This repository is in early implementation stage.
A minimal Python project scaffold now exists so the local development and build environments can install the application dependencies, but the production runtime slices are still mostly unimplemented.

Current source of truth:

- `docs/design/Architecture.md`
- `docs/design/UX.md`
- `docs/design/VOICEJOURNAL_WIREFRAMES_FINAL.html`
- `plan.md`

## Product Summary

- Local-first desktop app built with PySide6
- No accounts and no cloud dependency after first-run model setup
- Local speech-to-text, local LLM, local text-to-speech, local storage
- SQLite in phase 1 with repository abstraction for future storage changes
- Standalone packaged app target, not a Python tool the end user installs

Planned default AI stack:

- STT: `faster-whisper`
- LLM: `llama-cpp-python` with official ggml-org Gemma 4 E2B `Q8_0` default, Gemma 4 E4B `Q4_K_M` selectable upgrade, and Gemma 3 1B fallback; the minimum Gemma 4-capable `llama-cpp-python` release is still a release gate
- TTS: `kokoro-onnx`
- VAD: bundled Silero ONNX via `onnxruntime`

## Repository Layout

```text
VoiceJournal/
├── voicejournal.spec            # PyInstaller bundle definition
├── voicejournal/
│   ├── app/                     # namespaced application package scaffold
│   └── assets/                  # bundled themes and model assets
├── tests/                       # initial test scaffold
├── docs/design/                 # current design and wireframe artifacts
├── main.py                      # top-level startup shell
├── pyproject.toml               # dependency and build metadata
├── plan.md                      # implementation sequencing
├── todo.txt                     # working notes
├── .venv/                       # local development environment
├── .venv-build/                 # isolated build environment
└── README.md
```

## Local Setup

This project should use Python 3.10 for local environments.
The machine default `python3` may be newer than the intended app stack.

Development environment:

```bash
source .venv/bin/activate
python -m pip install -e .[dev]
python --version
```

Build environment:

```bash
source .venv-build/bin/activate
python -m pip install -e .[build]
python --version
pyinstaller --version
```

Current local environment notes:

- `.venv` is for development work
- `.venv-build` is for packaging work
- both environments were created with Python 3.10
- the build environment includes `build` and `pyinstaller`
- the project metadata now lives in `pyproject.toml`

Base application dependencies install from `pyproject.toml`.
Use editable installs in `.venv` for development and `.venv-build` for packaging-oriented verification.

## Packaging Direction

VoiceJournal is intended to ship as a standalone desktop executable.

Rules:

- Build from an isolated environment only
- Bundle Python and Python packages into the app
- Do not install Python dependencies globally on the user's machine
- Do not run `pip` at runtime
- Store models, database, photos, logs, and caches in per-user app-data directories
- Do not place the VoiceJournal data folder inside a cloud-synced directory such as Dropbox, iCloud Drive, OneDrive, or Google Drive
- Minimum system requirement: re-validate before `0.1.0` now that official Gemma 4 E2B `Q8_0` is the default artifact; the old 8 GB note is no longer authoritative

The target release artifact is a packaged desktop app, not a Python development checkout.

Bundled asset contract:

- `voicejournal/assets/models/silero_vad.onnx` is vendored from Silero VAD release `v6.2.1`
- the bundled model SHA256 is pinned in `tests/test_vad.py`
- PyInstaller collects `voicejournal.assets` via `voicejournal.spec`
- the macOS app bundle uses PyInstaller's supported onedir `COLLECT -> BUNDLE` path instead of a deprecated onefile windowed bundle

macOS PyInstaller smoke example:

```bash
source .venv-build/bin/activate
pyinstaller --clean --noconfirm voicejournal.spec
smoke_file="$(mktemp /tmp/voicejournal-smoke.XXXXXX)"
VOICEJOURNAL_SMOKE=1 VOICEJOURNAL_SMOKE_OUTPUT="$smoke_file" dist/VoiceJournal.app/Contents/MacOS/VoiceJournal
cat "$smoke_file"
```

## Planned Implementation Order

Implementation should follow the current plan:

1. Run validation spikes for model loading, assets, toolbar behavior, motion behavior, and VAD I/O.
2. Build shared foundations: config, single-instance lock, repository, downloader, registry, and worker infrastructure.
3. Build the first vertical slice: Home -> Session -> Review -> Save -> Return to Home.
4. Add archive continuity and Entry Viewer behavior.
5. Add photo hierarchy, Settings, and lightbox behavior.
6. Finish platform-fit checks, packaging, and release validation.

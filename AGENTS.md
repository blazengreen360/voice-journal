# AGENTS.md

This file follows the open AGENTS.md format: root-level, plain Markdown instructions for coding agents working in this repository.

## Project Overview

VoiceJournal is a desktop journaling app for macOS, Windows, and Linux. It is implemented in Python 3.10 with PySide6 and is intended to ship as a standalone packaged app, not as a Python tool users install.

Core product direction:

- Voice-first journaling flow: speak, receive local follow-up prompts, review a polished entry, and save it locally.
- Local AI stack: `faster-whisper` for STT, `llama-cpp-python` with Gemma GGUF models for LLM, `kokoro-onnx` for TTS, and bundled Silero VAD via `onnxruntime`.
- Local storage: SQLite in phase 1 behind a repository abstraction, with app data stored in per-user application directories.
- Privacy posture: no cloud dependency after first-run model setup, and no runtime `pip` installs.

## Source Of Truth

Read these before making design or implementation decisions:

- `docs/design/Architecture.md` for architecture, dependencies, storage, workers, packaging, model loading, audio, VAD, and startup sequence.
- `docs/design/UX.md` for user experience, screen behavior, acceptance flows, and platform-fit requirements.
- `docs/design/VOICEJOURNAL_WIREFRAMES_FINAL.html` for visual and interaction intent.
- `plan.md` for staged implementation order and current delivery priorities.
- `docs/impl/*.md` for implementation plans when present.

If these sources conflict with existing scaffold code, treat the design docs and plan as the intended direction and surface the mismatch rather than drifting silently.

## Repository Layout

- `voicejournal/app/` contains the application package scaffold.
- `voicejournal/assets/` contains bundled assets, including QSS themes and the pinned Silero VAD ONNX model.
- `tests/` contains the current pytest suite and validation-spike tests.
- `docs/design/` contains current product, UX, architecture, and wireframe sources.
- `docs/impl/` is for implementation-ready plans and architecture notes.
- `voicejournal.spec` defines the PyInstaller bundle.
- `main.py` is the packaged startup shell.

Avoid committing or relying on generated `build/`, `dist/`, `.pytest_cache/`, `__pycache__/`, or local virtualenv contents.

## Environment Setup

Use Python 3.10. The project declares `requires-python = ">=3.10,<3.11"` and the local environments are expected to be Python 3.10.

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
pyinstaller --version
```

Do not use the system Python or global packages for project work. Do not run `pip` at application runtime.

## Common Commands

Run all tests:

```bash
.venv/bin/python -m pytest
```

Run a focused test:

```bash
.venv/bin/python -m pytest tests/test_config.py -q
```

Launch the scaffold app:

```bash
.venv/bin/python main.py
```

Run the Gemma floor spike:

```bash
.venv/bin/python main.py spike gemma-floor --model-path /path/to/gemma-4-E2B-it-Q8_0.gguf --output /tmp/gemma-floor.json
```

Build the macOS PyInstaller app from the build environment:

```bash
.venv-build/bin/pyinstaller --clean --noconfirm voicejournal.spec
```

Run the packaged asset smoke check after building on macOS:

```bash
smoke_file="$(mktemp /tmp/voicejournal-smoke.XXXXXX)"
VOICEJOURNAL_SMOKE=1 VOICEJOURNAL_SMOKE_OUTPUT="$smoke_file" dist/VoiceJournal.app/Contents/MacOS/VoiceJournal
cat "$smoke_file"
```

## Coding Guidelines

- Follow widely adopted Python conventions rather than inventing a project-specific style: PEP 8 for code style, PEP 257 for docstrings, PEP 484/526 for type hints, and Black-compatible formatting.
- Prefer small, direct Python modules with explicit state and failure handling.
- Follow the current package style: `from __future__ import annotations`, type hints, `pathlib.Path`, dataclasses where they clarify data shape, and narrow imports.
- Keep user-facing UI text free of implementation jargon.
- Keep long-running work off the UI thread. Worker signal containers should be `QObject` instances; do not place `Signal()` directly on `QRunnable`.
- Use `importlib.resources.files("voicejournal.assets")` for bundled assets so dev and packaged builds behave consistently.
- Store config, data, models, photos, logs, and caches under `platformdirs` per-user app directories.
- Keep SQLite access repository-abstracted. Connections should use WAL, `synchronous=NORMAL`, `foreign_keys=ON`, and `sqlite3.Row` as described in the architecture docs.
- Preserve the single-instance lock behavior around the journal database lock file.
- Avoid speculative frameworks, broad rewrites, and abstractions that are not needed for the current slice.

## Python Style And Typing

Use the standard professional Python stack as the convention baseline:

- Format code in Black style with an 88-character target line length.
- Keep imports clean and deterministic in the usual stdlib, third-party, local-package order. Prefer import shapes that Ruff/isort would accept.
- Use PEP 8 naming: `snake_case` for functions, methods, variables, and modules; `PascalCase` for classes; `UPPER_SNAKE_CASE` for constants.
- Write docstrings in PEP 257 style for public modules, classes, functions, and non-obvious behavior. Do not add docstrings that only repeat the signature.
- Type public function signatures, dataclass fields, module constants, and values crossing module boundaries.
- Prefer precise standard collection types such as `list[str]`, `dict[str, object]`, `Path`, and `Sequence[str]` over untyped or overly broad values.
- Use `object`, `Protocol`, `TypedDict`, dataclasses, or small domain types when they communicate a real boundary. Avoid `Any` unless integrating with an untyped third-party API or a dynamic Qt surface.
- Return `None` explicitly in annotations for side-effect-only functions.
- Keep optional values explicit with `T | None`, and handle the `None` case near the boundary where it is introduced.
- Use `from __future__ import annotations` in new Python modules to keep annotations lightweight and consistent with the existing codebase.
- Prefer `pathlib.Path` over string path manipulation, and keep filesystem effects visible at the call site.

## Testing And Validation

- Add or update pytest coverage for meaningful behavior changes.
- Prefer focused tests near the changed module, then run the full suite when the change affects shared behavior.
- For bundled assets, keep hash and packaging tests aligned with `tests/test_vad.py` and `voicejournal/app/bundled_assets.py`.
- For CLI changes, cover argument routing and report-writing behavior in `tests/test_cli.py` or nearby tests.
- For packaging-sensitive changes, validate both the dev path and the PyInstaller packaged path when practical.
- Do not treat manual confidence as a substitute for executable checks. If a check cannot be run, report why.

## Packaging And Runtime Constraints

- VoiceJournal ships as a standalone desktop executable with Python and dependencies bundled.
- The app must not depend on a system Python, write to global `site-packages`, or install packages at runtime.
- Models, databases, photos, logs, and caches belong in per-user app-data directories, not in cloud-synced folders.
- `voicejournal/assets/models/silero_vad.onnx` is a pinned bundled asset. Preserve the source tag and SHA256 contract unless intentionally updating the asset and tests.
- On macOS, use the PyInstaller onedir `COLLECT -> BUNDLE` path reflected in `voicejournal.spec`.
- Revalidate minimum hardware requirements and the exact Gemma 4-capable `llama-cpp-python` floor before a `0.1.0` release.

## Working Agreements

- Keep implementation aligned with `plan.md`; current priority is validation spikes, foundations, then the first Home -> Session -> Review -> Save vertical slice.
- Put new implementation plans in `docs/impl/` and keep them concrete, staged, and testable.
- When external library behavior matters, prefer official docs or source material and record the contract relied on.
- The repository may contain user or agent work in progress. Do not revert unrelated changes.
- Use the smallest safe change that advances the requested slice, then validate it.

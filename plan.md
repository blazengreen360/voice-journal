# VoiceJournal Implementation Plan

This file is the execution plan for building VoiceJournal from the current source-of-truth spec:

- `docs/design/UX.md`
- `docs/design/Architecture.md`
- `docs/design/VOICEJOURNAL_WIREFRAMES_FINAL.html`

No new design pass should happen before this plan is attempted. If implementation or spike results falsify a design assumption, update the relevant current file under `docs/design/` and then return here.

---

## Goal

Deliver a buildable desktop app that satisfies the current engineering and UX specs, starting with the smallest end-to-end slice that proves the product loop:

1. Home
2. New Entry
3. Session
4. Review
5. Save entry
6. Return to Home

The plan prioritizes risk reduction first, then shared foundations, then one vertical slice, then the rest of the surface area.

---

## Source Of Truth

Use these rules while implementing:

- `docs/design/Architecture.md` owns architecture, dependencies, data model, worker model, packaging, model loading/downloading, audio, VAD, repository, and startup sequence.
- `docs/design/UX.md` owns screen behavior, shortcuts, cross-platform interaction details, microcopy, visual states, accessibility, and acceptance flows.
- `docs/design/VOICEJOURNAL_WIREFRAMES_FINAL.html` owns the current pass-3 visual direction, archive continuity decisions, finish-path decisions, photo hierarchy direction, settings readiness direction, and the current phase ordering.
- Historical design files remain useful for traceability, but they are not authoritative.
- Do not invent a second source of truth. Resolve ambiguity by updating the relevant current file under `docs/design/`.
- `plan.md` is sequencing, not product truth.

---

## Working Rules

- Do not start with broad UI polish.
- Do not build every screen in parallel.
- Do not add infra that the current specs do not need.
- Do the verification spikes before committing to deep implementation in high-risk areas.
- Convert UX acceptance flows into tests and smoke scripts as early as possible.

### Packaging And Dependency Isolation

- Ship VoiceJournal as a standalone packaged desktop app, not as a Python project the end user installs.
- Build the app only from an isolated build environment or CI environment, never from a global Python installation.
- Bundle the Python interpreter, application code, and required Python packages inside the packaged app.
- Do not run `pip` or install Python packages on the end user's machine at runtime.
- Do not write to global `site-packages` or assume a system Python on the user's `PATH`.
- Store mutable app data only in per-user application directories resolved through `platformdirs`.
- Treat AI models, the database, logs, photos, and caches as app data, not Python dependencies.
- Runtime downloads are allowed only into the app's own user-data directories.
- Packaging must work without admin rights for normal use after install.

---

## Delivery Strategy

### Phase 0 — Validation Spikes

These are short, falsifiable checks for assumptions that can still invalidate the implementation.

1. Gemma 3n load spike
Outcome:
Confirm `llama-cpp-python==0.3.10` can load the default Gemma 3n GGUF on a clean machine, or record the minimum working version and update the current design set under `docs/design/`.

2. Bundled asset loading spike
Outcome:
Confirm `importlib.resources.files("voicejournal.assets")` works in dev and in a packaged PyInstaller build; on macOS, validate the supported onedir `.app` bundle path.

3. macOS unified toolbar spike
Outcome:
Confirm `QToolBar`-based headers actually unify under `setUnifiedTitleAndToolBarOnMac(True)` and still support the button content required by the UX spec.

4. Motion provider spike
Outcome:
Confirm startup probe plus live notifications behave as specified on macOS and Windows; confirm Linux degrades cleanly to read-once.

5. Silero VAD I/O spike
Outcome:
Confirm the ONNX session, `[1,576]` input shape, `[2,1,128]` state carry, 64-sample context, and session-reset behavior work exactly as specified.

Exit criteria:
All five are either confirmed or the specs are updated before main implementation proceeds.

---

### Phase 1 — Shared Foundations

Build the infrastructure that every later slice depends on.

1. App configuration and paths
Modules:
- `app/config.py`
- asset/package path handling under `voicejournal.assets`

Acceptance:
- App can resolve config, data, models, photos, and bundled asset paths across dev and packaged builds.

2. Single-instance and startup safety
Modules:
- `app/single_instance.py`
- startup path in `main.py`

Acceptance:
- Second launch exits cleanly with the specified alert.
- Lock survives process lifetime and is released on exit.

3. Repository and local storage
Modules:
- `app/core/repository.py`
- `app/core/local_repo.py`
- `app/maintenance.py`

Acceptance:
- SQLite uses WAL, `foreign_keys=ON`, and the documented schema.
- Entry, session-turn, and photo persistence all work.
- FTS and orphan-photo cleanup pass tests.

4. Model source registry and downloader
Modules:
- `app/model_sources.py`
- `app/model_downloader.py`
- settings wiring later

Acceptance:
- Resume, cancel, hash verification, atomic install, queueing, and already-installed fast path all work.

5. Worker signal infrastructure
Modules:
- `app/workers/_signals.py`
- worker tests in `tests/test_workers.py`

Acceptance:
- Every worker exposes a `QObject` signal companion.
- Smoke tests confirm `done` and related signals are connectable and emitted.

6. Model registry
Modules:
- `app/model_registry.py`

Acceptance:
- Parallel load path works.
- Synchronous LLM fast-path works.
- Gemma 3n fallback to Gemma 3 1B works.
- Unload timer contract can be exercised from `MainWindow` later.

7. Theme and QSS template system
Modules:
- token/theme loader path described by the UX spec
- bundled QSS templates/assets

Acceptance:
- Light/dark themes render from strict `string.Template` templates.
- Missing token names fail fast at startup.

Exit criteria:
The app can launch, acquire the single-instance lock, open the repo, resolve assets, and initialize model-management primitives without UI dead-ends.

---

### Phase 2 — Core Audio/AI Runtime

Build the engine layer needed for the voice loop before finishing screens.

1. Audio capture and resampling
Modules:
- `app/core/audio.py`

Acceptance:
- 16 kHz capture works directly where available.
- Unsupported device rates fall back to soxr `ResampleStream`.
- Stop drains tail samples with `last=True`.

2. VAD worker
Modules:
- `app/core/vad.py`

Acceptance:
- 512-sample frame handling is exact.
- 64-sample context and `[2,1,128]` state persist correctly.
- `reset_for_new_session()` fully clears state.
- Muting/unmuting behavior around TTS is correct.

3. Transcriber
Modules:
- `app/core/transcriber.py`
- transcribe worker

Acceptance:
- Audio buffers transcribe locally and produce stable text for session turns.

4. TTS engine and playback worker
Modules:
- `app/core/tts.py`
- `app/workers/tts_worker.py`

Acceptance:
- `TTSEngine.synthesize()` wraps `Kokoro.create()` exactly as specified.
- Voice-name mapping, speed clamping, fallback voice, and `done` signaling work.

5. LLM engine
Modules:
- `app/core/llm.py`
- `app/workers/llm_worker.py`

Acceptance:
- Supports the three call types defined by the software design.
- `question` call is non-streaming and respects summarize decision handling.
- `prose` and `metadata` stream as designed.

Exit criteria:
A headless integration path can perform capture → VAD → STT → LLM → TTS using the designed interfaces.

---

### Phase 3 — First Vertical Slice

This is the first product-complete path. Do this before filling in archive refinements and secondary surfaces.

#### Slice A — Home -> Session -> Review -> Save

1. `HomeScreen`
Modules:
- `app/ui/home_screen.py`
- `app/ui/main_window.py`

Acceptance:
- Search input, full month view, selected-day summary row, recent-entry list, and New Entry state machine exist.
- `Cmd/Ctrl+N` routes through the same `NewEntryButton.activate()` logic as the button.
- Model-missing path opens Settings -> Downloads instead of attempting session start.

2. `SessionScreen`
Modules:
- `app/ui/session_screen.py`

Acceptance:
- New session starts cleanly.
- `vad_worker.reset_for_new_session()` is called at session start.
- A visible `Finish Entry` action exists in the main layout.
- `Add Photo` remains visible inline and does not interrupt the flow.
- Trust-state behavior and the minimum 3-user-turn rule follow the current UX and wireframe specs.

3. `ReviewScreen`
Modules:
- `app/ui/review_screen.py`

Acceptance:
- Generated prose loads into the editor.
- Title, mood, tags, and the initial photo shelf surface correctly.
- Primary action language follows the current user-facing states, including `Save Entry` and `Finish and Save` where applicable.
- `Cmd/Ctrl+S` uses the same code path as the toolbar action/button.

4. Save to repository
Modules:
- repo implementation
- review screen save path

Acceptance:
- Entry, session turns, and photo metadata persist correctly.
- Home reflects the new entry after save.

Exit criteria:
A user can complete one journal entry from the Home screen through save without requiring unfinished screens.

---

### Phase 4 — Archive And Viewer

After the first vertical slice works, complete the archive behaviors that make the journal feel continuous.

1. Home archive continuity
Modules:
- `app/ui/home_screen.py`
- `app/ui/main_window.py`

Acceptance:
- Selected month, selected day, and list scroll position are preserved when entering and leaving Viewer.
- Month and year stepping work without leaving the full month view.
- Calendar cells can show multiple-entry density without becoming noisy.

2. Entry Viewer
Modules:
- `app/ui/entry_viewer.py`

Acceptance:
- Read/edit modes work.
- The back action is contextual, such as `Back to April` or the selected day context.
- macOS swipe navigation matches the current gesture contract, including `ScrollMomentum` handling.

Exit criteria:
The archive feels continuous rather than reset-prone, and the Viewer is tied back to the selected month and day.

---

### Phase 5 — Photos, Settings, Lightbox

After archive continuity is working, complete the supporting surfaces that shape the rest of the product feel.

1. Photo hierarchy
Modules:
- `app/ui/review_screen.py`
- `app/ui/entry_viewer.py`

Acceptance:
- Photos are presented as a shelf in Review rather than a loose strip.
- Captioning and ordering behavior exist without crowding the writing area.
- Viewer presents multiple photos as a gallery rather than a flat row.

2. Photo Lightbox
Modules:
- `app/ui/photo_lightbox.py`

Acceptance:
- Previous, next, close, quiet overflow actions, and keyboard navigation all behave as specified.
- The lightbox has no persistent instructional footer copy.

3. Settings Dialog
Modules:
- `app/ui/settings_dialog.py`

Acceptance:
- A top-level readiness summary appears before the detailed rows.
- Appearance live-preview works.
- Download progress, pause/cancel/retry, and voice settings reflect real state.
- Plain-language labels are used throughout the screen.

4. Shortcut system and cheat sheet
Modules:
- `MainWindow`
- shortcut registry path

Acceptance:
- `F1` is canonical.
- `?` alias respects text-input and IME-capable widget rules.
- Current-screen plus global shortcuts render from a single registry.

Exit criteria:
All primary screens and pass-3 supporting surfaces from the current UX and wireframe docs are implemented.

---

### Phase 6 — Platform Fit, Hardening, Packaging

1. Cross-platform behavior pass
Acceptance:
- Home, Review, and Viewer toolbar layouts are locked per OS family.
- macOS toolbar unification, menu-role behavior, swipe, reduce-motion observer, and microphone permission flows work.
- Windows reduce-motion update and shortcut behavior work.
- Linux read-once motion fallback, file reveal behavior, and packaging assumptions hold.

2. Packaging pass
Acceptance:
- PyInstaller build includes assets and bundled Silero ONNX.
- Packaged builds contain their own Python runtime and required Python dependencies.
- App launches cleanly on packaged builds.
- App never invokes `pip`, writes to global `site-packages`, or depends on system Python.
- Models, database, photos, logs, and caches are written only to per-user app-data directories.
- No hidden dev-path assumptions remain.

3. Reliability pass
Acceptance:
- Download interruption, model fallback, hash mismatch, second-instance launch, archive-return state, and repo recovery paths are exercised.

4. Acceptance demo pass
Acceptance:
- Convert the current UX acceptance flows and pass-3 wireframe gates into a repeatable release checklist.

Exit criteria:
The app is usable as a packaged local-first desktop app on all three platforms.

---

## Backlog By Slice

This is the practical build order.

### Slice 0 — Project Skeleton

- package layout
- `pyproject.toml`
- top-level startup shell
- test scaffolding

### Slice 1 — Persistence Core

- config
- single-instance lock
- repository
- maintenance

### Slice 2 — Model Management Core

- model sources
- model downloader
- model registry
- worker signals

### Slice 3 — Runtime AI Core

- audio
- VAD
- transcriber
- TTS
- LLM
- workers

### Slice 4 — App Shell

- `MainWindow`
- navigation shell
- theme/template system
- unload timer ownership

### Slice 5 — Core Product Loop

- Home
- Session
- Review
- Save

### Slice 6 — Archive And Viewer

- month and year stepping
- selected-day continuity
- Entry Viewer
- contextual back path

### Slice 7 — Photos, Settings, Lightbox

- photo shelf and gallery
- Photo Lightbox
- Settings Dialog
- shortcut/help system

### Slice 8 — Platform + Packaging

- packaging
- permissions
- cross-platform verification
- release checklist

---

## UX Surface To Owning Module Map

| UX surface | Primary modules | Acceptance check |
|------------|-----------------|------------------|
| New Entry state machine | `main_window.py`, `home_screen.py`, `settings_dialog.py` | Button and `Cmd/Ctrl+N` always agree on behavior, and model-missing routes into Settings -> Downloads |
| Session finish path | `session_screen.py` | `Finish Entry` is always visible and transitions cleanly into Review |
| Session trust states | `session_screen.py`, `vad.py`, `tts_worker.py` | Voice playback and mic trust cues remain clear while TTS is active |
| Archive continuity | `home_screen.py`, `entry_viewer.py` | Month, day, and list position are preserved when opening and closing entries |
| Save action states | `review_screen.py`, `llm_worker.py`, repo save path | User-facing completion labels stay aligned with actual save behavior |
| Theme/template system | theme loader, bundled QSS templates/assets | Theme loads in dev and packaged builds, missing tokens fail fast |
| Settings readiness UX | `settings_dialog.py`, `model_downloader.py`, `model_sources.py` | Summary card and detail rows reflect real downloader/install state |
| Photo hierarchy and lightbox | `review_screen.py`, `entry_viewer.py`, `photo_lightbox.py` | Photos appear as shelf/gallery, captions and order persist, lightbox stays quiet and clear |
| Entry viewer swipe | `entry_viewer.py` | Horizontal swipe works; momentum and vertical scroll do not trigger nav |
| Shortcut cheat sheet | `main_window.py`, shortcut registry | `F1` always works; `?` never steals text/IME composition |

---

## Test Strategy

### Automated first

1. Repository tests
2. Model downloader tests
3. Worker signal tests
4. Registry tests
5. Audio/VAD tests
6. LLM/TTS wrapper tests
7. Archive continuity state tests

### Integration next

1. Startup path
2. Home -> Session -> Review -> Save path
3. Visible Finish Entry path
4. Model-missing onboarding path
5. LLM reload/unload path
6. Return-to-archive-position path

### Manual smoke last

1. UX acceptance flows from `docs/design/UX.md`
2. Pass-3 archive, session, and surface gates from `docs/design/VOICEJOURNAL_WIREFRAMES_FINAL.html`
3. Cross-platform packaging smoke
4. Permission and offline flows

---

## Release Gates

Do not call the app ready until all of these are true:

1. The Gemma 3n spike is resolved and documented in the current design set.
2. The packaged asset-loading path is verified.
3. The core vertical slice works end to end.
4. The current archive, session, and surface gates from the wireframe deck have been exercised.
5. Packaging smoke tests pass on macOS, Windows, and Linux.

---

## Immediate Next Actions

1. Create the package skeleton and tests directory to match the current `docs/design/` set.
2. Implement `config.py`, `single_instance.py`, and repository scaffolding.
3. Implement `model_sources.py`, `model_downloader.py`, and their tests.
4. Run the Gemma 3n and asset-loading spikes before deep UI work.
5. Build the Home -> Session -> Review -> Save slice with the visible `Finish Entry` path before archive, photo, and settings refinements.

---

## Not Next

These are intentionally deferred until the core loop exists:

- broad visual polish beyond the current spec
- secondary export/import features
- phase-2 Supabase work
- speculative settings not present in the current design docs
- any new redesign cycle without implementation feedback

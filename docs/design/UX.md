# VoiceJournal — Final Design

> Supersedes `docs/design/Software-DESIGN6.md` and `docs/UX_DESIGN_v4.md`.
> Use this document together with `docs/design/Architecture.md`,
> `docs/design/VOICEJOURNAL_WIREFRAMES_FINAL.html`, and `plan.md`.
>
> Historical design files remain in the repository for traceability, but
> they are no longer authoritative. If this document conflicts with any
> earlier UX-specific design file, this document wins.

---

## 0. How To Use This Document

This file is the authoritative UX companion to the architecture and planning
docs. It does not replace the split source-of-truth set defined in `plan.md`.

- Product definition, platform conventions, screen behavior, accessibility,
  and microcopy are defined here.
- Architecture, dependencies, storage, audio, packaging, model management,
  and startup/build sequencing are defined in `docs/design/Architecture.md`.
- `docs/design/VOICEJOURNAL_WIREFRAMES_FINAL.html` owns the current visual
  direction, and `plan.md` owns execution sequencing.

Audience guide:

| Audience | Start here |
|----------|------------|
| Stakeholder / product review | §1 Product, §2 Locked Decisions, §8 Screens |
| Implementer | §3 Stack, §4 Architecture, §5 Runtime Contracts, §6 Design System, §8 Screens, §10 Build Sequence |
| QA / accessibility | §7 Platform + Accessibility, §9 Acceptance, §11 Open Decisions |

---

## 1. Product Definition

VoiceJournal is a local-first desktop app for macOS, Windows, and Linux.
Users speak a journal entry, the app asks follow-up questions in a local
voice, then generates a polished rich-text entry for review and save. The
default path is voice-first and low-friction; editing and browsing exist for
repair, reflection, and export.

Core principles:

1. Local-first. No accounts. No cloud dependency after first-run model setup.
2. Voice is primary; the user should not have to think in the happy path.
3. Trust through visible state. The user must always know whether the mic is
   live and whose turn it is.
4. Respect the host OS. Use native window chrome, menus, dialogs, and
   shortcuts.
5. Accessibility is part of the design, not a later audit.
6. Avoid false promises. Progress labels and controls must reflect real app
   behavior, not optimistic guesses.

---

## 2. Locked Decisions

| Area | Locked decision |
|------|-----------------|
| App form | Cross-platform desktop app built with PySide6 |
| Data model | Local-first, repository-abstracted, SQLite in phase 1 |
| Storage safety | SQLite `WAL`, `synchronous=NORMAL`, `foreign_keys=ON`, single-instance lock |
| Default STT | `faster-whisper` `base.en` |
| Default LLM | Gemma 3n E2B IT Q4_K_M GGUF, with automatic fallback to Gemma 3 1B IT on load failure |
| Default TTS | `kokoro-onnx` via `Kokoro.create(text, voice=..., speed=..., lang="en-us")` |
| Default VAD | Bundled Silero ONNX through `onnxruntime`; no `silero-vad` PyPI dependency |
| Theme system | Strict `string.Template` QSS templates loaded via `importlib.resources.files("voicejournal.assets")` |
| Home primary CTA | Header `NewEntryButton` state machine; `Cmd/Ctrl+N` routes through the same logic |
| Review save policy | Save remains available during prose generation as `Save when ready` |
| Session trust model | Multi-redundant mic-muted signaling during SPEAKING |
| Screen switching | Instant `QStackedWidget` swaps; only in-screen animation is allowed |
| LLM lifecycle | `MainWindow` solely owns the unload timer; only Session/Review boundaries affect it |
| Packaging | Bundled assets and Silero model must work in dev and packaged PyInstaller builds; on macOS, the supported path is an onedir `.app` bundle |

Required pre-release spike:

- Before tagging `0.1.0`, verify that `llama-cpp-python==0.3.10` can load the
  default Gemma 3n GGUF. If it cannot, update the minimum version or demote
  Gemma 3n from default status in this file.

---

## 3. Technology And Package Contract

| Concern | Library / contract | Notes |
|---------|--------------------|-------|
| GUI | `PySide6>=6.7` | Native desktop UI |
| Audio capture | `sounddevice>=0.4.6` | 16 kHz mono float32; resample when needed |
| Resampler | `soxr>=0.4.0` | `ResampleStream`, variable output, flush with `last=True` |
| VAD | `onnxruntime>=1.17.0` + bundled `silero_vad.onnx` | Direct ONNX inference |
| STT | `faster-whisper>=1.0.0` | Local transcription |
| LLM | `llama-cpp-python>=0.3.10` | Gemma 3n default, Gemma 3 1B fallback |
| TTS | `kokoro-onnx>=0.5.0` | Wrapped behind `TTSEngine.synthesize()` |
| Rich text | `QTextEdit` | HTML is canonical storage/display format |
| Markdown ingest | `markdown` | One-time markdown to HTML conversion |
| Storage | stdlib `sqlite3` | Repository pattern + FTS5 |
| Config | `platformdirs` + JSON | OS-appropriate paths |
| macOS motion observer | `pyobjc-framework-Cocoa ; sys_platform == "darwin"` | Optional live motion updates on macOS |

Bundled assets live under `voicejournal/assets/` as a Python sub-package.
Packaging must collect them with the app.

---

## 4. Architecture

### 4.1 Project Structure

Detailed package and module ownership lives in `docs/design/Architecture.md`.
This summary is intentionally limited to the current scaffold surfaces that are
relevant to UX work.

```text
VoiceJournal/
├── main.py
├── pyproject.toml
├── voicejournal.spec
├── README.md
├── plan.md
├── docs/design/
│   ├── Architecture.md
│   ├── UX.md
│   └── VOICEJOURNAL_WIREFRAMES_FINAL.html
└── voicejournal/
  ├── app/
  │   ├── config.py
  │   ├── single_instance.py
  │   ├── bundled_assets.py
  │   ├── packaging_smoke.py
  │   ├── models/
  │   └── ui/
  └── assets/
    ├── __init__.py
    ├── style/
    ├── icons/
    └── models/
├── tests/
```

### 4.2 Startup Sequence

1. Start `QApplication`.
2. Load `AppConfig`.
3. Acquire the single-instance lock before opening the repository.
4. Open the local repository with WAL and foreign keys enabled.
5. Run maintenance tasks: orphan-photo cleanup and FTS maintenance.
6. Build `ModelRegistry`.
7. Show `MainWindow` on Home immediately.
8. Start parallel model loading.
9. Enable or gate UI actions as model readiness changes.

### 4.3 Data Model

Core models:

- `JournalEntry`: title, body HTML, timestamps, mood, tags, voice name.
- `SessionTurn`: per-turn transcript for assistant/user turns.
- `EntryPhoto`: relative file path, caption, sort order.
- `ActiveSession`: in-memory session state only, with `user_turn_count()` and
  prompt-formatting helpers.

IDs are UUID4 strings. Timestamps are UTC ISO-8601.

### 4.4 Storage And Single-Instance Safety

Repository requirements:

- SQLite connection opens with `journal_mode=WAL`, `synchronous=NORMAL`,
  `foreign_keys=ON`, and `row_factory=sqlite3.Row`.
- FTS5 indexes `title`, `body_text`, and `tags`.
- Photos are stored on disk under the app data directory and referenced in
  the database by relative path.
- The app must hold a cross-platform exclusive lock on the data directory's
  database lock file for the full process lifetime.
- The app must warn users not to place the data directory in a cloud-synced
  folder.

### 4.5 Model Sources And Downloader

`MODEL_SOURCES` is the single source of truth for:

- model key
- display name
- final filename
- download URL
- expected SHA256
- expected byte size

Downloader requirements:

- serial queue, one active download at a time
- resume via HTTP `Range`
- `.partial` files preserved on cancel
- SHA256 verification before promotion
- atomic `os.replace()` into final filename
- already-installed fast path if final file exists and hash matches

### 4.6 Model Registry And LLM Lifecycle

`ModelRegistry` owns engine objects and loader workers.

- Whisper, LLM, and TTS load in parallel.
- LLM loader retries once with the fallback model if default load fails.
- `ensure_llm_loaded()` is a synchronous fast-path: return immediately if the
  LLM is already in memory, otherwise schedule load and report readiness later.
- Only `MainWindow` touches the unload timer.
- Only Session and Review navigation boundaries start/stop the unload timer.
- Entry Viewer and Settings do not reset the timer.

---

## 5. Runtime Contracts

### 5.1 Audio Capture And Resampling

- Preferred input: 16 kHz mono float32 with `sounddevice.InputStream`.
- If the chosen input device cannot capture at 16 kHz, capture at the device's
  native sample rate and resample to 16 kHz with `soxr.ResampleStream`.
- `ResampleStream` output block size is variable.
- On stop, flush trailing audio with `resample_chunk(np.zeros(0), last=True)`.

### 5.2 VAD Contract

Silero ONNX exact contract:

| Tensor | Shape | dtype | Meaning |
|--------|-------|-------|---------|
| `input` | `[1, 576]` | `float32` | 64-sample context + 512 new samples |
| `state` | `[2, 1, 128]` | `float32` | persistent recurrent state |
| `sr` | `[1]` | `int64` | always `16000` |
| `output` | `[1, 1]` | `float32` | speech probability |
| `stateN` | `[2, 1, 128]` | `float32` | next recurrent state |

Worker rules:

- Accumulate arbitrary input chunks into exact 512-sample frames.
- Persist both recurrent state and 64-sample context across frames.
- Reset recurrent state, context, and ring buffer at session start.
- Muting during TTS must prevent speech-end detection from assistant audio.

### 5.3 STT Contract

- Whisper transcription is local and non-streaming.
- Transcript preview updates after a completed processing pass.
- Failures return the UI to LISTENING with explicit recovery affordance.

### 5.4 LLM Contract

Three call types:

1. `question`: non-streaming JSON result for next prompt and summarize intent.
2. `prose`: streaming rich-text content for the Review screen body.
3. `metadata`: streaming JSON for title, mood, and tags.

Conversation rules:

- Session must ignore summarize intent until the user has completed at least
  three real turns.
- If the LLM exposes `summarize_probability`, Session uses it to render the
  calibrated TurnLabel text.

### 5.5 TTS Contract

`TTSEngine.synthesize(text, voice_name, speed)` is the only public TTS entry.

- Internally call `Kokoro.create(text, voice=..., speed=..., lang="en-us")`.
- Map UI voice names to Kokoro voice IDs through one centralized map.
- Clamp speed to Kokoro's supported range.
- On missing configured voice ID, fall back to the first available voice and log a warning.

### 5.6 Worker Signal Contract

- Worker signals live on a companion `QObject`, not on `QRunnable` itself.
- Tests must assert `isinstance(worker.signals, QObject)` per worker.
- Tests must smoke-connect at least one signal and verify it fires.

---

## 6. Design System

### 6.1 Token And QSS System

- Tokens are Python data, not CSS custom properties.
- QSS files are `string.Template` templates.
- QSS loads from `voicejournal.assets` through `importlib.resources.files()`.
- `render_qss()` uses strict `Template.substitute(...)`; missing tokens must
  fail fast instead of degrading silently.

### 6.2 Color And Contrast

Semantic tokens define surfaces, text, action colors, mood colors, borders,
 and focus rings.

Important locked values:

- Light accent: `#4858E6`
- Dark accent: `#9AA6FF`
- Dark `text_secondary`: `#A6ACB3`
- Light `speaking_off`: `#7E7BA5`
- Dark `speaking_off`: `#9692C4`
- Dark `accent_quiet`: `rgba(154,166,255,0.24)`

All normal text on primary surfaces must meet WCAG AA. Non-text indicators
must remain distinguishable under deuteranopia and protanopia simulation.

### 6.3 Typography

| Role | Font family | Size / line |
|------|-------------|-------------|
| H1 | platform display / journal title | 28 / 36 |
| H2 | platform UI | 20 / 28 |
| H3 | platform UI | 16 / 24 |
| Body UI | platform UI | 15 / 24 |
| Body journal | platform serif | 17 / 28 |
| Caption | platform UI | 13 / 18 |
| Mono | platform mono | 13 / 20 |

Tabular numerals are best-effort through the Qt 6.7 font feature API.

### 6.4 Spacing, Radius, Elevation

- 4 px spacing grid.
- Minimum hit target: 40 x 40 px.
- Radius tokens: 4, 8, 14, 24, pill.
- Dark mode uses both tinted surfaces and shadow where elevation matters.

### 6.5 Icons

- Stroke-based SVG icons recolored at runtime.
- Required set includes mic, mic-off, mic-strike, calendar, search, image,
  settings, arrows, formatting icons, expand, warning, success, and journal.

---

## 7. Platform, Motion, Accessibility, And Shortcuts

### 7.1 Platform Conventions

- macOS uses native title bar plus unified `QToolBar` headers.
- Windows and Linux use native chrome and no tray presence.
- Settings shortcut is `Cmd+,` on macOS via `MenuRole.PreferencesRole`, and
  `Ctrl+,` elsewhere.
- File pickers are always native.

### 7.2 Motion

- Screen swaps are instant.
- In-screen animation is allowed only when `MOTION_OK` is true.
- `MotionProvider` probes once at startup asynchronously.
- Live updates are supported where cheap: macOS observer, Windows setting
  change filter. Linux degrades to read-once.

### 7.3 Accessibility

- All primary actions must be keyboard reachable.
- Screen-reader announcements must cover Session state changes and save
  outcomes.
- The SPEAKING state must remain identifiable through shape and text, not
  color alone.
- Review photo reorder and tag overflow must be keyboard reachable.

### 7.4 Critical Shortcuts

| Action | macOS | Win/Linux |
|--------|-------|-----------|
| New Entry | `Cmd+N` | `Ctrl+N` |
| Search | `Cmd+F` | `Ctrl+F` |
| Settings | `Cmd+,` | `Ctrl+,` |
| Quit | `Cmd+Q` | `Ctrl+Q` / `Alt+F4` |
| Save Entry | `Cmd+S` | `Ctrl+S` |
| Toggle edit | `Cmd+E` | `Ctrl+E` |
| Reorder focused photo | `Cmd+Shift+Left/Right` | `Ctrl+Shift+Left/Right` |
| Help / cheat sheet | `F1` | `F1` |

`?` is a courtesy alias for the cheat sheet, implemented through an event
filter that avoids text inputs and IME-capable widgets.

### 7.5 macOS EntryViewer Swipe Contract

On macOS only:

- Ignore `NoScrollPhase` and `ScrollMomentum` for swipe navigation.
- Reset accumulator on `ScrollBegin`.
- Navigate only on `ScrollEnd` when all are true:
  - `abs(x) >= 120`
  - `abs(x) >= 2 * abs(y)`
  - gesture duration `< 0.6 s`
- Suppress swipe navigation in edit mode.

---

## 8. Screen Specifications

### 8.1 Home Screen

Purpose: browse recent entries and start a session.

Header:

- implemented as `HomeToolBar(QToolBar)`
- title label
- overflow menu
- settings action
- `NewEntryButton` as a `QWidgetAction`

`NewEntryButton` state machine:

| Models state | Label | Enabled | Action |
|--------------|-------|---------|--------|
| ready | `New Entry` | yes | start session |
| loading | `Loading models…` | no | none |
| missing | `Set up models ->` | yes | open Settings -> Models |
| partial | `New Entry` | no | partial-install banner + Settings affordance |

Additional rules:

- `Cmd/Ctrl+N` must call `NewEntryButton.activate()` so keyboard and button
  behavior can never diverge.
- Calendar uses 44 x 44 visual day cells.
- Entry rows are 88 px tall with explicit density rules for title, snippet,
  date column, mood dot, and optional photo glyph.
- Empty state replaces the browsing layout with a centered hero CTA.

### 8.2 Session Screen

Purpose: conduct the voice conversation.

Top bar:

- cancel button left
- TurnLabel pill center
- voice selector right
- top-bar mic-strike icon visible during SPEAKING

TurnLabel behavior:

- default text: `Turn N`
- if `summarize_probability` is available and `< 0.85`: `Turn N · about M-K to go`
- if `summarize_probability >= 0.85`: `Turn N · last turn or two`

VAD states:

| State | Label | Trust cue |
|-------|-------|-----------|
| LISTENING | `Listening…` | neutral ring |
| RECORDING | `Recording…` | red ring + RMS-driven inner dot |
| PROCESSING | `Transcribing…` | spinner/arc |
| THINKING | `Thinking…` | three-dot indicator |
| SPEAKING | `Speaking · mic off` | mauve ring + 32 px mic-off icon + top-bar mic-strike |

Photo prompt overlay:

- takes focus on show
- `Esc` closes the overlay only
- does not leak `Esc` to Session cancel

Coach-mark:

- shown once per lifetime
- dismissed only after the first user turn with at least three transcript
  words and at least 1.5 seconds duration

Failure fallback:

- if SPEAKING state rendering fails, force mic-muted behavior, show `[MIC OFF]`
  in error color, and log the failure

### 8.3 Review Screen

Purpose: review, lightly edit, attach photos, and save.

Save behavior:

| State | Label | Enabled |
|-------|-------|---------|
| idle | `Save Entry` | yes |
| streaming | `Save when ready` | yes |
| intent captured | `Saving when ready…` | no |
| saving | `Saving…` | no |

Rules:

- the editor is read-only while prose is streaming
- `Cmd/Ctrl+S` is bound to the same save `QAction` as the visible Save button
- if save is requested during streaming, capture intent and auto-save when the
  stream completes

Other controls:

- title is pre-filled from metadata
- mood is visible and editable
- tags support overflow into a keyboard-accessible popover
- photo strip supports keyboard reorder with `Cmd/Ctrl+Shift+Left/Right`

### 8.4 Entry Viewer

Purpose: read and lightly edit saved entries.

Rules:

- header is `EntryViewerToolBar(QToolBar)`
- read mode uses journal typography
- edit mode reveals the formatting toolbar
- left/right navigate only when editor focus does not own the key event
- macOS horizontal swipe follows the strict contract in §7.5

### 8.5 Photo Lightbox

Purpose: maximize a photo.

Hit zones are definitive:

| Zone | Action |
|------|--------|
| close button | close |
| left gutter | previous photo |
| right gutter | next photo |
| photo rect | tooltip on click, context menu on right click |
| photo rect + 16 px buffer | no-op |
| outer scrim | double-click to close |

Context menu:

- reveal in Finder / Explorer / Files
- open in default viewer
- copy

### 8.6 Settings Dialog

Purpose: configure voice, models, audio, appearance, and storage.

Locked behavior:

- left-rail tab layout
- model rows reflect downloader state, progress, queue, cancel, retry, and
  manual placement info
- Audio tab gates mic meter behind `Test microphone`
- Appearance changes live-apply
- one dialog-level privacy footer only

---

## 9. Acceptance And Release Gates

The app is not done until these are true:

1. Home -> Session -> Review -> Save works end to end.
2. Missing-models state routes to Settings -> Models for both button click and
   `Cmd/Ctrl+N`.
3. Session trust states are visibly correct, including SPEAKING mic-muted cues.
4. Review `Save when ready` works for both button click and `Cmd/Ctrl+S`.
5. EntryViewer horizontal swipe works on macOS without phantom navigation from
   vertical scroll momentum.
6. PhotoLightbox double-click scrim close and right-click menu both work.
7. Bundled assets load in a packaged build.
8. Second-instance launch is prevented.
9. Model downloads resume, verify, and install atomically.
10. Cross-platform packaging smoke passes on macOS, Windows, and Linux.

Recommended release smoke flows:

1. Cold launch -> onboarding -> models ready -> first session -> save.
2. Relaunch with missing models -> Home shows `Set up models ->` -> `Cmd/Ctrl+N` opens Settings.
3. Session with photo prompt overlay -> `Esc` skips photo only.
4. Review while prose is streaming -> `Cmd/Ctrl+S` captures save intent.
5. macOS EntryViewer long vertical scroll with momentum -> no accidental prev/next navigation.
6. PyInstaller bundled build -> theme and assets resolve correctly.

---

## 10. Build Sequence

Build in this order:

1. Package skeleton, config, asset packaging path.
2. Single-instance lock.
3. Repository, schema, FTS, maintenance.
4. Model sources and downloader.
5. Model registry and loader workers.
6. Token system, QSS template loading, motion provider.
7. Audio capture, resampler, VAD, transcriber, TTS, LLM.
8. `MainWindow` shell and navigation.
9. Home screen.
10. Session screen.
11. Review screen and save path.
12. Entry Viewer.
13. Photo Lightbox.
14. Settings dialog.
15. Accessibility, packaging, and cross-platform hardening.

The first required vertical slice is:

- Home
- New Entry
- Session
- Review
- Save
- Return to Home

Do not widen UI scope before that slice works.

---

## 11. Open Decisions

These are intentionally left open and are not blockers for `0.1.0`:

1. Transcript correction during the live session.
2. Settings global search.
3. Tablet-optimized layout.
4. Click-to-zoom inside the lightbox.
5. Linux live reduce-motion updates beyond read-once fallback.
6. Single-instance focus IPC to raise an existing window.

Revisit only when implementation feedback or user research justifies it.

---

## 12. Historical Files

Retained for reference only:

- `docs/design/Software-DESIGN6.md`
- `docs/UX_DESIGN.md`
- `docs/UX_DESIGN_v2.md`
- `docs/UX_DESIGN_v3.md`
- `docs/UX_DESIGN_v4.md`
- prior critique files

They are archival. This file is the only build document.
# VoiceJournal — Design Document v6

> Supersedes DESIGN5.md. All issues from `docs/design/critic05.md` are
> addressed and every external API claim has been verified against the
> upstream source (links inline). Use this document together with
> `docs/design/UX.md`, `docs/design/VOICEJOURNAL_WIREFRAMES_FINAL.html`,
> and `plan.md`.

---

## Changelog vs v5

Every `[BLOCKING]`, `[VERIFY]`, and `[GAP]` from critic05 is resolved.

| # | Area | v5 → v6 | critic05 ref |
|---|------|---------|--------------|
| 1 | TTS API | Wrong call: `tts.generate(text, voice_id=…)` → **`tts.create(text, voice=…, speed=…, lang="en-us")`** matching upstream `Kokoro.create()` ([source](https://github.com/thewh1teagle/kokoro-onnx/blob/main/src/kokoro_onnx/__init__.py)). `TTSEngine.synthesize()` wrapper insulates the rest of the app. `config.speech_rate` is now actually wired in. | A1 |
| 2 | LLM default + dep floor | **Gemma 4 E2B IT `Q8_0` default** with **Gemma 4 E4B IT `Q4_K_M`** selectable and hard runtime fallback to Gemma 3 1B IT on `RuntimeError`. Official current llama.cpp docs list `ggml-org/gemma-4-E2B-it-GGUF` and `ggml-org/gemma-4-E4B-it-GGUF`, and upstream contains dedicated Gemma 4 chat-template/parser support. The earlier `llama-cpp-python==0.3.10` clean-room proof only covered Gemma 3n, so the minimum Gemma 4-capable release still has to be re-recorded before tagging 0.1.0. | A2 |
| 3 | Silero VAD ONNX I/O | Underspec'd ("run Silero ONNX") → **full I/O contract**: `input` is `[1, 576]` float32 (= 64-sample carry-over context + 512 new samples at 16 kHz), `state` is `[2, 1, 128]` float32 carried across calls, `sr` is `[1]` int64. Context buffer + state reset on session boundary. 512-sample ring buffer in front of the resampler. Verified against [`utils_vad.py`](https://github.com/snakers4/silero-vad/blob/master/src/silero_vad/utils_vad.py) and [`silero-vad-onnx.cpp`](https://github.com/snakers4/silero-vad/blob/master/examples/cpp/silero-vad-onnx.cpp). | A3 |
| 4 | soxr semantics | "resample_chunk(chunk)" → **document variable output size**, **drain with `resample_chunk(np.zeros(0), last=True)` on stop**, `quality="HQ"`. Verified against [python-soxr docs](https://python-soxr.readthedocs.io/en/stable/). | A4 |
| 5 | silero-vad rationale | "avoid torch" → **"reproducible installer + SHA256-pinned model"** (silero-vad v6.2.1, 2026-02-24, made ONNX runtime optional, weakening the torch-avoidance argument). | A5 |
| 6 | LLM unload timer policy | Implicit → **Explicit**: only `MainWindow` touches the timer; only `SessionScreen` and `ReviewScreen` transitions affect it; `EntryViewer` and `SettingsDialog` do not. Long stays in those screens may force a `~5 s` reload on the next "New Entry" — the existing "Getting ready…" overlay covers it. | B1 |
| 7 | Model downloader | One bullet ("download button + progress") → **`ModelDownloader` spec**: `MODEL_SOURCES` URL+SHA256+size table, HTTP `Range` resume, atomic `.partial → rename`, SHA256 verify before rename, cancel, serialised queue. Stdlib `urllib.request` + `hashlib`; no extra dependency. | B2 |
| 8 | SQLite durability + concurrency | None → **WAL + single-instance lock**: `PRAGMA journal_mode=WAL`, `synchronous=NORMAL`, `foreign_keys=ON` set on every connection. Cross-platform single-instance lock (`fcntl.flock` POSIX, `msvcrt.locking` Windows) on `journal.db.lock`. README warning against cloud-synced data folders. | B3 |
| 9 | Worker signals test | "test cancellation" → **also assert `isinstance(worker.signals, QObject)` per worker + smoke-connect a slot to each signal**. Catches the silent regression where a future refactor moves a `Signal()` directly onto a `QRunnable`. | B4 |

The full v4→v5 changelog from DESIGN5.md still applies and is not repeated here.

---

## What It Is

VoiceJournal is a cross-platform desktop app (Windows / macOS / Linux) built
with Python and PySide6. Users speak journal entries; the app listens
automatically, asks follow-up questions in a natural voice, then generates a
polished rich-text entry from the conversation. Everything runs locally — no
accounts, no internet after first-run model downloads.

---

## Core Principles

- **Local-first.** All processing on device.
- **Voice-native.** Primary interaction is speaking.
- **Zero cognitive load.** The LLM formats the entry; the user just talks.
- **Repository-abstracted storage.** Phase 1 SQLite; Phase 2 Supabase swap with
  no UI changes.

---

## Technology Stack

| Concern | Library | Notes |
|---------|---------|-------|
| GUI | `PySide6` ≥ 6.7 | Qt for Python |
| Audio capture | `sounddevice` ≥ 0.4.6 | 16 kHz mono float32; soxr resample if device unsupported |
| Resampler | `soxr` ≥ 0.4 | Anti-aliased polyphase; variable output block size; drain with `last=True` |
| VAD | `onnxruntime` ≥ 1.17 + bundled `silero_vad.onnx` (~2 MB, SHA256-pinned) | Direct ONNX inference; no `silero-vad` PyPI dep; no torch |
| STT | `faster-whisper` ≥ 1.0 | CTranslate2 |
| LLM | `llama-cpp-python` + Gemma 4 E2B IT `Q8_0` GGUF | In-process; no server; Gemma 4 E4B `Q4_K_M` is selectable; Gemma 3 1B remains the runtime fallback; exact minimum Gemma 4-capable release is re-pinned before `0.1.0` |
| TTS | `kokoro-onnx` ≥ 0.5 + `sounddevice.OutputStream` | `Kokoro.create(text, voice=, speed=, lang=)`; per-utterance OutputStream |
| Editor | `QTextEdit` rich text only | HTML canonical |
| Markdown ingest | `markdown.markdown()` (one-time) | LLM markdown → HTML on ingest |
| Storage (Phase 1) | `sqlite3` stdlib + repository pattern | UUID PKs; FTS5 on `body_text`; **WAL** mode |
| Storage (Phase 2) | Supabase | Same interface |
| Settings | `platformdirs` + JSON | OS-appropriate config dir |
| Photos | Filesystem under app data dir | Relative paths in DB |
| Single-instance lock | stdlib `fcntl` (POSIX) / `msvcrt` (Windows) | On `<data_dir>/journal.db.lock` |

### Default AI Models — verified

| Model | Default | Source | Upgrade | Fallback |
|-------|---------|--------|---------|----------|
| STT | `faster-whisper` `base.en` (~150 MB total directory) | huggingface.co/Systran/faster-whisper-base.en | `small.en`, `medium.en`, `base` | — |
| LLM | **Gemma 4 E2B IT, Q8_0 GGUF** | `ggml-org/gemma-4-E2B-it-GGUF` (official ggml-org GGUF) | Gemma 4 E4B IT Q4_K_M | **Gemma 3 1B IT Q4_K_M GGUF** (auto on load failure) |
| TTS | Kokoro v1.0 int8 ONNX + voices (~115 MB total download) | github.com/thewh1teagle/kokoro-onnx releases | — | — |
| VAD | Silero VAD ONNX (~2 MB, currently pinned from upstream `v6.2.1`) | snakers4/silero-vad releases (`silero_vad.onnx`) | — | `webrtcvad-wheels` |

**Gemma 4 support — upstream confirmed.** Official current `llama.cpp` docs
list `ggml-org/gemma-4-E2B-it-GGUF` and `ggml-org/gemma-4-E4B-it-GGUF` among
the supported pre-quantized models, and current upstream contains dedicated
Gemma 4 chat-template and parser support (`COMMON_CHAT_FORMAT_PEG_GEMMA4`,
`google-gemma-4-31B-it.jinja`, `google-gemma-4-31B-it-interleaved.jinja`).
The official ggml-org E2B repo currently exposes `gemma-4-E2B-it-Q8_0.gguf`
as the practical default artifact, while the official E4B repo exposes
`gemma-4-E4B-it-Q4_K_M.gguf` for the selectable upgrade path. `llama-cpp-python`
tracks upstream `llama.cpp`, auto-loads GGUF chat-template metadata, and
exposes `llama_chat_apply_template`, so Gemma 4 is now the design default.
The earlier clean-room `0.3.10` proof only covered Gemma 3n, so the minimum
Gemma 4-capable `llama-cpp-python` release still must be re-recorded before
tagging `0.1.0`. On `RuntimeError` / unsupported-arch error during load, the
registry **automatically falls back** to Gemma 3 1B IT and shows a one-time
banner; the user can pick either Gemma 4 profile in Settings.

> **Build-time spike (must run before tagging 0.1.0):** in a clean venv,
> start from the current project floor, install a candidate `llama-cpp-python`
> release, then `Llama(model_path=…gemma-4-E2B-it-Q8_0.gguf, n_ctx=4096)`.
> If load fails, bump to the next release and retry until it succeeds, then
> re-record that minimum working release here. Until that spike closes, treat
> the current `llama-cpp-python>=0.3.10` floor as provisional for Gemma 4 and
> keep Gemma 3 1B IT as the mandatory runtime fallback.

**Kokoro voice mapping** — verified against
`huggingface.co/hexgrad/Kokoro-82M/blob/main/VOICES.md`:

```python
# tts.py — single source of truth
VOICE_MAP = {
    "Lucy":  "af_bella",     # American English F, grade A
    "Allen": "am_michael",   # American English M, grade B
}
```

UI shows "Lucy" / "Allen". On missing voice ID at runtime (corrupt model
file), `TTSEngine` falls back to the first available voice and logs a warning.

**First-run download budget:** ~5.24 GB total for the default stack (Whisper
~150 MB directory bundle + Gemma 4 E2B `Q8_0` 4,967,494,592 bytes + Kokoro int8 model
92,361,271 bytes + Kokoro voices 28,214,398 bytes + Silero 2 MB
bundled-not-downloaded). Downloading the selectable Gemma 4 E4B `Q4_K_M`
upgrade adds another 5,335,289,824 bytes.

**Offline / manual placement:** Place artifacts in the exact install layout
shown in Settings → Manual Installation. Single-file sources go directly in
`config.models_dir`; bundle-backed sources go under their install directory
inside `config.models_dir` (for example `faster-whisper-base.en/config.json`
and `kokoro-v1.0/voices-v1.0.bin`). App detects by expected path and skips
download when every required artifact is present.

**Minimum system requirement:** re-validate before `0.1.0`; the earlier 8 GB
note was written against the smaller Gemma 3n default and is no longer treated
as authoritative once the official Gemma 4 E2B `Q8_0` artifact became default.

---

## Directory Structure

```
VoiceJournal/
├── main.py
├── pyproject.toml
├── voicejournal.spec              # PyInstaller — see "Packaging" section
├── README.md
├── plan.md
│
├── voicejournal/
│   ├── app/
│   │   ├── __init__.py
│   │   ├── cli.py                 # startup entrypoint + packaging smoke switch
│   │   ├── config.py              # AppConfig + per-user paths + assets_dir()
│   │   ├── single_instance.py     # cross-platform lock file
│   │   ├── bundled_assets.py      # bundled model metadata + asset helpers
│   │   ├── packaging_smoke.py     # non-UI packaged-asset smoke report
│   │   ├── model_registry.py      # owns engines + reload + sync fast-path
│   │   ├── model_sources.py       # MODEL_SOURCES table (URL/SHA256/size)
│   │   ├── model_downloader.py    # resumable, atomic, verifiable download
│   │   ├── maintenance.py         # Startup orphan-photo reaper + FTS rebuild check
│   │   │
│   │   ├── core/
│   │   │   ├── audio.py           # AudioStream — capture only; pushes to vad_queue
│   │   │   ├── vad.py             # VADWorker — dedicated thread; ONNX runtime
│   │   │   ├── transcriber.py     # Transcriber — faster-whisper wrapper
│   │   │   ├── llm.py             # LLMEngine — llama-cpp-python wrapper
│   │   │   ├── tts.py             # TTSEngine — kokoro-onnx wrapper + OutputStream
│   │   │   ├── repository.py      # Abstract JournalRepository
│   │   │   └── local_repo.py      # SQLite + FTS5 + HTML utilities + reaper hook
│   │   │
│   │   ├── models/entry.py        # JournalEntry, SessionTurn, EntryPhoto, ActiveSession
│   │   │
│   │   ├── ui/
│   │   │   ├── main_window.py     # QStackedWidget + theme event filter + LLM timer
│   │   │   ├── home_screen.py     # Calendar + search (debounced) + entry list
│   │   │   ├── session_screen.py
│   │   │   ├── review_screen.py
│   │   │   ├── entry_viewer.py
│   │   │   ├── photo_lightbox.py
│   │   │   └── settings_dialog.py # Includes downloader UI
│   │   │
│   │   └── workers/
│   │       ├── _signals.py        # WorkerSignals(QObject) — shared signal classes
│   │       ├── whisper_loader.py
│   │       ├── llm_loader.py
│   │       ├── tts_loader.py
│   │       ├── transcribe_worker.py
│   │       ├── llm_worker.py
│   │       └── tts_worker.py
│   └── assets/
│       ├── icons/{mic,waveform,journal}.svg
│       ├── style/{light,dark}.qss
│       └── models/silero_vad.onnx # Bundled at build time (SHA256-pinned)
│
├── tests/
│   ├── conftest.py
│   ├── test_config.py
│   ├── test_vad.py
│   └── test_single_instance.py
```

---

## Data Models (`voicejournal/app/models/entry.py`)

UUID4 string IDs; UTC ISO-8601 timestamps. Unchanged from v5.

```python
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
import uuid

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

def _uuid() -> str:
    return str(uuid.uuid4())


@dataclass
class JournalEntry:
    title: str
    body: str                      # Qt HTML — canonical display format.
    id: str = field(default_factory=_uuid)
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    saved_at: str | None = None
    mood: str | None = None
    tags: list[str] = field(default_factory=list)
    voice_name: str = "Lucy"


@dataclass
class SessionTurn:
    entry_id: str
    turn_index: int
    role: str                      # "assistant" | "user"
    text: str
    id: str = field(default_factory=_uuid)
    created_at: str = field(default_factory=_now)


@dataclass
class EntryPhoto:
    entry_id: str
    file_path: str                 # always relative: photos/{entry_id}/{filename}
    sort_order: int = 0
    caption: str = ""
    id: str = field(default_factory=_uuid)
    created_at: str = field(default_factory=_now)


@dataclass
class ActiveSession:
    """In-memory state. Never written to DB until Save."""
    entry_id: str
    voice_name: str
    turns: list[dict] = field(default_factory=list)

    def add_turn(self, role: str, text: str) -> None:
        self.turns.append({"role": role, "text": text})

    def user_turn_count(self) -> int:
        return sum(1 for t in self.turns if t["role"] == "user")

    def format_for_prompt(self) -> str:
        return "\n".join(
            f"{'Assistant' if t['role'] == 'assistant' else 'User'}: {t['text']}"
            for t in self.turns
        )

    def snapshot(self) -> "ActiveSession":
        return ActiveSession(
            entry_id=self.entry_id,
            voice_name=self.voice_name,
            turns=[dict(t) for t in self.turns],
        )
```

---

## SQLite Schema and Connection Configuration

### Connection pragmas — applied on every connection (`local_repo.py`)

> **NEW in v6 (B3)** — DESIGN5 was silent on these. Without them, default
> rollback-journal mode + foreign-keys-off allows FTS corruption on a hard
> crash and silently breaks `ON DELETE CASCADE` we already rely on.

```python
def _open_connection(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL;")        # crash-safe + concurrent reader
    conn.execute("PRAGMA synchronous=NORMAL;")      # safe with WAL; faster than FULL
    conn.execute("PRAGMA foreign_keys=ON;")         # enforce ON DELETE CASCADE
    conn.execute("PRAGMA temp_store=MEMORY;")
    conn.row_factory = sqlite3.Row
    return conn
```

`LocalRepo.__init__` calls `_open_connection()`; same for any background-thread
helper that opens its own connection (sqlite3 connections are not shareable
across threads).

### Schema

Unchanged from v5 (entries, session_turns, entry_photos, FTS5 virtual table +
3 triggers).

```sql
CREATE TABLE IF NOT EXISTS entries (
    id           TEXT PRIMARY KEY,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    saved_at     TEXT,
    title        TEXT NOT NULL DEFAULT '',
    body         TEXT NOT NULL DEFAULT '',
    body_text    TEXT NOT NULL DEFAULT '',
    body_snippet TEXT NOT NULL DEFAULT '',
    mood         TEXT,
    tags         TEXT NOT NULL DEFAULT '[]',
    voice_name   TEXT NOT NULL DEFAULT 'Lucy'
);
CREATE INDEX IF NOT EXISTS entries_created_idx ON entries(created_at);

CREATE TABLE IF NOT EXISTS session_turns (
    id          TEXT PRIMARY KEY,
    entry_id    TEXT NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
    turn_index  INTEGER NOT NULL,
    role        TEXT NOT NULL,
    text        TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS entry_photos (
    id          TEXT PRIMARY KEY,
    entry_id    TEXT NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
    file_path   TEXT NOT NULL,
    caption     TEXT NOT NULL DEFAULT '',
    sort_order  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS entries_fts USING fts5(
    title, body_text, tags,
    content=entries,
    content_rowid=rowid
);

-- AI / AD / AU triggers identical to v5; omitted for brevity.
```

### HTML utilities, FTS sanitizer, maintenance — unchanged

`_HTMLStripper` (block-tag newlines), `_compress_photo_src` /
`_expand_photo_src` (img.src-only regex), `_sanitize_fts_query`
(quoted-phrase aware), `reap_orphan_photo_dirs`, `maintain_fts` —
all identical to DESIGN5.md.

---

## Single-Instance Lock (`voicejournal/app/single_instance.py`) — NEW

> **NEW in v6 (B3).** Two simultaneous app instances against the same
> `journal.db` corrupt FTS even with WAL. Cloud syncers replacing the file
> mid-session are a documentation problem.

```python
import sys
from pathlib import Path

class AlreadyRunningError(RuntimeError):
    pass

class SingleInstanceLock:
    """Cross-platform exclusive file lock held for the lifetime of the process."""

    def __init__(self, lock_path: Path) -> None:
        self._lock_path = lock_path
        self._fh = None

    def acquire(self) -> None:
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self._lock_path, "a+")
        if sys.platform == "win32":
            import msvcrt
            try:
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as e:
                self._fh.close()
                self._fh = None
                raise AlreadyRunningError(str(e))
        else:
            import fcntl
            try:
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as e:
                self._fh.close()
                self._fh = None
                raise AlreadyRunningError(str(e))

    def release(self) -> None:
        if self._fh is None:
            return
        try:
            if sys.platform == "win32":
                import msvcrt
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        finally:
            self._fh.close()
            self._fh = None
```

`main.py` calls `SingleInstanceLock(config.db_path.with_suffix('.db.lock')).acquire()`
before opening `LocalRepo`. On `AlreadyRunningError`, show:

> **VoiceJournal is already running.** Switch to the existing window.

…and `sys.exit(0)`.

`atexit` registers `release()`. The lock file is never deleted (next launch
re-uses the same path).

**README warning (Storage section):** *"Do not place your VoiceJournal data
folder inside a cloud-synced directory (Dropbox, iCloud Drive, OneDrive,
Google Drive). External processes replacing the database file while the app
is open can corrupt your journal."*

---

## Repository Interface

Unchanged from v5. Includes `import_photo_file` (collision-safe),
`all_entry_ids`, `update_photo_orders`, `entry_days_in_month`
(most-recent-of-day mood wins).

---

## `ModelRegistry` (`voicejournal/app/model_registry.py`) — clarified

```python
class ModelRegistry(QObject):
    whisper_ready = Signal()
    llm_ready     = Signal()      # Re-emitted on every successful ensure_llm_loaded(),
                                  # even if the model was already in memory.
    tts_ready     = Signal()
    all_ready     = Signal()
    load_error    = Signal(str, str)  # (model_name, message)

    def __init__(self, config: AppConfig) -> None: ...

    def load_all(self) -> None:
        """Submit Whisper, LLM, TTS loaders in parallel (QThreadPool).
        LLM loader catches RuntimeError / unsupported-arch errors and
        retries once with the configured fallback model (Gemma 3 1B IT),
        then emits load_error if both fail."""

    def ensure_llm_loaded(self) -> bool:
        """
        Synchronous fast-path:
          - If self._llm is not None: emit llm_ready immediately and return True.
          - Else: submit LLMLoaderWorker, return False; llm_ready emits later.
        """

    def unload_llm(self) -> None: ...

    @property
    def transcriber(self) -> Transcriber | None: ...
    @property
    def llm(self) -> LLMEngine | None: ...
    @property
    def tts(self) -> TTSEngine | None: ...
    @property
    def all_loaded(self) -> bool: ...
```

### Unload timer policy (`MainWindow` owns it) — clarified per critic05 B1

> **NEW explicit rule (B1).** Only `MainWindow` may touch
> `_llm_unload_timer`. `EntryViewer` and `SettingsDialog` **do not** affect
> it. Long stays in those screens may cause the next "New Entry" to take up
> to ~5 s while the LLM reloads — the existing "Getting ready…" overlay
> covers it.

```python
self._llm_unload_timer = QTimer(self)
self._llm_unload_timer.setSingleShot(True)
self._llm_unload_timer.setInterval(180_000)
self._llm_unload_timer.timeout.connect(self._registry.unload_llm)

# State transitions affecting the timer:
def _on_navigate_to_session(self, session): self._llm_unload_timer.stop()        # CANCEL
def _on_navigate_to_review(self, *_):       pass                                  # no change
def _on_navigate_to_home_from_session(self): self._llm_unload_timer.start()       # start fresh
def _on_navigate_to_home_from_review(self):  self._llm_unload_timer.start()       # start fresh
def _on_navigate_to_entry(self, entry_id):  pass                                  # NO CHANGE
def _on_navigate_to_settings(self):         pass                                  # NO CHANGE
```

Behaviour summary:

| Path | Timer outcome | "New Entry" UX |
|------|---------------|----------------|
| Home → New Entry (within 3 min of last session end) | LLM still loaded | Instant |
| Home → New Entry (after timeout, no viewer/settings detour) | LLM unloaded → reload | "Getting ready…" overlay (~5 s) |
| Home → Viewer → Home → New Entry (any duration in viewer) | Timer continued running while in Viewer | Instant or reload-with-overlay depending on elapsed wall time |
| Home → Settings → Home → New Entry | Same as above | Same |

This is correct. Documented so a future implementer doesn't add a second
timer thinking it's missing.

---

## `ModelDownloader` (`voicejournal/app/model_downloader.py`) — NEW

> **NEW in v6 (B2).** DESIGN5 had only "download button + progress"; v6
> specifies the full mechanism. Stdlib only — no extra dependency.

### `MODEL_SOURCES` table (`voicejournal/app/model_sources.py`)

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class ModelArtifact:
    filename: str       # final filename inside the install directory
    url: str            # HTTPS download URL
    sha256: str         # lowercase hex
    size_bytes: int     # expected byte size for resume + progress

@dataclass(frozen=True)
class ModelSource:
    key: str            # config.llm_model / whisper_model value
    display: str        # UI label
    artifacts: tuple[ModelArtifact, ...]
    install_dir: str | None = None   # required for multi-file directory installs

    @property
    def size_bytes(self) -> int:
        return sum(artifact.size_bytes for artifact in self.artifacts)

MODEL_SOURCES: dict[str, ModelSource] = {
    "whisper-base.en": ModelSource(
        key="whisper-base.en",
        display="Whisper base.en",
        install_dir="faster-whisper-base.en",
        artifacts=(
            ModelArtifact(filename="config.json", url="https://huggingface.co/Systran/faster-whisper-base.en/resolve/main/config.json", ...),
            ModelArtifact(filename="model.bin", url="https://huggingface.co/Systran/faster-whisper-base.en/resolve/main/model.bin", ...),
            ModelArtifact(filename="tokenizer.json", url="https://huggingface.co/Systran/faster-whisper-base.en/resolve/main/tokenizer.json", ...),
            ModelArtifact(filename="vocabulary.txt", url="https://huggingface.co/Systran/faster-whisper-base.en/resolve/main/vocabulary.txt", ...),
        ),
    ),
    "gemma-4-E2B-it-Q8_0": ModelSource.single_file(
        key="gemma-4-E2B-it-Q8_0",
        display="Gemma 4 E2B IT (Q8_0)",
        filename="gemma-4-E2B-it-Q8_0.gguf",
        url="https://huggingface.co/ggml-org/gemma-4-E2B-it-GGUF/resolve/main/gemma-4-E2B-it-Q8_0.gguf",
        sha256="<TBD-FILL-AT-RELEASE>",
        size_bytes=4_967_494_592,
    ),
    "gemma-4-E4B-it-Q4_K_M": ModelSource.single_file(
        key="gemma-4-E4B-it-Q4_K_M",
        display="Gemma 4 E4B IT (Q4_K_M)",
        filename="gemma-4-E4B-it-Q4_K_M.gguf",
        url="https://huggingface.co/ggml-org/gemma-4-E4B-it-GGUF/resolve/main/gemma-4-E4B-it-Q4_K_M.gguf",
        sha256="<TBD-FILL-AT-RELEASE>",
        size_bytes=5_335_289_824,
    ),
    "gemma-3-1B-it-Q4_K_M":   ModelSource.single_file(...),    # fallback model
    "kokoro-v1.0":            ModelSource(
        install_dir="kokoro-v1.0",
        artifacts=(
            ModelArtifact(filename="kokoro-v1.0.int8.onnx", url="https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.int8.onnx", ...),
            ModelArtifact(filename="voices-v1.0.bin", url="https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin", ...),
        ),
    ),
    # Silero VAD is NOT in this table — it is bundled in voicejournal/assets/models/.
}
```

`faster-whisper` local loading expects a converted CTranslate2 model directory,
not an arbitrary renamed `model.bin`, so the Speech recognition source installs
the full `faster-whisper-base.en/` directory under `config.models_dir`.

`<TBD-FILL-AT-RELEASE>` values are pinned by the release engineer at tag time
by downloading once and recording the hash; release validation verifies that
each pinned hash still matches the URL before packaging. If a hash drifts
(model author re-uploads), the release is blocked until the table is reviewed.

### Download mechanism

```python
class ModelDownloader(QObject):
    progress    = Signal(str, int, int)  # (key, bytes_done, bytes_total)
    finished    = Signal(str)            # (key)
    failed      = Signal(str, str)       # (key, message)

    def __init__(self, models_dir: Path) -> None:
        self._models_dir = models_dir
        self._cancel = threading.Event()
        self._queue: deque[ModelSource] = deque()
        self._active_thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def enqueue(self, source: ModelSource) -> None:
        """Add to the serialised download queue. Idempotent — already-completed
        downloads (file present + hash match) finish immediately."""
        with self._lock:
            self._queue.append(source)
            if self._active_thread is None or not self._active_thread.is_alive():
                self._cancel.clear()
                self._active_thread = threading.Thread(target=self._run, daemon=True)
                self._active_thread.start()

    def cancel(self) -> None:
        """Cancel the active download. Partial file is kept on disk for resume."""
        self._cancel.set()

    def _run(self) -> None:
        while True:
            with self._lock:
                if not self._queue:
                    return
                source = self._queue.popleft()
            try:
                self._download_one(source)
            except Exception as e:
                self.failed.emit(source.key, str(e))

    def _download_one(self, source: ModelSource) -> None:
        done = 0
        for artifact in source.artifacts:
            done += self._download_artifact(source, artifact, done)
        self.finished.emit(source.key)

    @staticmethod
    def _sha256_file(p: Path) -> str:
        h = hashlib.sha256()
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()
```

### Concurrency policy

- Downloads are **serialised** (one at a time) via the internal queue.
  Settings UI disables further "Download" buttons while a download is active
  except an "Add to queue" affordance for the others, mirroring the queue.
- Cancel applies only to the active download; queued items remain queued.
- Hash failure deletes the `.partial` so the next attempt starts fresh.
- Server `200` (Range ignored) restarts cleanly from byte 0.

### Tests (`tests/test_model_downloader.py`)

- Resume from `.partial` (truncate file, set `Range`, expect concatenation).
- Hash mismatch → file removed, `failed` signal.
- Already-installed-and-valid → instant `finished`, no network.
- Bundle source installs into its expected directory layout.
- Cancel mid-download → partial preserved, next call resumes.
- Cancel applies only to the active download; queued items remain queued.
- Server returns 200 instead of 206 → falls back to clean restart.
- Atomic install uses `os.replace()` after hash verification; focused tests
    cover valid-partial promotion and corrupt-partial recovery, while an explicit
    kill-between-write-and-rename crash probe remains release-hardening follow-up.

---

## Audio Specification (`audio.py` + `vad.py`) — fully specified per critic05 A3+A4

### Capture (`audio.py`)

```python
class AudioStream:
    def __init__(self, config: AppConfig, vad_queue: queue.Queue[np.ndarray]) -> None:
        self._vad_queue = vad_queue
        self._native_rate = 16000
        self._resampler: soxr.ResampleStream | None = None
        try:
            self._stream = sd.InputStream(
                samplerate=16000, channels=1, dtype='float32',
                blocksize=512, callback=self._callback, device=config.audio_device,
            )
        except sd.PortAudioError:
            info = sd.query_devices(config.audio_device or sd.default.device[0])
            self._native_rate = int(info['default_samplerate'])
            self._stream = sd.InputStream(
                samplerate=self._native_rate, channels=1, dtype='float32',
                blocksize=512, callback=self._callback, device=config.audio_device,
            )
            self._resampler = soxr.ResampleStream(
                self._native_rate, 16000, 1, dtype="float32", quality="HQ",
            )

    def _callback(self, indata, frames, time, status):
        # Realtime audio thread — keep work TINY.
        chunk = indata[:, 0].copy()
        try:
            self._vad_queue.put_nowait(chunk)
        except queue.Full:
            pass

    def stop(self) -> None:
        self._stream.stop()
        self._stream.close()
        # Drain soxr internal buffer so the VAD thread's downstream ring buffer
        # sees the trailing samples. The drained chunk is appended to vad_queue;
        # VAD thread will process whatever fits in its ring buffer and discard
        # the rest at thread shutdown.
        if self._resampler is not None:
            tail = self._resampler.resample_chunk(
                np.zeros(0, dtype="float32"), last=True
            )
            if tail.size:
                try:
                    self._vad_queue.put_nowait(tail)
                except queue.Full:
                    pass
```

> **soxr facts (verified):** [`ResampleStream`](https://python-soxr.readthedocs.io/en/stable/)
> output frame count is **variable per call** ("ex. [0, 0, 0, 186, 186, 166,
> 186, …]"); a final call with `last=True` is required to flush the
> internal buffer. `quality="HQ"` is the recommended default; `"VHQ"` is ~2×
> CPU for negligible perceptual gain on VAD-grade audio.

### VAD worker (`vad.py`) — full Silero ONNX I/O contract

> **Verified against** [silero-vad `utils_vad.py` `OnnxWrapper.__call__`](https://github.com/snakers4/silero-vad/blob/master/src/silero_vad/utils_vad.py)
> and [the C++ reference `silero-vad-onnx.cpp`](https://github.com/snakers4/silero-vad/blob/master/examples/cpp/silero-vad-onnx.cpp).

**Model I/O:**

| Tensor name | Shape | dtype | Notes |
|-------------|-------|-------|-------|
| `input` | `[1, 576]` | `float32` | **64-sample context (carry-over from previous call) prepended to 512 new samples.** Initial context is zeros. |
| `state` | `[2, 1, 128]` | `float32` | Combined LSTM h+c state. Persists across calls. **Must be reset to zeros on session boundary.** |
| `sr` | `[1]` | `int64` | `16000`. Allocated once, reused. |
| `output` | `[1, 1]` | `float32` | Speech probability ∈ [0, 1]. |
| `stateN` | `[2, 1, 128]` | `float32` | Feed back as `state` next call. |

**Frame-size requirement:** the model accepts exactly **512 new samples per
call at 16 kHz** (256 at 8 kHz, but VAD runs only at 16 kHz here). Any
non-512 chunk size into the inference call is a bug.

```python
class VADWorker(threading.Thread):
    """
    Reads chunks from vad_queue, accumulates into a 512-sample ring buffer,
    runs Silero VAD ONNX with persistent state + 64-sample context buffer,
    drives state machine, emits speech-end via on_speech_end(io.BytesIO WAV).
    """

    _CONTEXT_SIZE = 64
    _FRAME_SIZE   = 512
    _SAMPLE_RATE  = 16000

    def __init__(
        self,
        vad_queue: queue.Queue[np.ndarray],
        on_speech_end: Callable[[io.BytesIO], None],
        params: VADParams,
        ort_session: onnxruntime.InferenceSession,
    ):
        super().__init__(daemon=True)
        self._q = vad_queue
        self._on_speech_end = on_speech_end
        self._params = params
        self._sess = ort_session
        self._stop = threading.Event()
        self._muted = False

        # Persistent ONNX state — ALL float32, all batch=1
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros((1, self._CONTEXT_SIZE), dtype=np.float32)
        self._sr_tensor = np.array([self._SAMPLE_RATE], dtype=np.int64)

        # Ring buffer accumulating arbitrary-length input chunks into 512-sample frames
        self._ring = np.zeros(0, dtype=np.float32)

    def reset_for_new_session(self) -> None:
        """MUST be called at session boundary. Resets both LSTM state and
        the 64-sample context buffer; otherwise leftover speech state from
        the previous session biases the first frames of the next."""
        self._state.fill(0.0)
        self._context.fill(0.0)
        self._ring = np.zeros(0, dtype=np.float32)

    def set_muted(self, muted: bool) -> None:
        self._muted = muted

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                chunk = self._q.get(timeout=0.1)
            except queue.Empty:
                continue

            self._emit_amplitude(chunk)        # waveform UI — always
            if not self._muted:
                self._ring = np.concatenate([self._ring, chunk])
                while self._ring.size >= self._FRAME_SIZE:
                    frame = self._ring[: self._FRAME_SIZE]
                    self._ring = self._ring[self._FRAME_SIZE :]
                    prob = self._infer(frame)
                    self._drive_state_machine(prob, frame)

    def _infer(self, frame: np.ndarray) -> float:
        """frame is [512] float32. Returns speech probability."""
        # Build [1, 576]: prepend 64-sample context buffer
        x = np.concatenate([self._context[0], frame]).reshape(1, -1)
        out, new_state = self._sess.run(
            None,
            {
                "input": x.astype(np.float32, copy=False),
                "state": self._state,
                "sr":    self._sr_tensor,
            },
        )
        self._state = new_state                 # [2, 1, 128]
        # Update context to the LAST 64 samples of the input we just processed
        self._context = x[:, -self._CONTEXT_SIZE :].copy()
        return float(out[0, 0])
```

**Session boundary call:** `SessionScreen.on_session_start()` calls
`vad_worker.reset_for_new_session()` *before* the first `set_muted(False)`.

**Bundled model:** `voicejournal/assets/models/silero_vad.onnx`, pinned to a specific
release tag. SHA256 of the bundled file is recorded in
`tests/test_vad.py` and asserted at test time. If the file is updated, the
SHA in the test must be updated in the same commit; PR review checks this.

VAD muting in `session_screen.py` (unchanged from v5):

```python
tts_worker.signals.started.connect(lambda: vad_worker.set_muted(True))

def _on_tts_done():
    QTimer.singleShot(300, lambda: vad_worker.set_muted(False))
tts_worker.signals.done.connect(_on_tts_done)
```

---

## VAD Parameters and Visual States

Unchanged from v5. Sensitivity 0.5 (0.1–0.9) is the threshold compared
against `_infer()`'s returned probability.

---

## LLM Prompt Design

Unchanged from v5: `"question"` non-streaming JSON; `"prose"` streaming;
`"metadata"` streaming JSON. `summarize`-substring scan precedes JSON parse
in `"question"` handling. SessionScreen enforces `user_turn_count() >= 3`
before honouring a summarize decision.

---

## TTS Playback (`tts.py` + `tts_worker.py`) — corrected per critic05 A1

> **Wrapper rationale.** The rest of the app calls `TTSEngine.synthesize()`.
> If `kokoro-onnx` renames or restructures `Kokoro.create()` upstream, the
> diff is contained to this one file.

### `TTSEngine` (`voicejournal/app/core/tts.py`)

```python
from kokoro_onnx import Kokoro
import numpy as np

VOICE_MAP = {
    "Lucy":  "af_bella",
    "Allen": "am_michael",
}

class TTSEngine:
    def __init__(self, model_path: str, voices_path: str) -> None:
        self._kokoro = Kokoro(model_path, voices_path)
        # Build display-name → voice-id map, with safe fallback
        self._available = set(self._kokoro.get_voices())

    def synthesize(
        self,
        text: str,
        voice_name: str,
        speed: float = 1.0,
    ) -> tuple[np.ndarray, int]:
        """
        Returns (samples_float32_1d, sample_rate). Uses Kokoro.create() —
        verified signature: create(text, voice=str|ndarray, speed=float,
        lang=str, is_phonemes=bool, trim=bool) -> tuple[ndarray, int].
        """
        voice_id = VOICE_MAP.get(voice_name, "af_bella")
        if voice_id not in self._available:
            log.warning("Voice %s missing; falling back to %s",
                        voice_id, next(iter(self._available)))
            voice_id = next(iter(self._available))
        # Clamp per Kokoro's documented assert (0.5 ≤ speed ≤ 2.0)
        speed = max(0.5, min(2.0, speed))
        return self._kokoro.create(
            text,
            voice=voice_id,
            speed=speed,
            lang="en-us",
        )
```

### `TTSWorker.run()` (`voicejournal/app/workers/tts_worker.py`)

```python
def run(self) -> None:
    try:
        samples, sample_rate = self._engine.synthesize(
            self._text,
            voice_name=self._voice_name,
            speed=self._config.speech_rate,        # ← config.speech_rate now actually used
        )
        if self._cancel.is_set():
            return
        self.signals.started.emit()

        with sd.OutputStream(samplerate=sample_rate, channels=1, dtype="float32") as out:
            block = 1024
            i = 0
            while i < len(samples):
                if self._cancel.is_set():
                    break
                end = min(i + block, len(samples))
                out.write(samples[i:end])
                i = end
    except Exception as e:
        self.signals.error.emit(str(e))
    finally:
        self.signals.done.emit()
```

> **`done` signal contract** (unchanged from v5): fires exactly once per
> `run()`, regardless of outcome. `SessionScreen` connects `done` (not
> `finished`) to its VAD-unmute handler.

`speech_rate` is read from `AppConfig` (Settings → Voice slider 0.5×–2.0×)
and passed through on every call. v5 declared the setting but never wired it.

---

## Editor, UI Screens, Dark/Light Mode, AppConfig

Unchanged from v5 except:

- `SettingsDialog` "Language Model" row gains a **download progress bar**
  driven by `ModelDownloader.progress(key, done, total)` for the active key.
  "Download" / "Cancel" / "Add to queue" buttons reflect downloader state.
- `SettingsDialog` "Voice" row's speech-rate slider is now bound to
  `config.speech_rate` (was decorative in v5).

---

## Threading Model

Unchanged from v5. Worker count, signal pattern, and cancellation policy
identical. The `WorkerSignals(QObject)` companion-object pattern remains the
only way to attach signals to QRunnables.

### Worker tests — strengthened per critic05 B4

`tests/test_workers.py` adds, **for each worker class** (TranscribeWorker,
LLMWorker, TTSWorker, all three loader workers):

```python
def test_signals_is_qobject(worker_factory):
    worker = worker_factory()
    # Catches the silent regression where Signal() is moved onto the QRunnable.
    assert isinstance(worker.signals, QObject)

def test_signal_smoke_fires(qtbot, worker_factory):
    worker = worker_factory()
    fired = []
    worker.signals.done.connect(lambda: fired.append("done"))
    QThreadPool.globalInstance().start(worker)
    qtbot.waitUntil(lambda: fired == ["done"], timeout=5000)
```

The smoke test also doubles as a check that `done` fires on every code path
(success, error, cancel) — re-using the v5 contract.

---

## Startup Sequence

1. `QApplication`; detect OS theme; install theme event filter; apply QSS.
2. `AppConfig.load()`.
3. **`SingleInstanceLock(config.db_path.with_suffix('.db.lock')).acquire()`**.
   On `AlreadyRunningError`: show modal alert, `sys.exit(0)`.
4. `LocalRepo(config.db_path)` — open connection (with WAL/FK pragmas),
   create schema if not exists.
5. `maintenance.reap_orphan_photo_dirs(config.photos_dir, repo)`.
6. `maintenance.maintain_fts(repo)`.
7. `ModelRegistry(config)`; connect signals.
8. Show `MainWindow` → `HomeScreen` immediately.
9. `registry.load_all()`.
10. `HomeScreen` connects `registry.all_ready` → enable "New Entry";
    `registry.load_error` → red dot + banner.

`atexit` registers `lock.release()` and `repo.close()`.

---

## Error Handling

Same table as v5, plus:

| Failure | Response |
|---------|----------|
| Second instance attempted | Modal alert "VoiceJournal is already running"; exit 0. |
| Model download hash mismatch | `ModelDownloader.failed` signal → toast + Settings row marked "Verification failed; retry". Partial deleted. |
| Model download network failure mid-stream | `.partial` preserved; "Resume" button enabled in Settings. |

---

## Logging Policy

Unchanged. Adds two lines:

- `INFO  SingleInstance lock acquired` / `WARN  SingleInstance lock held by another process`
- `INFO  ModelDownloader finished key=<key> bytes=<n> sha256=<…>` /
  `ERROR ModelDownloader hash mismatch key=<key> expected=<a> got=<b>`

No URL is logged with the user's data folder path; only the model key.

---

## `pyproject.toml`

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "voicejournal"
version = "0.1.0a0"
description = "A voice-first local journaling desktop app"
readme = "README.md"
requires-python = ">=3.10,<3.11"
dependencies = [
    "PySide6>=6.7.0",
    "faster-whisper>=1.0.0",
    "llama-cpp-python>=0.3.10",      # Gemma 4 is the design default; exact minimum working release re-pinned by spike before tagging
    "onnxruntime>=1.17.0",            # Direct Silero VAD inference; also used by kokoro-onnx
    "sounddevice>=0.4.6",
    "soundfile>=0.12.1",
    "soxr>=0.4.0",                    # ResampleStream(quality='HQ'); drain with last=True
    "kokoro-onnx>=0.5.0",
    "markdown>=3.6",
    "platformdirs>=4.0.0",
    "numpy>=1.26.0",
    "pyobjc-framework-Cocoa>=10.0; sys_platform == 'darwin'",
]

[project.scripts]
voicejournal = "voicejournal.app.cli:main"

[tool.hatch.build.targets.wheel]
packages = ["voicejournal"]
```

> **No `silero-vad` PyPI dependency.** v6 ships
> `voicejournal/assets/models/silero_vad.onnx` (~2 MB) directly,
> SHA256-pinned in tests.
> The original v5 rationale ("avoid torch") is partially obsolete — silero-vad
> [v6.2.1 (2026-02-24)](https://github.com/snakers4/silero-vad/releases) made
> the ONNX runtime optional. The remaining and primary reasons to bundle
> directly are: (1) reproducible installer size; (2) SHA-pinned offline
> reproducibility; (3) no runtime backend selection logic.
> Release validation should verify `import torch` raises
> `ModuleNotFoundError` in a clean env.

---

## Packaging (`voicejournal.spec`)

`voicejournal.assets` is bundled via `datas`, including the pinned
`silero_vad.onnx`; `torch*` remains excluded; macOS `Info.plist` includes
`NSMicrophoneUsageDescription`; hidden imports cover `onnxruntime.capi`,
`soxr`, and `soundfile`.

On macOS, the supported PyInstaller path is an onedir app bundle:
`EXE(exclude_binaries=True) -> COLLECT(...) -> BUNDLE(VoiceJournal.app)`.
Do not route the `.app` through a onefile windowed executable. Packaged asset
smoke runs against `dist/VoiceJournal.app/Contents/MacOS/VoiceJournal`, while
user-writable data remains outside the bundle under `platformdirs`.

---

## Implementation Order for AI Agents (v6)

1. `voicejournal/app/models/entry.py`.
2. `voicejournal/app/single_instance.py` + `tests/test_single_instance.py`.
3. `voicejournal/app/config.py` — `AppConfig` with `assets_dir()` + theme.
4. `voicejournal/app/core/repository.py` — abstract interface.
5. `voicejournal/app/core/local_repo.py` — `_open_connection()` with WAL/FK pragmas;
   schema, HTML utilities, FTS5, sanitizer, photo handling,
   `import_photo_file`, `fts_integrity_ok` + `rebuild_fts`.
   Tests: `tests/test_local_repo.py`.
6. `voicejournal/app/maintenance.py` + `tests/test_maintenance.py`.
7. `voicejournal/app/model_sources.py` — `MODEL_SOURCES` table (hashes left as `<TBD>` until
   release pinning).
8. `voicejournal/app/model_downloader.py` + `tests/test_model_downloader.py` — resume,
   atomic install, hash verify, cancel, queue.
9. `voicejournal/app/model_registry.py` + `tests/test_registry.py` — sync fast-path; Gemma 4 E2B `Q8_0`→1B
   fallback on RuntimeError; cancel-on-session-start timer.
10. `voicejournal/app/workers/_signals.py`.
11. `voicejournal/app/core/audio.py` — capture-only callback; soxr resampler with `quality="HQ"`,
    drain on stop with `last=True`.
12. `voicejournal/app/core/vad.py` — Silero ONNX with full `[1, 576]` input contract,
    persistent `[2,1,128]` state, 64-sample context buffer, 512-sample ring
    buffer, `reset_for_new_session()`.
    Tests: `tests/test_audio.py`, `tests/test_vad.py` (asserts
    `import torch` raises; asserts bundled ONNX SHA256).
13. `voicejournal/app/core/transcriber.py`.
14. `voicejournal/app/core/tts.py` — `TTSEngine.synthesize(text, voice_name, speed)`
    wrapping `Kokoro.create(text, voice=, speed=, lang="en-us")`.
15. `voicejournal/app/core/llm.py` — three call types per spec.
    Tests: `tests/test_llm.py`.
16. `voicejournal/app/workers/*` — six workers; cancel for each.
    Tests: `tests/test_workers.py` — includes `isinstance(signals, QObject)`
    + signal-fires smoke per worker.
17. `voicejournal/app/ui/settings_dialog.py` — wires `speech_rate`; binds to
    `ModelDownloader`.
18. `voicejournal/app/ui/main_window.py` + `voicejournal/app/ui/home_screen.py` — navigation wiring; LLM unload
    timer policy (only Session/Review boundaries touch it); debounced search.
19. `voicejournal/app/ui/session_screen.py` — full loop; calls `vad_worker.reset_for_new_session()`
    at start; VAD muting via `done`; min-3-turns enforcement; TTS retry budget;
    cancel-path photo dir cleanup.
20. Photo prompt overlay.
21. `voicejournal/app/ui/review_screen.py` — `load(html, metadata, session)`.
22. `voicejournal/app/ui/photo_lightbox.py`.
23. `voicejournal/app/ui/entry_viewer.py` — debounced `update_photo_orders` on drag.
24. `voicejournal/assets/style/light.qss`, `voicejournal/assets/style/dark.qss`,
    `voicejournal/assets/models/silero_vad.onnx`.
25. `main.py` — full startup sequence with `SingleInstanceLock`.
26. `voicejournal.spec` + packaged smoke validation before release.

---

## Top-level decisions locked in v6

1. **Default LLM is Gemma 4 E2B IT (`Q8_0` GGUF)**, with Gemma 4 E4B IT `Q4_K_M` as the
    user-selectable upgrade and automatic runtime fallback to Gemma 3 1B IT on
    load failure. `llama-cpp-python>=0.3.10` remains the current project floor,
    but the minimum Gemma 4-capable release still must be re-recorded by the
    verification spike before tagging 0.1.0.
2. **VAD is `onnxruntime` + bundled, SHA256-pinned `silero_vad.onnx`.** No
   `silero-vad` PyPI dependency. Full ONNX I/O contract (`[1,576]` input
   with 64-sample context carry-over, persistent `[2,1,128]` state,
   `int64` sr) is now part of the design, not implicit.
3. **`"question"` calls are non-streaming.** `"prose"` and `"metadata"` are
   streaming. `summarize`-substring scan precedes JSON parse.
4. **VAD inference runs on a dedicated thread** consuming a `queue.Queue`
   fed by the PortAudio callback. Resampling uses **`soxr` with `quality="HQ"`**
   and is drained with `last=True` on stop.
5. **LLM unload timer is owned solely by `MainWindow`** and is touched only
   on Home↔Session and Home↔Review transitions. Viewer/Settings detours have
   no effect on it.
6. **TTS goes through `TTSEngine.synthesize()` wrapping `Kokoro.create()`**
   with verified `(voice=, speed=, lang=)` keyword arguments.
   `config.speech_rate` is wired in.
7. **SQLite is opened in WAL mode with `foreign_keys=ON`** on every
   connection. A cross-platform single-instance lock prevents concurrent app
   processes against the same database. README warns against cloud-synced
   data folders.
8. **Model downloads are resumable, atomic, and SHA256-verified** via the
   stdlib-only `ModelDownloader`. `MODEL_SOURCES` is the single source of
    truth for URL/hash/size; release validation verifies hashes before
    packaging.

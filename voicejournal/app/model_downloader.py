from __future__ import annotations

from collections import deque
import hashlib
import os
from pathlib import Path
import threading
from urllib import request as urllib_request

from PySide6.QtCore import QObject, Signal

from voicejournal.app.model_sources import ModelArtifact, ModelSource


_READ_CHUNK_SIZE = 1024 * 1024


class ModelDownloader(QObject):
    progress = Signal(str, int, int)
    finished = Signal(str)
    failed = Signal(str, str)

    def __init__(self, models_dir: Path) -> None:
        super().__init__()
        self._models_dir = Path(models_dir)
        self._cancel = threading.Event()
        self._queue: deque[ModelSource] = deque()
        self._active_thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def enqueue(self, source: ModelSource) -> None:
        with self._lock:
            self._queue.append(source)
            if self._active_thread is None or not self._active_thread.is_alive():
                self._cancel.clear()
                self._active_thread = threading.Thread(
                    target=self._run,
                    daemon=True,
                    name="voicejournal-model-downloader",
                )
                self._active_thread.start()

    def cancel(self) -> None:
        self._cancel.set()

    def _run(self) -> None:
        while True:
            with self._lock:
                if not self._queue:
                    self._active_thread = None
                    return
                source = self._queue.popleft()

            try:
                self._download_one(source)
            except Exception as exc:
                self.failed.emit(source.key, str(exc))
            finally:
                self._cancel.clear()

    def _download_one(self, source: ModelSource) -> None:
        self._models_dir.mkdir(parents=True, exist_ok=True)
        bytes_completed = 0
        for artifact in source.artifacts:
            if self._cancel.is_set():
                raise RuntimeError("Cancelled")
            bytes_completed += self._download_artifact(source, artifact, bytes_completed)
        self.finished.emit(source.key)

    def _download_artifact(
        self,
        source: ModelSource,
        artifact: ModelArtifact,
        completed_bytes: int,
    ) -> int:
        final_path = self._final_path(source, artifact)
        partial_path = self._partial_path(source, artifact)

        if final_path.is_file() and self._sha256_file(final_path) == artifact.sha256:
            partial_path.unlink(missing_ok=True)
            return artifact.size_bytes

        if partial_path.is_file() and self._sha256_file(partial_path) == artifact.sha256:
            os.replace(partial_path, final_path)
            return artifact.size_bytes

        existing_bytes = partial_path.stat().st_size if partial_path.exists() else 0
        if partial_path.is_file() and existing_bytes == artifact.size_bytes:
            partial_path.unlink(missing_ok=True)
            existing_bytes = 0
        if artifact.size_bytes > 0 and existing_bytes > artifact.size_bytes:
            partial_path.unlink(missing_ok=True)
            existing_bytes = 0

        request = urllib_request.Request(artifact.url)
        if existing_bytes > 0:
            request.add_header("Range", f"bytes={existing_bytes}-")

        with urllib_request.urlopen(request, timeout=30) as response:
            status = getattr(response, "status", 200)
            mode = "ab" if existing_bytes > 0 and status == 206 else "wb"
            if mode == "wb":
                existing_bytes = 0

            with partial_path.open(mode) as handle:
                done_bytes = existing_bytes
                while True:
                    if self._cancel.is_set():
                        raise RuntimeError("Cancelled")
                    chunk = response.read(_READ_CHUNK_SIZE)
                    if not chunk:
                        break
                    handle.write(chunk)
                    done_bytes += len(chunk)
                    self.progress.emit(
                        source.key,
                        completed_bytes + done_bytes,
                        source.size_bytes,
                    )
                handle.flush()
                os.fsync(handle.fileno())

        if self._cancel.is_set():
            raise RuntimeError("Cancelled")

        actual_sha256 = self._sha256_file(partial_path)
        if actual_sha256 != artifact.sha256:
            partial_path.unlink(missing_ok=True)
            raise RuntimeError(
                f"Hash mismatch (expected {artifact.sha256}, got {actual_sha256})"
            )

        os.replace(partial_path, final_path)
        return artifact.size_bytes

    def _install_root(self, source: ModelSource) -> Path:
        root = self._models_dir if source.install_dir is None else self._models_dir / source.install_dir
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _final_path(self, source: ModelSource, artifact: ModelArtifact) -> Path:
        return self._install_root(source) / artifact.filename

    def _partial_path(self, source: ModelSource, artifact: ModelArtifact) -> Path:
        return self._install_root(source) / f".{artifact.filename}.partial"

    @staticmethod
    def _sha256_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(_READ_CHUNK_SIZE), b""):
                digest.update(chunk)
        return digest.hexdigest()
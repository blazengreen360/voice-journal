from __future__ import annotations

import errno
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
        self._fh = open(self._lock_path, "a+", encoding="utf-8")

        if sys.platform == "win32":
            import msvcrt

            try:
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                self._close_file_handle()
                if _is_lock_contention_error(exc):
                    raise AlreadyRunningError(str(exc)) from exc
                raise
        else:
            import fcntl

            try:
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                self._close_file_handle()
                if _is_lock_contention_error(exc):
                    raise AlreadyRunningError(str(exc)) from exc
                raise

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
            self._close_file_handle()

    def _close_file_handle(self) -> None:
        if self._fh is None:
            return
        self._fh.close()
        self._fh = None


def _is_lock_contention_error(exc: OSError) -> bool:
    contention_errnos = {errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK}
    contention_winerrors = {32, 33}
    winerror = getattr(exc, "winerror", None)

    if winerror is not None:
        return winerror in contention_winerrors

    return exc.errno in contention_errnos

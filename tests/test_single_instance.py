from __future__ import annotations

import errno
import subprocess
import sys
from types import SimpleNamespace

import pytest

from voicejournal.app import single_instance
from voicejournal.app.single_instance import AlreadyRunningError, SingleInstanceLock


def test_single_instance_lock_blocks_second_process(tmp_path) -> None:
    lock_path = tmp_path / "journal.db.lock"
    first_lock = SingleInstanceLock(lock_path)
    first_lock.acquire()

    script = (
        "from pathlib import Path\n"
        "from voicejournal.app.single_instance import AlreadyRunningError, SingleInstanceLock\n"
        "import sys\n"
        "lock = SingleInstanceLock(Path(sys.argv[1]))\n"
        "try:\n"
        "    lock.acquire()\n"
        "except AlreadyRunningError:\n"
        "    raise SystemExit(0)\n"
        "raise SystemExit(1)\n"
    )

    result = subprocess.run([sys.executable, "-c", script, str(lock_path)], check=False)

    first_lock.release()

    assert result.returncode == 0


def test_single_instance_lock_is_released_when_process_exits(tmp_path) -> None:
    lock_path = tmp_path / "journal.db.lock"
    script = (
        "from pathlib import Path\n"
        "from voicejournal.app.single_instance import SingleInstanceLock\n"
        "import sys\n"
        "lock = SingleInstanceLock(Path(sys.argv[1]))\n"
        "lock.acquire()\n"
        "raise SystemExit(0)\n"
    )

    result = subprocess.run([sys.executable, "-c", script, str(lock_path)], check=False)

    assert result.returncode == 0

    second_lock = SingleInstanceLock(lock_path)
    second_lock.acquire()

    assert lock_path.exists()

    second_lock.release()


def test_single_instance_lock_uses_windows_locking(monkeypatch, tmp_path) -> None:
    calls: list[tuple[int, int]] = []

    class FakeMSVCRT:
        LK_NBLCK = 1
        LK_UNLCK = 2

        @staticmethod
        def locking(fd: int, mode: int, size: int) -> None:
            calls.append((mode, size))

    monkeypatch.setattr(single_instance, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setitem(sys.modules, "msvcrt", FakeMSVCRT)

    lock = SingleInstanceLock(tmp_path / "journal.db.lock")
    lock.acquire()
    lock.release()

    assert calls == [(FakeMSVCRT.LK_NBLCK, 1), (FakeMSVCRT.LK_UNLCK, 1)]


def test_single_instance_lock_raises_already_running_for_posix_contention(monkeypatch, tmp_path) -> None:
    class FakeFCNTL:
        LOCK_EX = 1
        LOCK_NB = 2

        @staticmethod
        def flock(fd: int, flags: int) -> None:
            raise BlockingIOError(errno.EWOULDBLOCK, "busy")

    monkeypatch.setattr(single_instance, "sys", SimpleNamespace(platform="darwin"))
    monkeypatch.setitem(sys.modules, "fcntl", FakeFCNTL)

    with pytest.raises(AlreadyRunningError, match="busy"):
        SingleInstanceLock(tmp_path / "journal.db.lock").acquire()


def test_single_instance_lock_propagates_non_contention_posix_error(monkeypatch, tmp_path) -> None:
    class FakeFCNTL:
        LOCK_EX = 1
        LOCK_NB = 2

        @staticmethod
        def flock(fd: int, flags: int) -> None:
            raise PermissionError(errno.EPERM, "forbidden")

    monkeypatch.setattr(single_instance, "sys", SimpleNamespace(platform="darwin"))
    monkeypatch.setitem(sys.modules, "fcntl", FakeFCNTL)

    with pytest.raises(PermissionError, match="forbidden"):
        SingleInstanceLock(tmp_path / "journal.db.lock").acquire()


def test_single_instance_lock_raises_already_running_for_windows_contention(monkeypatch, tmp_path) -> None:
    class FakeWindowsLockError(OSError):
        def __init__(self) -> None:
            super().__init__(errno.EACCES, "busy")
            self.winerror = 33

    class FakeMSVCRT:
        LK_NBLCK = 1
        LK_UNLCK = 2

        @staticmethod
        def locking(fd: int, mode: int, size: int) -> None:
            raise FakeWindowsLockError()

    monkeypatch.setattr(single_instance, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setitem(sys.modules, "msvcrt", FakeMSVCRT)

    with pytest.raises(AlreadyRunningError, match="busy"):
        SingleInstanceLock(tmp_path / "journal.db.lock").acquire()


def test_single_instance_lock_propagates_non_contention_windows_error(monkeypatch, tmp_path) -> None:
    class FakeWindowsPermissionError(OSError):
        def __init__(self) -> None:
            super().__init__(errno.EACCES, "forbidden")
            self.winerror = 5

    class FakeMSVCRT:
        LK_NBLCK = 1
        LK_UNLCK = 2

        @staticmethod
        def locking(fd: int, mode: int, size: int) -> None:
            raise FakeWindowsPermissionError()

    monkeypatch.setattr(single_instance, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setitem(sys.modules, "msvcrt", FakeMSVCRT)

    with pytest.raises(FakeWindowsPermissionError, match="forbidden"):
        SingleInstanceLock(tmp_path / "journal.db.lock").acquire()

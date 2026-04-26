from __future__ import annotations

import subprocess
import sys

from voicejournal.app.single_instance import SingleInstanceLock


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

import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from bgl.errors import LockTimeoutError
from bgl.locks import file_lock, ledger_lock

HOLDER = textwrap.dedent(
    """
    import sys, time
    from pathlib import Path
    from bgl.locks import ledger_lock
    with ledger_lock(Path(sys.argv[1])):
        print("locked", flush=True)
        time.sleep(float(sys.argv[2]))
    """
)


def start_holder(root: Path, seconds: float) -> subprocess.Popen[str]:
    process = subprocess.Popen(
        [sys.executable, "-c", HOLDER, str(root), str(seconds)],
        stdout=subprocess.PIPE,
        text=True,
    )
    assert process.stdout is not None
    assert process.stdout.readline().strip() == "locked"
    return process


class TestLedgerLock:
    def test_creates_lock_file(self, tmp_path: Path) -> None:
        with ledger_lock(tmp_path):
            pass
        assert (tmp_path / ".bgl" / "ledger.lock").exists()

    def test_can_be_taken_again_after_release(self, tmp_path: Path) -> None:
        with ledger_lock(tmp_path):
            pass
        with ledger_lock(tmp_path, timeout=0.5):
            pass

    def test_released_on_exception(self, tmp_path: Path) -> None:
        with pytest.raises(RuntimeError):
            with ledger_lock(tmp_path):
                raise RuntimeError("boom")
        with ledger_lock(tmp_path, timeout=0.5):
            pass

    def test_second_process_waits_then_times_out(self, tmp_path: Path) -> None:
        holder = start_holder(tmp_path, 3.0)
        try:
            started = time.monotonic()
            with pytest.raises(LockTimeoutError):
                with ledger_lock(tmp_path, timeout=0.3):
                    pass
            assert time.monotonic() - started >= 0.3
        finally:
            holder.kill()
            holder.wait()

    def test_second_process_gets_it_after_release(self, tmp_path: Path) -> None:
        holder = start_holder(tmp_path, 0.3)
        try:
            with ledger_lock(tmp_path, timeout=10):
                assert holder.wait(timeout=10) == 0
        finally:
            holder.kill()
            holder.wait()

    def test_killed_holder_releases(self, tmp_path: Path) -> None:
        holder = start_holder(tmp_path, 30.0)
        holder.kill()
        holder.wait()
        with ledger_lock(tmp_path, timeout=5):
            pass


def test_file_lock_on_any_path(tmp_path: Path) -> None:
    path = tmp_path / "locks" / "AL-R001-CG1.lock"
    with file_lock(path, timeout=0.5):
        assert path.exists()

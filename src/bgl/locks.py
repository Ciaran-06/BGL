"""OS file locks for the ledger (R18) and, later, run locks.

The lock is held by the operating system on an open file, so it is released
automatically if the process dies. It is not reentrant: taking the same lock
twice in one process waits on itself until the timeout.
"""

import contextlib
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import BinaryIO, Final

from bgl.errors import LockTimeoutError

LEDGER_LOCK: Final[str] = ".bgl/ledger.lock"
# [DECISION] Wait up to 60 s for a lock, polling every 50 ms, then raise.
# Locks are not reentrant.
DEFAULT_TIMEOUT: Final[float] = 60.0
_POLL_SECONDS: Final[float] = 0.05

if sys.platform == "win32":
    import msvcrt

    def _try_lock(handle: BinaryIO) -> bool:
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True

    def _unlock(handle: BinaryIO) -> None:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _try_lock(handle: BinaryIO) -> bool:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        return True

    def _unlock(handle: BinaryIO) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextlib.contextmanager
def file_lock(path: Path, *, timeout: float = DEFAULT_TIMEOUT) -> Iterator[None]:
    """Hold an exclusive OS lock on path for the duration of the with block.

    Creates path and its folder if needed. Waits up to timeout seconds, then
    raises LockTimeoutError.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+b") as handle:
        deadline = time.monotonic() + timeout
        while not _try_lock(handle):
            if time.monotonic() >= deadline:
                raise LockTimeoutError(str(path), timeout)
            time.sleep(_POLL_SECONDS)
        try:
            yield
        finally:
            _unlock(handle)


def ledger_lock(
    root: Path, *, timeout: float = DEFAULT_TIMEOUT
) -> contextlib.AbstractContextManager[None]:
    """R18: the lock every ledger read, verification and append happens under."""
    return file_lock(root / LEDGER_LOCK, timeout=timeout)
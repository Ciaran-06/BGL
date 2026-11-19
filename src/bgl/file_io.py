"""Durable file writes (R13). Moves bytes only; decides nothing."""

import os
import sys
import tempfile
from pathlib import Path


def fsync_dir(path: Path) -> None:
    """Flush a folder's entry list to disk, so a rename or new file survives a crash.

    Windows cannot open a folder for fsync; there, renames are already durable
    enough for NTFS, so this does nothing.
    """
    if sys.platform == "win32":
        return
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write(path: Path, data: bytes) -> None:
    """Write data to path so a crash leaves either the old file or the new one.

    Writes a temporary file in the same folder, fsyncs it, renames it over path,
    then fsyncs the folder. The folder must already exist.
    """
    folder = path.parent
    fd, temp_name = tempfile.mkstemp(dir=folder, prefix=f".{path.name}.", suffix=".tmp")
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise
    fsync_dir(folder)


def create_exclusive(path: Path, data: bytes, *, mode: int = 0o600) -> None:
    """Create a new file holding data, refusing if it already exists.

    Used for secrets and identities that must never be overwritten. mode sets the
    permissions on POSIX; Windows ignores it.
    """
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _BINARY, mode)
    try:
        written = 0
        while written < len(data):
            written += os.write(fd, data[written:])
        os.fsync(fd)
    finally:
        os.close(fd)
    fsync_dir(path.parent)


_BINARY = getattr(os, "O_BINARY", 0)

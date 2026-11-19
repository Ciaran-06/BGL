"""Reading and appending chain files. Moves bytes only; ledger.py judges them."""

import os
from pathlib import Path

from bgl.config import Project
from bgl.file_io import fsync_dir

_NEWLINE = b"\n"


def ledger_dir(root: Path, project: Project) -> Path:
    """The folder holding every chain file."""
    return root / project.ledger


def chain_path(root: Path, project: Project, code: str) -> Path:
    """The chain file for one writer: <ledger>/<CODE>.jsonl."""
    return ledger_dir(root, project) / f"{code}.jsonl"


def chain_files(root: Path, project: Project) -> list[Path]:
    """Every *.jsonl file directly in the ledger folder, sorted by name."""
    folder = ledger_dir(root, project)
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.glob("*.jsonl") if p.is_file())


def read_chain(path: Path) -> tuple[list[bytes], bytes | None]:
    """Split a chain file into complete lines and a torn tail.

    Lines are returned without their newline. The torn tail is the bytes after
    the last newline, or None if the file ends with a newline. A missing or
    empty file gives ([], None). Nothing is judged here.
    """
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return [], None
    if not data:
        return [], None
    parts = data.split(_NEWLINE)
    tail = parts.pop()
    return parts, (tail if tail else None)


def append_line(path: Path, line: bytes) -> None:
    """Append one complete line and fsync it.

    line must end with exactly one newline and contain no other. Creates the
    ledger folder and the file if needed, and fsyncs the folder when the file is
    new.
    """
    if not line.endswith(_NEWLINE) or _NEWLINE in line[:-1]:
        raise ValueError("append_line takes exactly one line ending in a newline")
    path.parent.mkdir(parents=True, exist_ok=True)
    is_new = not path.exists()
    with open(path, "ab") as handle:
        handle.write(line)
        handle.flush()
        os.fsync(handle.fileno())
    if is_new:
        fsync_dir(path.parent)


def truncate_tail(path: Path, length: int) -> None:
    """Remove the last length bytes of path and fsync it.

    Refuses if those bytes contain a newline, because that would delete a
    complete row (R14). Only ever used to remove a torn tail.
    """
    if length <= 0:
        raise ValueError("truncate_tail needs a positive length")
    with open(path, "r+b") as handle:
        size = handle.seek(0, os.SEEK_END)
        if length > size:
            raise ValueError(f"cannot remove {length} bytes from a {size}-byte file")
        handle.seek(size - length)
        if _NEWLINE in handle.read(length):
            raise ValueError("refusing to remove bytes that include a complete line")
        handle.truncate(size - length)
        handle.flush()
        os.fsync(handle.fileno())

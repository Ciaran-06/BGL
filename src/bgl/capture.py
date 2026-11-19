"""Output capture for counted runs: stdout and stderr, each to its own file.

[DECISION] Capture wraps sys.stdout and sys.stderr, so it sees what Python
writes. Output that native code writes straight to the terminal is not
captured; that is a declared limit, like native file reads (N9).
"""

import os
import sys
from pathlib import Path
from typing import Any, BinaryIO, TextIO


class Tee:
    """A text stream that also copies everything written to a binary file."""

    def __init__(self, original: TextIO, sink: BinaryIO) -> None:
        self._original = original
        self._sink = sink
        self.position = 0  # bytes written to the sink so far

    def write(self, text: str) -> int:
        written = self._original.write(text)
        data = text.encode("utf-8", "backslashreplace")
        self._sink.write(data)
        self.position += len(data)
        return written

    def writelines(self, lines: Any) -> None:
        for line in lines:
            self.write(line)

    def flush(self) -> None:
        self._original.flush()
        self._sink.flush()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._original, name)


class OutputCapture:
    """Copies stdout and stderr to two files between start() and stop()."""

    def __init__(self, stdout_path: Path, stderr_path: Path) -> None:
        self.paths = {"stdout": stdout_path, "stderr": stderr_path}
        self._files: dict[str, BinaryIO] = {}
        self._tees: dict[str, Tee] = {}
        self._originals: dict[str, TextIO] = {}

    def start(self) -> None:
        for name, path in self.paths.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            self._files[name] = open(path, "wb")  # noqa: SIM115 (closed in stop)
            original: TextIO = getattr(sys, name)
            self._originals[name] = original
            self._tees[name] = Tee(original, self._files[name])
            setattr(sys, name, self._tees[name])

    def positions(self) -> dict[str, int]:
        """Bytes captured so far in each stream."""
        return {name: tee.position for name, tee in self._tees.items()}

    def stop(self) -> None:
        """Restore the streams and close the files, flushed to disk."""
        for name, tee in self._tees.items():
            try:
                tee.flush()
            except (OSError, ValueError):
                pass
            # Leave the stream alone if the script replaced it meanwhile.
            if getattr(sys, name) is tee:
                setattr(sys, name, self._originals[name])
        for handle in self._files.values():
            handle.flush()
            os.fsync(handle.fileno())
            handle.close()
        self._tees.clear()
        self._files.clear()

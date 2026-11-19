"""The audit hook: what a run's main process opened and executed (R8, R17, R20, R25).

Python calls an audit hook for every file opened or code object executed
through the runtime. A hook cannot be removed once installed, so one hook is
installed on first use and does nothing unless a run is being watched.

What it cannot see (N9, docs/spikes/audit-hook.md): files opened by native
readers such as pyarrow's own readers and polars, and anything in worker
processes.
"""

import contextlib
import importlib.util
import os
import site
import sys
import sysconfig
from collections.abc import Iterable, Iterator, Mapping
from pathlib import Path
from typing import Any, Final

_CODE_SUFFIXES: Final[tuple[str, ...]] = (".py", ".pyc")
_PROCESS_EVENTS: Final[frozenset[str]] = frozenset(
    {
        "subprocess.Popen",
        "os.system",
        "os.fork",
        "os.forkpty",
        "os.posix_spawn",
        "os.spawn",
        "os.exec",
        "os.startfile",
        "_winapi.CreateProcess",
    }
)
_WORKER_MODULES: Final[tuple[str, ...]] = ("multiprocessing", "concurrent.futures")
# [DECISION] At most this many undeclared files are listed per run; the close
# file says when the list was cut short.
MAX_FILES_RECORDED: Final[int] = 1000


def _norm(path: str) -> str:
    """One spelling per file: symlinks resolved, case folded where the OS does."""
    return os.path.normcase(os.path.realpath(path))


def _as_prefix(path: str) -> str:
    return _norm(path).rstrip(os.sep) + os.sep


def python_prefixes() -> tuple[str, ...]:
    """Folders holding the Python installation and installed packages.

    Files under these are never recorded or blocked: they are the interpreter
    and its libraries, not the run's data or the repository's code.

    [DECISION] This covers the standard library, every site-packages folder and
    the active virtual environment, even when the environment sits inside the
    repository (.venv is the usual layout). Code placed there is treated like
    code outside the repository, which the spec leaves unchecked.
    """
    paths = sysconfig.get_paths()
    folders = {paths[name] for name in ("stdlib", "platstdlib", "purelib", "platlib")}
    folders.update(site.getsitepackages())
    folders.add(site.getusersitepackages())
    if sys.prefix != sys.base_prefix:
        folders.add(sys.prefix)
    return tuple(sorted({_as_prefix(folder) for folder in folders if folder}))


class Watch:
    """What one run may open, and what it was seen to open.

    Paths are compared in normalised form. Records hold repository-relative
    paths with forward slashes for files in the repository, and absolute paths
    otherwise.
    """

    def __init__(
        self,
        root: Path,
        *,
        declared: Iterable[Path] = (),
        guarded: Mapping[Path, str] | None = None,
        dry_allowed: Iterable[Path] | None = None,
    ) -> None:
        self._root_text = os.path.realpath(root)
        self._root_prefix = _as_prefix(os.fspath(root))
        self._python = python_prefixes()
        self._declared = {_norm(os.fspath(path)) for path in declared}
        self._guarded = {
            _norm(os.fspath(path)): kind for path, kind in (guarded or {}).items()
        }
        self._allowed_guarded: set[str] = set()
        self._dry_allowed: set[str] | None = None
        if dry_allowed is not None:
            self._dry_allowed = {_norm(os.fspath(path)) for path in dry_allowed}
            self._dry_allowed.add(_norm(os.devnull))
        self._exec_seen: set[str] = set()
        self.paused = False
        self.files_opened: dict[str, None] = {}  # insertion-ordered set
        self.files_truncated = False
        self.code_files: dict[str, None] = {}
        self.violations: list[str] = []
        self.processes: dict[str, int] = {}
        self.hook_errors = 0

    def allow_guarded(self, path: Path) -> None:
        """Let the run open one holdout file (run.holdout was called for it)."""
        self._allowed_guarded.add(_norm(os.fspath(path)))

    def _relative(self, real: str) -> str | None:
        """Repository-relative path for a real path inside the repository."""
        if os.path.normcase(real).startswith(self._root_prefix):
            return real[len(self._root_prefix) :].replace(os.sep, "/")
        return None

    def _in_python(self, key: str) -> bool:
        return key.startswith(self._python)

    def _note_code(self, real: str) -> None:
        if real.endswith(".pyc"):
            try:
                real = importlib.util.source_from_cache(real)
            except ValueError:
                pass  # a .pyc outside __pycache__: keep it, it has no source
        relative = self._relative(real)
        if relative is not None:
            self.code_files[relative] = None

    def on_open(self, path: object) -> None:
        if isinstance(path, int) or self.paused:
            return  # a file descriptor, or BGL's own work
        try:
            text = os.fsdecode(path)  # type: ignore[arg-type]
        except TypeError:
            return
        real = os.path.realpath(text)
        key = os.path.normcase(real)
        if self._in_python(key):
            return
        if key.endswith(_CODE_SUFFIXES):
            self._note_code(real)
            return
        if self._dry_allowed is not None:
            if key in self._dry_allowed:
                return
            raise PermissionError(
                f"R25: a dry run may open only its fixtures and Python files, "
                f"not {text}"
            )
        kind = self._guarded.get(key)
        relative = self._relative(real)
        shown = relative if relative is not None else real
        if kind is not None and key not in self._allowed_guarded:
            self.violations.append(f"R20: the run opened the {kind} file {shown}")
        if key in self._declared:
            return
        if shown not in self.files_opened:
            if len(self.files_opened) >= MAX_FILES_RECORDED:
                self.files_truncated = True
            else:
                self.files_opened[shown] = None

    def on_exec(self, code: object) -> None:
        filename = getattr(code, "co_filename", None)
        if not isinstance(filename, str) or filename in self._exec_seen:
            return
        self._exec_seen.add(filename)
        if self.paused or not filename.endswith(_CODE_SUFFIXES):
            return
        real = os.path.realpath(filename)
        if not self._in_python(os.path.normcase(real)):
            self._note_code(real)

    def on_process(self, event: str) -> None:
        if not self.paused:
            self.processes[event] = self.processes.get(event, 0) + 1

    @contextlib.contextmanager
    def pause(self) -> Iterator[None]:
        """Ignore events while BGL itself reads files or runs git."""
        previous, self.paused = self.paused, True
        try:
            yield
        finally:
            self.paused = previous

    def process_record(self) -> dict[str, Any]:
        """The close file's processes entry (N9)."""
        return {
            "starts": dict(sorted(self.processes.items())),
            "worker_modules_loaded": [
                name for name in _WORKER_MODULES if name in sys.modules
            ],
        }


_active: Watch | None = None
_installed = False


def _hook(event: str, args: tuple[Any, ...]) -> None:
    watch = _active
    if watch is None:
        return
    try:
        if event == "open":
            watch.on_open(args[0])
        elif event == "exec":
            watch.on_exec(args[0])
        elif event in _PROCESS_EVENTS:
            watch.on_process(event)
    except PermissionError:
        raise  # a dry run's refusal (R25) must reach the caller
    except Exception:
        # [DECISION] A bug in the hook must not break the user's file access.
        # The count goes in the close file, so missing records are visible.
        watch.hook_errors += 1


def start_watching(watch: Watch) -> None:
    """Make watch the active one. The hook itself is installed once per process."""
    global _active, _installed
    if not _installed:
        sys.addaudithook(_hook)
        _installed = True
    _active = watch


def stop_watching() -> None:
    global _active
    _active = None


def is_watching() -> bool:
    """True while a run in this process is open."""
    return _active is not None


def loaded_repo_modules(root: Path) -> list[str]:
    """R17's scan: every loaded module whose file is inside the repository.

    Returns repository-relative source paths, sorted. Files under the Python
    installation or site-packages are left out (see python_prefixes).
    """
    prefix = _as_prefix(os.fspath(root))
    python = python_prefixes()
    found: set[str] = set()
    for module in list(sys.modules.values()):
        file = getattr(module, "__file__", None)
        if not isinstance(file, str):
            continue
        real = os.path.realpath(file)
        key = os.path.normcase(real)
        if not key.startswith(prefix) or key.startswith(python):
            continue
        if real.endswith(".pyc"):
            try:
                real = importlib.util.source_from_cache(real)
            except ValueError:
                pass
        found.add(real[len(prefix) :].replace(os.sep, "/"))
    return sorted(found)

"""bgl verify: everything about the record that can be checked on this machine.

A failure means the record is broken or incomplete and exits non-zero, so verify
can run in CI. A warning is reported but does not fail: something to look at,
or something this machine cannot check.
"""

import dataclasses
import hashlib
import posixpath
from pathlib import Path
from typing import Final

from bgl import git
from bgl.canonical import HASH_HEX
from bgl.config import Config, load_config
from bgl.errors import GitError, KeyFileError, LockError, LockTimeoutError
from bgl.home import home_dir, load_machine_id
from bgl.locks import file_lock, ledger_lock
from bgl.records import LedgerView, Row, read_ledger_unlocked
from bgl.setup import sync_warning
from bgl.studylock import effective_lock, lock_content, lock_differences
from bgl.witness import chain_witness

CONFIG_FILE: Final[str] = "bgl.toml"
_LOCKS_PREFIX: Final[str] = ".bgl/locks/"
_ABANDONED: Final[str] = "abandoned"
_REACHABILITY_HELP: Final[str] = (
    "Branches carrying run commits must be merged or fast-forwarded, never "
    "squashed or rebased, and CI must check out full history"
)


@dataclasses.dataclass(frozen=True)
class Finding:
    check: str
    message: str


@dataclasses.dataclass(frozen=True)
class OpenRuns:
    """Runs with an open row and no close row, sorted by what verify can tell."""

    unclosed: tuple[str, ...]
    in_progress: tuple[str, ...]
    cannot_tell: tuple[str, ...]


@dataclasses.dataclass(frozen=True)
class VerifyReport:
    failures: tuple[Finding, ...]
    warnings: tuple[Finding, ...]
    in_progress: tuple[str, ...]
    contains: tuple[str, ...] | None  # where --contains found the hash

    @property
    def ok(self) -> bool:
        return not self.failures


def this_machine_id() -> str | None:
    """This machine's ID, or None where no writer was ever set up (CI)."""
    try:
        return load_machine_id(home_dir())
    except KeyFileError:
        return None


def _safe_relative(path: object) -> str | None:
    """A stored path if it stays inside the repository, else None."""
    if not isinstance(path, str) or not path or "\\" in path:
        return None
    tidy = posixpath.normpath(path)
    if tidy.startswith(("/", "..")) or ":" in tidy.split("/")[0]:
        return None
    return tidy


def find_open_runs(root: Path, view: LedgerView, machine_id: str | None) -> OpenRuns:
    """Classify every open row without a close row. Call under the ledger lock.

    Under the lock, a run whose own lock can be taken has ended: a run writes
    its close row (which needs the ledger lock) before releasing its run lock.
    """
    closed = {row["id"] for row in view.events("close")}
    unclosed: list[str] = []
    in_progress: list[str] = []
    cannot_tell: list[str] = []
    synced = sync_warning(root) is not None
    for row in view.events("open"):
        run_id = row["id"]
        if run_id in closed:
            continue
        if synced or machine_id is None or row["machine_id"] != machine_id:
            cannot_tell.append(run_id)
            continue
        lock = _safe_relative(row["lock_file"])
        if lock is None or not lock.startswith(_LOCKS_PREFIX):
            unclosed.append(run_id)
            continue
        try:
            with file_lock(root / lock, timeout=0):
                unclosed.append(run_id)
        except LockTimeoutError:
            in_progress.append(run_id)
    return OpenRuns(tuple(unclosed), tuple(in_progress), tuple(cannot_tell))


def _pairing(view: LedgerView, open_runs: OpenRuns) -> list[Finding]:
    failures: list[Finding] = []
    opened = {row["id"] for row in view.events("open")}
    acked = {row["id"] for row in view.events("ack")}
    closes: dict[str, list[Row]] = {}
    for row in view.events("close"):
        closes.setdefault(row["id"], []).append(row)
    for run_id, rows in closes.items():
        if run_id not in opened:
            failures.append(
                Finding("pairing", f"close row for {run_id} has no open row")
            )
        normal = [row for row in rows if row["status"] != _ABANDONED]
        if len(normal) > 1:
            failures.append(
                Finding("pairing", f"{run_id} has {len(normal)} close rows")
            )
        if normal and len(normal) < len(rows) and run_id not in acked:
            failures.append(
                Finding(
                    "late close",
                    f"{run_id} closed after it was abandoned; review it and record "
                    f'bgl ack {run_id} "reason"',
                )
            )
    for run_id in open_runs.unclosed:
        failures.append(
            Finding(
                "unclosed",
                f"{run_id} has no close row and its process has ended (for "
                f'example a hard kill). Clear it with bgl abandon {run_id} "reason"',
            )
        )
    for run_id in open_runs.cannot_tell:
        failures.append(
            Finding(
                "unclosed",
                f"{run_id} has no close row and was opened on another machine (or "
                "this project is in a synced or network folder), so verify cannot "
                "tell whether it is still running. Run verify on that machine, or "
                "abandon the run",
            )
        )
    return failures


def _check_file(
    root: Path, path: str | None, expected: object, what: str, *, committed: bool
) -> Finding | None:
    """Compare one file with its recorded hash.

    [DECISION] A missing committed file (manifest, close file) fails: git
    should have it. A missing git-ignored file (return series, captured output)
    is a warning, because other machines never have it.
    """
    if path is None:
        return Finding("hash", f"{what}: the recorded path is not a safe relative path")
    target = root / path
    if not target.is_file():
        check = "missing" if committed else "not checkable"
        return Finding(check, f"{what}: {path} is missing")
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    if digest != expected:
        return Finding("hash", f"{what}: {path} does not match its recorded hash")
    return None


def _file_hashes(
    root: Path, config: Config, view: LedgerView
) -> tuple[list[Finding], list[Finding]]:
    failures: list[Finding] = []
    warnings: list[Finding] = []
    runs_dir = config.project.runs_dir

    def add(finding: Finding | None) -> None:
        if finding is not None:
            (warnings if finding.check == "not checkable" else failures).append(finding)

    for row in view.events("open"):
        add(
            _check_file(
                root,
                _safe_relative(row["manifest"]),
                row["manifest_sha256"],
                f"manifest of {row['id']}",
                committed=True,
            )
        )
    for row in view.events("close"):
        run_id = row["id"]
        if row["close_file"] is not None:
            add(
                _check_file(
                    root,
                    _safe_relative(row["close_file"]),
                    row["close_sha256"],
                    f"close file of {run_id}",
                    committed=True,
                )
            )
        if row["returns_sha256"] is not None:
            add(
                _check_file(
                    root,
                    f"{runs_dir}/returns/{run_id}.csv",
                    row["returns_sha256"],
                    f"return series of {run_id}",
                    committed=False,
                )
            )
        for stream, digest in (row["output_sha256"] or {}).items():
            add(
                _check_file(
                    root,
                    f"{runs_dir}/output/{run_id}.{stream}",
                    digest,
                    f"captured {stream} of {run_id}",
                    committed=False,
                )
            )
    return failures, warnings


def _orphans(root: Path, config: Config, view: LedgerView) -> list[Finding]:
    """Run files with no row (R13). Their IDs are never reused.

    [DECISION] Orphans are warnings: a manifest without an open row belongs to
    a run whose code never started, and a close file without a close row
    belongs to a run that verify already fails as unclosed.
    """
    opened = {row["id"] for row in view.events("open")}
    closed = {row["id"] for row in view.events("close")}
    runs = root / config.project.runs_dir
    found: list[Finding] = []
    checks = [
        (runs, ".close.json", closed, "close file"),
        (runs, ".json", opened, "manifest"),
        (runs / "returns", ".csv", closed, "return series"),
        (runs / "output", ".stdout", closed, "captured output"),
        (runs / "output", ".stderr", closed, "captured output"),
    ]
    for folder, suffix, known, what in checks:
        if not folder.is_dir():
            continue
        for entry in sorted(folder.iterdir()):
            name = entry.name
            if not entry.is_file() or not name.endswith(suffix):
                continue
            if suffix == ".json" and name.endswith(".close.json"):
                continue
            run_id = name[: -len(suffix)]
            if run_id not in known:
                found.append(
                    Finding("orphan", f"{what} {name} has no row in any chain")
                )
    return found


def _commits(root: Path, view: LedgerView) -> list[Finding]:
    """Every commit a run used, and every fix commit, must stay reachable.

    [DECISION] Checked: the commit of every open row and the fix_commit of
    every row that has one. Refused and dry rows used no data, so their
    commits are not checked.
    """
    used: dict[str, list[str]] = {}
    for row in view.rows:
        commit = row["commit"] if row["event"] == "open" else None
        for value in (commit, row["fix_commit"]):
            if isinstance(value, str):
                used.setdefault(value, []).append(str(row["id"]))
    if not used:
        return []
    if git.is_shallow(root):
        return [
            Finding(
                "commit",
                "this is a shallow clone, so commits cannot be "
                "checked; fetch full history (git fetch --unshallow)",
            )
        ]
    return [
        Finding(
            "commit",
            f"commit not reachable: {commit} (used by "
            f"{', '.join(sorted(set(ids)))}). {_REACHABILITY_HELP}",
        )
        for commit, ids in sorted(used.items())
        if not git.is_reachable(root, commit)
    ]


def _locks(
    root: Path, config: Config, view: LedgerView
) -> tuple[list[Finding], list[Finding]]:
    failures: list[Finding] = []
    warnings: list[Finding] = []
    locked = sorted({row["study"] for row in view.events("lock")})
    if not locked:
        return failures, warnings
    try:
        commit = git.head_commit(root)
    except GitError as error:
        return [Finding("lock", str(error))], warnings
    for name in locked:
        study = config.studies.get(name)
        if study is None:
            failures.append(
                Finding(
                    "lock", f"study {name} is locked but no longer in {CONFIG_FILE}"
                )
            )
            continue
        try:
            current = lock_content(root, config.project, study, commit)
        except LockError as error:
            failures.append(Finding("lock", f"study {name}: {error}"))
            continue
        amends = view.events("amend", name)
        hard: dict[str, None] = {}
        spec_changed = False
        for lock in view.events("lock", name):
            for difference in lock_differences(
                effective_lock(lock["lock"], amends), current
            ):
                if difference.spec_only:
                    spec_changed = True
                else:
                    hard[difference.key] = None
        if hard:
            failures.append(
                Finding(
                    "lock",
                    f"{CONFIG_FILE} does not match the lock "
                    f"of study {name}: {', '.join(hard)}",
                )
            )
        if spec_changed:
            warnings.append(
                Finding(
                    "lock",
                    f"the spec files of study {name} changed "
                    f"since its lock; record it with bgl amend "
                    f"{name} spec_files before a confirmatory run",
                )
            )
    return failures, warnings


def _contains(view: LedgerView, wanted: str) -> tuple[str, ...]:
    places = []
    for code, chain in view.report.chains.items():
        for number, row in enumerate(chain.rows, start=1):
            if row["hash"] == wanted:
                places.append(f"{code} line {number}")
    return tuple(places)


def verify_project(root: Path, *, contains: str | None = None) -> VerifyReport:
    """Run every check verify makes. Raises ConfigError on an invalid bgl.toml.

    Witness files are checked offline: each record and stamp must belong to a
    head still in its chain. Whether Rekor holds heads with no file here is
    the gate's network check (G2).

    [DECISION] Not checked yet: plaintext holdout files (needs seal rows, build
    step 9).
    """
    if contains is not None and not HASH_HEX.fullmatch(contains):
        raise ValueError("--contains takes a 64-character lowercase hex hash")
    config = load_config(root / CONFIG_FILE)
    failures: list[Finding] = []
    warnings: list[Finding] = []
    with ledger_lock(root):
        view = read_ledger_unlocked(root, config.project)
        open_runs = find_open_runs(root, view, this_machine_id())
    for code, chain in view.report.chains.items():
        if chain.problem is not None:
            failures.append(
                Finding(
                    "chain",
                    f"chain {code} line {chain.problem.line}: {chain.problem.reason}",
                )
            )
    for name in view.report.unregistered:
        failures.append(
            Finding(
                "chain", f"{name} is in the ledger folder but is not a registered chain"
            )
        )
    failures.extend(_pairing(view, open_runs))
    hash_failures, hash_warnings = _file_hashes(root, config, view)
    failures.extend(hash_failures)
    warnings.extend(hash_warnings)
    warnings.extend(_orphans(root, config, view))
    failures.extend(_commits(root, view))
    for witnessed in chain_witness(root, config.project, view):
        failures.extend(Finding("witness", text) for text in witnessed.problems)
    lock_failures, lock_warnings = _locks(root, config, view)
    failures.extend(lock_failures)
    warnings.extend(lock_warnings)
    found: tuple[str, ...] | None = None
    if contains is not None:
        found = _contains(view, contains)
        if not found:
            failures.append(
                Finding(
                    "contains",
                    f"{contains} is not the hash of any "
                    "row in the chains on this machine",
                )
            )
    return VerifyReport(tuple(failures), tuple(warnings), open_runs.in_progress, found)

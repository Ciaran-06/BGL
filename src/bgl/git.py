"""Git facts BGL records or refuses on (R3, R3b, R4, workflow, user, signature).

Every call runs the git command line in the repository root and reads
NUL-separated output (-z), so paths with spaces or non-ASCII characters are
read exactly. Nothing here decides a refusal; the run lifecycle does.
"""

import hashlib
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Final

from bgl.canonical import canonical_json
from bgl.errors import GitError, GitMissingError

_PY_SUFFIX: Final[str] = ".py"
_REACHABLE_REFS: Final[tuple[str, ...]] = ("refs/heads", "refs/remotes", "refs/tags")


def run_git(root: Path, *args: str) -> bytes:
    """Run git in root and return its standard output. Raises GitError on failure."""
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            check=False,
        )
    except FileNotFoundError:
        raise GitMissingError("git is not installed or not on PATH") from None
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", "replace").strip()
        raise GitError(f"git {' '.join(args)} failed: {message}")
    return result.stdout


def _text(output: bytes) -> str:
    return output.decode("utf-8").strip()


def _split_z(output: bytes) -> list[str]:
    return [item.decode("utf-8") for item in output.split(b"\0") if item]


def repo_root(start: Path) -> Path:
    """The top folder of the git repository containing start."""
    return Path(_text(run_git(start, "rev-parse", "--show-toplevel")))


def head_commit(root: Path) -> str:
    """The full hash of the checked-out commit. Raises GitError with no commits."""
    try:
        return _text(run_git(root, "rev-parse", "--verify", "HEAD^{commit}"))
    except GitMissingError:
        raise
    except GitError:
        raise GitError("the repository has no commits yet; commit first") from None


def git_user(root: Path) -> str:
    """The configured git user as 'Name <email>'. Raises GitError if either is unset.

    N8: a run with no git user is refused, so this never returns a blank user.
    """
    name = _config_value(root, "user.name")
    email = _config_value(root, "user.email")
    if not name or not email:
        raise GitError(
            "git has no user configured; run git config user.name and "
            "git config user.email"
        )
    return f"{name} <{email}>"


def _config_value(root: Path, key: str) -> str:
    try:
        return _text(run_git(root, "config", "--get", key))
    except GitMissingError:
        raise
    except GitError:
        return ""


def commit_signature(root: Path, commit: str) -> str:
    """Git's one-letter signature status for commit (information only).

    [DECISION] Stored exactly as git reports it (%G?): G good, B bad, U good but
    untrusted, X/Y expired signature/key, R revoked, E cannot check (for example
    an unknown key), N unsigned. The spec lists only G, B, U and N.
    """
    return _text(run_git(root, "log", "-1", "--format=%G?", commit, "--"))


def _exempt(path: str, exempt: Sequence[str]) -> bool:
    return any(path == folder or path.startswith(folder + "/") for folder in exempt)


def _check_exempt(exempt: Sequence[str]) -> None:
    # [DECISION] An exempt folder of "." would exempt the whole repository, so
    # it is refused here. (Config currently allows ledger = "."; see C2.)
    for folder in exempt:
        if folder in ("", "."):
            raise ValueError(f"cannot exempt the repository root ({folder!r})")


def uncommitted_changes(root: Path, exempt: Sequence[str]) -> list[str]:
    """R3: tracked files with uncommitted changes, staged or not, sorted.

    exempt holds repository-relative folders (the ledger folder and runs_dir)
    whose changes are ignored. Untracked files are not listed. A rename lists
    both the old and the new path.
    """
    _check_exempt(exempt)
    output = run_git(root, "status", "--porcelain=v1", "-z", "--untracked-files=no")
    records = _split_z(output)
    changed: set[str] = set()
    index = 0
    while index < len(records):
        record = records[index]
        status, path = record[:2], record[3:]
        changed.add(path)
        if "R" in status or "C" in status:
            index += 1
            changed.add(records[index])  # the original path of a rename or copy
        index += 1
    return sorted(path for path in changed if not _exempt(path, exempt))


def is_tracked(root: Path, path: str) -> bool:
    """True if path (repository-relative, forward slashes) is a tracked file."""
    output = run_git(root, "ls-files", "-z", "--full-name", "--", path)
    return path in _split_z(output)


def untracked_python(root: Path) -> list[str]:
    """R3b: untracked, not-ignored .py files, sorted (the spec's exact command)."""
    output = run_git(
        root, "ls-files", "-z", "--others", "--exclude-standard", "--", "*.py"
    )
    return sorted(_split_z(output))


def python_blobs(root: Path, commit: str, exempt: Sequence[str]) -> list[list[str]]:
    """Every .py file in commit as [path, blob_hash], sorted by path."""
    _check_exempt(exempt)
    output = run_git(root, "ls-tree", "-r", "-z", "--full-tree", commit)
    blobs: list[list[str]] = []
    for entry in _split_z(output):
        meta, path = entry.split("\t", 1)
        _mode, kind, blob = meta.split(" ")
        if kind == "blob" and path.endswith(_PY_SUFFIX) and not _exempt(path, exempt):
            blobs.append([path, blob])
    return sorted(blobs)


def code_fingerprint(root: Path, commit: str, exempt: Sequence[str]) -> str:
    """R4, N8: SHA-256 of the canonical JSON of [[path, blob_hash], ...].

    [DECISION] Blobs come from the commit, not the working tree. R3 already
    requires the tree to match the commit, and this way verify can recompute
    the fingerprint from history alone.
    """
    return hashlib.sha256(
        canonical_json(python_blobs(root, commit, exempt))
    ).hexdigest()


def commit_exists(root: Path, commit: str) -> bool:
    try:
        run_git(root, "cat-file", "-e", f"{commit}^{{commit}}")
    except GitMissingError:
        raise
    except GitError:
        return False
    return True


def is_reachable(root: Path, commit: str) -> bool:
    """N8: some branch, remote-tracking branch or tag contains commit."""
    if not commit_exists(root, commit):
        return False
    output = run_git(
        root,
        "for-each-ref",
        "--count=1",
        "--format=%(refname)",
        f"--contains={commit}",
        *_REACHABLE_REFS,
    )
    return bool(output.strip())


def is_shallow(root: Path) -> bool:
    """True in a shallow clone, where reachability cannot be fully checked."""
    return _text(run_git(root, "rev-parse", "--is-shallow-repository")) == "true"


def tracked_files(root: Path) -> frozenset[str]:
    """Every tracked path, repository-relative with forward slashes."""
    return frozenset(_split_z(run_git(root, "ls-files", "-z", "--full-name")))


def has_untracked_files(root: Path) -> bool:
    """True if any untracked, not-ignored file exists (recorded in the manifest)."""
    output = run_git(root, "ls-files", "-z", "--others", "--exclude-standard")
    return bool(output)


def current_branch(root: Path) -> str | None:
    """The checked-out branch name, or None with a detached HEAD."""
    try:
        return _text(run_git(root, "symbolic-ref", "--quiet", "--short", "HEAD"))
    except GitMissingError:
        raise
    except GitError:
        return None


def blob_sha256(root: Path, commit: str, path: str) -> str | None:
    """SHA-256 of path's content in commit, or None if the commit lacks it.

    [DECISION] Hashes come from the committed blob, not the working file, so a
    checkout with converted line endings gives the same hash on every machine
    and verify can recompute it from history.
    """
    try:
        content = run_git(root, "cat-file", "blob", f"{commit}:{path}")
    except GitMissingError:
        raise
    except GitError:
        return None
    return hashlib.sha256(content).hexdigest()


def resolve_commit(root: Path, name: str) -> str | None:
    """The full hash of the commit name refers to, or None if there is none."""
    try:
        return _text(run_git(root, "rev-parse", "--verify", f"{name}^{{commit}}"))
    except GitMissingError:
        raise
    except GitError:
        return None

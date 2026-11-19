"""The study lock (R22) and typed amendments.

A study's first counted open writes a lock row holding a canonical copy of the
study table, the chain registry and the spec-file hashes. After that, every open
compares the current bgl.toml with the locked values plus accepted amendments.
"""

import copy
import dataclasses
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Final

from bgl import git, witness
from bgl.config import (
    Project,
    Study,
    canonical_registry,
    canonical_study,
    load_config,
)
from bgl.errors import AmendError, LockError
from bgl.ledger import append_row_unlocked
from bgl.locks import ledger_lock
from bgl.records import read_ledger_unlocked
from bgl.rowmeta import base_fields, utc_now
from bgl.writers import load_identity

# The only keys bgl amend accepts (spec: "Study lock and amendments").
AMENDABLE: Final[tuple[str, ...]] = (
    "budget",
    "prior_trials",
    "claims",
    "gate",
    "spec_files",
)
_INCREASE_ONLY: Final[frozenset[str]] = frozenset({"budget", "prior_trials"})

LockContent = dict[str, Any]


def spec_file_hashes(root: Path, study: Study, commit: str) -> dict[str, str]:
    """SHA-256 of each spec file as committed in commit.

    [DECISION] Spec files are hashed from the committed blob, so they must be
    tracked and committed. A working-file hash would differ between machines
    whose git converts line endings, and the lock would refuse a teammate.
    """
    hashes: dict[str, str] = {}
    for path in study.spec_files:
        digest = git.blob_sha256(root, commit, path)
        if digest is None:
            raise LockError(
                f"spec file {path} is not in the current commit; commit it first"
            )
        hashes[path] = digest
    return hashes


def lock_content(
    root: Path, project: Project, study: Study, commit: str
) -> LockContent:
    """What a lock row for study would hold right now.

    [DECISION] The lock also holds the project's mode and witness settings. The
    spec says they can never be amended, and the study table and registry alone
    would not show a change to them.
    """
    witness: Any = False
    if project.witness is not None:
        witness = {
            "rekor_url": project.witness.rekor_url,
            "ots_calendars": list(project.witness.ots_calendars),
        }
    return {
        "study": canonical_study(study),
        "registry": canonical_registry(project.writers),
        "spec_files": spec_file_hashes(root, study, commit),
        "project": {"mode": project.mode, "witness": witness},
    }


def effective_lock(
    locked: Mapping[str, Any], amend_rows: Sequence[Mapping[str, Any]]
) -> LockContent:
    """The locked values with every accepted amendment applied, in the order given.

    [DECISION] Amendments are applied in registry order of chains, then row
    order, and each replaces the value for its key. Each was checked against
    the allowed changes when it was written, so nothing is re-checked here.
    """
    state: LockContent = copy.deepcopy(dict(locked))
    for row in amend_rows:
        amend = row["amend"]
        if not isinstance(amend, dict):
            continue
        key, new = amend.get("key"), amend.get("new")
        if key == "spec_files" and isinstance(new, dict):
            state["study"]["spec_files"] = list(new.get("paths", []))
            state["spec_files"] = dict(new.get("hashes", {}))
        elif key in ("budget", "prior_trials", "claims", "gate"):
            if new is None:
                state["study"].pop(key, None)
            else:
                state["study"][key] = new
    return state


@dataclasses.dataclass(frozen=True)
class Difference:
    """One place where bgl.toml no longer matches the lock plus amendments."""

    key: str
    spec_only: bool = False


def lock_differences(
    locked: Mapping[str, Any], current: Mapping[str, Any]
) -> list[Difference]:
    """Every difference between an effective lock and the current content."""
    found: list[Difference] = []
    locked_project = locked.get("project", {})
    for key in ("mode", "witness"):
        if locked_project.get(key) != current["project"][key]:
            found.append(Difference(f"project.{key}"))
    # [DECISION] The registry may grow without an amend row (the spec gives that
    # change no effect). A locked entry that changed or vanished is a difference.
    for code, entry in locked["registry"].items():
        if current["registry"].get(code) != entry:
            found.append(Difference(f"project.chains.{code}"))
    locked_study, current_study = locked["study"], current["study"]
    for key in sorted(set(locked_study) | set(current_study)):
        if key == "spec_files":
            continue
        if locked_study.get(key) != current_study.get(key):
            found.append(Difference(key))
    if (
        locked_study.get("spec_files") != current_study.get("spec_files")
        or locked["spec_files"] != current["spec_files"]
    ):
        found.append(Difference("spec_files", spec_only=True))
    return found


def _amendable_value(content: Mapping[str, Any], key: str) -> Any:
    if key == "spec_files":
        return {
            "paths": list(content["study"]["spec_files"]),
            "hashes": dict(content["spec_files"]),
        }
    return content["study"].get(key)


def check_amendment(key: str, old: Any, new: Any, fix_commit: str | None) -> None:
    """Raise AmendError unless old -> new is an allowed change for key.

    Every allowed change makes the evidence weaker on paper, never stronger.
    """
    if key not in AMENDABLE:
        raise AmendError(
            f"{key!r} cannot be amended; only {', '.join(AMENDABLE)} can "
            "(a custodian handover arrives with sealing)"
        )
    if new == old:
        raise AmendError(f"bgl.toml already matches the lock for {key}; nothing to do")
    if key in _INCREASE_ONLY:
        if new <= old:
            raise AmendError(f"{key} can only increase (locked {old}, now {new})")
    elif key == "claims":
        if not set(old) < set(new):
            raise AmendError("claims can only be added, never removed or renamed")
    elif key == "gate":
        if new is None:
            raise AmendError("a gate table cannot be removed once locked")
        # [DECISION] A gate correction replaces the whole locked gate table and
        # records both tables, so the gate can show the original verdict first.
        if old is not None and fix_commit is None:
            raise AmendError(
                "changing a locked gate value is a correction and needs the "
                "commit that fixed the bug (--fix COMMIT)"
            )


def amend_study(
    root: Path,
    study_name: str,
    key: str,
    reason: str,
    *,
    fix_commit: str | None = None,
) -> dict[str, Any]:
    """Append an amend row accepting bgl.toml's current value for key.

    [DECISION] The new value is read from bgl.toml, not passed in: edit the
    file, then amend. The command line's VALUE argument can be checked against
    it. The row records the old and new values.
    """
    if not isinstance(reason, str) or not reason.strip():
        raise AmendError("an amendment needs a written reason")
    config = load_config(root / "bgl.toml")
    study = config.studies.get(study_name)
    if study is None:
        raise AmendError(f"unknown study {study_name!r}")
    if key not in AMENDABLE:
        check_amendment(key, None, None, fix_commit)
    identity = load_identity(root, config.project)
    commit = git.head_commit(root)
    fix: str | None = None
    if fix_commit is not None:
        fix = git.resolve_commit(root, fix_commit)
        if fix is None:
            raise AmendError(f"--fix names no commit in this repository: {fix_commit}")
    with ledger_lock(root):
        view = read_ledger_unlocked(root, config.project)
        locks = view.events("lock", study_name)
        if not locks:
            raise AmendError(
                f"study {study_name} is not locked yet; bgl.toml can still be "
                "edited freely"
            )
        current = lock_content(root, config.project, study, commit)
        new = _amendable_value(current, key)
        old = None
        for lock in locks:
            state = effective_lock(lock["lock"], view.events("amend", study_name))
            old = _amendable_value(state, key)
            check_amendment(key, old, new, fix)
        fields = {
            "event": "amend",
            "study": study_name,
            "reason": reason.strip(),
            "fix_commit": fix,
            "amend": {"key": key, "old": old, "new": new},
            "started": utc_now(),
            **base_fields(identity),
        }
        row = append_row_unlocked(
            root, config.project, identity.writer, identity.private_key, fields
        )
    witness.after_append(root, config.project, identity)
    return row

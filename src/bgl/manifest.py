"""The open manifest (R8, R9): what a run was given, written before its open row."""

import hashlib
import importlib.metadata
import math
import os
import platform
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Final

from bgl.canonical import canonical_json
from bgl.config import Study, canonical_number

_CHUNK: Final[int] = 1 << 20
_LOCK_FILE: Final[str] = "uv.lock"
MANIFEST_FORMAT_VERSION: Final[int] = 1


def repo_relative(root: Path, path: str | os.PathLike[str]) -> str:
    """path as repository-relative text with forward slashes.

    [DECISION] A relative path is relative to the repository root, not the
    working directory, so a run records the same paths wherever it is started.
    A path outside the repository raises ValueError: stored paths must mean the
    same thing on every machine.
    """
    root_text = os.path.normpath(os.fspath(root))
    raw = os.fspath(path)
    absolute = raw if os.path.isabs(raw) else os.path.join(root_text, raw)
    for candidate, base in (
        (os.path.normpath(absolute), root_text),
        (os.path.realpath(absolute), os.path.realpath(root_text)),
    ):
        prefix = os.path.normcase(base).rstrip(os.sep) + os.sep
        if os.path.normcase(candidate).startswith(prefix):
            return candidate[len(prefix) :].replace(os.sep, "/")
    raise ValueError(f"{raw} is outside the repository at {root_text}")


def hash_file(path: Path) -> tuple[int, str]:
    """(size in bytes, SHA-256 hex) of a file, read in chunks."""
    digest = hashlib.sha256()
    size = 0
    with open(path, "rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
            size += len(chunk)
    return size, digest.hexdigest()


def canonical_config(config: object, path: str = "config") -> Any:
    """The script's settings in a form canonical JSON can hold (R9).

    Floats become their shortest round-trip text, as C19 does for bgl.toml, and
    tuples become lists. Anything else that JSON cannot hold raises ValueError.

    [DECISION] A float and the same number written as text therefore hash the
    same. Only R4's duplicate check reads this hash, and it errs towards
    refusing.
    """
    if config is None or isinstance(config, (bool, int, str)):
        return config
    if isinstance(config, float):
        if not math.isfinite(config):
            raise ValueError(f"{path}: nan and inf cannot be recorded")
        return canonical_number(config)
    if isinstance(config, (list, tuple)):
        return [
            canonical_config(item, f"{path}[{index}]")
            for index, item in enumerate(config)
        ]
    if isinstance(config, Mapping):
        result: dict[str, Any] = {}
        for key, item in config.items():
            if not isinstance(key, str):
                raise ValueError(f"{path}: key {key!r} is not text")
            result[key] = canonical_config(item, f"{path}.{key}")
        return result
    raise ValueError(
        f"{path}: {type(config).__name__} cannot be recorded; pass plain "
        "numbers, text, lists and dicts"
    )


def sha256_json(value: object) -> str:
    """SHA-256 of a value's canonical JSON."""
    return hashlib.sha256(canonical_json(value)).hexdigest()


def inputs_hash(inputs: Sequence[Mapping[str, Any]]) -> str:
    """The open row's inputs_sha256 (R4).

    [DECISION] SHA-256 of the canonical JSON of [[path, sha256], ...] sorted by
    path. Sizes are left out: the content hash already fixes them.
    """
    pairs = sorted([item["path"], item["sha256"]] for item in inputs)
    return sha256_json(pairs)


def environment(root: Path) -> dict[str, Any]:
    """Python version, platform, and the lock file's hash or the package list."""
    lock_path = root / _LOCK_FILE
    lock: dict[str, Any] | None = None
    packages: list[str] | None = None
    if lock_path.is_file():
        lock = {"path": _LOCK_FILE, "sha256": hash_file(lock_path)[1]}
    else:
        packages = sorted(
            {
                f"{dist.metadata['Name']}=={dist.version}"
                for dist in importlib.metadata.distributions()
                if dist.metadata["Name"]
            }
        )
    return {
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "lock_file": lock,
        "packages": packages,
    }


def locked_costs(study: Study) -> dict[str, Any] | None:
    """A copy of the study's cost model, or None when it has no gate table."""
    if study.gate is None:
        return None
    return {
        "model": study.gate.costs.model,
        "cost_bps": canonical_number(study.gate.costs.cost_bps),
    }


def build_manifest(
    *,
    run_id: str,
    study: Study,
    config: Any,
    inputs: Sequence[Mapping[str, Any]],
    environment_info: Mapping[str, Any],
    git_info: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "format_version": MANIFEST_FORMAT_VERSION,
        "id": run_id,
        "study": study.name,
        "config": config,
        "inputs": [dict(item) for item in inputs],
        "environment": dict(environment_info),
        "git": dict(git_info),
        "costs": locked_costs(study),
    }


def file_bytes(value: object) -> bytes:
    """A manifest or close file's exact bytes: canonical JSON plus a newline."""
    return canonical_json(value) + b"\n"

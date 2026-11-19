"""Canonical JSON: the exact bytes every BGL hash is computed over.

The settings are fixed by the spec ("Canonical bytes") and must never change
within a format version.
"""

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any, Final

GENESIS_HASH: Final[str] = "0" * 64
UNHASHED_FIELDS: Final[frozenset[str]] = frozenset({"hash", "sig"})
HASH_HEX: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{64}")


def _check_value(value: object, path: str) -> None:
    """Refuse anything canonical JSON cannot hold the same way everywhere.

    Allowed: None, bool, int, str, lists and tuples of allowed values, and plain
    dicts with string keys. A float is refused (C19 stores decimals as text).
    path names where the value sits, for the error message.
    """
    if value is None or isinstance(value, (bool, int, str)):
        return
    # [DECISION] Tuples are accepted and written as lists (config records use
    # tuples). Only plain dicts are accepted: json cannot write mappingproxy.
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _check_value(item, f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{path}: key {key!r} is not text")
            _check_value(item, f"{path}.{key}")
        return
    raise ValueError(
        f"{path}: {type(value).__name__} cannot be hashed (store decimals as text, C19)"
    )


def canonical_json(value: object) -> bytes:
    """Any allowed value as canonical JSON bytes: sorted keys, no spaces, UTF-8.

    Used for rows, manifests, the settings hash (R9) and the lock's contents.
    """
    _check_value(value, "value")
    return _dump(value)


def canonical_bytes(row: Mapping[str, Any]) -> bytes:
    """The bytes a row's hash covers: the row without hash and sig."""
    hashed = {key: item for key, item in row.items() if key not in UNHASHED_FIELDS}
    _check_value(hashed, "row")
    return _dump(hashed)


def row_hash(row: Mapping[str, Any]) -> str:
    """SHA-256 of canonical_bytes(row), as 64 lowercase hex characters."""
    return hashlib.sha256(canonical_bytes(row)).hexdigest()


def line_bytes(row: Mapping[str, Any]) -> bytes:
    """A row as it is written to its chain file: full canonical JSON plus a newline
    (N7). A chain line in any other form fails verify."""
    full = dict(row)
    _check_value(full, "row")
    return _dump(full) + b"\n"


def _dump(value: object) -> bytes:
    text = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    try:
        return text.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ValueError("value contains text that is not valid Unicode") from error

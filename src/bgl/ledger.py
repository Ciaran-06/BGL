"""The ledger: building rows, and the verdicts on chains.

verify_chain, append_row and repair_chain decide what counts as intact evidence
and what may change it; they are written by hand against the spec (R7, R14, R15,
R18). The rest is a direct translation of the spec's tables.
"""

import dataclasses
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Final

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from bgl.canonical import HASH_HEX, row_hash
from bgl.config import Project, Writer
from bgl.ledger_io import chain_files, chain_path, read_chain
from bgl.locks import ledger_lock
from bgl.signing import sign_row_hash

FORMAT_VERSION: Final[int] = 1
EVENTS: Final[frozenset[str]] = frozenset(
    {
        "open",
        "close",
        "refused",
        "dry",
        "note",
        "ack",
        "void",
        "lock",
        "amend",
        "repair",
        "seal",
        "imported",
        "incubation",
        "live_return",
        "gate",
        "registration",
    }
)
ROW_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "id",
        "legacy_id",
        "event",
        "chain",
        "study",
        "type",
        "claims",
        "selects",
        "variants",
        "sample",
        "sample_start",
        "sample_end",
        "script",
        "script_sha256",
        "code_sha256",
        "config_sha256",
        "inputs_sha256",
        "what_changed",
        "prediction",
        "status",
        "outcome",
        "result",
        "commit",
        "user",
        "commit_signature",
        "machine_id",
        "host",
        "pid",
        "lock_file",
        "started",
        "ended",
        "manifest",
        "manifest_sha256",
        "close_file",
        "close_sha256",
        "returns_sha256",
        "output_sha256",
        "other_heads",
        "reason",
        "fix_commit",
        "amend",
        "lock",
        "seal",
        "removed",
        "period",
        "value",
        "turnover",
        "cost",
        "position_sha256",
        "live_inputs",
        "report_sha256",
        "format_version",
        "prev_hash",
        "hash",
        "sig",
    }
)
BUILD_ONLY_FIELDS: Final[frozenset[str]] = frozenset(
    {"prev_hash", "hash", "sig", "format_version"}
)


@dataclasses.dataclass(frozen=True)
class ChainProblem:
    """Where a chain first breaks. line is 1-based."""

    line: int
    reason: str


@dataclasses.dataclass(frozen=True)
class ChainReport:
    """One chain's verification result.

    rows are the rows that passed, in order. head is the last good row's hash,
    or GENESIS_HASH for an empty chain.
    """

    code: str
    rows: tuple[Mapping[str, Any], ...]
    head: str
    problem: ChainProblem | None


@dataclasses.dataclass(frozen=True)
class LedgerReport:
    """Every registered chain's report, plus chain files outside the registry (N7)."""

    chains: Mapping[str, ChainReport]
    unregistered: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.unregistered and all(
            report.problem is None for report in self.chains.values()
        )


def build_row(
    fields: Mapping[str, Any], prev_hash: str, private_key: Ed25519PrivateKey
) -> dict[str, Any]:
    """A complete, signed row: every field present (null when unused), N6.

    Raises ValueError for unknown fields, fields only this function sets, an
    unknown event, a missing chain code or a malformed prev_hash. These mean a
    bug in BGL, not a user error.
    """
    unknown = sorted(set(fields) - ROW_FIELDS)
    if unknown:
        raise ValueError(f"unknown row fields: {', '.join(unknown)}")
    owned = sorted(set(fields) & BUILD_ONLY_FIELDS)
    if owned:
        raise ValueError(f"build_row sets these itself: {', '.join(owned)}")
    if fields.get("event") not in EVENTS:
        raise ValueError(f"unknown event: {fields.get('event')!r}")
    if not isinstance(fields.get("chain"), str):
        raise ValueError("a row needs its chain code")
    if not HASH_HEX.fullmatch(prev_hash):
        raise ValueError(f"not a lowercase SHA-256 hex hash: {prev_hash!r}")
    row: dict[str, Any] = dict.fromkeys(ROW_FIELDS)
    row.update(fields)
    row["format_version"] = FORMAT_VERSION
    row["prev_hash"] = prev_hash
    row["hash"] = row_hash(row)
    row["sig"] = sign_row_hash(private_key, row["hash"])
    return row


def verify_chain(
    lines: Sequence[bytes], torn_tail: bytes | None, writer: Writer
) -> ChainReport:
    """Check one chain and report the first broken row. Written by hand."""
    raise NotImplementedError("verify_chain is not written yet")


def verify_all_chains(root: Path, project: Project) -> LedgerReport:
    """Verify every registered chain, and list chain files outside the registry.

    Runs under the ledger lock (R18), so no append can land halfway through.
    """
    with ledger_lock(root):
        chains = {
            code: verify_chain(*read_chain(chain_path(root, project, code)), writer)
            for code, writer in project.writers.items()
        }
        unregistered = tuple(
            path.name
            for path in chain_files(root, project)
            if path.stem not in project.writers
        )
    return LedgerReport(chains=chains, unregistered=unregistered)


def append_row(
    root: Path,
    project: Project,
    writer: Writer,
    private_key: Ed25519PrivateKey,
    fields: Mapping[str, Any],
) -> dict[str, Any]:
    """R7, R18: verify the writer's chain under the lock, then append. By hand."""
    raise NotImplementedError("append_row is not written yet")


def repair_chain(
    root: Path,
    project: Project,
    writer: Writer,
    private_key: Ed25519PrivateKey,
    fields: Mapping[str, Any],
) -> dict[str, Any]:
    """R14: remove a torn tail and record it in a repair row. By hand."""
    raise NotImplementedError("repair_chain is not written yet")

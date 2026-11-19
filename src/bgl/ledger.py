"""The ledger: building rows, and the verdicts on chains.

verify_chain, append_row and repair_chain decide what counts as intact evidence
and what may change it (R7, R14, R15, R18); review them most carefully. The rest
is a direct translation of the spec's tables.
"""

import dataclasses
import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Final

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from bgl.canonical import GENESIS_HASH, HASH_HEX, line_bytes, row_hash
from bgl.config import Project, Writer
from bgl.errors import ChainBrokenError, NothingToRepairError
from bgl.ledger_io import (
    append_line,
    chain_files,
    chain_path,
    read_chain,
    truncate_tail,
)
from bgl.locks import ledger_lock
from bgl.signing import sign_row_hash, verify_row_signature

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
# [DECISION] Only build_row sets these, so a caller cannot fake them.
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
# [DECISION] verify_all_chains returns every chain's report plus unregistered
# chain files; ok means no problems and no strays.
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
    """Check one chain and report the first broken row (R7, R15, N6, N7).

    Pure: no file access. lines are complete lines without their newline;
    torn_tail is the bytes after the last newline, or None.
    """
    prev = GENESIS_HASH
    good: list[dict[str, Any]] = []
    first_machine: object = None

    def fail(number: int, reason: str) -> ChainReport:
        # head is prev: the last hash this function confirmed itself.
        problem = ChainProblem(number, reason)
        return ChainReport(writer.code, tuple(good), prev, problem)

    # [DECISION] Check order: readable, canonical, fields, version, chain, link,
    # hash, signature, machine. Structural checks first give specific reasons;
    # link before hash names a removed row; hash before signature names an edit.
    for number, line in enumerate(lines, start=1):
        try:
            text = line.decode("utf-8")
        except UnicodeDecodeError:
            return fail(number, "not UTF-8")
        try:
            row = json.loads(text)
        except json.JSONDecodeError:
            return fail(number, "not valid JSON")
        if not isinstance(row, dict):
            return fail(number, "not a JSON object")

        try:
            expected = line_bytes(row)
        except ValueError:
            return fail(number, "contains a float or other value that cannot be hashed")
        if expected != line + b"\n":
            return fail(number, "not in canonical form")

        missing = sorted(ROW_FIELDS - row.keys())
        extra = sorted(row.keys() - ROW_FIELDS)
        if missing or extra:
            parts = []
            if missing:
                parts.append("missing fields: " + ", ".join(missing))
            if extra:
                parts.append("extra fields: " + ", ".join(extra))
            return fail(number, "; ".join(parts))

        version = row["format_version"]
        # type() rather than isinstance: True == 1 in Python.
        if type(version) is not int or version != FORMAT_VERSION:
            return fail(
                number, f"format_version {version!r}, expected {FORMAT_VERSION}"
            )
        if row["chain"] != writer.code:
            return fail(number, f"chain {row['chain']!r}, expected {writer.code}")
        if row["prev_hash"] != prev:
            return fail(
                number,
                "prev_hash does not match the previous row "
                "(a row was removed, reordered or inserted)",
            )
        if row_hash(row) != row["hash"]:
            return fail(
                number, "hash does not match the row's contents (the row was edited)"
            )
        if not verify_row_signature(writer.public_key, row["hash"], row["sig"]):
            return fail(number, f"signature does not verify with {writer.code}'s key")

        machine = row["machine_id"]
        if not isinstance(machine, str) or not machine:
            return fail(number, "machine_id missing")
        if number == 1:
            first_machine = machine
        elif machine != first_machine:
            return fail(
                number,
                "machine_id differs from the first row's "
                "(two machines writing one chain)",
            )

        good.append(row)
        prev = row["hash"]  # equal to the hash computed above

    if torn_tail is not None:
        # ChainBrokenError looks for the "torn tail" prefix to suggest bgl repair.
        return fail(
            len(lines) + 1,
            f"torn tail: {len(torn_tail)} bytes after the last complete line",
        )
    return ChainReport(writer.code, tuple(good), prev, None)


def verify_all_chains(root: Path, project: Project) -> LedgerReport:
    """Verify every registered chain, and list chain files outside the registry.

    Runs under the ledger lock (R18), so no append can land halfway through.
    """
    with ledger_lock(root):
        return verify_all_chains_unlocked(root, project)


def verify_all_chains_unlocked(root: Path, project: Project) -> LedgerReport:
    """verify_all_chains for a caller that already holds the ledger lock.

    [DECISION] The run lifecycle holds the ledger lock from its pre-open checks
    through the open row, so a second process cannot use the same budget unit
    or run ID in between. Locks are not reentrant, hence the unlocked variants.
    """
    chains = {
        code: verify_chain(*read_chain(chain_path(root, project, code)), writer)
        for code, writer in project.writers.items()
    }
    # [DECISION] Only *.jsonl directly in the ledger folder count. Names are
    # compared exactly, so cg1.jsonl is unregistered when the code is CG1.
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
    """The only way a row enters the ledger (R7, R18).

    Verifies the writer's chain under the ledger lock, then builds the row on the
    verified head and appends it. Raises ChainBrokenError on any problem,
    including a torn tail, without touching the file.
    """
    with ledger_lock(root):
        return append_row_unlocked(root, project, writer, private_key, fields)


def append_row_unlocked(
    root: Path,
    project: Project,
    writer: Writer,
    private_key: Ed25519PrivateKey,
    fields: Mapping[str, Any],
) -> dict[str, Any]:
    """append_row for a caller that already holds the ledger lock.

    Still verifies the writer's whole chain first (R7): holding the lock is no
    reason to trust the file.
    """
    merged = _with_chain_code(fields, writer)
    path = chain_path(root, project, writer.code)
    lines, tail = read_chain(path)
    report = verify_chain(lines, tail, writer)
    if report.problem is not None:
        raise ChainBrokenError(writer.code, report.problem.line, report.problem.reason)
    row = build_row(merged, report.head, private_key)
    append_line(path, line_bytes(row))
    return row


def repair_chain(
    root: Path,
    project: Project,
    writer: Writer,
    private_key: Ed25519PrivateKey,
    fields: Mapping[str, Any],
) -> dict[str, Any]:
    """Remove a torn tail and record it in a repair row (R14).

    Only a torn tail is ever removed. A broken complete line refuses the repair,
    so repair can never hide a broken row.
    """
    if "event" in fields or "removed" in fields:
        raise ValueError("repair_chain sets event and removed itself")
    merged = _with_chain_code(fields, writer)
    with ledger_lock(root):
        path = chain_path(root, project, writer.code)
        lines, tail = read_chain(path)
        report = verify_chain(lines, None, writer)  # complete lines only
        if report.problem is not None:
            raise ChainBrokenError(
                writer.code, report.problem.line, report.problem.reason
            )
        if tail is None:
            raise NothingToRepairError(writer.code)
        # Hash the bytes before they are gone.
        merged["event"] = "repair"
        merged["removed"] = {
            "length": len(tail),
            "sha256": hashlib.sha256(tail).hexdigest(),
        }
        # [DECISION] A crash between truncating and appending leaves a clean
        # chain with no repair row. Accepted: the removed bytes were never a
        # complete signed row, so no evidence is lost.
        truncate_tail(path, len(tail))
        row = build_row(merged, report.head, private_key)
        append_line(path, line_bytes(row))
    return row


def _with_chain_code(fields: Mapping[str, Any], writer: Writer) -> dict[str, Any]:
    """A copy of fields with chain set to the writer's code.

    [DECISION] Checked before the lock is taken or any file changes. A caller may
    leave chain out; a different code is refused, so no caller can write into
    another writer's chain.
    """
    merged = dict(fields)
    chain = merged.setdefault("chain", writer.code)
    if chain != writer.code:
        raise ValueError(
            f"fields name chain {chain!r}, but the writer is {writer.code}"
        )
    return merged

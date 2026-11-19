"""Witnessing (R23, G2): Rekor shows no head was thrown away, OpenTimestamps when.

After each append the writer's new head goes to Rekor and to the declared
OpenTimestamps calendars. What comes back is committed with the ledger:

- <ledger>/witness/<CODE>/<head>.json: the Rekor entry, its signed timestamp,
  its inclusion proof, and the log key it was checked with;
- <ledger>/proofs/<CODE>/<head>.ots: the OpenTimestamps proof.

Witness status is worked out from these files and the logs, never written in a
row. Network failures never stop a run: the head simply stays pending, and
`bgl witness` retries it.
"""

import contextlib
import dataclasses
import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, Final

from bgl import rekor, stamps
from bgl.canonical import HASH_HEX, canonical_json
from bgl.config import Project, Writer
from bgl.errors import WitnessError
from bgl.file_io import atomic_write
from bgl.ledger import verify_chain
from bgl.ledger_io import chain_path, ledger_dir, read_chain
from bgl.locks import ledger_lock
from bgl.records import LedgerView
from bgl.writers import Identity

WITNESS_DIR: Final[str] = "witness"
PROOFS_DIR: Final[str] = "proofs"


def record_path(root: Path, project: Project, code: str, head: str) -> Path:
    return ledger_dir(root, project) / WITNESS_DIR / code / f"{head}.json"


def proof_path(root: Path, project: Project, code: str, head: str) -> Path:
    return ledger_dir(root, project) / PROOFS_DIR / code / f"{head}.ots"


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, data)


@dataclasses.dataclass
class SubmitResult:
    logged: list[str] = dataclasses.field(default_factory=list)
    stamped: list[str] = dataclasses.field(default_factory=list)
    problems: list[str] = dataclasses.field(default_factory=list)


def own_heads(root: Path, project: Project, writer: Writer) -> list[str]:
    """Every head of the writer's chain, oldest first, as far as it verifies."""
    with ledger_lock(root):
        lines, tail = read_chain(chain_path(root, project, writer.code))
    report = verify_chain(lines, tail, writer)
    return [row["hash"] for row in report.rows]


def submit_heads(
    root: Path, project: Project, identity: Identity, heads: Iterable[str]
) -> SubmitResult:
    """Log and stamp each head that has no record or proof yet."""
    result = SubmitResult()
    witness = project.witness
    if witness is None:
        return result
    code = identity.writer.code
    for head in heads:
        record_file = record_path(root, project, code, head)
        if not record_file.exists():
            try:
                record = rekor.submit(witness.rekor_url, identity.private_key, head)
                rekor.check_record(record, identity.writer.public_key)
                _write(record_file, canonical_json(record) + b"\n")
                result.logged.append(head)
            except WitnessError as error:
                result.problems.append(f"Rekor, head {head[:12]}: {error}")
        proof_file = proof_path(root, project, code, head)
        if not proof_file.exists():
            try:
                proof, failed = stamps.stamp(head, witness.ots_calendars)
                _write(proof_file, proof)
                result.stamped.append(head)
                result.problems.extend(
                    f"OpenTimestamps calendar {url} did not answer" for url in failed
                )
            except WitnessError as error:
                result.problems.append(f"OpenTimestamps, head {head[:12]}: {error}")
    return result


def submit_current_head(
    root: Path, project: Project, identity: Identity
) -> SubmitResult:
    """R23: witness the writer's newest head.

    [DECISION] After an append only the new head is submitted, so a backlog
    from an offline spell never delays a run's code. `bgl witness` submits
    the backlog.
    """
    if project.witness is None:
        return SubmitResult()
    heads = own_heads(root, project, identity.writer)
    return submit_heads(root, project, identity, heads[-1:])


def after_append(root: Path, project: Project, identity: Identity) -> None:
    """submit_current_head, never raising: witnessing must not stop a run."""
    with contextlib.suppress(Exception):
        submit_current_head(root, project, identity)


def submit_pending(root: Path, project: Project, identity: Identity) -> SubmitResult:
    """Every head of the writer's chain still missing a record or a proof."""
    return submit_heads(
        root, project, identity, own_heads(root, project, identity.writer)
    )


@dataclasses.dataclass
class UpgradeResult:
    upgraded: int = 0
    pending: int = 0


def upgrade_proofs(root: Path, project: Project) -> UpgradeResult:
    """Fetch completed Bitcoin paths for every pending proof of every chain."""
    result = UpgradeResult()
    witness = project.witness
    if witness is None:
        return result
    for code in project.writers:
        folder = ledger_dir(root, project) / PROOFS_DIR / code
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.ots")):
            head = path.stem
            if not HASH_HEX.fullmatch(head):
                continue
            try:
                data, changed = stamps.upgrade(
                    path.read_bytes(), head, witness.ots_calendars
                )
            except WitnessError:
                continue
            if changed:
                _write(path, data)
                result.upgraded += 1
            if not stamps.state(data, head).heights:
                result.pending += 1
    return result


# --- what the files show (offline) ------------------------------------------


@dataclasses.dataclass(frozen=True)
class ChainWitness:
    code: str
    heads: int
    logged: frozenset[str]  # heads with a Rekor record that checks out
    stamped: frozenset[str]  # heads with a readable proof
    confirmed: frozenset[str]  # heads whose proof claims a Bitcoin block
    problems: tuple[str, ...]


def _heads_of(view: LedgerView, code: str) -> list[str]:
    chain = view.report.chains.get(code)
    return [] if chain is None else [row["hash"] for row in chain.rows]


def _check_records(
    folder: Path, code: str, writer: Writer, heads: set[str]
) -> tuple[set[str], list[str]]:
    logged: set[str] = set()
    problems: list[str] = []
    for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        head = path.stem
        if head not in heads:
            problems.append(
                f"Rekor record {path.name} is for a head that is not in chain "
                f"{code}: the chain was rewritten after it was witnessed"
            )
            continue
        try:
            record = json.loads(path.read_bytes())
            if not isinstance(record, dict) or record.get("head") != head:
                raise WitnessError("the record names a different head")
            rekor.check_record(record, writer.public_key)
            logged.add(head)
        except (WitnessError, ValueError) as error:
            problems.append(f"Rekor record {path.name} does not check out: {error}")
    return logged, problems


def _check_proofs(
    folder: Path, code: str, heads: set[str]
) -> tuple[set[str], set[str], list[str]]:
    stamped: set[str] = set()
    confirmed: set[str] = set()
    problems: list[str] = []
    for path in sorted(folder.glob("*.ots")) if folder.is_dir() else []:
        head = path.stem
        if head not in heads:
            problems.append(
                f"stamp {path.name} is for a head that is not in chain {code}: "
                "the chain was rewritten after it was stamped"
            )
            continue
        try:
            stamp_state = stamps.state(path.read_bytes(), head)
        except WitnessError as error:
            problems.append(f"stamp {path.name} is not valid: {error}")
            continue
        stamped.add(head)
        if stamp_state.heights:
            confirmed.add(head)
    return stamped, confirmed, problems


def chain_witness(root: Path, project: Project, view: LedgerView) -> list[ChainWitness]:
    """Check every witness file against the chains, without the network.

    A record or proof for a head that is not in its chain is a problem: the
    head was witnessed, then left the chain, which is what a thrown-away run
    looks like.
    """
    found = []
    base = ledger_dir(root, project)
    for code, writer in project.writers.items():
        heads = set(_heads_of(view, code))
        logged, record_problems = _check_records(
            base / WITNESS_DIR / code, code, writer, heads
        )
        stamped, confirmed, proof_problems = _check_proofs(
            base / PROOFS_DIR / code, code, heads
        )
        found.append(
            ChainWitness(
                code=code,
                heads=len(heads),
                logged=frozenset(logged),
                stamped=frozenset(stamped),
                confirmed=frozenset(confirmed),
                problems=tuple(record_problems + proof_problems),
            )
        )
    for folder_name in (WITNESS_DIR, PROOFS_DIR):
        folder = base / folder_name
        if not folder.is_dir():
            continue
        for entry in sorted(folder.iterdir()):
            if entry.is_dir() and entry.name not in project.writers:
                problem = f"{folder_name}/{entry.name} belongs to no registered chain"
                empty: frozenset[str] = frozenset()
                found.append(
                    ChainWitness(entry.name, 0, empty, empty, empty, (problem,))
                )
    return found


def unwitnessed_opens(view: LedgerView, chains: Iterable[ChainWitness]) -> list[str]:
    """Opens with no witnessed head between the open row and its close row.

    A head is witnessed when it has both a Rekor record and a stamp. Its
    timing (whether Bitcoin confirmed it, and when) is judged by the gate.
    """
    witnessed = {chain.code: chain.logged & chain.stamped for chain in chains}
    missing = []
    for code, chain in view.report.chains.items():
        rows = list(chain.rows)
        closes = {
            row["id"]: index
            for index, row in enumerate(rows)
            if row["event"] == "close"
        }
        for index, row in enumerate(rows):
            if row["event"] != "open":
                continue
            end = closes.get(row["id"], len(rows))
            window = {rows[i]["hash"] for i in range(index, end)}
            if not window & witnessed.get(code, set()):
                missing.append(str(row["id"]))
    return missing


# --- the gate's discard check (network) -------------------------------------


@dataclasses.dataclass(frozen=True)
class DiscardReport:
    code: str
    entries: int
    matched: int
    unmatched: tuple[tuple[str, int | None], ...]  # (uuid, integrated time)
    invalid: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.unmatched and not self.invalid


def discard_check(
    project: Project, writer: Writer, heads: Iterable[str]
) -> DiscardReport:
    """G2: every head Rekor holds for this writer's key must still be in the chain.

    Raises WitnessError if Rekor cannot be asked; the gate then cannot pass the
    witnessed level.
    """
    witness = project.witness
    if witness is None:
        raise WitnessError("witnessing is off for this project")
    expected = {rekor.message_digest(head) for head in heads}
    uuids = rekor.search_by_key(witness.rekor_url, writer.public_key)
    entries = rekor.retrieve(witness.rekor_url, uuids)
    matched = 0
    unmatched: list[tuple[str, int | None]] = []
    invalid: list[str] = []
    for uuid in uuids:
        entry = _entry_for(entries, uuid)
        if entry is None:
            invalid.append(f"{uuid}: listed by the index but not returned")
            continue
        try:
            data_hash, _ = rekor.check_body(entry, writer.public_key)
        except WitnessError as error:
            invalid.append(f"{uuid}: {error}")
            continue
        if data_hash in expected:
            matched += 1
        else:
            time = entry.get("integratedTime")
            unmatched.append((uuid, time if isinstance(time, int) else None))
    return DiscardReport(
        code=writer.code,
        entries=len(uuids),
        matched=matched,
        unmatched=tuple(unmatched),
        invalid=tuple(invalid),
    )


def _entry_for(entries: Mapping[str, Any], uuid: str) -> Mapping[str, Any] | None:
    """Rekor may key entries by the long UUID or by the 64-character leaf hash."""
    for key, entry in entries.items():
        if key == uuid or key.endswith(uuid[-64:]) or uuid.endswith(key[-64:]):
            return entry if isinstance(entry, Mapping) else None
    return None

"""Witnessing (R23, G2): Rekor shows no head was thrown away, OpenTimestamps when.

After each append the writer's new head goes to Rekor and to the declared
OpenTimestamps calendars. What comes back is committed with the ledger:

- <ledger>/witness/<CODE>/<head>.json: the Rekor entry, its signed timestamp,
  its inclusion proof, and the log key it was checked with;
- <ledger>/proofs/<CODE>/<head>.ots: the OpenTimestamps proof.

Witness status is worked out from these files and the logs, never written in a
row. The one exception (N10): a run's close file records the entry the R23 call
returned for its open head, as `open_witness`, and the close row binds it
through close_sha256. Network failures never stop a run: the head simply stays
pending, and `bgl witness` retries it, though a retried open head never makes
that run's open witnessed.
"""

import dataclasses
import hashlib
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

_ABANDONED: Final[str] = "abandoned"
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
    # Each head logged by this call, mapped to the record Rekor returned for it.
    records: dict[str, dict[str, Any]] = dataclasses.field(default_factory=dict)


OPEN_WITNESS_FIELDS: Final[tuple[str, ...]] = (
    "uuid",
    "log_index",
    "integrated_time",
    "log_id",
    "signed_entry_timestamp",
)


def open_witness(record: Mapping[str, Any]) -> dict[str, Any]:
    """The close file's open_witness: the Rekor entry the R23 call returned.

    [DECISION] N10 (spec text pending): UUID, log index, integrated time, log
    ID and signed entry timestamp, taken from a record that check_record has
    already accepted. Raises WitnessError if a field is missing or malformed.
    """
    try:
        entry = record["entry"]
        found = {
            "uuid": record["uuid"],
            "log_index": entry["logIndex"],
            "integrated_time": entry["integratedTime"],
            "log_id": entry["logID"],
            "signed_entry_timestamp": entry["verification"]["signedEntryTimestamp"],
        }
    except (KeyError, TypeError):
        raise WitnessError("the Rekor record lacks an open_witness field") from None
    for name in ("uuid", "log_id", "signed_entry_timestamp"):
        if not isinstance(found[name], str) or not found[name]:
            raise WitnessError(f"the Rekor record's {name} is not text")
    for name in ("log_index", "integrated_time"):
        if type(found[name]) is not int:
            raise WitnessError(f"the Rekor record's {name} is not a whole number")
    return found


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
                result.records[head] = record
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
    root: Path, project: Project, identity: Identity, head: str | None = None
) -> dict[str, Any] | None:
    """R23: witness the writer's newest head, or the given head.

    Returns the Rekor record this call obtained and checked for the head, or
    None if Rekor was not reached, refused it, or witnessing is off (N10).

    [DECISION] After an append only the new head is submitted, so a backlog
    from an offline spell never delays a run's code. `bgl witness` submits
    the backlog.

    [DECISION] N10 (spec text pending): a caller that knows the head it just
    appended passes it. Another process of the same writer may have appended
    since, and "the newest head" would then be that process's row. A head
    that already had a record before this call gets None: only an entry
    this call made is the R23 call's result.
    """
    if project.witness is None:
        return None
    if head is None:
        heads = own_heads(root, project, identity.writer)
        if not heads:
            return None
        head = heads[-1]
    result = submit_heads(root, project, identity, [head])
    return result.records.get(head)


def after_append(
    root: Path, project: Project, identity: Identity, head: str | None = None
) -> dict[str, Any] | None:
    """submit_current_head, never raising Exception: witnessing must not stop a run.

    Returns the record from the call, or None on any failure (N10). A
    KeyboardInterrupt still propagates; the caller decides what it means.
    """
    try:
        return submit_current_head(root, project, identity, head)
    except Exception:
        return None


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
                # [DECISION] F21: a proof that cannot be upgraded now is left as
                # it is and retried next time; verify reports unreadable ones.
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


@dataclasses.dataclass(frozen=True)
class OpenWitnessReport:
    """Every open row's witness state as far as the files here show (N10, N60).

    witnessed rests on stored records only: not checked against the log
    (N11). The gate asks the live log.
    """

    witnessed: tuple[str, ...]
    unwitnessed: tuple[str, ...]
    pending: tuple[str, ...]  # in progress here, with no close row yet


def _stored_open_witness(
    root: Path, project: Project, code: str, head: str
) -> dict[str, Any] | None:
    """open_witness built from the stored record for head, or None."""
    try:
        record = json.loads(record_path(root, project, code, head).read_bytes())
        return open_witness(record)
    except (OSError, ValueError, WitnessError):
        return None


def _recorded_open_witness(
    root: Path, project: Project, run_id: str, close: Mapping[str, Any]
) -> object:
    """The open_witness a normal close's close file holds, or None.

    [DECISION] The close file is read only at the place BGL writes it, and only
    if it still matches the close row's close_sha256, so an edited open_witness
    never counts (N10).
    """
    expected = f"{project.runs_dir}/{run_id}.close.json"
    if close["close_file"] != expected:
        return None
    try:
        data = (root / expected).read_bytes()
    except OSError:
        return None
    if hashlib.sha256(data).hexdigest() != close["close_sha256"]:
        return None
    try:
        content = json.loads(data)
    except ValueError:
        return None
    return content.get("open_witness") if isinstance(content, dict) else None


def classify_opens(
    root: Path,
    project: Project,
    view: LedgerView,
    chains: Iterable[ChainWitness],
    in_progress: Iterable[str] = (),
) -> OpenWitnessReport:
    """Sort every open row into witnessed, unwitnessed or pending.

    Only the open head matters: the open row's own hash, which R23 submitted
    before the run's code started (N10). An entry made later, by `bgl witness`
    say, never makes a closed run's open witnessed.

    - A run with a normal (not abandoned) close is witnessed when its close
      file's open_witness is not null, equals the stored record for its open
      head, and that head has a stamp.
    - A run with no normal close (no close row, or only an abandoned one) is
      pending while in progress here. Otherwise it is witnessed when a stored
      record and a stamp exist for its open head (N60): it has no result or
      series, so a late entry gains nothing.

    [DECISION] F18 is met by construction: no window of heads after the open
    is consulted, so no close row (in this or another chain) can extend it.
    [DECISION] Both cases also need a stamp of the open head, as the spec's
    witness definition does; whether Bitcoin confirmed it, and when, is the
    gate's question. Checking the record against the live log is the gate's
    too (N11).
    """
    usable = {chain.code: chain.logged & chain.stamped for chain in chains}
    running = set(in_progress)
    closes: dict[object, list[Mapping[str, Any]]] = {}
    for row in view.rows:
        if row["event"] == "close":
            closes.setdefault(row["id"], []).append(row)
    witnessed: list[str] = []
    unwitnessed: list[str] = []
    pending: list[str] = []
    for code, chain in view.report.chains.items():
        for row in chain.rows:
            if row["event"] != "open":
                continue
            run_id, head = str(row["id"]), row["hash"]
            run_closes = closes.get(row["id"], [])
            # [DECISION] N10, N60: a close is normal by its status, not by its
            # close_file. Only an abandoned close may lack a close file (spec),
            # so a normal close with none never earns N60's leniency: it has
            # no open_witness and the open is unwitnessed. verify fails it too.
            normal = [close for close in run_closes if close["status"] != _ABANDONED]
            if not run_closes and run_id in running:
                pending.append(run_id)
                continue
            ok = head in usable.get(code, set())
            if ok and normal:
                stored = _stored_open_witness(root, project, code, head)
                ok = stored is not None and all(
                    _recorded_open_witness(root, project, run_id, close) == stored
                    for close in normal
                )
            (witnessed if ok else unwitnessed).append(run_id)
    return OpenWitnessReport(tuple(witnessed), tuple(unwitnessed), tuple(pending))


def unwitnessed_opens(
    root: Path,
    project: Project,
    view: LedgerView,
    chains: Iterable[ChainWitness],
    in_progress: Iterable[str] = (),
) -> list[str]:
    """The run IDs classify_opens finds unwitnessed."""
    return list(classify_opens(root, project, view, chains, in_progress).unwitnessed)


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

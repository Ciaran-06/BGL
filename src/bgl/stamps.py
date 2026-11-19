"""OpenTimestamps: proof that each head existed by a Bitcoin block's time.

A head's stamp is a standard .ots proof for the witness message
"bgl-witness-v1:" + head, so the `ots` tools can check it too. Calendars reply
at once with a pending proof; a few hours later, once Bitcoin has confirmed the
calendar's transaction, `upgrade` fetches the completed path to a block header.
"""

import base64
import dataclasses
import hashlib
import json
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable, Sequence
from typing import Any, Final

from opentimestamps.calendar import RemoteCalendar
from opentimestamps.core.notary import BitcoinBlockHeaderAttestation, PendingAttestation
from opentimestamps.core.op import OpSHA256
from opentimestamps.core.serialize import (
    BytesDeserializationContext,
    BytesSerializationContext,
    DeserializationError,
)
from opentimestamps.core.timestamp import DetachedTimestampFile, Timestamp

from bgl import netio
from bgl.errors import WitnessError
from bgl.rekor import witness_message

# [DECISION] Used for stamp checks when the gate is given neither a node nor
# explorers. Two independent operators of the same (Esplora) API.
DEFAULT_EXPLORERS: Final[tuple[str, ...]] = (
    "https://blockstream.info/api",
    "https://mempool.space/api",
)


def digest(head: str) -> bytes:
    """What gets stamped: SHA-256 of the witness message."""
    return hashlib.sha256(witness_message(head)).digest()


def _to_bytes(proof: DetachedTimestampFile) -> bytes:
    context = BytesSerializationContext()
    proof.serialize(context)
    data: bytes = context.getbytes()
    return data


def read(data: bytes, head: str) -> DetachedTimestampFile:
    """Parse an .ots proof and check it is a proof for this head."""
    try:
        proof = DetachedTimestampFile.deserialize(BytesDeserializationContext(data))
    except (DeserializationError, ValueError, TypeError) as error:
        raise WitnessError(f"not a valid OpenTimestamps proof: {error}") from None
    if not isinstance(proof.file_hash_op, OpSHA256) or proof.file_digest != digest(
        head
    ):
        raise WitnessError("the proof is for a different head")
    return proof


def stamp(head: str, calendars: Sequence[str]) -> tuple[bytes, list[str]]:
    """Submit a head to every calendar. Returns the proof and the calendars that failed.

    Raises WitnessError only if every calendar failed.
    """
    target = digest(head)
    proof = DetachedTimestampFile(OpSHA256(), Timestamp(target))
    failed: list[str] = []
    for url in calendars:
        try:
            reply = RemoteCalendar(netio.resolve(url)).submit(
                target, timeout=netio.TIMEOUT
            )
            proof.timestamp.merge(reply)
        except Exception:  # noqa: BLE001 - the library raises plain Exception
            failed.append(url)
    if len(failed) == len(calendars):
        raise WitnessError("no OpenTimestamps calendar accepted the head")
    return _to_bytes(proof), failed


def _pending(timestamp: Timestamp) -> list[tuple[Timestamp, str]]:
    found = [
        (timestamp, attestation.uri)
        for attestation in timestamp.attestations
        if isinstance(attestation, PendingAttestation)
    ]
    for child in timestamp.ops.values():
        found.extend(_pending(child))
    return found


def upgrade(data: bytes, head: str, calendars: Iterable[str]) -> tuple[bytes, bool]:
    """Ask calendars for completed paths. Returns the proof and whether it grew.

    [DECISION] Only calendars named in bgl.toml are asked: a pending
    attestation's URL comes from a file anyone could edit.
    """
    proof = read(data, head)
    allowed = {url.rstrip("/") for url in calendars}
    changed = False
    for timestamp, uri in _pending(proof.timestamp):
        if uri.rstrip("/") not in allowed:
            continue
        try:
            reply = RemoteCalendar(netio.resolve(uri)).get_timestamp(
                timestamp.msg, timeout=netio.TIMEOUT
            )
        except Exception:  # noqa: BLE001 - not confirmed yet, or unreachable
            continue
        before = _to_bytes(proof)
        timestamp.merge(reply)
        changed = changed or _to_bytes(proof) != before
    return _to_bytes(proof), changed


@dataclasses.dataclass(frozen=True)
class StampState:
    pending: tuple[str, ...]  # calendars still to confirm
    heights: tuple[int, ...]  # Bitcoin block heights the proof claims


def state(data: bytes, head: str) -> StampState:
    """What a proof claims, without checking anything against Bitcoin."""
    proof = read(data, head)
    pending, heights = set(), set()
    for _, attestation in proof.timestamp.all_attestations():
        if isinstance(attestation, PendingAttestation):
            pending.add(attestation.uri)
        elif isinstance(attestation, BitcoinBlockHeaderAttestation):
            heights.add(attestation.height)
    return StampState(tuple(sorted(pending)), tuple(sorted(heights)))


# --- checking against Bitcoin ----------------------------------------------

BlockSource = Callable[[int], tuple[str, int]]
"""height -> (merkle root as displayed hex, block time in Unix seconds)."""


def explorer_source(urls: Sequence[str]) -> BlockSource:
    """Block data from Esplora-style explorers, which must all agree.

    [DECISION] At least two explorers are required, per the spec, and any
    disagreement is an error rather than a vote.
    """
    if len(urls) < 2:
        raise ValueError("stamp checks need at least two block explorers")

    def lookup(height: int) -> tuple[str, int]:
        answers = set()
        for url in urls:
            block_hash = (
                netio.request(
                    "GET",
                    netio.join(url, f"/block-height/{height}"),
                    accept="text/plain",
                )
                .decode("ascii")
                .strip()
            )
            block = netio.request_json("GET", netio.join(url, f"/block/{block_hash}"))
            try:
                answers.add((str(block["merkle_root"]), int(block["timestamp"])))
            except (KeyError, TypeError, ValueError):
                raise WitnessError(f"{url} sent an unexpected block") from None
        if len(answers) != 1:
            raise WitnessError(f"block explorers disagree about block {height}")
        return answers.pop()

    return lookup


def node_source(url: str) -> BlockSource:
    """Block data from a Bitcoin Core node's JSON-RPC: http://user:pass@host:port."""
    parts = urllib.parse.urlsplit(url)
    host = parts.netloc.rpartition("@")[2]
    endpoint = urllib.parse.urlunsplit((parts.scheme, host, parts.path or "/", "", ""))
    headers = {"Content-Type": "application/json"}
    if parts.username is not None:
        token = f"{parts.username}:{parts.password or ''}".encode()
        headers["Authorization"] = "Basic " + base64.b64encode(token).decode("ascii")

    def call(method: str, params: list[Any]) -> Any:
        body = json.dumps(
            {"jsonrpc": "1.0", "id": "bgl", "method": method, "params": params}
        ).encode("utf-8")
        request = urllib.request.Request(
            netio.resolve(endpoint), data=body, method="POST", headers=headers
        )
        try:
            with urllib.request.urlopen(request, timeout=netio.TIMEOUT) as response:
                reply = json.loads(response.read())
        except (OSError, ValueError) as error:
            raise WitnessError(f"Bitcoin node call {method} failed: {error}") from None
        if not isinstance(reply, dict) or reply.get("error"):
            raise WitnessError(f"Bitcoin node call {method} failed: {reply}")
        return reply["result"]

    def lookup(height: int) -> tuple[str, int]:
        header = call("getblockheader", [call("getblockhash", [height])])
        return str(header["merkleroot"]), int(header["time"])

    return lookup


@dataclasses.dataclass(frozen=True)
class Confirmation:
    height: int
    block_time: int


def confirm(data: bytes, head: str, source: BlockSource) -> list[Confirmation]:
    """Every Bitcoin attestation in the proof that checks out, earliest first.

    A Bitcoin attestation's message must equal the block's Merkle root (in
    internal byte order). One that does not is an error, not a skip.
    """
    proof = read(data, head)
    found: dict[int, Confirmation] = {}
    for message, attestation in proof.timestamp.all_attestations():
        if not isinstance(attestation, BitcoinBlockHeaderAttestation):
            continue
        root_hex, block_time = source(attestation.height)
        if bytes.fromhex(root_hex)[::-1] != message:
            raise WitnessError(
                f"the proof claims block {attestation.height}, but its path does "
                "not reach that block's Merkle root"
            )
        found[attestation.height] = Confirmation(attestation.height, block_time)
    return [found[height] for height in sorted(found)]

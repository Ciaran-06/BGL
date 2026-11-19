"""Rekor: the public transparency log that witnesses each writer's heads.

Each head is logged as a Rekor v1 `rekord` entry whose content is the text
"bgl-witness-v1:" + head, signed by the writer's Ed25519 key in SSH signature
format. Rekor returns the entry with a signed entry timestamp and an inclusion
proof; BGL keeps both, so the record can be checked later without trusting the
writer. At gate time, BGL lists every entry made with each writer's key: a
head that is in Rekor but not in the chain was thrown away (G2).
"""

import base64
import binascii
import dataclasses
import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any, Final

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from bgl import merkle, netio, sshsig
from bgl.canonical import HASH_HEX
from bgl.errors import WitnessError

WITNESS_PREFIX: Final[str] = "bgl-witness-v1:"
RECORD_FORMAT_VERSION: Final[int] = 1
# [DECISION] Search results are fetched 10 entries per request.
RETRIEVE_BATCH: Final[int] = 10


def witness_message(head: str) -> bytes:
    """The bytes a writer signs and logs for one head."""
    if not HASH_HEX.fullmatch(head):
        raise ValueError(f"not a head hash: {head!r}")
    return (WITNESS_PREFIX + head).encode("ascii")


def message_digest(head: str) -> str:
    """SHA-256 of the witness message: what Rekor stores as the data hash."""
    return hashlib.sha256(witness_message(head)).hexdigest()


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def public_key_content(public: bytes) -> str:
    """The publicKey.content Rekor expects for an SSH key: base64 of the line."""
    return _b64(sshsig.authorized_key(public).encode("ascii"))


def build_entry(private_key: Ed25519PrivateKey, head: str) -> dict[str, Any]:
    """The rekord entry proposing one head."""
    message = witness_message(head)
    public = private_key.public_key().public_bytes_raw()
    return {
        "apiVersion": "0.0.1",
        "kind": "rekord",
        "spec": {
            "signature": {
                "format": "ssh",
                "content": _b64(sshsig.sign(private_key, message).encode("ascii")),
                "publicKey": {"content": public_key_content(public)},
            },
            "data": {"content": _b64(message)},
        },
    }


# --- the service -----------------------------------------------------------


def submit(url: str, private_key: Ed25519PrivateKey, head: str) -> dict[str, Any]:
    """Log one head. Returns the record BGL stores (see make_record)."""
    created = netio.request_json(
        "POST", netio.join(url, "/api/v1/log/entries"), build_entry(private_key, head)
    )
    if not isinstance(created, dict) or len(created) != 1:
        raise WitnessError("Rekor's reply to a new entry was not one entry")
    uuid, entry = next(iter(created.items()))
    return make_record(url, head, uuid, entry, log_public_key(url))


def log_public_key(url: str) -> str:
    """The log's public key as PEM text, as the log publishes it.

    [DECISION] The key is taken from the log itself when an entry is made and
    stored in the record. The SHA-256 of its DER form must equal the entry's
    logID, which ties the key to the entry; pinning the key through Sigstore's
    TUF root is not done.
    """
    data = netio.request(
        "GET", netio.join(url, "/api/v1/log/publicKey"), accept="application/x-pem-file"
    )
    return data.decode("ascii", "replace")


def search_by_key(url: str, public: bytes) -> list[str]:
    """UUIDs of every entry Rekor's index holds for this public key."""
    found = netio.request_json(
        "POST",
        netio.join(url, "/api/v1/index/retrieve"),
        {"publicKey": {"format": "ssh", "content": public_key_content(public)}},
    )
    if not isinstance(found, list) or not all(isinstance(u, str) for u in found):
        raise WitnessError("Rekor's index search did not return a list of UUIDs")
    return sorted(set(found))


def retrieve(url: str, uuids: Sequence[str]) -> dict[str, dict[str, Any]]:
    """The entries for these UUIDs, keyed by UUID."""
    entries: dict[str, dict[str, Any]] = {}
    for start in range(0, len(uuids), RETRIEVE_BATCH):
        batch = list(uuids[start : start + RETRIEVE_BATCH])
        found = netio.request_json(
            "POST",
            netio.join(url, "/api/v1/log/entries/retrieve"),
            {"entryUUIDs": batch},
        )
        if not isinstance(found, list):
            raise WitnessError("Rekor's entry retrieval did not return a list")
        for item in found:
            if isinstance(item, dict):
                entries.update(item)
    return entries


# --- records and their checks ---------------------------------------------


def make_record(
    url: str, head: str, uuid: str, entry: Mapping[str, Any], log_key_pem: str
) -> dict[str, Any]:
    """What BGL commits for one witnessed head: the entry as Rekor returned it."""
    return {
        "format_version": RECORD_FORMAT_VERSION,
        "head": head,
        "rekor_url": url,
        "uuid": uuid,
        "entry": dict(entry),
        "log_public_key": log_key_pem,
    }


@dataclasses.dataclass(frozen=True)
class EntryFacts:
    """What a checked Rekor entry shows."""

    uuid: str
    log_index: int
    integrated_time: int
    data_hash: str


def _body(entry: Mapping[str, Any]) -> tuple[bytes, dict[str, Any]]:
    try:
        raw = base64.b64decode(entry["body"], validate=True)
        body = json.loads(raw)
    except (KeyError, TypeError, binascii.Error, ValueError):
        raise WitnessError("the entry's body is not base64 JSON") from None
    if not isinstance(body, dict):
        raise WitnessError("the entry's body is not a JSON object")
    return raw, body


def check_body(entry: Mapping[str, Any], public: bytes) -> tuple[str, str]:
    """Check the entry is a rekord made with this key.

    Returns (data hash, armored SSH signature). The body holds only the hash of
    the message, so the signature can be checked only by someone who knows the
    message: check_record does, for BGL's own records.
    """
    _, body = _body(entry)
    try:
        spec = body["spec"]
        signature = spec["signature"]
        data_hash = spec["data"]["hash"]
        armored = base64.b64decode(signature["content"], validate=True).decode()
        key_content = signature["publicKey"]["content"]
        kind = (body["kind"], body["apiVersion"], signature["format"])
    except (KeyError, TypeError, binascii.Error, UnicodeDecodeError):
        raise WitnessError("the entry is not a rekord with an SSH signature") from None
    if kind != ("rekord", "0.0.1", "ssh"):
        raise WitnessError(f"unexpected entry kind {kind}")
    if key_content != public_key_content(public):
        raise WitnessError("the entry was made with a different public key")
    if not isinstance(data_hash, dict) or data_hash.get("algorithm") != "sha256":
        raise WitnessError("the entry's data hash is not SHA-256")
    value = data_hash.get("value")
    if not isinstance(value, str) or not HASH_HEX.fullmatch(value):
        raise WitnessError("the entry's data hash is malformed")
    return value, armored


def _load_key(pem: str) -> ec.EllipticCurvePublicKey:
    try:
        key = serialization.load_pem_public_key(pem.encode("ascii"))
    except ValueError:
        raise WitnessError("the stored log public key is not a PEM key") from None
    if not isinstance(key, ec.EllipticCurvePublicKey):
        raise WitnessError("the log public key is not an ECDSA key")
    return key


def _log_id(key: ec.EllipticCurvePublicKey) -> str:
    der = key.public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return hashlib.sha256(der).hexdigest()


def _ecdsa_ok(key: ec.EllipticCurvePublicKey, signature: bytes, data: bytes) -> bool:
    try:
        key.verify(signature, data, ec.ECDSA(hashes.SHA256()))
    except InvalidSignature:
        return False
    return True


def check_set(entry: Mapping[str, Any], key: ec.EllipticCurvePublicKey) -> None:
    """The signed entry timestamp: the log's signature over the entry and its time."""
    payload = json.dumps(
        {
            "body": entry["body"],
            "integratedTime": entry["integratedTime"],
            "logID": entry["logID"],
            "logIndex": entry["logIndex"],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    try:
        signature = base64.b64decode(
            entry["verification"]["signedEntryTimestamp"], validate=True
        )
    except (KeyError, TypeError, binascii.Error):
        raise WitnessError("the entry has no signed entry timestamp") from None
    if not _ecdsa_ok(key, signature, payload):
        raise WitnessError("the signed entry timestamp does not verify")


def parse_checkpoint(
    text: str,
) -> tuple[str, int, bytes, list[tuple[str, bytes]], bytes]:
    """(origin, tree size, root hash, signatures, signed body) of a signed note."""
    body, separator, rest = text.partition("\n\n")
    if not separator:
        raise WitnessError("the checkpoint has no signature block")
    lines = body.split("\n")
    if len(lines) < 3:
        raise WitnessError("the checkpoint is too short")
    try:
        size = int(lines[1])
        root = base64.b64decode(lines[2], validate=True)
    except (ValueError, binascii.Error):
        raise WitnessError("the checkpoint's size or root is malformed") from None
    signatures = []
    for line in rest.strip("\n").split("\n"):
        parts = line.split(" ")
        if len(parts) != 3 or parts[0] != "—":
            raise WitnessError("the checkpoint's signature line is malformed")
        try:
            signatures.append((parts[1], base64.b64decode(parts[2], validate=True)))
        except binascii.Error:
            raise WitnessError("the checkpoint signature is not base64") from None
    return lines[0], size, root, signatures, (body + "\n").encode("utf-8")


def check_inclusion(
    entry: Mapping[str, Any], raw_body: bytes, key: ec.EllipticCurvePublicKey
) -> None:
    """The inclusion proof, and the checkpoint the log signed for its root."""
    try:
        proof = entry["verification"]["inclusionProof"]
        index, size = int(proof["logIndex"]), int(proof["treeSize"])
        root = bytes.fromhex(proof["rootHash"])
        path = [bytes.fromhex(item) for item in proof["hashes"]]
        checkpoint = proof["checkpoint"]
    except (KeyError, TypeError, ValueError):
        raise WitnessError("the entry has no usable inclusion proof") from None
    if not merkle.verify_inclusion(index, size, merkle.leaf_hash(raw_body), path, root):
        raise WitnessError("the inclusion proof does not lead to its root hash")
    _, cp_size, cp_root, signatures, signed = parse_checkpoint(checkpoint)
    if (cp_size, cp_root) != (size, root):
        raise WitnessError("the checkpoint does not match the proof's tree")
    # [DECISION] A checkpoint signature is accepted if any signature on the note
    # verifies with the log key; the 4-byte key hint is not checked.
    if not any(_ecdsa_ok(key, sig[4:], signed) for _, sig in signatures):
        raise WitnessError("the checkpoint signature does not verify")


def check_entry(
    uuid: str, entry: Mapping[str, Any], public: bytes, log_key_pem: str
) -> EntryFacts:
    """Every check on one entry. Raises WitnessError naming the first failure."""
    raw, _ = _body(entry)
    data_hash, _ = check_body(entry, public)
    if not uuid.endswith(merkle.leaf_hash(raw).hex()):
        raise WitnessError("the UUID does not match the entry's body")
    key = _load_key(log_key_pem)
    if entry.get("logID") != _log_id(key):
        raise WitnessError("the log key does not match the entry's log ID")
    check_set(entry, key)
    check_inclusion(entry, raw, key)
    try:
        log_index, integrated = int(entry["logIndex"]), int(entry["integratedTime"])
    except (KeyError, TypeError, ValueError):
        raise WitnessError("the entry's index or time is malformed") from None
    return EntryFacts(uuid, log_index, integrated, data_hash)


def check_record(record: Mapping[str, Any], public: bytes) -> EntryFacts:
    """A stored record: its entry checks out and logs exactly its head."""
    try:
        head, uuid = record["head"], record["uuid"]
        entry, pem = record["entry"], record["log_public_key"]
    except (KeyError, TypeError):
        raise WitnessError("the witness record is missing fields") from None
    if record.get("format_version") != RECORD_FORMAT_VERSION:
        raise WitnessError("the witness record has an unknown format version")
    if not isinstance(head, str) or not HASH_HEX.fullmatch(head):
        raise WitnessError("the witness record's head is malformed")
    facts = check_entry(uuid, entry, public, pem)
    if facts.data_hash != message_digest(head):
        raise WitnessError("the entry logs a different head")
    _, armored = check_body(entry, public)
    if not sshsig.verify(public, witness_message(head), armored):
        raise WitnessError("the entry's SSH signature does not verify")
    return facts

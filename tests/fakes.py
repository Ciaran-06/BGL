"""Local fakes for the witness services, so no test touches the network.

FakeRekor keeps a real RFC 6962 Merkle tree and signs entry timestamps and
checkpoints with its own ECDSA key, so BGL's checks run for real against it.
FakeCalendar and FakeExplorer stand in for OpenTimestamps calendars and an
Esplora block explorer.
"""

import base64
import hashlib
import http.server
import json
import threading
from typing import Any

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from opentimestamps.core.notary import BitcoinBlockHeaderAttestation, PendingAttestation
from opentimestamps.core.op import OpAppend, OpSHA256
from opentimestamps.core.serialize import BytesSerializationContext
from opentimestamps.core.timestamp import Timestamp

from bgl import merkle, sshsig


def tree_root(leaves: list[bytes]) -> bytes:
    """RFC 6962 MTH over leaf hashes."""
    if len(leaves) == 1:
        return leaves[0]
    split = 1
    while split * 2 < len(leaves):
        split *= 2
    return merkle.node_hash(tree_root(leaves[:split]), tree_root(leaves[split:]))


def tree_path(index: int, leaves: list[bytes]) -> list[bytes]:
    """RFC 6962 PATH(m, D[n])."""
    if len(leaves) == 1:
        return []
    split = 1
    while split * 2 < len(leaves):
        split *= 2
    if index < split:
        return tree_path(index, leaves[:split]) + [tree_root(leaves[split:])]
    return tree_path(index - split, leaves[split:]) + [tree_root(leaves[:split])]


class _Server:
    """A threaded HTTP server whose requests go to self.handle."""

    def __init__(self) -> None:
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                pass

            def _serve(self, method: str) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                status, content_type, data = outer.handle(method, self.path, body)
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self) -> None:  # noqa: N802
                self._serve("GET")

            def do_POST(self) -> None:  # noqa: N802
                self._serve("POST")

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_port}"
        self.thread = threading.Thread(
            target=self.httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
        )
        self.thread.start()
        self.down = False

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    def handle(self, method: str, path: str, body: bytes) -> tuple[int, str, bytes]:
        raise NotImplementedError


def _json(status: int, value: Any) -> tuple[int, str, bytes]:
    return status, "application/json", json.dumps(value).encode()


class FakeRekor(_Server):
    TREE_ID = 1193050959916656506

    def __init__(self) -> None:
        super().__init__()
        self.key = ec.generate_private_key(ec.SECP256R1())
        der = self.key.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        self.log_id = hashlib.sha256(der).hexdigest()
        self.pem = self.key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        self.bodies: list[bytes] = []
        self.entries: dict[str, dict[str, Any]] = {}
        self.by_key: dict[bytes, list[str]] = {}
        self.offset = 5000  # global log index of this shard's first entry
        self.search_works = True
        self.check_signatures = True  # False: a dishonest log
        self.lock = threading.Lock()

    def _sign(self, data: bytes) -> bytes:
        return self.key.sign(data, ec.ECDSA(hashes.SHA256()))

    def _entry(self, index: int) -> dict[str, Any]:
        body = self.bodies[index]
        leaves = [merkle.leaf_hash(b) for b in self.bodies]
        root = tree_root(leaves)
        size = len(leaves)
        note = (
            f"fake.rekor - {self.TREE_ID}\n{size}\n{base64.b64encode(root).decode()}\n"
        )
        signature = base64.b64encode(b"\x00\x01\x02\x03" + self._sign(note.encode()))
        checkpoint = note + "\n— fake.rekor " + signature.decode() + "\n"
        entry: dict[str, Any] = {
            "body": base64.b64encode(body).decode(),
            "integratedTime": 1795000000 + index,
            "logID": self.log_id,
            "logIndex": self.offset + index,
        }
        payload = json.dumps(entry, sort_keys=True, separators=(",", ":")).encode()
        entry["verification"] = {
            "signedEntryTimestamp": base64.b64encode(self._sign(payload)).decode(),
            "inclusionProof": {
                "logIndex": index,
                "treeSize": size,
                "rootHash": root.hex(),
                "hashes": [h.hex() for h in tree_path(index, leaves)],
                "checkpoint": checkpoint,
            },
        }
        return entry

    def uuid(self, index: int) -> str:
        return f"{self.TREE_ID:016x}" + merkle.leaf_hash(self.bodies[index]).hex()

    def handle(self, method: str, path: str, body: bytes) -> tuple[int, str, bytes]:
        if self.down:
            return _json(503, {"message": "unavailable"})
        with self.lock:
            if method == "GET" and path.startswith("/api/v1/log/publicKey"):
                return 200, "application/x-pem-file", self.pem
            request = json.loads(body) if body else None
            if method == "POST" and path == "/api/v1/log/entries":
                return self._create(request)
            if method == "POST" and path == "/api/v1/index/retrieve":
                if not self.search_works:
                    return _json(404, {"message": "search disabled"})
                text = base64.b64decode(request["publicKey"]["content"]).decode()
                key = base64.b64decode(text.split()[1])[-32:]
                return _json(200, list(self.by_key.get(key, [])))
            if method == "POST" and path == "/api/v1/log/entries/retrieve":
                found = []
                for uuid in request["entryUUIDs"]:
                    for index in range(len(self.bodies)):
                        if self.uuid(index) == uuid:
                            found.append({uuid: self._entry(index)})
                return _json(200, found)
        return _json(404, {"message": "not found"})

    def _create(self, request: dict[str, Any]) -> tuple[int, str, bytes]:
        spec = request["spec"]
        content = base64.b64decode(spec["data"]["content"])
        key_text = base64.b64decode(spec["signature"]["publicKey"]["content"]).decode()
        blob = base64.b64decode(key_text.split()[1])
        public = blob[-32:]
        armored = base64.b64decode(spec["signature"]["content"]).decode()
        if self.check_signatures and not sshsig.verify(public, content, armored):
            return _json(400, {"message": "signature does not verify"})
        stored = {
            "apiVersion": "0.0.1",
            "kind": "rekord",
            "spec": {
                "data": {
                    "hash": {
                        "algorithm": "sha256",
                        "value": hashlib.sha256(content).hexdigest(),
                    }
                },
                # Real Rekor stores its canonical key text (ssh.MarshalAuthorizedKey:
                # no comment, trailing newline), not the text it was sent.
                "signature": {
                    **spec["signature"],
                    "publicKey": {
                        "content": base64.b64encode(
                            (sshsig.authorized_key(public) + "\n").encode()
                        ).decode()
                    },
                },
            },
        }
        self.bodies.append(
            json.dumps(stored, sort_keys=True, separators=(",", ":")).encode()
        )
        index = len(self.bodies) - 1
        uuid = self.uuid(index)
        self.by_key.setdefault(public, []).append(uuid)
        return _json(201, {uuid: self._entry(index)})

    def add_entry_for(self, public: bytes, content: bytes, private: Any) -> str:
        """Log an entry directly, as a writer who later threw the head away would."""
        request = {
            "spec": {
                "data": {"content": base64.b64encode(content).decode()},
                "signature": {
                    "format": "ssh",
                    "content": base64.b64encode(
                        sshsig.sign(private, content).encode()
                    ).decode(),
                    "publicKey": {
                        "content": base64.b64encode(
                            sshsig.authorized_key(public).encode()
                        ).decode()
                    },
                },
            }
        }
        status, _, data = self._create(request)
        assert status == 201
        return next(iter(json.loads(data)))


def _serialize(timestamp: Timestamp) -> bytes:
    context = BytesSerializationContext()
    timestamp.serialize(context)
    data: bytes = context.getbytes()
    return data


class FakeCalendar(_Server):
    """An OpenTimestamps calendar. confirm() makes every commitment complete."""

    HEIGHT = 850000

    def __init__(self, public_url: str) -> None:
        super().__init__()
        self.public_url = public_url
        self.confirmed = False
        # commitment -> (block height, merkle root) once confirmed
        self.commitments: dict[bytes, tuple[int, bytes] | None] = {}

    def handle(self, method: str, path: str, body: bytes) -> tuple[int, str, bytes]:
        if self.down:
            return 503, "text/plain", b"down"
        if method == "POST" and path == "/digest":
            timestamp = Timestamp(body)
            after_nonce = timestamp.ops.add(
                OpAppend(hashlib.sha256(body).digest()[:16])
            )
            commitment = after_nonce.ops.add(OpSHA256())
            commitment.attestations.add(PendingAttestation(self.public_url))
            self.commitments[commitment.msg] = None
            return 200, "application/octet-stream", _serialize(timestamp)
        if method == "GET" and path.startswith("/timestamp/"):
            commitment = bytes.fromhex(path.rsplit("/", 1)[1])
            if commitment not in self.commitments or not self.confirmed:
                return 404, "text/plain", b"Pending confirmation in Bitcoin blockchain"
            # Each commitment gets its own block, as if mined at different times.
            height = self.HEIGHT + list(self.commitments).index(commitment)
            timestamp = Timestamp(commitment)
            appended = timestamp.ops.add(OpAppend(b"\x02" * 32))
            root = appended.ops.add(OpSHA256())
            root.attestations.add(BitcoinBlockHeaderAttestation(height))
            self.commitments[commitment] = (height, root.msg)
            return 200, "application/octet-stream", _serialize(timestamp)
        return 404, "text/plain", b"not found"


class FakeExplorer(_Server):
    """An Esplora-style explorer that knows the blocks the calendars produced."""

    def __init__(self, calendars: list[FakeCalendar]) -> None:
        super().__init__()
        self.calendars = calendars
        self.lie = False

    def block_root(self, height: int) -> bytes:
        for calendar in self.calendars:
            for found in calendar.commitments.values():
                if found is not None and found[0] == height:
                    return found[1]
        return b"\x00" * 32

    def handle(self, method: str, path: str, body: bytes) -> tuple[int, str, bytes]:
        if path.startswith("/block-height/"):
            height = int(path.rsplit("/", 1)[1])
            return 200, "text/plain", f"{height:064x}".encode()
        if path.startswith("/block/"):
            height = int(path.rsplit("/", 1)[1], 16)
            root = b"\xff" * 32 if self.lie else self.block_root(height)
            return _json(
                200, {"merkle_root": root[::-1].hex(), "timestamp": 1795000000 + height}
            )
        return 404, "text/plain", b"not found"

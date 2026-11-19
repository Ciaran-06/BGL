"""Rekor probe (build step 8): can a BGL writer key witness heads on public Rekor?

Checks, against the live public instance:
  1. Rekor v1 accepts a `rekord` entry signed with an Ed25519 key in SSH
     signature format (the format BGL would use, so writers need no second key);
  2. the entry comes back with an inclusion proof;
  3. searching the index by that public key finds the entry.

It uses a fresh throwaway key, never your writer key. The entry it creates is
public and permanent: it holds that throwaway public key and a random hash,
nothing else. Run with:  uv run python spikes/rekor/probe.py
"""

import base64
import hashlib
import json
import os
import struct
import sys
import time
import urllib.error
import urllib.request
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

REKOR = os.environ.get("BGL_REKOR_URL", "https://rekor.sigstore.dev")
NAMESPACE = b"file"  # the namespace rekor's own SSH examples use (ssh-keygen -n file)
TIMEOUT = 30


def ssh_string(data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + data


def ssh_public_blob(public: bytes) -> bytes:
    return ssh_string(b"ssh-ed25519") + ssh_string(public)


def authorized_key(public: bytes) -> str:
    return "ssh-ed25519 " + base64.b64encode(ssh_public_blob(public)).decode()


def sshsig(key: Ed25519PrivateKey, message: bytes) -> str:
    """An armored SSH signature (PROTOCOL.sshsig) over message, SHA-512 hashed."""
    public = key.public_key().public_bytes_raw()
    digest = hashlib.sha512(message).digest()
    signed = (
        b"SSHSIG"
        + ssh_string(NAMESPACE)
        + ssh_string(b"")
        + ssh_string(b"sha512")
        + ssh_string(digest)
    )
    raw = key.sign(signed)
    blob = (
        b"SSHSIG"
        + struct.pack(">I", 1)
        + ssh_string(ssh_public_blob(public))
        + ssh_string(NAMESPACE)
        + ssh_string(b"")
        + ssh_string(b"sha512")
        + ssh_string(ssh_string(b"ssh-ed25519") + ssh_string(raw))
    )
    text = base64.b64encode(blob).decode()
    lines = [text[i : i + 70] for i in range(0, len(text), 70)]
    return (
        "-----BEGIN SSH SIGNATURE-----\n"
        + "\n".join(lines)
        + ("\n-----END SSH SIGNATURE-----\n")
    )


def call(method: str, path: str, body: Any = None) -> Any:
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(
        REKOR + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")[:500]
        message = f"{method} {path} failed: HTTP {error.code}: {detail}"
        raise SystemExit(message) from None
    except urllib.error.URLError as error:
        raise SystemExit(f"cannot reach {REKOR}: {error.reason}") from None


def main() -> int:
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes_raw()
    head = os.urandom(32).hex()
    message = f"bgl-witness-v1:{head}".encode()
    public_b64 = base64.b64encode(authorized_key(public).encode()).decode()
    entry = {
        "apiVersion": "0.0.1",
        "kind": "rekord",
        "spec": {
            "signature": {
                "format": "ssh",
                "content": base64.b64encode(sshsig(key, message).encode()).decode(),
                "publicKey": {"content": public_b64},
            },
            "data": {"content": base64.b64encode(message).decode()},
        },
    }
    print(f"Rekor: {REKOR}")
    print("1. Submitting a rekord entry signed with an SSH Ed25519 key ...")
    created = call("POST", "/api/v1/log/entries", entry)
    uuid, record = next(iter(created.items()))
    proof = record.get("verification", {}).get("inclusionProof")
    print(f"   accepted: uuid {uuid}, log index {record.get('logIndex')}")
    print(f"   integrated time: {record.get('integratedTime')}")
    print(f"2. Inclusion proof returned: {'yes' if proof else 'NO'}")
    print("3. Searching the index by public key ...")
    found: list[str] = []
    for attempt in range(6):
        found = call(
            "POST",
            "/api/v1/index/retrieve",
            {"publicKey": {"format": "ssh", "content": public_b64}},
        )
        if any(
            uuid.endswith(item[-64:]) or item.endswith(uuid[-64:]) for item in found
        ):
            break
        time.sleep(5 * (attempt + 1))
    hit = any(uuid.endswith(item[-64:]) or item.endswith(uuid[-64:]) for item in found)
    print(
        f"   search returned {len(found)} entr{'y' if len(found) == 1 else 'ies'}; "
        f"our entry {'FOUND' if hit else 'NOT FOUND'}"
    )
    print()
    ok = bool(proof) and hit
    print("RESULT:", "all three checks passed" if ok else "a check failed (see above)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

"""SSH signatures (OpenSSH PROTOCOL.sshsig) with Ed25519 keys.

Rekor accepts Ed25519 keys in SSH signature format for `rekord` entries, which
lets a writer witness heads with the same key that signs rows. The SSHSIG
envelope starts with the bytes "SSHSIG", so these signatures can never be
mistaken for row signatures (which sign "bgl-row-v1:" + hash).
"""

import base64
import hashlib
import struct
from typing import Final

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

MAGIC: Final[bytes] = b"SSHSIG"
VERSION: Final[int] = 1
# [DECISION] Namespace "file", the one Rekor's own SSH examples use. The
# message itself carries the bgl-witness-v1 prefix (see bgl.rekor).
NAMESPACE: Final[bytes] = b"file"
HASH: Final[bytes] = b"sha512"
KEY_TYPE: Final[bytes] = b"ssh-ed25519"
_BEGIN: Final[str] = "-----BEGIN SSH SIGNATURE-----"
_END: Final[str] = "-----END SSH SIGNATURE-----"


def _string(data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + data


def public_blob(public: bytes) -> bytes:
    """The SSH wire form of an Ed25519 public key."""
    return _string(KEY_TYPE) + _string(public)


def authorized_key(public: bytes) -> str:
    """The key as one authorized_keys line: "ssh-ed25519 AAAA..."."""
    return "ssh-ed25519 " + base64.b64encode(public_blob(public)).decode("ascii")


def _signed_data(message: bytes) -> bytes:
    return (
        MAGIC
        + _string(NAMESPACE)
        + _string(b"")
        + _string(HASH)
        + _string(hashlib.sha512(message).digest())
    )


def sign(private_key: Ed25519PrivateKey, message: bytes) -> str:
    """An armored SSH signature over message."""
    public = private_key.public_key().public_bytes_raw()
    raw = private_key.sign(_signed_data(message))
    blob = (
        MAGIC
        + struct.pack(">I", VERSION)
        + _string(public_blob(public))
        + _string(NAMESPACE)
        + _string(b"")
        + _string(HASH)
        + _string(_string(KEY_TYPE) + _string(raw))
    )
    text = base64.b64encode(blob).decode("ascii")
    lines = [text[i : i + 70] for i in range(0, len(text), 70)]
    return "\n".join([_BEGIN, *lines, _END]) + "\n"


class _Reader:
    def __init__(self, data: bytes) -> None:
        self.data, self.at = data, 0

    def take(self, count: int) -> bytes:
        if self.at + count > len(self.data):
            raise ValueError("truncated")
        part = self.data[self.at : self.at + count]
        self.at += count
        return part

    def string(self) -> bytes:
        (length,) = struct.unpack(">I", self.take(4))
        return self.take(length)


def verify(public: bytes, message: bytes, armored: str) -> bool:
    """True only if armored is a valid SSHSIG by this key over message.

    Checks the key, namespace and hash named inside the signature as well as
    the signature itself. Anything malformed returns False.
    """
    try:
        text = armored.strip()
        if not text.startswith(_BEGIN) or not text.endswith(_END):
            return False
        body = "".join(text[len(_BEGIN) : -len(_END)].split())
        reader = _Reader(base64.b64decode(body, validate=True))
        if reader.take(6) != MAGIC or struct.unpack(">I", reader.take(4))[0] != 1:
            return False
        if reader.string() != public_blob(public):
            return False
        if reader.string() != NAMESPACE or reader.string() != b"":
            return False
        if reader.string() != HASH:
            return False
        signature = _Reader(reader.string())
        if reader.at != len(reader.data):
            return False
        if signature.string() != KEY_TYPE:
            return False
        raw = signature.string()
        if signature.at != len(signature.data):
            return False
        Ed25519PublicKey.from_public_bytes(public).verify(raw, _signed_data(message))
    except (ValueError, InvalidSignature):
        return False
    return True

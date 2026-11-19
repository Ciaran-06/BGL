"""Row signatures (R15, N6): Ed25519 over "bgl-row-v1:" + the row hash."""

import base64
import binascii
from typing import Final

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from bgl.canonical import HASH_HEX

SIGNATURE_PREFIX: Final[str] = "bgl-row-v1:"
SIGNATURE_BYTES: Final[int] = 64


def signed_message(hash_hex: str) -> bytes:
    """The bytes a row signature covers. Refuses a malformed hash."""
    if not HASH_HEX.fullmatch(hash_hex):
        raise ValueError(f"not a lowercase SHA-256 hex hash: {hash_hex!r}")
    return (SIGNATURE_PREFIX + hash_hex).encode("utf-8")


def sign_row_hash(private_key: Ed25519PrivateKey, hash_hex: str) -> str:
    """Sign a row hash. Returns standard base64 text (88 characters)."""
    signature = private_key.sign(signed_message(hash_hex))
    return base64.b64encode(signature).decode("ascii")


def verify_row_signature(public_key: bytes, hash_hex: object, sig: object) -> bool:
    """True only if sig is the exact base64 of a valid signature of hash_hex.

    hash_hex and sig come from a file, so anything malformed returns False rather
    than raising. public_key comes from the validated config.
    """
    if not isinstance(hash_hex, str) or not HASH_HEX.fullmatch(hash_hex):
        return False
    if not isinstance(sig, str):
        return False
    try:
        raw = base64.b64decode(sig, validate=True)
    except (binascii.Error, ValueError):
        return False
    # [DECISION] The signature text must be the exact encoding of its bytes, so
    # each signature has one spelling (same rule as keys in C12).
    if len(raw) != SIGNATURE_BYTES or base64.b64encode(raw).decode("ascii") != sig:
        return False
    try:
        Ed25519PublicKey.from_public_bytes(public_key).verify(
            raw, signed_message(hash_hex)
        )
    except InvalidSignature:
        return False
    return True

"""RFC 6962 / RFC 9162 Merkle trees: leaf hashes and inclusion proofs.

Rekor's log is such a tree. An inclusion proof shows that one entry is in the
tree whose root the log signed, without trusting the log's word for it.
"""

import hashlib
from collections.abc import Sequence


def leaf_hash(leaf: bytes) -> bytes:
    """SHA-256(0x00 || leaf)."""
    return hashlib.sha256(b"\x00" + leaf).digest()


def node_hash(left: bytes, right: bytes) -> bytes:
    """SHA-256(0x01 || left || right)."""
    return hashlib.sha256(b"\x01" + left + right).digest()


def root_from_inclusion(
    index: int, size: int, leaf: bytes, path: Sequence[bytes]
) -> bytes | None:
    """The root implied by an inclusion proof, or None if the proof is malformed.

    RFC 9162 section 2.1.3.2. leaf is the leaf hash, not the entry.
    """
    if not 0 <= index < size:
        return None
    fn, sn, result = index, size - 1, leaf
    for sibling in path:
        if sn == 0:
            return None
        if fn & 1 or fn == sn:
            result = node_hash(sibling, result)
            if not fn & 1:
                while not fn & 1 and fn != 0:
                    fn >>= 1
                    sn >>= 1
        else:
            result = node_hash(result, sibling)
        fn >>= 1
        sn >>= 1
    return result if sn == 0 else None


def verify_inclusion(
    index: int, size: int, leaf: bytes, path: Sequence[bytes], root: bytes
) -> bool:
    """True if path proves the leaf hash is at index in the tree with this root."""
    return root_from_inclusion(index, size, leaf, path) == root

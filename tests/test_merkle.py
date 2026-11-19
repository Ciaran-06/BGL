import hashlib

import pytest
from fakes import tree_path, tree_root

from bgl.merkle import leaf_hash, node_hash, root_from_inclusion, verify_inclusion


def leaves(count: int) -> list[bytes]:
    return [leaf_hash(f"entry {i}".encode()) for i in range(count)]


def test_hashes_use_domain_bytes() -> None:
    assert leaf_hash(b"") == hashlib.sha256(b"\x00").digest()
    assert node_hash(b"a", b"b") == hashlib.sha256(b"\x01ab").digest()


def test_rfc6962_empty_leaf_vector() -> None:
    # RFC 6962 test vector: the leaf hash of the empty string.
    assert leaf_hash(b"").hex() == (
        "6e340b9cffb37a989ca544e6bb780a2c78901d3fb33738768511a30617afa01d"
    )


@pytest.mark.parametrize("size", list(range(1, 18)) + [31, 32, 33, 100])
def test_every_leaf_of_every_size_verifies(size: int) -> None:
    hashed = leaves(size)
    root = tree_root(hashed)
    for index in range(size):
        assert verify_inclusion(
            index, size, hashed[index], tree_path(index, hashed), root
        )


def test_wrong_leaf_index_size_or_path_fails() -> None:
    hashed = leaves(7)
    root = tree_root(hashed)
    path = tree_path(3, hashed)
    assert not verify_inclusion(3, 7, hashed[4], path, root)
    assert not verify_inclusion(4, 7, hashed[3], path, root)
    # The last leaf's path depends on the tree size. (For leaf 3 it does not:
    # the signed checkpoint, which pairs size and root, pins the size there.)
    last = tree_path(6, hashed)
    assert verify_inclusion(6, 7, hashed[6], last, root)
    assert not verify_inclusion(6, 8, hashed[6], last, root)
    assert not verify_inclusion(3, 7, hashed[3], path[:-1], root)
    assert not verify_inclusion(3, 7, hashed[3], path + [hashed[0]], root)
    assert not verify_inclusion(3, 7, hashed[3], list(reversed(path)), root)


@pytest.mark.parametrize(("index", "size"), [(-1, 3), (3, 3), (0, 0)])
def test_index_out_of_range(index: int, size: int) -> None:
    assert root_from_inclusion(index, size, b"x" * 32, []) is None


def test_path_too_short_gives_no_root() -> None:
    hashed = leaves(7)
    assert root_from_inclusion(3, 7, hashed[3], tree_path(3, hashed)[:-1]) is None
    assert root_from_inclusion(3, 7, hashed[3], []) is None

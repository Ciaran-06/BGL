import base64

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from bgl.signing import (
    SIGNATURE_PREFIX,
    sign_row_hash,
    signed_message,
    verify_row_signature,
)

KEY = Ed25519PrivateKey.from_private_bytes(bytes(32))
PUBLIC = KEY.public_key().public_bytes_raw()
HASH = "0" * 64
KNOWN_SIG = (
    "sDdPy8S6i/OM8Pum0hnQPKSrQGH1x5Rb8TCeWzhRt+TL5sjtzivqHT3gIn/dMrYW5fU7/6F3qBdo"
    "N5axcV31Aw=="
)


class TestSignedMessage:
    def test_prefix_then_hash(self) -> None:
        assert signed_message(HASH) == b"bgl-row-v1:" + b"0" * 64
        assert SIGNATURE_PREFIX == "bgl-row-v1:"

    @pytest.mark.parametrize(
        "bad",
        [
            pytest.param("A" * 64, id="upper_case"),
            pytest.param("0" * 63, id="63_chars"),
            pytest.param("0" * 65, id="65_chars"),
            pytest.param("g" * 64, id="not_hex"),
        ],
    )
    def test_malformed_hash_refused(self, bad: str) -> None:
        with pytest.raises(ValueError):
            signed_message(bad)


class TestSignAndVerify:
    def test_known_answer(self) -> None:
        assert sign_row_hash(KEY, HASH) == KNOWN_SIG

    def test_known_public_key(self) -> None:
        assert base64.b64encode(PUBLIC).decode() == (
            "O2onvM62pC1io6jQKm8Nc2UyFXcd4kOmOsBIoYtZ2ik="
        )

    def test_round_trip(self) -> None:
        digest = "ab" * 32
        assert verify_row_signature(PUBLIC, digest, sign_row_hash(KEY, digest))

    def test_other_key_fails(self) -> None:
        other = Ed25519PrivateKey.from_private_bytes(bytes([1]) * 32)
        other_public = other.public_key().public_bytes_raw()
        assert not verify_row_signature(other_public, HASH, KNOWN_SIG)

    def test_changed_hash_fails(self) -> None:
        assert not verify_row_signature(PUBLIC, "1" + HASH[1:], KNOWN_SIG)

    def test_changed_signature_fails(self) -> None:
        changed = ("t" if KNOWN_SIG[0] != "t" else "u") + KNOWN_SIG[1:]
        assert not verify_row_signature(PUBLIC, HASH, changed)

    def test_signature_without_prefix_fails(self) -> None:
        bare = base64.b64encode(KEY.sign(HASH.encode())).decode()
        assert not verify_row_signature(PUBLIC, HASH, bare)

    def test_second_spelling_of_same_signature_fails(self) -> None:
        # The character before "==" carries 2 data bits and 4 unused bits.
        # Setting an unused bit spells the same 64 bytes differently.
        alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
        last = alphabet.index(KNOWN_SIG[-3])
        other = KNOWN_SIG[:-3] + alphabet[last ^ 1] + "=="
        assert base64.b64decode(other) == base64.b64decode(KNOWN_SIG)
        assert not verify_row_signature(PUBLIC, HASH, other)

    @pytest.mark.parametrize(
        "sig",
        [
            pytest.param(None, id="null"),
            pytest.param(5, id="number"),
            pytest.param("not base64!", id="not_base64"),
            pytest.param("é", id="non_ascii"),
            pytest.param(base64.b64encode(bytes(63)).decode(), id="63_bytes"),
            pytest.param(KNOWN_SIG[:-1], id="padding_cut"),
            pytest.param(KNOWN_SIG + "\n", id="trailing_newline"),
        ],
    )
    def test_malformed_signature_is_false(self, sig: object) -> None:
        assert not verify_row_signature(PUBLIC, HASH, sig)

    @pytest.mark.parametrize("bad_hash", [None, 5, "A" * 64, "0" * 63])
    def test_malformed_hash_is_false(self, bad_hash: object) -> None:
        assert not verify_row_signature(PUBLIC, bad_hash, KNOWN_SIG)

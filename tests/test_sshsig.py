import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from bgl import sshsig

KEY = Ed25519PrivateKey.from_private_bytes(bytes(32))
PUBLIC = KEY.public_key().public_bytes_raw()
MESSAGE = b"bgl-witness-v1:" + b"0" * 64


def test_round_trip() -> None:
    assert sshsig.verify(PUBLIC, MESSAGE, sshsig.sign(KEY, MESSAGE))


def test_armor_shape() -> None:
    armored = sshsig.sign(KEY, MESSAGE)
    lines = armored.strip().split("\n")
    assert lines[0] == "-----BEGIN SSH SIGNATURE-----"
    assert lines[-1] == "-----END SSH SIGNATURE-----"
    assert all(len(line) <= 70 for line in lines[1:-1])


def test_authorized_key_matches_ssh_format() -> None:
    assert sshsig.authorized_key(PUBLIC) == (
        "ssh-ed25519 "
        "AAAAC3NzaC1lZDI1NTE5AAAAIDtqJ7zOtqQtYqOo0CpvDXNlMhV3HeJDpjrASKGLWdop"
    )


def test_independent_verifier_accepts_it() -> None:
    external = pytest.importorskip("sshsig")
    armored = sshsig.sign(KEY, MESSAGE)
    key = external.PublicKey.from_openssh_str(sshsig.authorized_key(PUBLIC))
    assert external.verify(MESSAGE, armored, [key], namespace="file") == key


@pytest.mark.parametrize(
    "case",
    ["other_message", "other_key", "corrupted", "not_armored", "truncated", "empty"],
)
def test_rejects(case: str) -> None:
    armored = sshsig.sign(KEY, MESSAGE)
    public, message = PUBLIC, MESSAGE
    if case == "other_message":
        message = MESSAGE + b"x"
    elif case == "other_key":
        public = Ed25519PrivateKey.generate().public_key().public_bytes_raw()
    elif case == "corrupted":
        lines = armored.split("\n")
        lines[2] = ("A" if lines[2][0] != "A" else "B") + lines[2][1:]
        armored = "\n".join(lines)
    elif case == "not_armored":
        armored = armored.replace("SSH SIGNATURE", "PGP SIGNATURE")
    elif case == "truncated":
        lines = armored.split("\n")
        armored = "\n".join(lines[:2] + lines[-2:])
    else:
        armored = ""
    assert not sshsig.verify(public, message, armored)

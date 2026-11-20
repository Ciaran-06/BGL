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


# Made by OpenSSH 10.5p1, independently of BGL: KEY written as an OpenSSH
# private key file, MESSAGE written to msg with no newline, then
#   ssh-keygen -Y sign -f key -n file msg
# Ed25519 signatures are deterministic, so BGL must produce the same bytes. The
# reverse was also checked once by hand: ssh-keygen -Y verify -n file accepted
# the signature sshsig.sign makes ("Good "file" signature").
SSH_KEYGEN_SIGNATURE = (
    "-----BEGIN SSH SIGNATURE-----\n"
    "U1NIU0lHAAAAAQAAADMAAAALc3NoLWVkMjU1MTkAAAAgO2onvM62pC1io6jQKm8Nc2UyFX\n"
    "cd4kOmOsBIoYtZ2ikAAAAEZmlsZQAAAAAAAAAGc2hhNTEyAAAAUwAAAAtzc2gtZWQyNTUx\n"
    "OQAAAEDzUFMXHptNZ2fjBOLA90bZMBgza7QKGw3XhousXcfSS3ixTgbLc3k9PODT6rKfoz\n"
    "8WQZSTZp1CjLU+IyIMNDUJ\n"
    "-----END SSH SIGNATURE-----\n"
)


def test_matches_ssh_keygen() -> None:
    assert sshsig.sign(KEY, MESSAGE) == SSH_KEYGEN_SIGNATURE


def test_verifies_ssh_keygen_output() -> None:
    assert sshsig.verify(PUBLIC, MESSAGE, SSH_KEYGEN_SIGNATURE)
    assert not sshsig.verify(PUBLIC, MESSAGE + b"x", SSH_KEYGEN_SIGNATURE)


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

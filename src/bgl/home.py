"""The user's home config folder: private keys and the machine ID.

These live outside the repository so git clean -x cannot delete them (decision i).
Files are created once and never overwritten.
"""

import hashlib
import os
import sys
import uuid
from pathlib import Path
from typing import Final

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from bgl.config import Writer
from bgl.errors import KeyFileError
from bgl.file_io import create_exclusive

HOME_ENV: Final[str] = "BGL_HOME"
_MACHINE_ID_FILE: Final[str] = "machine_id"
_KEYS_DIR: Final[str] = "keys"
_KEY_BYTES: Final[int] = 32
_FINGERPRINT_HEX: Final[int] = 16


# [DECISION] Home folder per OS as below, overridable with BGL_HOME. No
# platformdirs dependency.
def home_dir() -> Path:
    """BGL's folder in the user's config area. BGL_HOME overrides it (tests use it).

    Windows: %APPDATA%\\bgl. macOS: ~/Library/Application Support/bgl.
    Elsewhere: $XDG_CONFIG_HOME/bgl, or ~/.config/bgl.
    """
    override = os.environ.get(HOME_ENV)
    if override:
        return Path(override)
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        xdg = os.environ.get("XDG_CONFIG_HOME")
        base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "bgl"


def create_machine_id(home: Path) -> str:
    """Create this machine's ID. Refuses if one already exists."""
    home.mkdir(parents=True, exist_ok=True)
    # [DECISION] A machine ID is a random UUID4 as 32 lowercase hex characters.
    machine_id = uuid.uuid4().hex
    path = home / _MACHINE_ID_FILE
    try:
        create_exclusive(path, (machine_id + "\n").encode("ascii"), mode=0o644)
    except FileExistsError:
        raise KeyFileError(
            f"{path} already exists; a machine ID is never replaced"
        ) from None
    return machine_id


def load_machine_id(home: Path) -> str:
    """Read this machine's ID. A missing or malformed file is a KeyFileError."""
    path = home / _MACHINE_ID_FILE
    try:
        text = path.read_text(encoding="ascii").strip()
    except FileNotFoundError:
        raise KeyFileError(
            f"no machine ID at {path}; run bgl init or bgl join on this machine"
        ) from None
    except (OSError, UnicodeDecodeError) as error:
        raise KeyFileError(f"cannot read the machine ID at {path}: {error}") from error
    if len(text) != 32 or any(c not in "0123456789abcdef" for c in text):
        raise KeyFileError(f"the machine ID at {path} is malformed")
    return text


def key_fingerprint(public_key: bytes) -> str:
    """A short name for a public key, used in key file names."""
    return hashlib.sha256(public_key).hexdigest()[:_FINGERPRINT_HEX]


# [DECISION] Key files are keys/<CODE>-<first 16 hex of SHA-256(public key)>.key,
# holding the 32 raw private bytes, mode 0600.
def private_key_path(home: Path, code: str, public_key: bytes) -> Path:
    """keys/<CODE>-<fingerprint>.key. The fingerprint keeps two projects that
    both use the code CG1 from colliding."""
    return home / _KEYS_DIR / f"{code}-{key_fingerprint(public_key)}.key"


def create_private_key(home: Path, code: str) -> Ed25519PrivateKey:
    """Generate a writer key and save its 32 raw bytes with permissions 0600.

    Never overwrites a key file.
    """
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes_raw()
    path = private_key_path(home, code, public)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        create_exclusive(path, key.private_bytes_raw(), mode=0o600)
    except FileExistsError:
        raise KeyFileError(
            f"{path} already exists; a key file is never replaced"
        ) from None
    return key


# [DECISION] Loading never creates a missing key or machine ID; it raises and
# names bgl join, so a wiped home folder fails loudly.
def load_private_key(home: Path, writer: Writer) -> Ed25519PrivateKey:
    """Load the private key for a registered writer and check it matches (R19)."""
    path = private_key_path(home, writer.code, writer.public_key)
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        raise KeyFileError(
            f"no private key for chain {writer.code} on this machine (looked for "
            f"{path}); run bgl join to set up a writer here"
        ) from None
    if len(raw) != _KEY_BYTES:
        raise KeyFileError(f"the key file {path} is not a {_KEY_BYTES}-byte key")
    key = Ed25519PrivateKey.from_private_bytes(raw)
    if key.public_key().public_bytes_raw() != writer.public_key:
        raise KeyFileError(
            f"R19: the key file {path} does not match chain {writer.code}'s "
            "public key in bgl.toml"
        )
    return key

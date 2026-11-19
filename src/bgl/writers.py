"""Who is writing on this machine: .bgl/writer, the key and the machine ID (R19).

bgl init and bgl join create these (build step 7); this module only reads them.
"""

import dataclasses
from pathlib import Path
from typing import Final

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from bgl.config import Project, Writer
from bgl.errors import WriterError
from bgl.home import home_dir, load_machine_id, load_private_key

WRITER_FILE: Final[str] = ".bgl/writer"


@dataclasses.dataclass(frozen=True)
class Identity:
    """The writer on this machine, with what it needs to sign rows."""

    writer: Writer
    private_key: Ed25519PrivateKey
    machine_id: str


def read_writer_code(root: Path) -> str:
    """The chain code named in .bgl/writer.

    [DECISION] The file holds the chain code as one line of text and nothing else.
    """
    path = root / WRITER_FILE
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise WriterError(
            f"no writer is set up in this repository ({WRITER_FILE} is missing); "
            "run bgl join"
        ) from None
    except (OSError, UnicodeDecodeError) as error:
        raise WriterError(f"cannot read {WRITER_FILE}: {error}") from error
    code = text.strip()
    if not code or len(text.splitlines()) != 1:
        raise WriterError(f"{WRITER_FILE} must hold one chain code; run bgl join")
    return code


def load_identity(root: Path, project: Project) -> Identity:
    """R19, except the machine ID comparison with the chain's earlier rows.

    Raises WriterError or KeyFileError; every message names bgl join.
    """
    code = read_writer_code(root)
    writer = project.writers.get(code)
    if writer is None:
        raise WriterError(
            f"{WRITER_FILE} names chain {code!r}, which is not in the registry in "
            "bgl.toml; run bgl join and commit the registry entry"
        )
    home = home_dir()
    private_key = load_private_key(home, writer)
    machine_id = load_machine_id(home)
    return Identity(writer=writer, private_key=private_key, machine_id=machine_id)

"""bgl init and bgl join: setting up a project and its writers.

Both create a signing key and (on first use) a machine ID in the home config
folder, and write .bgl/writer naming the chain code. Neither ever overwrites a
file: init writes bgl.toml only when none exists, and appends only missing
lines to .gitignore and .gitattributes.
"""

import base64
import dataclasses
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Final

from bgl import git
from bgl.config import CHAIN_CODE, load_config
from bgl.errors import SetupError
from bgl.file_io import create_exclusive
from bgl.home import create_private_key, ensure_machine_id, home_dir
from bgl.writers import WRITER_FILE

CONFIG_FILE: Final[str] = "bgl.toml"
# [DECISION] init uses these folders; a project may change them before its
# first run (they are not part of any study lock).
DEFAULT_LEDGER: Final[str] = "bgl/ledger"
DEFAULT_RUNS_DIR: Final[str] = "bgl/runs"
SCRATCH: Final[str] = "scratch"

_SYNCED_NAMES: Final[tuple[str, ...]] = (
    "onedrive",
    "dropbox",
    "icloud drive",
    "mobile documents",
    "com~apple~clouddocs",
    "google drive",
    "googledrive",
    "my drive",
)
_NETWORK_FS: Final[frozenset[str]] = frozenset(
    {
        "nfs",
        "nfs4",
        "cifs",
        "smb3",
        "smbfs",
        "afpfs",
        "sshfs",
        "fuse.sshfs",
        "9p",
        "davfs",
        "fuse.rclone",
    }
)


@dataclasses.dataclass(frozen=True)
class SetupResult:
    """What init or join did, for the command line to report."""

    code: str
    person: str
    registry_entry: str
    created: tuple[str, ...]
    appended: tuple[str, ...]
    warnings: tuple[str, ...]


def registry_entry(code: str, person: str, public_key: bytes) -> str:
    """The [project.chains.CODE] table for bgl.toml."""
    key = "ed25519:" + base64.b64encode(public_key).decode("ascii")
    return f'[project.chains.{code}]\nperson = {_toml_string(person)}\nkey = "{key}"\n'


def _toml_string(text: str) -> str:
    """A TOML basic string. JSON's escapes are a subset of TOML's."""
    return json.dumps(text, ensure_ascii=False)


def _check_code_and_person(code: str, person: str) -> None:
    if not CHAIN_CODE.fullmatch(code):
        raise SetupError(
            f"chain code {code!r} must be an uppercase letter followed by "
            "uppercase letters or digits, 2 to 6 characters (for example CG1)"
        )
    if not person.strip() or any(not char.isprintable() for char in person):
        raise SetupError("person must be a non-empty name on one line")


def sync_warning(path: Path) -> str | None:
    """A warning if path is inside a synced folder or on a network drive.

    [DECISION] Synced folders are recognised by name (OneDrive, Dropbox,
    iCloud Drive, Google Drive); network drives by UNC path or drive type on
    Windows and by file-system type in /proc/mounts on Linux. macOS network
    mounts are not detected. Run locks are unreliable in all of these.
    """
    real = Path(os.path.realpath(path))
    for part in real.parts:
        lowered = part.lower()
        if any(
            lowered == name or lowered.startswith(name + " ") for name in _SYNCED_NAMES
        ) or lowered.startswith("onedrive"):
            return (
                f"{real} is inside a synced folder ({part}). BGL's locks are "
                "unreliable there; keep the project outside synced folders"
            )
    if _is_network(real):
        return (
            f"{real} is on a network drive. BGL's locks are unreliable there; "
            "keep the project on a local disk"
        )
    return None


def _is_network(path: Path) -> bool:
    text = str(path)
    if sys.platform == "win32":
        if text.startswith("\\\\"):
            return True
        import ctypes

        drive = os.path.splitdrive(text)[0] + "\\"
        remote = 4  # DRIVE_REMOTE
        return bool(ctypes.windll.kernel32.GetDriveTypeW(drive) == remote)
    mounts = Path("/proc/mounts")
    if not mounts.exists():
        return False
    best, best_type = "", ""
    for line in mounts.read_text(encoding="utf-8", errors="replace").splitlines():
        fields = line.split()
        if len(fields) < 3:
            continue
        point = fields[1].replace("\\040", " ")
        inside = text == point or text.startswith(point.rstrip("/") + "/")
        if inside and len(point) > len(best):
            best, best_type = point, fields[2]
    return best_type in _NETWORK_FS


def _append_missing(path: Path, lines: Sequence[str]) -> list[str]:
    """Append each line that the file does not already hold. Returns those added."""
    existing: list[str] = []
    text = ""
    if path.exists():
        text = path.read_text(encoding="utf-8")
        existing = [line.strip() for line in text.splitlines()]
    missing = [line for line in lines if line not in existing]
    if missing:
        prefix = "" if not text or text.endswith("\n") else "\n"
        with open(path, "a", encoding="utf-8", newline="\n") as handle:
            handle.write(prefix + "\n".join(missing) + "\n")
    return missing


def ignore_lines(runs_dir: str) -> list[str]:
    """The .gitignore entries the spec lists for init."""
    return [
        ".bgl/",
        f"{SCRATCH}/",
        f"{runs_dir}/returns/",
        f"{runs_dir}/output/",
        "*.sealed",
    ]


def attribute_lines(ledger: str, runs_dir: str) -> list[str]:
    """.gitattributes entries that turn off line-ending conversion for BGL's files."""
    return [f"{ledger}/** -text", f"{runs_dir}/** -text"]


def starter_config(name: str, entry: str) -> str:
    """bgl.toml as init writes it: one writer, no studies, no gate table.

    [DECISION] The starter project is solo with witness = false, so it loads
    straight away. Witnessing is switched on by replacing that line with a
    [project.witness] table. The example study is commented out, and every
    value a person must choose is marked TODO(bgl), so it cannot load unedited
    once uncommented (C20).
    """
    return f"""# BGL project config. No key has a default: every key below is required.

[project]
name = {_toml_string(name)}
mode = "solo"
ledger = "{DEFAULT_LEDGER}"
runs_dir = "{DEFAULT_RUNS_DIR}"
# A solo project may turn witnessing off; its studies are then not
# deployment-grade. A team project must witness (C17). To witness, replace the
# line below with this table (list calendars, not pool aggregators: only the
# calendars named here are asked to upgrade stamps):
#
# [project.witness]
# rekor_url = "https://rekor.sigstore.dev"
# ots_calendars = [
#     "https://alice.btc.calendar.opentimestamps.org",
#     "https://bob.btc.calendar.opentimestamps.org",
# ]
witness = false

{entry}
[studies]

# An example study. Remove the leading "# " from each line to use it, then
# replace every TODO(bgl) value. The first counted run locks the whole study.
#
# [studies.alpha]
# budget = 20
# prefix = "AL"
# claims = ["effect_exists"]
# prior_trials = 0
# holdout = false
# spec_files = []
#
# [studies.alpha.returns]
# periods_per_year = 252
# basis = "TODO(bgl): what one return is measured on"
# risk_free = "none"
# risk_free_reasoning = "TODO(bgl): why no risk-free rate is subtracted"
"""


def init_project(start: Path, code: str, person: str) -> SetupResult:
    """bgl init: the first writer, a starter bgl.toml, an empty chain, ignore files.

    [DECISION] init runs at the root of the git repository containing start,
    takes the first writer's code and person like join does, and refuses if
    bgl.toml or .bgl/writer already exists (use join for later writers).
    """
    _check_code_and_person(code, person)
    root = Path(os.path.realpath(git.repo_root(start)))
    config_path = root / CONFIG_FILE
    if config_path.exists():
        raise SetupError(
            f"{CONFIG_FILE} already exists. To add a writer on this machine, "
            "run bgl join CODE PERSON"
        )
    if (root / WRITER_FILE).exists():
        raise SetupError(f"{WRITER_FILE} already exists: a writer is set up here")
    home = home_dir()
    ensure_machine_id(home)
    key = create_private_key(home, code)
    entry = registry_entry(code, person, key.public_key().public_bytes_raw())
    created = [CONFIG_FILE]
    create_exclusive(
        config_path, starter_config(root.name, entry).encode("utf-8"), mode=0o644
    )
    chain = root / DEFAULT_LEDGER / f"{code}.jsonl"
    chain.parent.mkdir(parents=True, exist_ok=True)
    if not chain.exists():
        create_exclusive(chain, b"", mode=0o644)
        created.append(f"{DEFAULT_LEDGER}/{code}.jsonl")
    (root / SCRATCH).mkdir(exist_ok=True)
    _write_writer_file(root, code)
    created.append(WRITER_FILE)
    appended = [
        f".gitignore: {line}"
        for line in _append_missing(root / ".gitignore", ignore_lines(DEFAULT_RUNS_DIR))
    ] + [
        f".gitattributes: {line}"
        for line in _append_missing(
            root / ".gitattributes", attribute_lines(DEFAULT_LEDGER, DEFAULT_RUNS_DIR)
        )
    ]
    warning = sync_warning(root)
    load_config(config_path)  # the starter config must load
    return SetupResult(
        code=code,
        person=person,
        registry_entry=entry,
        created=tuple(created),
        appended=tuple(appended),
        warnings=() if warning is None else (warning,),
    )


def join_writer(start: Path, code: str, person: str) -> SetupResult:
    """bgl join: a new writer on this machine. Prints the registry entry to add.

    [DECISION] join refuses a code already in the registry: the registry only
    grows, and a new key always means a new chain code. It also refuses if this
    checkout already has a writer (.bgl/writer).
    """
    _check_code_and_person(code, person)
    root = Path(os.path.realpath(git.repo_root(start)))
    config = load_config(root / CONFIG_FILE)
    if code in config.project.writers:
        raise SetupError(
            f"chain {code} is already registered. A new key needs a new chain "
            "code; registry entries never change"
        )
    if (root / WRITER_FILE).exists():
        raise SetupError(
            f"{WRITER_FILE} already exists: this checkout already has a writer. "
            "One writer is one person on one machine"
        )
    home = home_dir()
    ensure_machine_id(home)
    key = create_private_key(home, code)
    entry = registry_entry(code, person, key.public_key().public_bytes_raw())
    _write_writer_file(root, code)
    warning = sync_warning(root)
    return SetupResult(
        code=code,
        person=person,
        registry_entry=entry,
        created=(WRITER_FILE,),
        appended=(),
        warnings=() if warning is None else (warning,),
    )


def _write_writer_file(root: Path, code: str) -> None:
    path = root / WRITER_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    create_exclusive(path, f"{code}\n".encode("ascii"), mode=0o644)

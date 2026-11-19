import base64
import binascii
import dataclasses
import datetime
import math
import posixpath
import re
import tomllib
import types
import urllib.parse
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

_MAX_FOUND_LENGTH = 80
_LINE_IN_MESSAGE = re.compile(r"at line (\d+)")
_POSITION_IN_MESSAGE = re.compile(
    r"\s*\(at (?:line \d+, column \d+|end of document)\)\s*$"
)
_UTF8_BOM = b"\xef\xbb\xbf"
_DRIVE_LETTER = re.compile(r"^[A-Za-z]:")
_CHAIN_CODE = re.compile(r"[A-Z][A-Z0-9]{1,5}")
_NAME = re.compile(r"[a-z][a-z0-9_]{0,31}")
_PREFIX = re.compile(r"[A-Z]{2,4}")
_KEY_PREFIX = "ed25519:"
_KEY_BYTES = 32


class ConfigError(Exception):
    """A bgl.toml problem. Every config failure raises this one type."""

    def __init__(
        self,
        file: Path,
        rule: str,
        expected: str,
        *,
        key: str | None = None,
        found: object = None,
        line: int | None = None,
    ) -> None:
        self.file = file
        self.rule = rule
        self.expected = expected
        self.key = key
        self.found = found
        self.line = line
        super().__init__(self._build_message())

    def _build_message(self) -> str:
        parts = [self.rule, str(self.file)]
        if self.line is not None:
            parts.append(f"line {self.line}")
        if self.key is not None:
            parts.append(self.key)
        message = ": ".join(parts) + f": expected {self.expected}"
        if self.key is not None:
            message += f", found {self._show_found()}"
        return message

    def _show_found(self) -> str:
        if self.found is None:
            return "missing"
        text = repr(self.found)
        if len(text) > _MAX_FOUND_LENGTH:
            text = text[: _MAX_FOUND_LENGTH - 3] + "..."
        return text


class ConfigErrorGroup(ExceptionGroup[ConfigError]):
    """Every phase 2 error found in one bgl.toml, raised together."""

    file: Path

    def __new__(cls, file: Path, errors: Sequence[ConfigError]) -> "ConfigErrorGroup":
        for error in errors:
            if not isinstance(error, ConfigError):
                name = type(error).__name__
                raise TypeError(f"ConfigErrorGroup holds ConfigError only, got {name}")
        count = len(errors)
        noun = "problem" if count == 1 else "problems"
        group = super().__new__(cls, f"{file.name} has {count} {noun}", errors)
        group.file = file
        return group

    def derive(  # type: ignore[override]
        self, excs: Sequence[ConfigError]
    ) -> "ConfigErrorGroup":
        """Keep the type and file when split() or subgroup() builds a smaller group."""
        return ConfigErrorGroup(self.file, excs)


class ErrorCollector:
    """Collects phase 2 errors for one file, then raises them together."""

    def __init__(self, file: Path) -> None:
        self.file = file
        self.errors: list[ConfigError] = []

    def add(
        self,
        rule: str,
        expected: str,
        *,
        key: str | None = None,
        found: object = None,
    ) -> None:
        """Record one error. A found value of None means the key is missing."""
        self.errors.append(ConfigError(self.file, rule, expected, key=key, found=found))

    def has_errors(self) -> bool:
        return bool(self.errors)

    def raise_if_any(self) -> None:
        """Raise every collected error as one ConfigErrorGroup, in the order found."""
        if self.errors:
            raise ConfigErrorGroup(self.file, self.errors)


def key_path(table_path: str, key: str) -> str:
    """The dotted key path of key inside the table at table_path."""
    return f"{table_path}.{key}" if table_path else key


def check_unknown_keys(
    collector: ErrorCollector,
    table: Mapping[str, Any],
    allowed: frozenset[str],
    table_path: str,
) -> None:
    """C7: report every key in table that is not in allowed, in sorted order."""
    for key in sorted(set(table) - allowed):
        collector.add(
            "C7",
            "only known keys (check the spelling)",
            key=key_path(table_path, key),
            found=table[key],
        )


def check_table(
    collector: ErrorCollector,
    table: Mapping[str, Any],
    key: str,
    table_path: str,
    rule: str,
) -> dict[str, Any] | None:
    """A sub-table that must be present. Returns it, or None after adding an error."""
    value = table.get(key)
    if not isinstance(value, dict):
        collector.add(rule, "a table", key=key_path(table_path, key), found=value)
        return None
    return value


def check_whole_number(
    collector: ErrorCollector,
    table: Mapping[str, Any],
    key: str,
    table_path: str,
    rule: str,
    *,
    minimum: int,
    even: bool = False,
) -> int | None:
    """An integer (not a boolean) of at least minimum, and even if asked."""
    value = table.get(key)
    if (
        isinstance(value, int)
        and not isinstance(value, bool)
        and value >= minimum
        and (not even or value % 2 == 0)
    ):
        return value
    expected = f"a whole number, at least {minimum}" + (", and even" if even else "")
    collector.add(rule, expected, key=key_path(table_path, key), found=value)
    return None


def check_number(
    collector: ErrorCollector,
    table: Mapping[str, Any],
    key: str,
    table_path: str,
    rule: str,
    *,
    above: float | None = None,
    at_least: float | None = None,
    below: float | None = None,
    at_most: float | None = None,
) -> float | None:
    """A finite number (integer or float, not a boolean) within the given bounds.

    Give at most one lower bound (above or at_least) and at most one upper bound
    (below or at_most). Integers are converted to floats.
    """
    value = table.get(key)
    expected = _describe_range(above, at_least, below, at_most)
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
    ):
        collector.add(rule, expected, key=key_path(table_path, key), found=value)
        return None
    number = float(value)
    in_range = (
        (above is None or number > above)
        and (at_least is None or number >= at_least)
        and (below is None or number < below)
        and (at_most is None or number <= at_most)
    )
    if not in_range:
        collector.add(rule, expected, key=key_path(table_path, key), found=value)
        return None
    return number


def _describe_range(
    above: float | None,
    at_least: float | None,
    below: float | None,
    at_most: float | None,
) -> str:
    parts = []
    if above is not None:
        parts.append(f"above {above:g}")
    if at_least is not None:
        parts.append(f"at least {at_least:g}")
    if below is not None:
        parts.append(f"below {below:g}")
    if at_most is not None:
        parts.append(f"at most {at_most:g}")
    return "a finite number" + (" " + " and ".join(parts) if parts else "")


def check_string(
    collector: ErrorCollector,
    table: Mapping[str, Any],
    key: str,
    table_path: str,
    rule: str,
) -> str | None:
    """A non-empty string."""
    value = table.get(key)
    if not isinstance(value, str) or not value:
        collector.add(
            rule, "a non-empty string", key=key_path(table_path, key), found=value
        )
        return None
    return value


def check_path(
    collector: ErrorCollector,
    table: Mapping[str, Any],
    key: str,
    table_path: str,
) -> str | None:
    """C2: a relative path with forward slashes that stays inside the repository."""
    value = table.get(key)
    problem = _path_problem(value)
    if problem is None and isinstance(value, str):
        return tidy_path(value)
    collector.add(
        "C2",
        f"a relative path inside the repository ({problem})",
        key=key_path(table_path, key),
        found=value,
    )
    return None


def check_path_list(
    collector: ErrorCollector,
    table: Mapping[str, Any],
    key: str,
    table_path: str,
    rule: str,
    *,
    allow_empty: bool,
) -> tuple[str, ...] | None:
    """A list of C2 paths, tidied. Shape and repeats are reported under rule."""
    value = table.get(key)
    if not isinstance(value, list) or (not value and not allow_empty):
        expected = "a list of paths" + ("" if allow_empty else ", not empty")
        collector.add(rule, expected, key=key_path(table_path, key), found=value)
        return None
    paths: list[str] = []
    ok = True
    for index, item in enumerate(value):
        problem = _path_problem(item)
        if problem is None and isinstance(item, str):
            tidied = tidy_path(item)
            if tidied in paths:
                collector.add(
                    rule,
                    f"each path once (already listed as {tidied!r})",
                    key=f"{key_path(table_path, key)}[{index}]",
                    found=item,
                )
                ok = False
                continue
            paths.append(tidied)
            continue
        collector.add(
            "C2",
            f"a relative path inside the repository ({problem})",
            key=f"{key_path(table_path, key)}[{index}]",
            found=item,
        )
        ok = False
    return tuple(paths) if ok else None


def tidy_path(path: str) -> str:
    """Remove "." segments, "name/.." pairs, repeated and trailing slashes.

    Only call this on a path that passed C2, so the result never leaves the
    repository and is never absolute.
    """
    return posixpath.normpath(path)


def _path_problem(value: object) -> str | None:
    """Why value is not an acceptable config path, or None if it is."""
    if not isinstance(value, str) or not value:
        return "a non-empty string"
    if "\\" in value:
        return "forward slashes only"
    if value.startswith("/") or _DRIVE_LETTER.match(value):
        return "not absolute"
    depth = 0
    for part in value.split("/"):
        if part == "..":
            depth -= 1
            if depth < 0:
                return "must not leave the repository"
        elif part not in ("", "."):
            depth += 1
    return None


PLACEHOLDER_MARKER = "TODO(bgl)"


def check_placeholder(collector: ErrorCollector, value: str, path: str) -> bool:
    """C20: refuse template text. Returns True if value is acceptable."""
    if PLACEHOLDER_MARKER in value:
        collector.add(
            "C20",
            f"real text, not a template placeholder (remove {PLACEHOLDER_MARKER})",
            key=path,
            found=value,
        )
        return False
    return True


def check_https_url(
    collector: ErrorCollector,
    table: Mapping[str, Any],
    key: str,
    table_path: str,
    rule: str,
) -> str | None:
    """An HTTPS URL with a host. A placeholder reports C20 and nothing else."""
    value = table.get(key)
    path = key_path(table_path, key)
    if isinstance(value, str) and not check_placeholder(collector, value, path):
        return None
    problem = _url_problem(value)
    if problem is None and isinstance(value, str):
        return value
    collector.add(rule, f"an HTTPS URL ({problem})", key=path, found=value)
    return None


def check_https_url_list(
    collector: ErrorCollector,
    table: Mapping[str, Any],
    key: str,
    table_path: str,
    rule: str,
) -> tuple[str, ...] | None:
    """A non-empty list of HTTPS URLs. Each bad item is reported at path[index]."""
    value = table.get(key)
    path = key_path(table_path, key)
    if not isinstance(value, list) or not value:
        collector.add(rule, "a list of HTTPS URLs, not empty", key=path, found=value)
        return None
    urls: list[str] = []
    ok = True
    for index, item in enumerate(value):
        item_path = f"{path}[{index}]"
        if isinstance(item, str) and not check_placeholder(collector, item, item_path):
            ok = False
            continue
        problem = _url_problem(item)
        if problem is None and isinstance(item, str):
            urls.append(item)
            continue
        collector.add(rule, f"an HTTPS URL ({problem})", key=item_path, found=item)
        ok = False
    return tuple(urls) if ok else None


# [DECISION] An HTTPS URL here means: scheme https, a host, a valid port if
# given, and no spaces or control characters. Nothing else is checked.
def _url_problem(value: object) -> str | None:
    """Why value is not an acceptable HTTPS URL, or None if it is."""
    if not isinstance(value, str) or not value:
        return "a non-empty string"
    if any(char.isspace() or not char.isprintable() for char in value):
        return "no spaces or control characters"
    try:
        parts = urllib.parse.urlsplit(value)
        parts.port  # noqa: B018  (raises ValueError on a bad port)
    except ValueError:
        return "a well-formed URL"
    if parts.scheme != "https":
        return "https only"
    if not parts.hostname:
        return "a host name"
    return None


def read_toml(file: Path) -> dict[str, Any]:
    """Read and parse bgl.toml. Every failure becomes a ConfigError."""
    if not isinstance(file, Path):
        raise TypeError(f"read_toml expects a pathlib.Path, got {type(file).__name__}")
    if file.is_dir():
        raise ConfigError(file, "C8", "a file, but this path is a folder")

    try:
        raw = file.read_bytes()
    except FileNotFoundError as error:
        raise ConfigError(
            file, "C8", "a bgl.toml file (run bgl init to create one)"
        ) from error
    except OSError as error:
        raise ConfigError(
            file, "C8", f"a readable file ({error.strerror or error})"
        ) from error

    if raw.startswith(_UTF8_BOM):
        raise ConfigError(
            file, "C9", "UTF-8 without a byte-order mark (save the file without a BOM)"
        )

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ConfigError(file, "C9", "a UTF-8 text file") from error

    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(
            file,
            "C9",
            f"valid TOML ({_error_text(error)})",
            line=_error_line(error),
        ) from error


def _error_text(error: tomllib.TOMLDecodeError) -> str:
    """A TOML syntax error's message without its position, which ConfigError shows."""
    msg = getattr(error, "msg", None)
    if isinstance(msg, str):
        return msg
    return _POSITION_IN_MESSAGE.sub("", str(error))


def _error_line(error: tomllib.TOMLDecodeError) -> int | None:
    """Line number of a TOML syntax error, or None if it cannot be found."""
    lineno = getattr(error, "lineno", None)
    if isinstance(lineno, int):
        return lineno
    match = _LINE_IN_MESSAGE.search(str(error))
    return int(match.group(1)) if match else None


Mode = Literal["solo", "team"]


@dataclasses.dataclass(frozen=True)
class Writer:
    """One registered writer: a chain code, the person, and their public key (C12)."""

    code: str
    person: str
    public_key: bytes


@dataclasses.dataclass(frozen=True)
class Witness:
    """Where heads are witnessed (C17)."""

    rekor_url: str
    ots_calendars: tuple[str, ...]


@dataclasses.dataclass(frozen=True)
class Project:
    """The [project] table (C1, C2, C12, C14, C17).

    ledger and runs_dir are repository-relative paths with forward slashes, as
    written in bgl.toml; join them to the repository root to use them. witness is
    None when a solo project sets witness = false.
    """

    name: str
    mode: Mode
    # [DECISION] Paths stay repository-relative strings; callers join them to
    # the repository root.
    ledger: str
    runs_dir: str
    writers: Mapping[str, Writer]
    witness: Witness | None


@dataclasses.dataclass(frozen=True)
# [DECISION] Partial project results, so a bad name does not hide C14/C15
# errors in studies (spec: skip only dependent checks).
class ProjectParts:
    """What check_project found. A field is None when its own check failed.

    Study checks read mode and writers from here, so a mistake elsewhere in
    [project] does not hide C14 or C15 errors. witness_valid separates
    "witness = false" (valid, witness None) from an invalid witness setting.
    """

    name: str | None = None
    mode: Mode | None = None
    ledger: str | None = None
    runs_dir: str | None = None
    writers: Mapping[str, Writer] | None = None
    witness_valid: bool = False
    witness: Witness | None = None

    def build(self) -> Project | None:
        """The Project record, or None if any part failed."""
        if (
            self.name is None
            or self.mode is None
            or self.ledger is None
            or self.runs_dir is None
            or self.writers is None
            or not self.witness_valid
        ):
            return None
        return Project(
            name=self.name,
            mode=self.mode,
            ledger=self.ledger,
            runs_dir=self.runs_dir,
            writers=self.writers,
            witness=self.witness,
        )


_PROJECT_KEYS = frozenset({"name", "mode", "ledger", "runs_dir", "chains", "witness"})
_WRITER_KEYS = frozenset({"person", "key"})
_WITNESS_KEYS = frozenset({"rekor_url", "ots_calendars"})


def check_project(collector: ErrorCollector, data: Mapping[str, Any]) -> ProjectParts:
    """Check the [project] table: C1, C2, C7, C12, C14, C17 and C20."""
    project = check_table(collector, data, "project", "", "C1")
    if project is None:
        return ProjectParts()
    check_unknown_keys(collector, project, _PROJECT_KEYS, "project")
    name = check_string(collector, project, "name", "project", "C1")
    mode = check_mode(collector, project)
    ledger = check_path(collector, project, "ledger", "project")
    runs_dir = check_path(collector, project, "runs_dir", "project")
    writers = check_registry(collector, project)
    witness_valid, witness = check_witness(collector, project, mode)
    return ProjectParts(
        name=name,
        mode=mode,
        ledger=ledger,
        runs_dir=runs_dir,
        writers=writers,
        witness_valid=witness_valid,
        witness=witness,
    )


def check_mode(collector: ErrorCollector, project: Mapping[str, Any]) -> Mode | None:
    """C14: mode is "solo" or "team"."""
    value = project.get("mode")
    if value == "solo":
        return "solo"
    if value == "team":
        return "team"
    collector.add(
        "C14", '"solo" or "team"', key=key_path("project", "mode"), found=value
    )
    return None


def check_registry(
    collector: ErrorCollector, project: Mapping[str, Any]
) -> Mapping[str, Writer] | None:
    """C12: the chain registry. Returns a read-only mapping, or None on any error.

    TOML itself refuses a key written twice, so a duplicate code never reaches here.
    A public key used twice is reported at the later entry, in file order.
    """
    chains = check_table(collector, project, "chains", "project", "C12")
    if chains is None:
        return None
    if not chains:
        collector.add(
            "C12",
            "at least one writer",
            key=key_path("project", "chains"),
            found=chains,
        )
        return None
    writers: dict[str, Writer] = {}
    first_code_for_key: dict[bytes, str] = {}
    ok = True
    for code, entry in chains.items():
        writer = _check_writer(collector, code, entry)
        if writer is None:
            ok = False
            continue
        first = first_code_for_key.setdefault(writer.public_key, code)
        if first != code:
            collector.add(
                "C12",
                f"a public key no other writer uses (already used by {first})",
                key=key_path(key_path("project.chains", code), "key"),
                found=entry["key"],
            )
            ok = False
            continue
        writers[code] = writer
    return types.MappingProxyType(writers) if ok else None


def _check_writer(collector: ErrorCollector, code: str, entry: object) -> Writer | None:
    """One registry entry: its code, then person and key."""
    path = key_path("project.chains", code)
    ok = True
    if not _CHAIN_CODE.fullmatch(code):
        collector.add(
            "C12",
            "a chain code: an uppercase letter, then uppercase letters or digits, "
            "2 to 6 characters",
            key=path,
            found=code,
        )
        ok = False
    if not isinstance(entry, dict):
        collector.add("C12", "a table with person and key", key=path, found=entry)
        return None
    check_unknown_keys(collector, entry, _WRITER_KEYS, path)
    person = check_string(collector, entry, "person", path, "C12")
    public_key = check_public_key(collector, entry, path)
    if not ok or person is None or public_key is None:
        return None
    return Writer(code=code, person=person, public_key=public_key)


def check_public_key(
    collector: ErrorCollector, entry: Mapping[str, Any], table_path: str
) -> bytes | None:
    """C12 key format, after C20. Returns the 32 raw key bytes.

    The text must be the exact encoding of those bytes, so one key has one
    spelling in bgl.toml.
    """
    value = entry.get("key")
    path = key_path(table_path, "key")
    if isinstance(value, str) and not check_placeholder(collector, value, path):
        return None
    raw = _decode_public_key(value)
    if raw is None:
        collector.add(
            "C12",
            f"{_KEY_PREFIX} followed by the base64 of a {_KEY_BYTES}-byte public key "
            "(as printed by bgl join)",
            key=path,
            found=value,
        )
    return raw


def _decode_public_key(value: object) -> bytes | None:
    if not isinstance(value, str) or not value.startswith(_KEY_PREFIX):
        return None
    text = value[len(_KEY_PREFIX) :]
    try:
        raw = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError):
        return None
    if len(raw) != _KEY_BYTES or base64.b64encode(raw).decode("ascii") != text:
        return None
    return raw


def check_witness(
    collector: ErrorCollector, project: Mapping[str, Any], mode: Mode | None
) -> tuple[bool, Witness | None]:
    """C17. Returns (valid, witness); witness is None when witnessing is off.

    witness = false is decided by mode, so with an invalid mode it is neither
    accepted nor reported (C14 already is).
    """
    path = key_path("project", "witness")
    value = project.get("witness")
    # [DECISION] witness = false in a team project is reported as C17 (the rule
    # that names it), not C14. With an invalid mode it is not reported at all.
    if value is False:
        if mode == "solo":
            return True, None
        if mode == "team":
            collector.add(
                "C17",
                "a [project.witness] table (team projects must witness)",
                key=path,
                found=value,
            )
        return False, None
    if not isinstance(value, dict):
        collector.add(
            "C17",
            "a [project.witness] table, or witness = false in a solo project",
            key=path,
            found=value,
        )
        return False, None
    check_unknown_keys(collector, value, _WITNESS_KEYS, path)
    rekor_url = check_https_url(collector, value, "rekor_url", path, "C17")
    calendars = check_https_url_list(collector, value, "ots_calendars", path, "C17")
    if rekor_url is None or calendars is None:
        return False, None
    return True, Witness(rekor_url=rekor_url, ots_calendars=calendars)


# --- Studies ---

GateKey = Literal[
    "dsr_threshold",
    "psr_threshold",
    "pbo_threshold",
    "pbo_min_trials",
    "cscv_blocks",
    "min_block_periods",
    "min_live_days",
    "live_witness_lag_days",
]
BasisKind = Literal["citation", "reasoning"]


@dataclasses.dataclass(frozen=True)
class Basis:
    """Why one gate value was chosen: a citation or written reasoning (C10)."""

    kind: BasisKind
    text: str


@dataclasses.dataclass(frozen=True)
class CostModel:
    """The cost BGL applies to every return series in the study (C10)."""

    model: Literal["flat"]
    cost_bps: float


@dataclasses.dataclass(frozen=True)
class Gate:
    """A study's deployment-gate thresholds, cost model and their bases (C10).

    basis holds one entry per gate key plus "costs".
    """

    dsr_threshold: float
    psr_threshold: float
    pbo_threshold: float
    pbo_min_trials: int
    cscv_blocks: int
    min_block_periods: int
    min_live_days: int
    live_witness_lag_days: int
    costs: CostModel
    basis: Mapping[str, Basis]


@dataclasses.dataclass(frozen=True)
class Holdout:
    """A study's holdout (C11). custodian is None only in solo mode (C14, C15)."""

    start: datetime.date
    files: tuple[str, ...]
    restricted_files: tuple[str, ...]
    custodian: str | None


@dataclasses.dataclass(frozen=True)
class Returns:
    """What a return is in this study (C16). risk_free is None for "none"."""

    periods_per_year: int
    basis: str
    risk_free: str | None
    risk_free_reasoning: str | None


@dataclasses.dataclass(frozen=True)
class Study:
    """One validated [studies.NAME] table. holdout is None with holdout = false."""

    name: str
    budget: int
    prefix: str
    claims: tuple[str, ...]
    prior_trials: int
    holdout: Holdout | None
    spec_files: tuple[str, ...]
    returns: Returns
    gate: Gate | None


@dataclasses.dataclass(frozen=True)
class Config:
    """A fully validated bgl.toml."""

    project: Project
    studies: Mapping[str, Study]


_TOP_LEVEL_KEYS = frozenset({"project", "studies"})
_STUDY_KEYS = frozenset(
    {
        "budget",
        "prefix",
        "claims",
        "prior_trials",
        "holdout_start",
        "holdout",
        "holdout_files",
        "restricted_files",
        "custodian",
        "spec_files",
        "returns",
        "gate",
    }
)
_RETURNS_KEYS = frozenset(
    {"periods_per_year", "basis", "risk_free", "risk_free_reasoning"}
)
_GATE_NUMBER_KEYS: tuple[GateKey, ...] = (
    "dsr_threshold",
    "psr_threshold",
    "pbo_threshold",
)
_GATE_COUNT_KEYS: tuple[GateKey, ...] = (
    "pbo_min_trials",
    "cscv_blocks",
    "min_block_periods",
    "min_live_days",
    "live_witness_lag_days",
)
_GATE_VALUE_KEYS = frozenset(_GATE_NUMBER_KEYS + _GATE_COUNT_KEYS)
_GATE_KEYS = _GATE_VALUE_KEYS | {"costs", "basis"}
_BASIS_KEYS = _GATE_VALUE_KEYS | {"costs"}
_BASIS_ENTRY_KEYS = frozenset({"kind", "text"})
_COSTS_KEYS = frozenset({"model", "cost_bps"})
_RISK_FREE_NONE = "none"


def check_studies(
    collector: ErrorCollector,
    data: Mapping[str, Any],
    mode: Mode | None,
    writers: Mapping[str, Writer] | None,
) -> Mapping[str, Study] | None:
    """Check [studies] and every study in it, then C6 across studies.

    mode and writers come from check_project. Either may be None when its own
    check failed; the study checks that depend on it are then skipped.
    """
    studies = check_table(collector, data, "studies", "", "C1")
    if studies is None:
        return None
    persons = None if writers is None else {w.person for w in writers.values()}
    built: dict[str, Study] = {}
    prefixes: dict[str, str] = {}
    ok = True
    for name, table in studies.items():
        path = key_path("studies", name)
        # [DECISION] A study with a bad name still has its contents checked.
        # A study that is not a table is C1.
        name_ok = check_name(collector, name, path, "C1")
        if not isinstance(table, dict):
            collector.add("C1", "a study table", key=path, found=table)
            ok = False
            continue
        prefix = check_prefix(collector, table, path)
        if prefix is not None:
            # [DECISION] C6 is reported at the later study's prefix, in file order.
            first = prefixes.setdefault(prefix, name)
            if first != name:
                collector.add(
                    "C6",
                    f"a prefix no other study uses (already used by {first})",
                    key=key_path(path, "prefix"),
                    found=prefix,
                )
                prefix = None
        study = _check_study(collector, name, table, prefix, mode, persons)
        if study is None or not name_ok:
            ok = False
        else:
            built[name] = study
    return types.MappingProxyType(built) if ok else None


def check_name(collector: ErrorCollector, name: object, path: str, rule: str) -> bool:
    """A study or claim name: a lowercase letter, then a-z, 0-9 or _, 1 to 32 long."""
    if isinstance(name, str) and _NAME.fullmatch(name):
        return True
    collector.add(
        rule,
        "a name of 1 to 32 characters: a lowercase letter, then lowercase "
        "letters, digits or underscores",
        key=path,
        found=name,
    )
    return False


def check_prefix(
    collector: ErrorCollector, study: Mapping[str, Any], path: str
) -> str | None:
    """C4: 2 to 4 uppercase letters."""
    value = study.get("prefix")
    if isinstance(value, str) and _PREFIX.fullmatch(value):
        return value
    collector.add(
        "C4", "2 to 4 uppercase letters", key=key_path(path, "prefix"), found=value
    )
    return None


def _check_study(
    collector: ErrorCollector,
    name: str,
    study: Mapping[str, Any],
    prefix: str | None,
    mode: Mode | None,
    persons: set[str] | None,
) -> Study | None:
    """Every rule inside one study table. prefix was checked by the caller."""
    path = key_path("studies", name)
    check_unknown_keys(collector, study, _STUDY_KEYS, path)
    budget = check_whole_number(collector, study, "budget", path, "C3", minimum=0)
    claims = check_claims(collector, study, path)
    prior_trials = check_whole_number(
        collector, study, "prior_trials", path, "C13", minimum=0
    )
    holdout_ok, holdout = check_holdout(collector, study, path, mode, persons)
    spec_files = check_path_list(
        collector, study, "spec_files", path, "C18", allow_empty=True
    )
    returns = check_returns(collector, study, path)
    gate_ok, gate = check_gate(collector, study, path)
    if (
        budget is None
        or prefix is None
        or claims is None
        or prior_trials is None
        or not holdout_ok
        or spec_files is None
        or returns is None
        or not gate_ok
    ):
        return None
    return Study(
        name=name,
        budget=budget,
        prefix=prefix,
        claims=claims,
        prior_trials=prior_trials,
        holdout=holdout,
        spec_files=spec_files,
        returns=returns,
        gate=gate,
    )


def check_claims(
    collector: ErrorCollector, study: Mapping[str, Any], path: str
) -> tuple[str, ...] | None:
    """C5: a list of unique claim names, which may be empty."""
    value = study.get("claims")
    claims_path = key_path(path, "claims")
    if not isinstance(value, list):
        collector.add("C5", "a list of claim names", key=claims_path, found=value)
        return None
    claims: list[str] = []
    ok = True
    for index, item in enumerate(value):
        # [DECISION] Claim errors are reported per item (claims[i]), like paths.
        item_path = f"{claims_path}[{index}]"
        if not check_name(collector, item, item_path, "C5"):
            ok = False
        elif item in claims:
            collector.add("C5", "each claim once", key=item_path, found=item)
            ok = False
        else:
            claims.append(item)
    return tuple(claims) if ok else None


def check_holdout(
    collector: ErrorCollector,
    study: Mapping[str, Any],
    path: str,
    mode: Mode | None,
    persons: set[str] | None,
) -> tuple[bool, Holdout | None]:
    """C11, plus the custodian rules C14 and C15. Returns (valid, holdout).

    holdout is None when the study sets holdout = false. With neither or both of
    holdout_start and holdout, the files and custodian are not checked, because
    it is unknown which set of rules applies.
    """
    # [DECISION] With both or neither of holdout_start and holdout, the files
    # and custodian are not checked: which rules apply is unknown.
    has_start = "holdout_start" in study
    has_flag = "holdout" in study
    if has_start and has_flag:
        collector.add(
            "C11",
            "exactly one of holdout_start or holdout = false, not both",
            key=key_path(path, "holdout"),
            found=study["holdout"],
        )
        return False, None
    if not has_start and not has_flag:
        collector.add(
            "C11",
            "holdout_start (a date) or holdout = false",
            key=key_path(path, "holdout_start"),
            found=None,
        )
        return False, None
    if has_flag:
        return _check_no_holdout(collector, study, path), None
    return _check_with_holdout(collector, study, path, mode, persons)


def _check_no_holdout(
    collector: ErrorCollector, study: Mapping[str, Any], path: str
) -> bool:
    ok = True
    if study["holdout"] is not False:
        collector.add(
            "C11",
            "false (or use holdout_start for a study with a holdout)",
            key=key_path(path, "holdout"),
            found=study["holdout"],
        )
        ok = False
    for key in ("holdout_files", "restricted_files", "custodian"):
        if key in study:
            collector.add(
                "C11",
                "nothing: this study has holdout = false",
                key=key_path(path, key),
                found=study[key],
            )
            ok = False
    return ok


def _check_with_holdout(
    collector: ErrorCollector,
    study: Mapping[str, Any],
    path: str,
    mode: Mode | None,
    persons: set[str] | None,
) -> tuple[bool, Holdout | None]:
    start = study["holdout_start"]
    # tomllib gives datetime.datetime for a date with a time, which is a subclass
    # of datetime.date, so the type must match exactly.
    start_ok = type(start) is datetime.date
    if not start_ok:
        collector.add(
            "C11",
            "an unquoted TOML date with no time, such as 2025-01-01",
            key=key_path(path, "holdout_start"),
            found=start,
        )
    files = check_path_list(
        collector, study, "holdout_files", path, "C11", allow_empty=False
    )
    restricted = check_path_list(
        collector, study, "restricted_files", path, "C11", allow_empty=True
    )
    overlap_ok = True
    if files is not None and restricted is not None:
        for index, item in enumerate(restricted):
            if item in files:
                collector.add(
                    "C11",
                    "a path not also in holdout_files",
                    key=f"{key_path(path, 'restricted_files')}[{index}]",
                    found=item,
                )
                overlap_ok = False
    custodian_ok, custodian = check_custodian(collector, study, path, mode, persons)
    if (
        not start_ok
        or files is None
        or restricted is None
        or not overlap_ok
        or not custodian_ok
    ):
        return False, None
    return True, Holdout(
        start=start, files=files, restricted_files=restricted, custodian=custodian
    )


def check_custodian(
    collector: ErrorCollector,
    study: Mapping[str, Any],
    path: str,
    mode: Mode | None,
    persons: set[str] | None,
) -> tuple[bool, str | None]:
    """C14 and C15 for a study with a holdout. Returns (valid, custodian).

    A missing custodian is C14 in team mode and fine in solo mode; with an
    invalid mode it is not reported. The registry check is skipped when the
    registry is invalid.
    """
    # [DECISION] A solo custodian, when given, is still checked against the
    # registry. A missing custodian with an invalid mode is not reported.
    custodian_path = key_path(path, "custodian")
    if "custodian" not in study:
        if mode == "solo":
            return True, None
        if mode == "team":
            collector.add(
                "C14",
                "a custodian (team studies with a holdout need one)",
                key=custodian_path,
                found=None,
            )
        return False, None
    custodian = check_string(collector, study, "custodian", path, "C15")
    if custodian is None:
        return False, None
    if persons is None:
        return False, None
    if custodian not in persons:
        collector.add(
            "C15",
            "a person in the chain registry",
            key=custodian_path,
            found=custodian,
        )
        return False, None
    return True, custodian


def check_free_text(
    collector: ErrorCollector,
    table: Mapping[str, Any],
    key: str,
    table_path: str,
    rule: str,
) -> str | None:
    """A non-empty string that is not a template placeholder (C20)."""
    value = table.get(key)
    if isinstance(value, str) and not check_placeholder(
        collector, value, key_path(table_path, key)
    ):
        return None
    return check_string(collector, table, key, table_path, rule)


def check_returns(
    collector: ErrorCollector, study: Mapping[str, Any], path: str
) -> Returns | None:
    """C16: the [returns] table."""
    returns = check_table(collector, study, "returns", path, "C16")
    if returns is None:
        return None
    returns_path = key_path(path, "returns")
    check_unknown_keys(collector, returns, _RETURNS_KEYS, returns_path)
    periods = check_whole_number(
        collector, returns, "periods_per_year", returns_path, "C16", minimum=1
    )
    basis = check_free_text(collector, returns, "basis", returns_path, "C16")
    risk_free_ok, risk_free, reasoning = _check_risk_free(
        collector, returns, returns_path
    )
    if periods is None or basis is None or not risk_free_ok:
        return None
    return Returns(
        periods_per_year=periods,
        basis=basis,
        risk_free=risk_free,
        risk_free_reasoning=reasoning,
    )


def _check_risk_free(
    collector: ErrorCollector, returns: Mapping[str, Any], returns_path: str
) -> tuple[bool, str | None, str | None]:
    """Returns (valid, risk_free path or None for "none", reasoning or None)."""
    # [DECISION] A missing or non-text risk_free is C16 (not C2), and then
    # risk_free_reasoning is not checked.
    value = returns.get("risk_free")
    if not isinstance(value, str) or not value:
        collector.add(
            "C16",
            f'a path to a risk-free file, or "{_RISK_FREE_NONE}"',
            key=key_path(returns_path, "risk_free"),
            found=value,
        )
        return False, None, None
    if value == _RISK_FREE_NONE:
        reasoning = check_free_text(
            collector, returns, "risk_free_reasoning", returns_path, "C16"
        )
        return reasoning is not None, None, reasoning
    path = check_path(collector, returns, "risk_free", returns_path)
    ok = path is not None
    if "risk_free_reasoning" in returns:
        collector.add(
            "C16",
            f'nothing: risk_free_reasoning goes with risk_free = "{_RISK_FREE_NONE}"',
            key=key_path(returns_path, "risk_free_reasoning"),
            found=returns["risk_free_reasoning"],
        )
        ok = False
    return ok, path, None


def check_gate(
    collector: ErrorCollector, study: Mapping[str, Any], path: str
) -> tuple[bool, Gate | None]:
    """C10: the optional [gate] table. Returns (valid, gate); gate None if absent."""
    if "gate" not in study:
        return True, None
    gate = check_table(collector, study, "gate", path, "C10")
    if gate is None:
        return False, None
    gate_path = key_path(path, "gate")
    check_unknown_keys(collector, gate, _GATE_KEYS, gate_path)
    dsr = check_number(
        collector, gate, "dsr_threshold", gate_path, "C10", above=0, below=1
    )
    psr = check_number(
        collector, gate, "psr_threshold", gate_path, "C10", above=0, below=1
    )
    pbo = check_number(
        collector, gate, "pbo_threshold", gate_path, "C10", above=0, at_most=0.5
    )
    pbo_min_trials = check_whole_number(
        collector, gate, "pbo_min_trials", gate_path, "C10", minimum=2
    )
    cscv_blocks = check_whole_number(
        collector, gate, "cscv_blocks", gate_path, "C10", minimum=4, even=True
    )
    min_block_periods = check_whole_number(
        collector, gate, "min_block_periods", gate_path, "C10", minimum=1
    )
    min_live_days = check_whole_number(
        collector, gate, "min_live_days", gate_path, "C10", minimum=1
    )
    lag_days = check_whole_number(
        collector, gate, "live_witness_lag_days", gate_path, "C10", minimum=1
    )
    costs = check_costs(collector, gate, gate_path)
    basis = check_basis(collector, gate, gate_path)
    if (
        dsr is None
        or psr is None
        or pbo is None
        or pbo_min_trials is None
        or cscv_blocks is None
        or min_block_periods is None
        or min_live_days is None
        or lag_days is None
        or costs is None
        or basis is None
    ):
        return False, None
    return True, Gate(
        dsr_threshold=dsr,
        psr_threshold=psr,
        pbo_threshold=pbo,
        pbo_min_trials=pbo_min_trials,
        cscv_blocks=cscv_blocks,
        min_block_periods=min_block_periods,
        min_live_days=min_live_days,
        live_witness_lag_days=lag_days,
        costs=costs,
        basis=basis,
    )


def check_costs(
    collector: ErrorCollector, gate: Mapping[str, Any], gate_path: str
) -> CostModel | None:
    """C10: [gate.costs] with model = "flat" and cost_bps zero or more."""
    costs = check_table(collector, gate, "costs", gate_path, "C10")
    if costs is None:
        return None
    costs_path = key_path(gate_path, "costs")
    check_unknown_keys(collector, costs, _COSTS_KEYS, costs_path)
    model_ok = costs.get("model") == "flat"
    if not model_ok:
        collector.add(
            "C10",
            '"flat"',
            key=key_path(costs_path, "model"),
            found=costs.get("model"),
        )
    cost_bps = check_number(collector, costs, "cost_bps", costs_path, "C10", at_least=0)
    if not model_ok or cost_bps is None:
        return None
    return CostModel(model="flat", cost_bps=cost_bps)


def check_basis(
    collector: ErrorCollector, gate: Mapping[str, Any], gate_path: str
) -> Mapping[str, Basis] | None:
    """C10: [gate.basis] with one {kind, text} entry per gate key and costs."""
    basis = check_table(collector, gate, "basis", gate_path, "C10")
    if basis is None:
        return None
    basis_path = key_path(gate_path, "basis")
    check_unknown_keys(collector, basis, _BASIS_KEYS, basis_path)
    entries: dict[str, Basis] = {}
    ok = True
    for key in sorted(_BASIS_KEYS):
        entry = _check_basis_entry(collector, basis, key, basis_path)
        if entry is None:
            ok = False
        else:
            entries[key] = entry
    return types.MappingProxyType(entries) if ok else None


def _check_basis_entry(
    collector: ErrorCollector,
    basis: Mapping[str, Any],
    key: str,
    basis_path: str,
) -> Basis | None:
    entry = basis.get(key)
    entry_path = key_path(basis_path, key)
    if not isinstance(entry, dict):
        collector.add(
            "C10", "a basis entry with kind and text", key=entry_path, found=entry
        )
        return None
    check_unknown_keys(collector, entry, _BASIS_ENTRY_KEYS, entry_path)
    kind = entry.get("kind")
    kind_ok = kind == "citation" or kind == "reasoning"
    if not kind_ok:
        collector.add(
            "C10",
            '"citation" or "reasoning"',
            key=key_path(entry_path, "kind"),
            found=kind,
        )
    text = check_free_text(collector, entry, "text", entry_path, "C10")
    if kind == "citation" and text is not None:
        return Basis(kind="citation", text=text)
    if kind == "reasoning" and text is not None:
        return Basis(kind="reasoning", text=text)
    return None


# --- Loading ---


def load_config(file: Path) -> Config:
    """Load and validate bgl.toml.

    Phase 1 (read_toml) raises one ConfigError. Phase 2 collects every error and
    raises one ConfigErrorGroup. Otherwise returns read-only config records.
    """
    data = read_toml(file)
    collector = ErrorCollector(file)
    check_unknown_keys(collector, data, _TOP_LEVEL_KEYS, "")
    parts = check_project(collector, data)
    studies = check_studies(collector, data, parts.mode, parts.writers)
    collector.raise_if_any()
    project = parts.build()
    if project is None or studies is None:
        # Every path that leaves a part unbuilt also adds an error.
        raise RuntimeError("bgl bug: config has no errors but did not build")
    return Config(project=project, studies=studies)


# --- Canonical tables for the study lock (C19) ---


def canonical_study(study: Study) -> dict[str, Any]:
    """The study table in canonical form: no floats, dates as ISO text.

    Decimals become their shortest round-trip text (repr), so 5, 5.0 and 5.00
    give the same table. Keys are written as in bgl.toml; the study name is not
    included, because the lock row records it separately.
    """
    # [DECISION] The study name is not in the canonical table (the lock row
    # records it); custodian is left out when absent; risk_free "none" is text.
    table: dict[str, Any] = {
        "budget": study.budget,
        "prefix": study.prefix,
        "claims": list(study.claims),
        "prior_trials": study.prior_trials,
        "spec_files": list(study.spec_files),
        "returns": _canonical_returns(study.returns),
    }
    if study.holdout is None:
        table["holdout"] = False
    else:
        table["holdout_start"] = study.holdout.start.isoformat()
        table["holdout_files"] = list(study.holdout.files)
        table["restricted_files"] = list(study.holdout.restricted_files)
        if study.holdout.custodian is not None:
            table["custodian"] = study.holdout.custodian
    if study.gate is not None:
        table["gate"] = _canonical_gate(study.gate)
    return table


def canonical_registry(writers: Mapping[str, Writer]) -> dict[str, dict[str, str]]:
    """The chain registry in canonical form, as written in bgl.toml."""
    return {
        code: {"person": writer.person, "key": public_key_text(writer.public_key)}
        for code, writer in writers.items()
    }


def public_key_text(public_key: bytes) -> str:
    """The registry text for a raw public key: ed25519: then standard base64."""
    return _KEY_PREFIX + base64.b64encode(public_key).decode("ascii")


def canonical_number(value: float) -> str:
    """C19: a decimal as Python's shortest round-trip text."""
    return repr(float(value))


def _canonical_returns(returns: Returns) -> dict[str, Any]:
    table: dict[str, Any] = {
        "periods_per_year": returns.periods_per_year,
        "basis": returns.basis,
        "risk_free": returns.risk_free
        if returns.risk_free is not None
        else _RISK_FREE_NONE,
    }
    if returns.risk_free_reasoning is not None:
        table["risk_free_reasoning"] = returns.risk_free_reasoning
    return table


def _canonical_gate(gate: Gate) -> dict[str, Any]:
    return {
        "dsr_threshold": canonical_number(gate.dsr_threshold),
        "psr_threshold": canonical_number(gate.psr_threshold),
        "pbo_threshold": canonical_number(gate.pbo_threshold),
        "pbo_min_trials": gate.pbo_min_trials,
        "cscv_blocks": gate.cscv_blocks,
        "min_block_periods": gate.min_block_periods,
        "min_live_days": gate.min_live_days,
        "live_witness_lag_days": gate.live_witness_lag_days,
        "costs": {
            "model": gate.costs.model,
            "cost_bps": canonical_number(gate.costs.cost_bps),
        },
        "basis": {
            key: {"kind": entry.kind, "text": entry.text}
            for key, entry in sorted(gate.basis.items())
        },
    }
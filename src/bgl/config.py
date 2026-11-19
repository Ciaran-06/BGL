import base64
import binascii
import dataclasses
import datetime
import math
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
        return value
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
    """A list of C2 paths. The list's own shape is reported under rule."""
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
            paths.append(item)
            continue
        collector.add(
            "C2",
            f"a relative path inside the repository ({problem})",
            key=f"{key_path(table_path, key)}[{index}]",
            found=item,
        )
        ok = False
    return tuple(paths) if ok else None


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


@dataclasses.dataclass(frozen=True)
class CostModel:
    """A study's declared, cited cost model (C10)."""

    model: str
    per_trade_bps: float | None
    commission_bps: float | None
    spread_estimator: str | None
    citation: str


@dataclasses.dataclass(frozen=True)
class Gate:
    """Deployment-gate thresholds for one study (C10)."""

    dsr_threshold: float
    pbo_threshold: float
    mintrl_confidence: float
    cscv_blocks: int
    min_block_periods: int
    min_live_days: int
    participation_limit: float
    capital: int
    citations: Mapping[str, str]
    costs: CostModel


@dataclasses.dataclass(frozen=True)
class Study:
    """One study's settings (C3, C4, C5, C11, C13)."""

    budget: int
    prefix: str
    claims: tuple[str, ...]
    holdout_start: datetime.date | None
    prior_trials: int
    gate: Gate | None


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
    ledger: str
    runs_dir: str
    writers: Mapping[str, Writer]
    witness: Witness | None


@dataclasses.dataclass(frozen=True)
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


@dataclasses.dataclass(frozen=True)
class Config:
    """A fully validated bgl.toml."""

    project: Project
    studies: Mapping[str, Study]


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


def load_config(file: Path) -> Config:
    """Load and validate bgl.toml. Not written yet."""
    raise NotImplementedError("load_config is not written yet")

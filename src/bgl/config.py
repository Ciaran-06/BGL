import dataclasses
import datetime
import re
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

_MAX_FOUND_LENGTH = 80
_LINE_IN_MESSAGE = re.compile(r"at line (\d+)")

_POSITION_IN_MESSAGE = re.compile(
    r"\s*\(at (?:line \d+, column \d+|end of document)\)\s*$"
)
_UTF8_BOM = b"\xef\xbb\xbf"


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


@dataclasses.dataclass(frozen=True)
class Project:
    """The [project] table (C1, C2, C12)."""

    name: str
    ledger: Path
    runs_dir: Path
    chains: tuple[str, ...]


@dataclasses.dataclass(frozen=True)
class Config:
    """A fully validated bgl.toml."""

    project: Project
    studies: Mapping[str, Study]


def load_config(file: Path) -> Config:
    """Load and validate bgl.toml. Not written yet."""
    raise NotImplementedError("load_config is not written yet")

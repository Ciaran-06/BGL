"""Shared test helpers and fixture texts. Imported by the test modules, never by bgl."""

from pathlib import Path

import pytest

from bgl.config import ConfigError, ConfigErrorGroup, load_config

MINIMAL_VALID = """
[project]
name = "example_project"
mode = "solo"
ledger = "bgl/ledger"
runs_dir = "bgl/runs"
witness = false

[project.chains.AA1]
person = "researcher_a"
key = "ed25519:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="

[studies.alpha]
budget = 20
prefix = "AL"
claims = ["effect_exists"]
prior_trials = 0
holdout = false
spec_files = []

[studies.alpha.returns]
periods_per_year = 252
basis = "Daily close-to-close portfolio return"
risk_free = "none"
risk_free_reasoning = "Self-financing long-short book"
"""

# A second study appended to MINIMAL_VALID, for rules that need two studies.
SECOND_STUDY = """
[studies.beta]
budget = 10
prefix = "BE"
claims = []
prior_trials = 0
holdout = false
spec_files = []

[studies.beta.returns]
periods_per_year = 365
basis = "Daily portfolio return"
risk_free = "none"
risk_free_reasoning = "Self-financing long-short book"
"""


def write(tmp_path: Path, text: str) -> Path:
    """Write text to bgl.toml in tmp_path as UTF-8 with LF line endings."""
    path = tmp_path / "bgl.toml"
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def write_bytes(tmp_path: Path, data: bytes) -> Path:
    """Write exact bytes to bgl.toml in tmp_path."""
    path = tmp_path / "bgl.toml"
    path.write_bytes(data)
    return path


def replace(fixture_text: str, old_text: str, new_text: str) -> str:
    """Replace old_text, which must appear exactly once, with new_text."""
    count = fixture_text.count(old_text)
    if count != 1:
        raise ValueError(f"expected {old_text!r} exactly once, found {count} times")
    return fixture_text.replace(old_text, new_text)


def load_error(tmp_path: Path, text: str) -> ConfigError | ConfigErrorGroup:
    """Write text as bgl.toml, load it, and return the error it must raise."""
    with pytest.raises((ConfigError, ConfigErrorGroup)) as info:
        load_config(write(tmp_path, text))
    error = info.value
    assert isinstance(error, (ConfigError, ConfigErrorGroup))
    return error


def _flatten(error: BaseException) -> list[ConfigError]:
    if isinstance(error, ConfigErrorGroup):
        return list(error.exceptions)
    if isinstance(error, ConfigError):
        return [error]
    raise TypeError(
        f"expected ConfigError or ConfigErrorGroup, got {type(error).__name__}"
    )


def assert_has_error(error: BaseException, rule: str, key: str | None = None) -> None:
    """Fail unless the error, or one inside the group, has this rule (and key)."""
    __tracebackhide__ = True
    found = _flatten(error)
    if any(e.rule == rule and (key is None or e.key == key) for e in found):
        return
    wanted = f"{rule} at {key}" if key is not None else rule
    pairs = ", ".join(f"{e.rule} at {e.key}" for e in found)
    raise AssertionError(f"no {wanted} error; found: {pairs}")


def assert_no_error(error: BaseException, rule: str) -> None:
    """Fail if the error, or any inside the group, has this rule."""
    __tracebackhide__ = True
    found = [e for e in _flatten(error) if e.rule == rule]
    if found:
        keys = ", ".join(str(e.key) for e in found)
        raise AssertionError(f"unexpected {rule} error at: {keys}")

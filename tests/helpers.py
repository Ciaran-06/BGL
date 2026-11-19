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


# A team project with a holdout, a custodian and a gate table: every optional
# part present. Mirrors the example in the spec, with real keys.
FULL_VALID = """
[project]
name = "example_project"
mode = "team"
ledger = "bgl/ledger"
runs_dir = "bgl/runs"

[project.chains.CG1]
person = "researcher_a"
key = "ed25519:AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQE="

[project.chains.CG2]
person = "researcher_a"
key = "ed25519:AgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgI="

[project.chains.HC1]
person = "custodian_a"
key = "ed25519:AwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwM="

[project.witness]
rekor_url = "https://rekor.example"
ots_calendars = ["https://calendar-a.example", "https://calendar-b.example"]

[studies.alpha]
budget = 20
prefix = "AL"
claims = ["effect_exists", "effect_persists"]
prior_trials = 0
holdout_start = 2025-01-01
holdout_files = ["data/holdout/alpha_holdout.parquet"]
restricted_files = ["data/raw/prices.parquet"]
custodian = "custodian_a"
spec_files = ["docs/studies/alpha.md"]

[studies.alpha.returns]
periods_per_year = 252
basis = "Daily close-to-close portfolio return on declared capital"
risk_free = "data/rates/rf_daily.csv"

[studies.alpha.gate]
dsr_threshold = 0.95
psr_threshold = 0.95
pbo_threshold = 0.05
pbo_min_trials = 10
cscv_blocks = 16
min_block_periods = 20
min_live_days = 90
live_witness_lag_days = 3

[studies.alpha.gate.costs]
model = "flat"
cost_bps = 5

[studies.alpha.gate.basis]
dsr_threshold = { kind = "citation", text = "Bailey and Lopez de Prado (2014)" }
psr_threshold = { kind = "reasoning", text = "Same confidence as the DSR bar" }
pbo_threshold = { kind = "citation", text = "Bailey et al. (2017), PBO < 0.5" }
pbo_min_trials = { kind = "reasoning", text = "Rank statistic too coarse below 10" }
cscv_blocks = { kind = "citation", text = "Bailey et al. (2017)" }
min_block_periods = { kind = "reasoning", text = "About one trading month per block" }
min_live_days = { kind = "reasoning", text = "One quarter of live trading" }
live_witness_lag_days = { kind = "reasoning", text = "Calendar days" }
costs = { kind = "reasoning", text = "Typical all-in cost for liquid large caps" }
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

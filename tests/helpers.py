"""Shared test helpers and fixture texts. Imported by the test modules, never by bgl."""

from pathlib import Path

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

"""A read-only view over every chain on this machine, for the run rules.

The rules that count across writers (R2, R4, R6, R20, R22) read rows from every
registered chain present here. What another machine holds is the gate's job.
"""

import dataclasses
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final

from bgl.config import Project
from bgl.ledger import LedgerReport, verify_all_chains, verify_all_chains_unlocked

COUNTED_TYPES: Final[frozenset[str]] = frozenset({"diagnostic", "confirmatory"})

Row = Mapping[str, Any]


@dataclasses.dataclass(frozen=True)
class LedgerView:
    """Every verified row on this machine, chains in registry order.

    [DECISION] Rows of a chain before its first broken row still count here, so
    another writer's broken chain neither blocks this writer (R7) nor hides the
    budget it already used. The gate fails a broken chain outright (G5).
    """

    report: LedgerReport
    rows: tuple[Row, ...]

    @classmethod
    def from_report(cls, report: LedgerReport) -> "LedgerView":
        rows = tuple(row for chain in report.chains.values() for row in chain.rows)
        return cls(report=report, rows=rows)

    def events(self, event: str, study: str | None = None) -> list[Row]:
        return [
            row
            for row in self.rows
            if row["event"] == event and (study is None or row["study"] == study)
        ]

    def opens(self, study: str) -> list[Row]:
        return self.events("open", study)

    def counted_opens(self, study: str) -> list[Row]:
        """R2, R12: every diagnostic or confirmatory open, whatever happened after."""
        return [row for row in self.opens(study) if row["type"] in COUNTED_TYPES]

    def voided_ids(self) -> frozenset[str]:
        return frozenset(
            row["id"] for row in self.events("void") if isinstance(row["id"], str)
        )

    def has_lock(self, study: str) -> bool:
        return bool(self.events("lock", study))

    def ids_in_chain(self, code: str) -> list[object]:
        """Every id in one chain, for run ID allocation (R16)."""
        chain = self.report.chains.get(code)
        return [] if chain is None else [row["id"] for row in chain.rows]

    def other_heads(self, code: str) -> dict[str, str | None]:
        """R24: each other registered chain's head here, or None with no rows."""
        return {
            other: (chain.head if chain.rows else None)
            for other, chain in self.report.chains.items()
            if other != code
        }


def read_ledger(root: Path, project: Project) -> LedgerView:
    """Verify and read every chain, under the ledger lock."""
    return LedgerView.from_report(verify_all_chains(root, project))


def read_ledger_unlocked(root: Path, project: Project) -> LedgerView:
    """read_ledger for a caller that already holds the ledger lock."""
    return LedgerView.from_report(verify_all_chains_unlocked(root, project))


@dataclasses.dataclass(frozen=True)
class Budget:
    """Counted runs used and left in one study, over the chains on this machine."""

    study: str
    budget: int
    used: int

    @property
    def left(self) -> int:
        return max(self.budget - self.used, 0)


def budget_of(view: LedgerView, study: str, budget: int) -> Budget:
    return Budget(study=study, budget=budget, used=len(view.counted_opens(study)))


def trials_n(view: LedgerView, study: str, prior_trials: int) -> int:
    """N = prior_trials + the variants of every non-data design run (Trials and N).

    Every open row counts, whatever its status, voided runs included.
    Confirmatory holdout runs, replay runs, data runs, dry runs, refusals and
    imported rows are not trials.
    """
    variants = sum(
        row["variants"] or 0
        for row in view.opens(study)
        if row["sample"] == "design" and row["type"] != "data"
    )
    return prior_trials + variants

"""bgl status: where each study and chain stands, as seen from this machine."""

import dataclasses
from pathlib import Path
from typing import Final

from bgl import git
from bgl.config import load_config
from bgl.locks import ledger_lock
from bgl.records import (
    LedgerView,
    budget_of,
    read_ledger_unlocked,
    trials_n,
)
from bgl.verify import OpenRuns, find_open_runs, this_machine_id
from bgl.witness import ChainWitness, chain_witness, unwitnessed_opens

CONFIG_FILE: Final[str] = "bgl.toml"


@dataclasses.dataclass(frozen=True)
class StudyStatus:
    name: str
    budget: int
    used: int
    n: int
    prior_trials: int
    locked: bool
    claims_decided: dict[str, str]  # claim -> confirmatory run that decides it
    claims_open: tuple[str, ...]
    voided: tuple[str, ...]
    unclosed: tuple[str, ...]
    in_progress: tuple[str, ...]
    unwitnessed: tuple[str, ...]


@dataclasses.dataclass(frozen=True)
class ChainStatus:
    code: str
    rows: int
    head: str | None
    problem: str | None
    witness: str


@dataclasses.dataclass(frozen=True)
class ProjectStatus:
    studies: tuple[StudyStatus, ...]
    chains: tuple[ChainStatus, ...]
    unregistered: tuple[str, ...]
    uncommitted_ledger: tuple[str, ...]


def _claims(view: LedgerView, study: str, claims: tuple[str, ...]) -> dict[str, str]:
    """Each claim decided by a confirmatory run that has not been voided."""
    voided = view.voided_ids()
    decided: dict[str, str] = {}
    for row in view.opens(study):
        if row["type"] != "confirmatory" or row["id"] in voided:
            continue
        for claim in row["claims"] or ():
            if claim in claims:
                decided.setdefault(claim, row["id"])
    return decided


def _witness_text(chain: ChainWitness | None, witnessing: bool) -> str:
    if not witnessing:
        return "off (witness = false)"
    if chain is None:
        return "no records"
    text = (
        f"{len(chain.logged)}/{chain.heads} heads in Rekor, "
        f"{len(chain.stamped)} stamped ({len(chain.confirmed)} confirmed in Bitcoin)"
    )
    if chain.problems:
        text += f"; {len(chain.problems)} problem(s), see bgl verify"
    return text


def project_status(root: Path) -> ProjectStatus:
    """Gather status for every study and chain."""
    config = load_config(root / CONFIG_FILE)
    with ledger_lock(root):
        view = read_ledger_unlocked(root, config.project)
        open_runs: OpenRuns = find_open_runs(root, view, this_machine_id())
    witnessed = {w.code: w for w in chain_witness(root, config.project, view)}
    witnessing = config.project.witness is not None
    unwitnessed = (
        set(unwitnessed_opens(view, witnessed.values())) if witnessing else set()
    )
    studies = []
    for name, study in config.studies.items():
        ids = {row["id"] for row in view.opens(name)}
        decided = _claims(view, name, study.claims)
        studies.append(
            StudyStatus(
                name=name,
                budget=study.budget,
                used=budget_of(view, name, study.budget).used,
                n=trials_n(view, name, study.prior_trials),
                prior_trials=study.prior_trials,
                locked=view.has_lock(name),
                claims_decided=decided,
                claims_open=tuple(c for c in study.claims if c not in decided),
                voided=tuple(sorted(view.voided_ids() & ids)),
                unclosed=tuple(
                    run_id
                    for run_id in open_runs.unclosed + open_runs.cannot_tell
                    if run_id in ids
                ),
                in_progress=tuple(r for r in open_runs.in_progress if r in ids),
                unwitnessed=tuple(sorted(unwitnessed & ids)),
            )
        )
    chains = tuple(
        ChainStatus(
            code=code,
            rows=len(report.rows),
            head=report.head if report.rows else None,
            problem=(
                None
                if report.problem is None
                else f"line {report.problem.line}: {report.problem.reason}"
            ),
            witness=_witness_text(witnessed.get(code), witnessing),
        )
        for code, report in view.report.chains.items()
    )
    return ProjectStatus(
        studies=tuple(studies),
        chains=chains,
        unregistered=view.report.unregistered,
        uncommitted_ledger=tuple(git.changed_paths(root, config.project.ledger)),
    )

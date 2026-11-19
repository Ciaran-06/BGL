"""The pre-open checks: every reason a run is refused before it starts.

Each check reads only what it is handed: the request (what the script asked
for), the facts (what git and the file system say), the study from bgl.toml,
and the ledger view (every chain on this machine). None of them writes
anything. They run in the order of the spec's rule index, and the first
failure is the refusal.

R19 and R7 come first in that index but are checked by the caller: a run with
no valid writer identity or with a broken chain cannot record a refusal.
"""

import dataclasses
import datetime
from collections.abc import Callable, Mapping
from typing import Any, Final

from bgl.config import Study
from bgl.records import COUNTED_TYPES, LedgerView
from bgl.studylock import LockContent, effective_lock, lock_differences
from bgl.writers import Identity

RUN_TYPES: Final[tuple[str, ...]] = (
    "data",
    "exploratory",
    "diagnostic",
    "confirmatory",
    "replay",
)
SAMPLES: Final[tuple[str, ...]] = ("design", "holdout", "live")
_NEED_PREDICTION: Final[frozenset[str]] = frozenset({"diagnostic", "confirmatory"})
_DESIGN_ONLY: Final[frozenset[str]] = frozenset({"exploratory", "diagnostic"})


@dataclasses.dataclass(frozen=True)
class Request:
    """What the script asked for, with paths made repository-relative."""

    study: str
    run_type: str
    script: str
    what_changed: str
    prediction: str | None
    inputs: tuple[str, ...]
    config: Any
    sample: str | None
    sample_start: datetime.date | None
    sample_end: datetime.date | None
    variants: int | None
    claims: tuple[str, ...] | None
    selects: Mapping[str, Any] | None
    dry: bool

    @property
    def counted(self) -> bool:
        return self.run_type in COUNTED_TYPES


@dataclasses.dataclass(frozen=True)
class InputFact:
    """One declared input as found on disk. size and sha256 are None if unreadable."""

    path: str
    is_file: bool
    size: int | None
    sha256: str | None


@dataclasses.dataclass(frozen=True)
class Facts:
    """What git and the file system said, gathered before the ledger lock is taken."""

    commit: str | None
    user: str | None
    git_problem: str | None
    commit_signature: str | None
    changes: tuple[str, ...]
    script_tracked: bool
    untracked_python: tuple[str, ...]
    untracked_modules: tuple[str, ...]
    inputs: tuple[InputFact, ...]
    script_sha256: str | None
    code_sha256: str | None
    config_sha256: str
    inputs_sha256: str | None
    lock_content: LockContent | None
    lock_problem: str | None


@dataclasses.dataclass(frozen=True)
class Refusal:
    rule: str
    reason: str


@dataclasses.dataclass(frozen=True)
class Context:
    """Everything a check may read."""

    request: Request
    facts: Facts
    study: Study
    view: LedgerView
    identity: Identity


def _list(items: Any) -> str:
    return ", ".join(str(item) for item in items)


def check_r22(ctx: Context) -> str | None:
    """Study lock: bgl.toml must equal the locked values plus amendments."""
    request, facts, name = ctx.request, ctx.facts, ctx.study.name
    locks = ctx.view.events("lock", name)
    if not locks:
        # This run writes the lock if it is counted, so the content must exist.
        return facts.lock_problem if request.counted else None
    if facts.lock_problem is not None:
        return facts.lock_problem
    assert facts.lock_content is not None
    amends = ctx.view.events("amend", name)
    hard: dict[str, None] = {}
    spec_changed = False
    # [DECISION] If chains on this machine hold more than one lock row for the
    # study (two writers each opened first), bgl.toml must match every one.
    for lock in locks:
        state = effective_lock(lock["lock"], amends)
        for difference in lock_differences(state, facts.lock_content):
            if difference.spec_only:
                spec_changed = True
            else:
                hard[difference.key] = None
    if hard:
        return (
            f"bgl.toml no longer matches the lock of study {name}: {_list(hard)}. "
            "Restore the locked values, or record an allowed change with bgl amend"
        )
    if spec_changed and request.run_type == "confirmatory":
        return (
            f"the spec files of study {name} changed since its lock. Record the "
            f"change with bgl amend {name} spec_files before a confirmatory run"
        )
    return None


def check_r3(ctx: Context) -> str | None:
    """Clean code: nothing uncommitted, and the script is tracked."""
    facts = ctx.facts
    if facts.git_problem is not None:
        return facts.git_problem
    if facts.changes:
        return (
            f"uncommitted changes to tracked files: {_list(facts.changes)}. "
            "Commit them first"
        )
    if not facts.script_tracked:
        return f"the script {ctx.request.script} is not tracked by git. Commit it"
    return None


def check_r3b(ctx: Context) -> str | None:
    """No untracked Python files anywhere in the repository."""
    if ctx.facts.untracked_python:
        return (
            f"untracked Python files in the repo: "
            f"{_list(ctx.facts.untracked_python)}. Commit them, or move them to "
            "scratch/ (ignored by git)."
        )
    return None


def check_r17(ctx: Context) -> str | None:
    """No loaded module from the repository that git does not track."""
    if ctx.facts.untracked_modules:
        return (
            f"code loaded from files git does not track: "
            f"{_list(ctx.facts.untracked_modules)}. Commit them, or do not import them"
        )
    return None


def check_r8(ctx: Context) -> str | None:
    """Every declared input exists and is a file."""
    paths = ctx.request.inputs
    repeated = sorted({path for path in paths if paths.count(path) > 1})
    if repeated:
        return f"inputs listed twice: {_list(repeated)}"
    missing = [fact.path for fact in ctx.facts.inputs if not fact.is_file]
    if missing:
        return f"declared inputs that are missing or not files: {_list(missing)}"
    return None


def check_r4(ctx: Context) -> str | None:
    """No duplicate of an earlier counted run; what_changed is present.

    [DECISION] what_changed must be non-empty for every run type, not only
    counted runs: the row field is always text.
    """
    request, facts = ctx.request, ctx.facts
    if not request.what_changed.strip():
        return "what_changed is empty. Say in words what this run changes"
    if not request.counted:
        return None
    mine = (
        facts.script_sha256,
        facts.code_sha256,
        facts.config_sha256,
        facts.inputs_sha256,
    )
    for row in ctx.view.counted_opens(ctx.study.name):
        theirs = (
            row["script_sha256"],
            row["code_sha256"],
            row["config_sha256"],
            row["inputs_sha256"],
        )
        if theirs == mine:
            return (
                f"same script, code, config and inputs as counted run {row['id']}. "
                "Change something, or make an exact rerun as an exploratory run"
            )
    return None


def check_r5(ctx: Context) -> str | None:
    """Diagnostic and confirmatory runs carry a prediction."""
    request = ctx.request
    if request.run_type in _NEED_PREDICTION and not (request.prediction or "").strip():
        return f"a {request.run_type} run needs a prediction, written before it runs"
    return None


def check_r21(ctx: Context) -> str | None:
    """Variants declared, in the range the run type allows."""
    run_type, variants = ctx.request.run_type, ctx.request.variants
    if run_type == "data":
        return None if variants is None else "data runs declare no variants"
    if variants is None:
        return f"a {run_type} run declares how many variants it evaluates"
    if run_type == "exploratory":
        return None if variants >= 0 else "variants cannot be negative"
    if run_type == "diagnostic":
        if variants >= 1:
            return None
        return "a diagnostic run evaluates at least 1 variant"
    return None if variants == 1 else f"a {run_type} run declares exactly 1 variant"


def _voided_once(rows: list[Mapping[str, Any]], voided: frozenset[str]) -> str | None:
    """None if the earlier runs leave room for one more, else the blocking run's ID.

    A void frees exactly one rerun (R6): no earlier run, or one earlier run that
    was voided. Two or more earlier runs block, voided or not.
    """
    standing = [row for row in rows if row["id"] not in voided]
    if standing:
        return str(standing[0]["id"])
    if len(rows) >= 2:
        return str(rows[-1]["id"])
    return None


def check_r6(ctx: Context) -> str | None:
    """Confirmatory runs: claims, one decision per claim, and the selected trial."""
    request, study, view = ctx.request, ctx.study, ctx.view
    if request.run_type != "confirmatory":
        if request.claims is not None or request.selects is not None:
            return "claims and selects are only for confirmatory runs"
        return None
    claims = request.claims or ()
    if not claims:
        return "a confirmatory run lists at least one of its study's claims"
    if len(set(claims)) != len(claims):
        return "a claim is listed twice"
    unknown = [claim for claim in claims if claim not in study.claims]
    if unknown:
        return f"not claims of study {study.name}: {_list(unknown)}"
    wanted = "holdout" if study.holdout is not None else "design"
    if request.sample != wanted:
        return (
            f"a confirmatory run in study {study.name} uses sample = {wanted} "
            f"(the study {'has' if wanted == 'holdout' else 'has no'} holdout)"
        )
    voided = view.voided_ids()
    confirmatory = [
        row for row in view.opens(study.name) if row["type"] == "confirmatory"
    ]
    for claim in claims:
        earlier = [row for row in confirmatory if claim in (row["claims"] or ())]
        blocker = _voided_once(earlier, voided)
        if blocker is not None:
            return (
                f"claim {claim} was already put to a confirmatory run ({blocker}). "
                "Each claim is decided once; a void frees one rerun only"
            )
    return _check_selects(ctx)


def _check_selects(ctx: Context) -> str | None:
    request, study = ctx.request, ctx.study
    selects = request.selects
    if study.holdout is None:
        # [DECISION] Without a holdout the confirmatory run is itself the
        # selected trial, so selects must be left out; the open row then
        # records {"run": its own ID, "variant": 1}.
        if selects is not None:
            return (
                "selects is for studies with a holdout; here the confirmatory "
                "run is its own selected trial"
            )
        return None
    if selects is None:
        return "a confirmatory holdout run names the design trial it confirms (selects)"
    target = [row for row in ctx.view.opens(study.name) if row["id"] == selects["run"]]
    if not target:
        return f"selects names run {selects['run']}, which is not a run of this study"
    row = target[0]
    # [DECISION] The selected trial may come from an exploratory or a
    # diagnostic run: both are design trials and both count in N.
    if row["sample"] != "design" or row["type"] not in _DESIGN_ONLY:
        return f"selects must name a design run; {row['id']} is not one"
    if not 1 <= selects["variant"] <= (row["variants"] or 0):
        return (
            f"run {row['id']} declared {row['variants']} variants; "
            f"variant {selects['variant']} is not one of them"
        )
    return None


def _sample_for_type(request: Request) -> str | None:
    run_type, sample = request.run_type, request.sample
    if run_type == "data":
        # [DECISION] A data run declares no sample: the split run reads both
        # periods, and a data run on design files alone tests no strategy.
        return None if sample is None else "data runs declare no sample"
    if run_type == "replay":
        return None if sample == "live" else "replay runs use sample = live"
    if sample == "live":
        return "sample = live is only for replay runs"
    if run_type in _DESIGN_ONLY and sample != "design":
        return f"{run_type} runs use sample = design"
    return None


def _guarded_inputs(ctx: Context) -> str | None:
    """Holdout and restricted files among the declared inputs."""
    request, holdout = ctx.request, ctx.study.holdout
    if holdout is None:
        return None
    locked = ctx.view.has_lock(ctx.study.name)
    split_run = request.run_type == "data" and not locked
    for path in request.inputs:
        if path in holdout.restricted_files and not split_run:
            when = "after the study lock" if locked else "outside a data run"
            return (
                f"{path} is a restricted file and cannot be declared {when}. "
                "Only a data run before the lock may read it, to do the split"
            )
        # [DECISION] A holdout file may be a declared input of the holdout run,
        # or of a data run before the lock (checking the split). Anything else
        # would be a look at the holdout.
        if path in holdout.files and request.sample != "holdout" and not split_run:
            return (
                f"{path} is a holdout file. Only the confirmatory holdout run "
                "may read it"
            )
    return None


def _sample_dates(ctx: Context) -> str | None:
    request, holdout = ctx.request, ctx.study.holdout
    if request.sample not in ("design", "holdout"):
        return None
    start, end = request.sample_start, request.sample_end
    if start is None or end is None:
        return f"a {request.sample} run declares sample_start and sample_end"
    if start > end:
        return f"sample_start {start} is after sample_end {end}"
    if holdout is None:
        return None
    if request.sample == "design" and end >= holdout.start:
        return (
            f"a design run must end before holdout_start {holdout.start}; "
            f"sample_end is {end}"
        )
    if request.sample == "holdout" and start < holdout.start:
        return (
            f"a holdout run must start on or after holdout_start {holdout.start}; "
            f"sample_start is {start}"
        )
    return None


def check_r20(ctx: Context) -> str | None:
    """Holdout: who may look, once, at which files and dates."""
    request, study, view = ctx.request, ctx.study, ctx.view
    problem = _sample_for_type(request)
    if problem is not None:
        return problem
    holdout = study.holdout
    person = ctx.identity.writer.person
    if request.sample == "holdout":
        if holdout is None:
            return f"study {study.name} has holdout = false"
        if request.run_type != "confirmatory":
            return "only a confirmatory run may use sample = holdout"
        if holdout.custodian is not None and person != holdout.custodian:
            return (
                f"only the custodian ({holdout.custodian}) may open the holdout "
                f"run; this writer is {person}"
            )
        earlier = [row for row in view.opens(study.name) if row["sample"] == "holdout"]
        blocker = _voided_once(earlier, view.voided_ids())
        if blocker is not None:
            return (
                f"the holdout of study {study.name} was already opened by "
                f"{blocker}. The holdout is looked at once"
            )
    if request.sample == "live":
        # [DECISION] "During incubation" is not checked yet: incubation rows
        # arrive with the gate (build step 12).
        if holdout is not None and holdout.custodian not in (None, person):
            return f"only the custodian ({holdout.custodian}) may open replay runs"
    return _guarded_inputs(ctx) or _sample_dates(ctx)


def check_r2(ctx: Context) -> str | None:
    """Budget: counted opens on this machine are fewer than the budget."""
    if not ctx.request.counted:
        return None
    used = len(ctx.view.counted_opens(ctx.study.name))
    if used >= ctx.study.budget:
        return (
            f"study {ctx.study.name} has used its budget ({used} of "
            f"{ctx.study.budget} counted runs). A larger budget needs bgl amend"
        )
    return None


# The spec's rule index order. R19, R7 and R1 are checked by the caller.
CHECKS: Final[tuple[tuple[str, Callable[[Context], str | None]], ...]] = (
    ("R22", check_r22),
    ("R3", check_r3),
    ("R3b", check_r3b),
    ("R17", check_r17),
    ("R8", check_r8),
    ("R4", check_r4),
    ("R5", check_r5),
    ("R21", check_r21),
    ("R6", check_r6),
    ("R20", check_r20),
    ("R2", check_r2),
)


def first_refusal(ctx: Context) -> Refusal | None:
    """Run every check in order and return the first failure, or None."""
    for rule, check in CHECKS:
        reason = check(ctx)
        if reason is not None:
            return Refusal(rule, reason)
    return None

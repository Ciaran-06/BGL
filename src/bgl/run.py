"""start_run and the Run object: a run's life from pre-open checks to close row.

Order at open (spec, "Before a run opens" and "Writing the open row"):
writer identity (R19), chain intact (R7), the checks in bgl.checks, then the
run lock, the lock row if this is the study's first counted open, the open
manifest, the open row, and the witness attempt (R23).

Order at close: the audit hook stops, output capture stops, R17 runs again, the
return series file and close file are written, then the close row (R10).
"""

import contextlib
import dataclasses
import datetime
import hashlib
import os
import shutil
import traceback
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import TracebackType
from typing import Any, Final

from bgl import audit, git, witness
from bgl.capture import OutputCapture
from bgl.checks import (
    RUN_TYPES,
    SAMPLES,
    Context,
    Facts,
    InputFact,
    Refusal,
    Request,
    first_refusal,
)
from bgl.config import Config, Study, load_config
from bgl.errors import (
    BglError,
    GitError,
    GitMissingError,
    LockError,
    LockTimeoutError,
    RunError,
    RunInvalid,
    RunRefused,
)
from bgl.file_io import atomic_write
from bgl.ids import DRY_LETTER, LETTER_FOR_TYPE, REFUSED_LETTER, next_run_id
from bgl.ledger import (
    LedgerReport,
    append_row,
    append_row_unlocked,
    verify_all_chains,
)
from bgl.locks import file_lock, ledger_lock
from bgl.manifest import (
    build_manifest,
    canonical_config,
    environment,
    file_bytes,
    hash_file,
    inputs_hash,
    repo_relative,
    sha256_json,
)
from bgl.records import Budget, LedgerView, budget_of, read_ledger, read_ledger_unlocked
from bgl.rowmeta import base_fields, utc_now
from bgl.series import SeriesData, check_series, series_bytes, summary
from bgl.studylock import lock_content
from bgl.writers import Identity, load_identity

OUTCOMES: Final[tuple[str, ...]] = ("supported", "not_supported", "inconclusive")
CONFIG_FILE: Final[str] = "bgl.toml"
_LOCKS_DIR: Final[str] = ".bgl/locks"
_TMP_DIR: Final[str] = ".bgl/tmp"
CLOSE_FORMAT_VERSION: Final[int] = 1

PathLike = str | os.PathLike[str]


def _as_date(value: object, name: str) -> datetime.date | None:
    if value is None:
        return None
    if isinstance(value, datetime.datetime):
        raise ValueError(f"{name} must be a date, not a datetime")
    if isinstance(value, datetime.date):
        return value
    if isinstance(value, str):
        try:
            return datetime.date.fromisoformat(value)
        except ValueError:
            pass
    raise ValueError(f"{name} must be a date or YYYY-MM-DD text, got {value!r}")


def _iso(value: datetime.date | None) -> str | None:
    return None if value is None else value.isoformat()


def _as_selects(value: object) -> dict[str, Any] | None:
    if value is None:
        return None
    if (
        not isinstance(value, Mapping)
        or set(value) != {"run", "variant"}
        or not isinstance(value["run"], str)
        or isinstance(value["variant"], bool)
        or not isinstance(value["variant"], int)
    ):
        raise ValueError(
            'selects must be {"run": a run ID, "variant": a whole number}, '
            f"got {value!r}"
        )
    return {"run": value["run"], "variant": value["variant"]}


def start_run(
    *,
    study: str,
    run_type: str,
    script: PathLike,
    what_changed: str,
    prediction: str | None = None,
    inputs: Sequence[PathLike] = (),
    config: Mapping[str, Any] | None = None,
    sample: str | None = None,
    sample_start: datetime.date | str | None = None,
    sample_end: datetime.date | str | None = None,
    variants: int | None = None,
    claims: Sequence[str] | None = None,
    selects: Mapping[str, Any] | None = None,
    dry: bool = False,
    dry_inputs: Mapping[PathLike, PathLike] | None = None,
) -> "Run":
    """Describe a run. Use the result in a with block; the checks run on entry.

    Arguments of the wrong kind raise ValueError here, before anything is
    recorded. Everything a rule decides is checked on entry and recorded as a
    refusal (RunRefused).
    """
    if run_type not in RUN_TYPES:
        raise ValueError(f"run_type must be one of {', '.join(RUN_TYPES)}")
    if sample is not None and sample not in SAMPLES:
        raise ValueError(f"sample must be one of {', '.join(SAMPLES)}")
    for name, text in (("study", study), ("what_changed", what_changed)):
        if not isinstance(text, str):
            raise ValueError(f"{name} must be text")
    if prediction is not None and not isinstance(prediction, str):
        raise ValueError("prediction must be text")
    if variants is not None and (
        isinstance(variants, bool) or not isinstance(variants, int)
    ):
        raise ValueError("variants must be a whole number")
    if isinstance(inputs, (str, bytes, os.PathLike)):
        raise ValueError("inputs must be a list of paths, not one path")
    if claims is not None and (
        isinstance(claims, str) or not all(isinstance(c, str) for c in claims)
    ):
        raise ValueError("claims must be a list of claim names")
    if config is not None and not isinstance(config, Mapping):
        raise ValueError("config must be a dict of settings")
    if dry_inputs is not None and not dry:
        raise ValueError("dry_inputs is only for dry=True")
    return Run(
        study=study,
        run_type=run_type,
        script=script,
        what_changed=what_changed,
        prediction=prediction,
        inputs=tuple(inputs),
        config=canonical_config({} if config is None else config),
        sample=sample,
        sample_start=_as_date(sample_start, "sample_start"),
        sample_end=_as_date(sample_end, "sample_end"),
        variants=variants,
        claims=None if claims is None else tuple(claims),
        selects=_as_selects(selects),
        dry=bool(dry),
        dry_inputs=dict(dry_inputs or {}),
    )


def gather_facts(root: Path, config: Config, request: Request) -> Facts:
    """Ask git and the file system everything the checks need. Reads only."""
    exempt = (config.project.ledger, config.project.runs_dir)
    commit = user = git_problem = None
    try:
        commit = git.head_commit(root)
        user = git.git_user(root)
    except GitMissingError:
        raise
    except GitError as error:
        git_problem = str(error)
    tracked = git.tracked_files(root)
    inputs = []
    for path in request.inputs:
        target = root / path
        if target.is_file():
            size, digest = hash_file(target)
            inputs.append(InputFact(path, True, size, digest))
        else:
            inputs.append(InputFact(path, False, None, None))
    all_found = all(fact.is_file for fact in inputs)
    study = config.studies.get(request.study)
    content = problem = None
    if study is not None and commit is not None:
        try:
            content = lock_content(root, config.project, study, commit)
        except LockError as error:
            problem = str(error)
    return Facts(
        commit=commit,
        user=user,
        git_problem=git_problem,
        commit_signature=(
            None if commit is None else git.commit_signature(root, commit)
        ),
        changes=tuple(git.uncommitted_changes(root, exempt)),
        script_tracked=request.script in tracked,
        untracked_python=tuple(git.untracked_python(root)),
        untracked_modules=tuple(
            module
            for module in audit.loaded_repo_modules(root)
            if module not in tracked
        ),
        inputs=tuple(inputs),
        # [DECISION] script_sha256 is the script's committed blob (see
        # git.blob_sha256); R3 has already required the working file to match.
        script_sha256=(
            None if commit is None else git.blob_sha256(root, commit, request.script)
        ),
        code_sha256=(
            None if commit is None else git.code_fingerprint(root, commit, exempt)
        ),
        config_sha256=sha256_json(request.config),
        inputs_sha256=(
            inputs_hash([{"path": fact.path, "sha256": fact.sha256} for fact in inputs])
            if all_found
            else None
        ),
        lock_content=content,
        lock_problem=problem,
    )


def _used_ids(root: Path, config: Config, view: LedgerView, code: str) -> list[object]:
    """Every ID that must not be reused: the chain's, and any file's (R13, R16)."""
    used = view.ids_in_chain(code)
    runs_dir = root / config.project.runs_dir
    for folder in (
        runs_dir,
        runs_dir / "returns",
        runs_dir / "output",
        root / _LOCKS_DIR,
    ):
        if folder.is_dir():
            used.extend(entry.name.split(".")[0] for entry in folder.iterdir())
    return used


@dataclasses.dataclass
class _Open:
    """State of a run between its open row and its close row."""

    root: Path
    config: Config
    study: Study
    identity: Identity
    request: Request
    run_id: str
    started: str
    watch: audit.Watch
    stack: contextlib.ExitStack
    capture: OutputCapture | None = None
    result: str | None = None
    outcomes: dict[str, str] = dataclasses.field(default_factory=dict)
    notes: list[str] = dataclasses.field(default_factory=list)
    outputs: list[str] = dataclasses.field(default_factory=list)
    series: SeriesData | None = None
    holdout_load: dict[str, int] | None = None
    read_holdout: bool = False
    fixtures: dict[str, Path] = dataclasses.field(default_factory=dict)


class Run:
    """One run. Created by start_run and used as a context manager."""

    def __init__(self, *, dry_inputs: dict[PathLike, PathLike], **arguments: Any):
        self._arguments = arguments
        self._dry_inputs = dry_inputs
        self._state = "new"  # new -> open -> closed
        self._open_state: _Open | None = None

    # --- opening -----------------------------------------------------------

    def __enter__(self) -> "Run":
        if self._state != "new":
            raise RunError("a run can be entered only once; call start_run again")
        self._state = "closed"  # until the open succeeds
        self._open_state = self._open()
        self._state = "open"
        return self

    def _request(self, root: Path) -> Request:
        arguments = dict(self._arguments)
        script = repo_relative(root, arguments.pop("script"))
        inputs = tuple(repo_relative(root, path) for path in arguments.pop("inputs"))
        return Request(script=script, inputs=inputs, **arguments)

    def _fixtures(self, root: Path, request: Request) -> dict[str, Path]:
        fixtures = {
            repo_relative(root, declared): Path(os.path.abspath(fixture))
            for declared, fixture in self._dry_inputs.items()
        }
        if request.dry and set(fixtures) != set(request.inputs):
            raise ValueError(
                "dry_inputs must give one fixture for each declared input, "
                "and nothing else"
            )
        return fixtures

    def _open(self) -> _Open:
        # [DECISION] One open run per process. The audit hook attributes what
        # the process opens to a single run, so a run inside a run could not be
        # told apart from its parent.
        if audit.is_watching():
            raise RunError("another run is already open in this process")
        script = Path(os.path.abspath(self._arguments["script"]))
        root = Path(os.path.realpath(git.repo_root(script.parent)))
        config = load_config(root / CONFIG_FILE)
        request = self._request(root)
        fixtures = self._fixtures(root, request)
        try:
            identity = load_identity(root, config.project)
        except BglError as error:
            # R19: with no valid identity nothing can be signed, so the
            # refusal cannot be recorded.
            raise RunRefused("R19", str(error)) from error
        facts = gather_facts(root, config, request)
        stack = contextlib.ExitStack()
        try:
            with ledger_lock(root):
                view = read_ledger_unlocked(root, config.project)
                self._check_own_chain(view, identity)
                study = self._refuse_or_study(
                    root, config, request, facts, view, identity, fixtures
                )
                state = self._write_open(
                    root, config, study, request, facts, view, identity, fixtures, stack
                )
        except BaseException:
            stack.close()
            raise
        if request.dry:
            audit.start_watching(state.watch)
            return state
        # R23: after the open row and before the run's code. A failure does not
        # stop the run; the open is then unwitnessed.
        with contextlib.suppress(Exception):
            witness.submit_head(root, config.project, identity)
        if request.counted:
            output = root / config.project.runs_dir / "output"
            state.capture = OutputCapture(
                output / f"{state.run_id}.stdout", output / f"{state.run_id}.stderr"
            )
            state.capture.start()
        audit.start_watching(state.watch)
        return state

    @staticmethod
    def _check_own_chain(view: LedgerView, identity: Identity) -> None:
        code = identity.writer.code
        own = view.report.chains[code]
        if own.problem is not None:
            torn = own.problem.reason.startswith("torn tail")
            hint = "bgl repair" if torn else "bgl verify"
            raise RunRefused(
                "R7",
                f"chain {code} is broken at line {own.problem.line}: "
                f"{own.problem.reason}. Run {hint}",
            )
        if own.rows and own.rows[0]["machine_id"] != identity.machine_id:
            raise RunRefused(
                "R19",
                f"chain {code} was written on another machine. One writer is one "
                "person on one machine; run bgl join for a new chain code here",
            )

    def _refuse_or_study(
        self,
        root: Path,
        config: Config,
        request: Request,
        facts: Facts,
        view: LedgerView,
        identity: Identity,
        fixtures: Mapping[str, Path],
    ) -> Study:
        """Run the checks. On failure, record the refusal and raise RunRefused."""
        study = config.studies.get(request.study)
        refusal: Refusal | None
        if study is None:
            refusal = Refusal(
                "R1", f"unknown study {request.study!r}. Declare it in {CONFIG_FILE}"
            )
        else:
            refusal = first_refusal(Context(request, facts, study, view, identity))
            if refusal is None and request.dry:
                missing = sorted(
                    path for path, fixture in fixtures.items() if not fixture.is_file()
                )
                if missing:
                    refusal = Refusal(
                        "R25", f"no fixture file for: {', '.join(missing)}"
                    )
        if refusal is None:
            assert study is not None
            return study
        # [DECISION] A refusal for an unknown study has no prefix to build an ID
        # from, so its row has id and study null and names the study in reason.
        # A refused dry run writes its one dry row, with status refused.
        fields = self._common_fields(request, facts, identity)
        letter = DRY_LETTER if request.dry else REFUSED_LETTER
        fields.update(
            event="dry" if request.dry else "refused",
            status="refused",
            reason=f"{refusal.rule}: {refusal.reason}",
            id=(
                None
                if study is None
                else next_run_id(
                    study.prefix,
                    letter,
                    identity.writer.code,
                    _used_ids(root, config, view, identity.writer.code),
                )
            ),
            study=None if study is None else study.name,
        )
        append_row_unlocked(
            root, config.project, identity.writer, identity.private_key, fields
        )
        raise RunRefused(refusal.rule, refusal.reason)

    @staticmethod
    def _common_fields(
        request: Request, facts: Facts, identity: Identity
    ) -> dict[str, Any]:
        """Fields shared by open, dry and refused rows."""
        return {
            "study": request.study,
            "type": request.run_type,
            "claims": None if request.claims is None else list(request.claims),
            "selects": None if request.selects is None else dict(request.selects),
            "variants": request.variants,
            "sample": request.sample,
            "sample_start": _iso(request.sample_start),
            "sample_end": _iso(request.sample_end),
            "script": request.script,
            "script_sha256": facts.script_sha256,
            "code_sha256": facts.code_sha256,
            "config_sha256": facts.config_sha256,
            "inputs_sha256": facts.inputs_sha256,
            "what_changed": request.what_changed,
            "prediction": request.prediction,
            "commit": facts.commit,
            "user": facts.user,
            "commit_signature": facts.commit_signature,
            "started": utc_now(),
            **base_fields(identity),
        }

    @staticmethod
    def _watch(
        root: Path,
        study: Study,
        request: Request,
        view: LedgerView,
        fixtures: Mapping[str, Path],
    ) -> audit.Watch:
        """What this run may open (R8, R20, R25)."""
        guarded: dict[Path, str] = {}
        holdout = study.holdout
        # A data run before the lock does the split, so it may open both kinds.
        split_run = request.run_type == "data" and not view.has_lock(study.name)
        if holdout is not None and not split_run:
            guarded.update({root / path: "holdout" for path in holdout.files})
            guarded.update(
                {root / path: "restricted" for path in holdout.restricted_files}
            )
        return audit.Watch(
            root,
            declared=[root / path for path in request.inputs],
            guarded=guarded,
            dry_allowed=list(fixtures.values()) if request.dry else None,
        )

    def _write_open(
        self,
        root: Path,
        config: Config,
        study: Study,
        request: Request,
        facts: Facts,
        view: LedgerView,
        identity: Identity,
        fixtures: dict[str, Path],
        stack: contextlib.ExitStack,
    ) -> _Open:
        """Take the run lock and write the lock row, manifest and open row."""
        code = identity.writer.code
        letter = DRY_LETTER if request.dry else LETTER_FOR_TYPE[request.run_type]
        run_id = next_run_id(
            study.prefix, letter, code, _used_ids(root, config, view, code)
        )
        fields = self._common_fields(request, facts, identity)
        fields["id"] = run_id
        watch = self._watch(root, study, request, view, fixtures)
        state = _Open(
            root=root,
            config=config,
            study=study,
            identity=identity,
            request=request,
            run_id=run_id,
            started=fields["started"],
            watch=watch,
            stack=stack,
            fixtures=fixtures,
        )
        project, key = config.project, identity.private_key
        if request.dry:
            # R25: one dry row, written before the body so the attempt is on
            # record whatever the body does. Nothing else is written.
            fields.update(event="dry", status="dry")
            append_row_unlocked(root, project, identity.writer, key, fields)
            return state
        lock_file = f"{_LOCKS_DIR}/{run_id}.lock"
        try:
            stack.enter_context(file_lock(root / lock_file, timeout=0))
        except LockTimeoutError as error:
            raise RunError(f"run lock {lock_file} is already held") from error
        if request.counted and not view.has_lock(study.name):
            # R22: the study's first counted open writes the lock row first.
            assert facts.lock_content is not None
            append_row_unlocked(
                root,
                project,
                identity.writer,
                key,
                {
                    "event": "lock",
                    "id": run_id,
                    "study": study.name,
                    "lock": facts.lock_content,
                    "started": fields["started"],
                    **base_fields(identity),
                },
            )
        manifest = build_manifest(
            run_id=run_id,
            study=study,
            config=request.config,
            inputs=[
                {"path": fact.path, "size": fact.size, "sha256": fact.sha256}
                for fact in sorted(facts.inputs, key=lambda fact: fact.path)
            ],
            environment_info=environment(root),
            git_info={
                "commit": facts.commit,
                "branch": git.current_branch(root),
                "untracked_files": git.has_untracked_files(root),
                "user": facts.user,
                "commit_signature": facts.commit_signature,
            },
        )
        manifest_path = f"{project.runs_dir}/{run_id}.json"
        data = file_bytes(manifest)
        (root / project.runs_dir).mkdir(parents=True, exist_ok=True)
        atomic_write(root / manifest_path, data)
        selects = request.selects
        if request.run_type == "confirmatory" and study.holdout is None:
            selects = {"run": run_id, "variant": 1}
        fields.update(
            event="open",
            status="open",
            selects=None if selects is None else dict(selects),
            lock_file=lock_file,
            manifest=manifest_path,
            manifest_sha256=hashlib.sha256(data).hexdigest(),
            other_heads=view.other_heads(code),
        )
        append_row_unlocked(root, project, identity.writer, key, fields)
        return state

    # --- while open --------------------------------------------------------

    def _need_open(self) -> _Open:
        if self._state != "open" or self._open_state is None:
            raise RunError("the run is not open; use it inside its with block")
        return self._open_state

    @property
    def id(self) -> str:
        """The run's ID, for example AL-R007-CG1."""
        return self._need_open().run_id

    def input(self, path: PathLike) -> Path:
        """The file to read for a declared input.

        [DECISION] Not in the spec's API table, but R25 needs it: a dry run
        replaces each declared input with its fixture, and the script can only
        follow that if it asks the run where to read. Outside a dry run this
        returns the declared file itself.
        """
        state = self._need_open()
        relative = repo_relative(state.root, path)
        if relative not in state.request.inputs:
            raise RunError(f"{relative} is not a declared input of this run")
        if state.request.dry:
            return state.fixtures[relative]
        return state.root / relative

    def returns(self, gross: object, turnover: object) -> None:
        """Record per-variant gross return and turnover series (see bgl.series)."""
        state = self._need_open()
        request = state.request
        if request.run_type == "data" or not request.variants:
            raise RunError("this run declared no variants, so it records no returns")
        # [DECISION] One call per run. A second call would silently replace
        # evidence the first one recorded.
        if state.series is not None:
            raise RunError("run.returns was already called for this run")
        state.series = check_series(
            gross,
            turnover,
            variants=request.variants,
            sample_start=request.sample_start,
            sample_end=request.sample_end,
        )

    def holdout(self, path: PathLike) -> Path:
        """Load a holdout file: the only way a holdout run may read one (R20).

        Records where in the captured output the load happened, which is the
        exposure point a later void is judged by (R6).
        """
        state = self._need_open()
        holdout = state.study.holdout
        relative = repo_relative(state.root, path)
        if state.request.sample != "holdout" or holdout is None:
            raise RunError("run.holdout is only for a confirmatory holdout run")
        if relative not in holdout.files:
            raise RunError(f"{relative} is not a holdout file of {state.study.name}")
        target = state.root / relative
        if state.request.dry:
            raise RunError("a dry run cannot load the holdout")
        if not target.is_file():
            # Sealed files are decrypted by the custodian step (build step 9).
            raise RunError(f"holdout file {relative} is not present as plain data")
        if state.holdout_load is None:
            state.holdout_load = (
                {"stdout": 0, "stderr": 0}
                if state.capture is None
                else state.capture.positions()
            )
        state.read_holdout = True
        state.watch.allow_guarded(target)
        return target

    def positions(self, period: object, weights: object) -> None:
        """Replay runs: record one period's position hash (build step 12)."""
        self._need_open()
        raise RunError("run.positions arrives with the gate's replay check")

    def output(self, path: PathLike) -> None:
        """Declare an output file. Its hash is recorded at close."""
        state = self._need_open()
        relative = repo_relative(state.root, path)
        if relative not in state.outputs:
            state.outputs.append(relative)

    def result(self, text: str) -> None:
        """Set the run's summary. Required for status ok (R11)."""
        state = self._need_open()
        if not isinstance(text, str) or not text.strip():
            raise RunError("a result is a non-empty summary in words")
        state.result = text

    def outcome(self, claim: str, value: str) -> None:
        """Confirmatory runs: declare one claim's outcome."""
        state = self._need_open()
        if state.request.run_type != "confirmatory":
            raise RunError("only a confirmatory run declares outcomes")
        if claim not in (state.request.claims or ()):
            raise RunError(f"{claim!r} is not one of this run's claims")
        if value not in OUTCOMES:
            raise RunError(f"an outcome is one of {', '.join(OUTCOMES)}")
        state.outcomes[claim] = value

    def note(self, text: str) -> None:
        """Add a remark to the close file."""
        state = self._need_open()
        if not isinstance(text, str) or not text.strip():
            raise RunError("a note is non-empty text")
        state.notes.append(text)

    # --- closing -----------------------------------------------------------

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        # Returning None never swallows an exception from the body (R10).
        state = self._open_state
        if self._state != "open" or state is None:
            return
        self._state = "closed"
        audit.stop_watching()
        try:
            if state.request.dry:
                return
            status, reason = self._close(state, exc)
        finally:
            state.stack.close()  # releases the run lock
            shutil.rmtree(state.root / _TMP_DIR, ignore_errors=True)
        # [DECISION] A run closed as invalid raises RunInvalid on exit when the
        # body itself raised nothing, so the script cannot carry on unaware.
        if status == "invalid" and exc is None:
            raise RunInvalid(state.run_id, reason or "")

    @staticmethod
    def _status(
        state: _Open, exc: BaseException | None, invalid: list[str]
    ) -> tuple[str, str | None]:
        """The close status and its reason.

        [DECISION] invalid wins over crashed and interrupted: the run loaded
        code or data it may not, however it ended. The error is still kept in
        the close file. sys.exit(0) counts as a normal end; any other exit code
        is a crash.
        """
        if invalid:
            return "invalid", "; ".join(invalid)
        clean_exit = isinstance(exc, SystemExit) and exc.code in (None, 0)
        if exc is None or clean_exit:
            claims = state.request.claims or ()
            undecided = [claim for claim in claims if claim not in state.outcomes]
            if state.result is None:
                return "no_result", None
            if state.request.run_type == "confirmatory" and undecided:
                # [DECISION] A confirmatory run that leaves a claim without a
                # declared outcome has not delivered its result (R11).
                missing = ", ".join(undecided)
                return "no_result", f"no outcome declared for: {missing}"
            return "ok", None
        if isinstance(exc, KeyboardInterrupt):
            return "interrupted", None
        summary_line = f"{type(exc).__name__}: {exc}".splitlines()[0]
        return "crashed", summary_line

    def _close(self, state: _Open, exc: BaseException | None) -> tuple[str, str | None]:
        root, project, run_id = state.root, state.config.project, state.run_id
        if state.capture is not None:
            state.capture.stop()
        watch = state.watch
        tracked = git.tracked_files(root)
        seen = sorted(path for path in watch.code_files if path not in tracked)
        scan = [path for path in audit.loaded_repo_modules(root) if path not in tracked]
        invalid = list(watch.violations)
        untracked = ", ".join(sorted(set(seen) | set(scan)))
        if untracked:
            invalid.insert(
                0, f"R17: the run loaded code git does not track: {untracked}"
            )
        status, reason = self._status(state, exc, invalid)
        runs_dir = root / project.runs_dir

        returns_sha: str | None = None
        if state.series is not None:
            data = series_bytes(state.series)
            (runs_dir / "returns").mkdir(parents=True, exist_ok=True)
            atomic_write(runs_dir / "returns" / f"{run_id}.csv", data)
            returns_sha = hashlib.sha256(data).hexdigest()

        outputs = []
        for path in state.outputs:
            target = root / path
            outputs.append(
                {
                    "path": path,
                    "sha256": hash_file(target)[1] if target.is_file() else None,
                    "tainted": state.read_holdout,
                }
            )

        output_record: dict[str, Any] | None = None
        output_sha: dict[str, str] | None = None
        if state.capture is not None:
            output_record, output_sha = {}, {}
            for name, captured in state.capture.paths.items():
                digest = hash_file(captured)[1]
                output_sha[name] = digest
                output_record[name] = {
                    "file": repo_relative(root, captured),
                    "sha256": digest,
                    "holdout_load": (
                        None if state.holdout_load is None else state.holdout_load[name]
                    ),
                }

        error: dict[str, Any] | None = None
        if exc is not None and status in ("crashed", "invalid"):
            error = {
                "type": type(exc).__name__,
                "message": str(exc),
                "traceback": "".join(traceback.format_exception(exc)),
            }
        outcome = self._outcome(state)
        close_file = {
            "format_version": CLOSE_FORMAT_VERSION,
            "id": run_id,
            "outputs": outputs,
            "files_opened": list(watch.files_opened),
            "files_opened_truncated": watch.files_truncated,
            "untracked_modules": {"seen": seen, "scan": scan},
            "processes": watch.process_record(),
            "hook_errors": watch.hook_errors,
            "notes": list(state.notes),
            "error": error,
            "output": output_record,
            "series": None if state.series is None else summary(state.series),
            "positions": None,
            "outcome": outcome,
        }
        close_path = f"{project.runs_dir}/{run_id}.close.json"
        close_bytes = file_bytes(close_file)
        atomic_write(root / close_path, close_bytes)

        # [DECISION] A close row repeats study and type from its open row, so a
        # chain reads without joining rows; everything else is close-only.
        append_row(
            root,
            project,
            state.identity.writer,
            state.identity.private_key,
            {
                "event": "close",
                "id": run_id,
                "study": state.study.name,
                "type": state.request.run_type,
                "status": status,
                "outcome": outcome,
                "result": state.result,
                "reason": reason,
                "started": state.started,
                "ended": utc_now(),
                "close_file": close_path,
                "close_sha256": hashlib.sha256(close_bytes).hexdigest(),
                "returns_sha256": returns_sha,
                "output_sha256": output_sha,
                **base_fields(state.identity),
            },
        )
        return status, reason

    @staticmethod
    def _outcome(state: _Open) -> dict[str, str | None] | None:
        """Each of a confirmatory run's claims mapped to its declared outcome.

        A claim with no declared outcome maps to null; BGL never invents one.
        """
        if state.request.run_type != "confirmatory":
            return None
        return {
            claim: state.outcomes.get(claim) for claim in state.request.claims or ()
        }


def verify_ledger(*, root: PathLike | None = None) -> LedgerReport:
    """Verify every chain on this machine. Each report names its first broken row."""
    top = _find_root(root)
    return verify_all_chains(top, load_config(top / CONFIG_FILE).project)


def budget(study: str, *, root: PathLike | None = None) -> Budget:
    """Counted runs used and left in a study, over the chains on this machine."""
    top = _find_root(root)
    config = load_config(top / CONFIG_FILE)
    if study not in config.studies:
        raise RunError(f"unknown study {study!r}")
    view = read_ledger(top, config.project)
    return budget_of(view, study, config.studies[study].budget)


def _find_root(root: PathLike | None) -> Path:
    start = Path.cwd() if root is None else Path(root)
    return Path(os.path.realpath(git.repo_root(start)))

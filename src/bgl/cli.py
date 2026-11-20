"""The bgl command line. It never creates a run: runs come only from start_run.

Commands so far: init, join, status, verify, repair, witness. Each is one entry
in COMMANDS; the rest arrive in build step 10, one entry each.
"""

import argparse
import dataclasses
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Final

from bgl import __version__, git, witness
from bgl.config import ConfigError, ConfigErrorGroup, load_config
from bgl.errors import BglError
from bgl.ledger import repair_chain
from bgl.rowmeta import base_fields, utc_now
from bgl.setup import SetupResult, init_project, join_writer
from bgl.status import ProjectStatus, project_status
from bgl.verify import VerifyReport, verify_project
from bgl.writers import load_identity

CONFIG_FILE: Final[str] = "bgl.toml"
EXIT_OK: Final[int] = 0
EXIT_FAILED: Final[int] = 1
EXIT_USAGE: Final[int] = 2


def _root() -> Path:
    return Path(os.path.realpath(git.repo_root(Path.cwd())))


def _print_setup(result: SetupResult, *, first: bool) -> None:
    for line in result.warnings:
        print(f"Warning: {line}", file=sys.stderr)
    for path in result.created:
        print(f"Created {path}")
    for line in result.appended:
        print(f"Added to {line}")
    print()
    if first:
        print(
            f"Writer {result.code} ({result.person}) is set up and registered in "
            f"{CONFIG_FILE}."
        )
        print(
            "Next: edit bgl.toml to declare a study, then commit bgl.toml, "
            ".gitignore, .gitattributes and the ledger folder."
        )
    else:
        print(f"Writer {result.code} ({result.person}) is set up on this machine.")
        print(f"Add this entry to {CONFIG_FILE} and commit it before your first run:")
        print()
        print(result.registry_entry, end="")
    print()
    print(
        "Back up your private key: a lost key means a new chain code, and a lost "
        "custodian key blocks a handover."
    )


def _cmd_init(args: argparse.Namespace) -> int:
    _print_setup(init_project(Path.cwd(), args.code, args.person), first=True)
    return EXIT_OK


def _cmd_join(args: argparse.Namespace) -> int:
    _print_setup(join_writer(Path.cwd(), args.code, args.person), first=False)
    return EXIT_OK


def _print_status(status: ProjectStatus) -> None:
    if not status.studies:
        print(f"No studies declared yet. Add one under [studies] in {CONFIG_FILE}.")
    for study in status.studies:
        lock = "locked" if study.locked else "not locked yet"
        print(f"Study {study.name} ({lock})")
        print(
            f"  Budget: {study.used} of {study.budget} counted runs used, "
            f"{max(study.budget - study.used, 0)} left"
        )
        print(f"  N: {study.n} trials ({study.prior_trials} prior)")
        for claim, run_id in study.claims_decided.items():
            print(f"  Claim {claim}: decided by {run_id}")
        for claim in study.claims_open:
            print(f"  Claim {claim}: open")
        if study.voided:
            print(f"  Voided: {', '.join(study.voided)}")
        if study.in_progress:
            print(f"  In progress: {', '.join(study.in_progress)}")
        if study.unclosed:
            print(f"  Unclosed: {', '.join(study.unclosed)} (see bgl verify)")
        if study.unwitnessed:
            print(
                f"  Unwitnessed opens: {', '.join(study.unwitnessed)} "
                "(not witnessed when opened; a later submission cannot change "
                "that for a run with a close file)"
            )
        if study.witnessed_offline:
            # N11: stored records are an offline convenience.
            print(
                f"  Witnessed opens: {len(study.witnessed_offline)}, from stored "
                "records, not checked against the log"
            )
    print()
    for chain in status.chains:
        head = chain.head[:12] if chain.head else "empty"
        state = "ok" if chain.problem is None else f"BROKEN at {chain.problem}"
        print(
            f"Chain {chain.code}: {chain.rows} rows, head {head}, {state}; "
            f"witness {chain.witness}"
        )
    for name in status.unregistered:
        print(f"Warning: {name} is in the ledger folder but is not a registered chain")
    if status.uncommitted_ledger:
        print(
            f"Warning: uncommitted ledger changes: "
            f"{', '.join(status.uncommitted_ledger)}. Commit them so git backs "
            "them up"
        )


def _cmd_status(args: argparse.Namespace) -> int:
    _print_status(project_status(_root()))
    return EXIT_OK


def _print_verify(report: VerifyReport) -> None:
    for finding in report.failures:
        print(f"FAIL ({finding.check}): {finding.message}")
    for finding in report.warnings:
        print(f"Warning ({finding.check}): {finding.message}")
    for run_id in report.in_progress:
        print(f"In progress: {run_id}")
    if report.contains:
        print(f"Found in: {', '.join(report.contains)}")
    print(
        "verify passed"
        if report.ok
        else f"verify failed: {len(report.failures)} problem(s)"
    )


def _cmd_verify(args: argparse.Namespace) -> int:
    report = verify_project(_root(), contains=args.contains)
    _print_verify(report)
    return EXIT_OK if report.ok else EXIT_FAILED


def _cmd_repair(args: argparse.Namespace) -> int:
    root = _root()
    config = load_config(root / CONFIG_FILE)
    identity = load_identity(root, config.project)
    row = repair_chain(
        root,
        config.project,
        identity.writer,
        identity.private_key,
        {"started": utc_now(), **base_fields(identity)},
    )
    witness.after_append(root, config.project, identity)
    removed = row["removed"]
    print(
        f"Removed a torn tail of {removed['length']} bytes from chain "
        f"{identity.writer.code} and recorded it (sha256 {removed['sha256']})."
    )
    return EXIT_OK


def _cmd_witness(args: argparse.Namespace) -> int:
    root = _root()
    config = load_config(root / CONFIG_FILE)
    if config.project.witness is None:
        print("Witnessing is off for this project (witness = false); nothing to do.")
        return EXIT_OK
    problems: list[str] = []
    try:
        identity = load_identity(root, config.project)
    except BglError as error:
        print(f"No writer on this machine ({error}); only upgrading stamps.")
    else:
        submitted = witness.submit_pending(root, config.project, identity)
        print(
            f"Logged {len(submitted.logged)} head(s) in Rekor and stamped "
            f"{len(submitted.stamped)} for chain {identity.writer.code}."
        )
        problems.extend(submitted.problems)
    upgraded = witness.upgrade_proofs(root, config.project)
    print(
        f"Upgraded {upgraded.upgraded} stamp(s); {upgraded.pending} still wait "
        "for Bitcoin (this takes a few hours)."
    )
    for problem in problems:
        print(f"Warning: {problem}")
    if problems:
        print(
            "Run bgl witness again later to retry. Commit the ledger folder to "
            "keep the records."
        )
    # [DECISION] F21: any submission that failed exits 1, so a script or CI
    # job notices; stamps still waiting for Bitcoin are not a failure.
    return EXIT_FAILED if problems else EXIT_OK


@dataclasses.dataclass(frozen=True)
class Argument:
    """One argparse argument: its names or flags, and add_argument's options."""

    flags: tuple[str, ...]
    options: Mapping[str, Any] = dataclasses.field(default_factory=dict)


def _arg(*flags: str, **options: Any) -> Argument:
    return Argument(flags, options)


@dataclasses.dataclass(frozen=True)
class Command:
    """One row of the command table. Each later build step adds one entry."""

    name: str
    help: str
    handler: Callable[[argparse.Namespace], int]
    arguments: tuple[Argument, ...] = ()


COMMANDS: Final[tuple[Command, ...]] = (
    Command(
        "init",
        "set up BGL in this git repository",
        _cmd_init,
        (
            _arg("code", help="your chain code, for example CG1"),
            _arg("person", help="your name as it appears in the registry"),
        ),
    ),
    Command(
        "join",
        "set up a new writer on this machine",
        _cmd_join,
        (
            _arg("code", help="a new chain code, for example CG2"),
            _arg("person", help="your name as it appears in the registry"),
        ),
    ),
    Command("status", "budgets, N, claims and chains", _cmd_status),
    Command(
        "verify",
        "check every chain and run file",
        _cmd_verify,
        (
            _arg(
                "--contains",
                metavar="HASH",
                help="also check that this row hash is in a chain here",
            ),
        ),
    ),
    Command("repair", "remove a torn tail from your chain", _cmd_repair),
    Command(
        "witness",
        "retry pending witness submissions and upgrade stamps",
        _cmd_witness,
    ),
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bgl", description="Backtest Governance Library"
    )
    parser.add_argument("--version", action="version", version=f"bgl {__version__}")
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    for command in COMMANDS:
        sub = commands.add_parser(command.name, help=command.help)
        for argument in command.arguments:
            sub.add_argument(*argument.flags, **argument.options)
        sub.set_defaults(handler=command.handler)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run one command. Returns the exit code: 0 ok, 1 failed, 2 usage error."""
    args = build_parser().parse_args(argv)
    try:
        code: int = args.handler(args)
    except ConfigErrorGroup as group:
        for error in group.exceptions:
            print(error, file=sys.stderr)
        return EXIT_FAILED
    except (ConfigError, BglError, ValueError) as error:
        print(f"bgl: {error}", file=sys.stderr)
        return EXIT_FAILED
    return code


if __name__ == "__main__":
    sys.exit(main())

"""The bgl command line. It never creates a run: runs come only from start_run.

Commands so far: init, join, status, verify, repair. The rest arrive in build
step 10.
"""

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Final

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
                "(bgl witness retries pending heads)"
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
    return EXIT_FAILED if problems else EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bgl", description="Backtest Governance Library"
    )
    parser.add_argument("--version", action="version", version=f"bgl {__version__}")
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    init = commands.add_parser("init", help="set up BGL in this git repository")
    init.add_argument("code", help="your chain code, for example CG1")
    init.add_argument("person", help="your name as it appears in the registry")
    init.set_defaults(handler=_cmd_init)

    join = commands.add_parser("join", help="set up a new writer on this machine")
    join.add_argument("code", help="a new chain code, for example CG2")
    join.add_argument("person", help="your name as it appears in the registry")
    join.set_defaults(handler=_cmd_join)

    status = commands.add_parser("status", help="budgets, N, claims and chains")
    status.set_defaults(handler=_cmd_status)

    verify = commands.add_parser("verify", help="check every chain and run file")
    verify.add_argument(
        "--contains",
        metavar="HASH",
        help="also check that this row hash is in a chain here",
    )
    verify.set_defaults(handler=_cmd_verify)

    repair = commands.add_parser("repair", help="remove a torn tail from your chain")
    repair.set_defaults(handler=_cmd_repair)
    witness_cmd = commands.add_parser(
        "witness", help="retry pending witness submissions and upgrade stamps"
    )
    witness_cmd.set_defaults(handler=_cmd_witness)
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

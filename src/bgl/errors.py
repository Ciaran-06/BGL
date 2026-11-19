"""Error types for the ledger, keys and locks. Config errors live in bgl.config."""


class BglError(Exception):
    """Base class for BGL's own errors outside config loading."""


class LedgerError(BglError):
    """A ledger operation was refused."""


class ChainBrokenError(LedgerError):
    """R7: a writer's chain does not verify, so nothing may be appended to it."""

    def __init__(self, code: str, line: int, reason: str) -> None:
        self.code = code
        self.line = line
        self.reason = reason
        hint = (
            "run bgl repair"
            if reason.startswith("torn tail")
            else "run bgl verify for details"
        )
        super().__init__(
            f"R7: chain {code} is broken at line {line}: {reason} ({hint})"
        )


class NothingToRepairError(LedgerError):
    """bgl repair was asked to fix a chain with no torn tail."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(f"chain {code} has no torn tail; there is nothing to repair")


class LockTimeoutError(BglError):
    """Another process held a lock for longer than the timeout."""

    def __init__(self, path: str, timeout: float) -> None:
        self.path = path
        self.timeout = timeout
        super().__init__(
            f"could not take the lock {path} within {timeout:g} s; "
            "another BGL process is using it"
        )


class KeyFileError(BglError):
    """A private key or machine ID file is missing, malformed or does not match."""


class GitError(BglError):
    """A git command failed, git is missing, or the repository is not usable."""


class GitMissingError(GitError):
    """git is not installed or not on PATH. Never treated as a 'no' answer."""


class WriterError(BglError):
    """.bgl/writer is missing or malformed, or names no registered writer."""


class RunRefused(BglError):
    """A pre-open check failed. The refusal is recorded first where possible."""

    def __init__(self, rule: str, reason: str) -> None:
        self.rule = rule
        self.reason = reason
        super().__init__(f"Refused ({rule}): {reason}")


class RunError(BglError):
    """The run object was used wrongly, for example returns off the period grid."""


class RunInvalid(BglError):
    """The run finished, but was closed as invalid (R17 or R20)."""

    def __init__(self, run_id: str, reason: str) -> None:
        self.run_id = run_id
        self.reason = reason
        super().__init__(f"Run {run_id} was closed as invalid: {reason}")


class AmendError(BglError):
    """An amendment is not one of the allowed changes to a locked study."""


class LockError(BglError):
    """The study lock's contents cannot be built, for example a missing spec file."""

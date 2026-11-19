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

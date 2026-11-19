"""BGL, the Backtest Governance Library: logged, budgeted, signed backtest runs."""

from importlib.metadata import version

from bgl.errors import RunError, RunInvalid, RunRefused
from bgl.run import Run, budget, start_run, verify_ledger

__version__ = version("bgl")

__all__ = [
    "Run",
    "RunError",
    "RunInvalid",
    "RunRefused",
    "__version__",
    "budget",
    "start_run",
    "verify_ledger",
]

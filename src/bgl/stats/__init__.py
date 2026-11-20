"""Statistics for the deployment gate: pure functions, arrays in, numbers out.

Nothing in this package reads a file, the ledger or the config. Every formula
names its source next to the code, and every one is tested against a value
computed without the code under test (docs/PLAN.md, section 3).

All statistics are per period, on net excess returns (spec, "Statistics").
"""

from bgl.errors import BglError


class NotComputable(BglError):
    """A statistic cannot be computed from the data given. Never read as zero.

    The message says why, for the report: for example a series whose standard
    deviation is zero has no Sharpe ratio (N34).
    """

"""The standard normal distribution function and its inverse.

Both come from the standard library's statistics.NormalDist (docs/PLAN.md,
section 3), so no outside package decides a verdict.
"""

import math
from statistics import NormalDist
from typing import Final

from bgl.stats import NotComputable

_STANDARD: Final[NormalDist] = NormalDist(0.0, 1.0)


def cdf(x: float) -> float:
    """Phi(x), the standard normal distribution function."""
    if math.isnan(x):
        raise NotComputable("the normal distribution function was given nan")
    return _STANDARD.cdf(x)


def inverse_cdf(p: float) -> float:
    """Phi^-1(p) for 0 < p < 1."""
    if not 0.0 < p < 1.0:
        raise NotComputable(f"the inverse normal needs 0 < p < 1, got {p!r}")
    return _STANDARD.inv_cdf(p)

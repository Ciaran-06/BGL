"""Lo's autocorrelation-adjusted annualised Sharpe ratio (reported only).

Source: Lo (2002), "The statistics of Sharpe ratios", Financial Analysts
Journal 58(4). The annualised ratio is SR(q) = eta(q) SR with

    eta(q) = q / sqrt(q + 2 sum_{k=1}^{q-1} (q - k) rho_k).

Equation number unconfirmed; the form is confirmed by secondary sources only
(docs/PLAN.md, section 6, item 3). With every rho_k = 0, eta(q) = sqrt(q), the
usual square-root-of-time rule.
"""

import dataclasses
import math
from collections.abc import Sequence

import numpy.typing as npt

from bgl.stats import NotComputable
from bgl.stats.moments import as_returns, scaled_deviations, sharpe


def autocorrelations(values: npt.ArrayLike, lags: int) -> list[float]:
    """rho_1 .. rho_lags of a series.

    [DECISION] The usual sample autocorrelation: rho_k = sum_{t>k} d_t d_{t-k}
    / sum_t d_t^2, d the deviations from the full-sample mean, both sums over
    the whole series. This is the estimator in Box and Jenkins and in Politis
    and White (2004); Lo (2002) does not fix a different one. The deviations
    are scaled by an exact power of two (scaled_deviations), which leaves every
    rho unchanged and keeps the squares from underflowing.
    """
    r = as_returns(values)
    t = len(r)
    if lags >= t:
        raise NotComputable(f"{lags} lags need more than {t} periods")
    if bool((r == r[0]).all()):
        raise NotComputable("a constant series has no autocorrelation")
    _, _, d, _ = scaled_deviations(r)
    denominator = math.fsum((d * d).tolist())  # at least 0.25: max |d| >= 0.5
    return [
        math.fsum((d[k:] * d[:-k]).tolist()) / denominator for k in range(1, lags + 1)
    ]


def eta(rho: Sequence[float], q: int) -> float:
    """eta(q) from rho_1 .. rho_{q-1} (rho[0] is rho_1). Lo (2002).

    Not computable when the term under the root is not positive (N40).
    """
    if isinstance(q, bool) or not isinstance(q, int) or q < 1:
        raise ValueError(f"q must be a whole number of at least 1, got {q!r}")
    if len(rho) < q - 1:
        raise ValueError(f"eta({q}) needs {q - 1} autocorrelations, got {len(rho)}")
    term = q + 2 * math.fsum((q - k) * rho[k - 1] for k in range(1, q))
    if not term > 0:
        raise NotComputable(
            f"the term under the root in Lo's eta({q}) is {term!r}, not positive"
        )
    return q / math.sqrt(term)


@dataclasses.dataclass(frozen=True)
class LoSharpe:
    """Lo's annualised Sharpe ratio and its scale factor."""

    q: int
    eta: float
    sharpe_per_period: float
    sharpe_annualised: float


def lo_sharpe(values: npt.ArrayLike, *, periods_per_year: int) -> LoSharpe:
    """eta(q) times the per-period Sharpe ratio, with q = periods_per_year.

    [DECISION] N40 All q - 1 lags are used, with q = periods_per_year, and it
    is not computable when T <= q or the term under the root is not positive.
    Reported only.
    """
    q = periods_per_year
    r = as_returns(values)
    if len(r) <= q:
        raise NotComputable(
            f"Lo's ratio needs more than q = {q} periods, got {len(r)} (N40)"
        )
    per_period = sharpe(r)
    factor = eta(autocorrelations(r, q - 1) if q > 1 else [], q)
    return LoSharpe(q, factor, per_period, factor * per_period)

"""Probabilistic and deflated Sharpe ratios, the trial variance and MinTRL.

Sources:

- PSR and MinTRL: Bailey and López de Prado (2012), "The Sharpe ratio efficient
  frontier", Journal of Risk 15(2). Equation numbers unconfirmed (docs/PLAN.md,
  section 6, item 3). PSR is restated as eq. (2) of the 2014 paper.
- The expected maximum and the DSR hurdle: Bailey and López de Prado (2014),
  "The deflated Sharpe ratio", Journal of Portfolio Management 40(5), eq. (1)
  and its appendix eqs. (5) and (6); DSR is eq. (2).
- The floor on V: BGL's own choice (spec decision 17), not the paper's.

All Sharpe ratios here are per period, never annualised.
"""

import dataclasses
import math
from collections.abc import Sequence
from typing import Final

from bgl.stats import NotComputable
from bgl.stats.normal import cdf, inverse_cdf

# Euler-Mascheroni constant, to double precision (OEIS A001620:
# 0.57721566490153286060...).
EULER_GAMMA: Final[float] = 0.5772156649015329


def _variance_term(sharpe: float, skewness: float, kurtosis: float) -> float:
    """1 - g3 SR + (g4 - 1)/4 SR^2, the term under the root in PSR and MinTRL.

    [DECISION] Not computable unless the term is positive. For any sample,
    g4 >= 1 + g3^2, so the term is never negative in exact arithmetic; it is
    zero only for a degenerate two-point sample, where PSR divides by zero.
    """
    term = 1.0 - skewness * sharpe + (kurtosis - 1.0) / 4.0 * (sharpe * sharpe)
    if not term > 0.0:
        raise NotComputable(
            "the PSR variance term 1 - skew*SR + (kurt-1)/4*SR^2 is not positive"
        )
    return term


def psr(
    *, sharpe: float, skewness: float, kurtosis: float, t: int, sr_star: float
) -> float:
    """PSR(SR*) = Phi((SR - SR*) sqrt(T-1) / sqrt(1 - g3 SR + (g4-1)/4 SR^2)).

    Bailey and López de Prado (2012); 2014 paper eq. (2). kurtosis is the
    plain (not excess) kurtosis, so a normal series gives 3.
    """
    if t < 2:
        raise NotComputable("PSR needs at least two periods")
    term = _variance_term(sharpe, skewness, kurtosis)
    z = (sharpe - sr_star) * math.sqrt(t - 1) / math.sqrt(term)
    return cdf(z)


def expected_max(n: int) -> float:
    """E[max of N standard normals], approximately (2014 paper eq. (1)).

    (1 - gamma) Phi^-1(1 - 1/N) + gamma Phi^-1(1 - 1/(N e)) for N >= 2; the
    spec sets it to 0 for N = 1 (the maximum of one draw has mean 0).
    """
    if isinstance(n, bool) or not isinstance(n, int) or n < 1:
        raise ValueError(f"N must be a whole number of at least 1, got {n!r}")
    if n == 1:
        return 0.0
    return (1.0 - EULER_GAMMA) * inverse_cdf(1.0 - 1.0 / n) + EULER_GAMMA * (
        inverse_cdf(1.0 - 1.0 / (n * math.e))
    )


def dsr_hurdle(*, n: int, variance: float) -> float:
    """SR* = sqrt(V) * expected_max(N) (2014 paper eq. (2)); 0 when N = 1."""
    if not math.isfinite(variance) or variance <= 0.0:
        raise ValueError(f"V must be positive and finite, got {variance!r}")
    return math.sqrt(variance) * expected_max(n)


@dataclasses.dataclass(frozen=True)
class TrialVariance:
    """V = max(V-hat, 1/(T-1)) and what went into it (spec decision 17, N34)."""

    value: float
    v_hat: float | None  # None with fewer than two Sharpe ratios
    floor: float
    floor_applied: bool
    trials: int  # K, the variants with a Sharpe ratio


def trial_variance(sharpes: Sequence[float], *, t: int) -> TrialVariance:
    """V = max(V-hat, 1/(T-1)), T the selected variant's length.

    [DECISION] N34 V-hat is the sample variance, divisor K-1, of the K
    per-period Sharpe ratios given (only variants that have one). With K < 2,
    V = 1/(T-1).

    [DECISION] floor_applied is true when V-hat is strictly below the floor or
    missing. At equality both give the same V, and the report says the
    recorded trials alone set it.
    """
    if t < 2:
        raise NotComputable("the floor 1/(T-1) needs at least two periods")
    floor = 1.0 / (t - 1)
    k = len(sharpes)
    if k < 2:
        return TrialVariance(floor, None, floor, True, k)
    values = [float(value) for value in sharpes]
    if not all(math.isfinite(value) for value in values):
        raise ValueError("Sharpe ratios must be finite")
    if all(value == values[0] for value in values):
        # Identical Sharpe ratios have V-hat = 0 exactly; the floating mean
        # could otherwise leave a residue of about 1e-32.
        v_hat = 0.0
    else:
        centre = math.fsum(values) / k
        v_hat = math.fsum((value - centre) * (value - centre) for value in values)
        v_hat /= k - 1
    if v_hat < floor:
        return TrialVariance(floor, v_hat, floor, True, k)
    return TrialVariance(v_hat, v_hat, floor, False, k)


def min_trl(
    *,
    sharpe: float,
    skewness: float,
    kurtosis: float,
    sr_star: float,
    threshold: float,
) -> float | None:
    """MinTRL = 1 + [1 - g3 SR + (g4-1)/4 SR^2] (z / (SR - SR*))^2.

    Bailey and López de Prado (2012), with z = Phi^-1(threshold). Returns None
    for "not reachable", when SR <= SR* (spec, "MinTRL").

    Derivation, for SR > SR*: PSR(SR*) >= threshold exactly when
    (SR - SR*) sqrt(T-1) / sqrt(term) >= z. The left side is never negative,
    so with z <= 0 (threshold <= 0.5) every T >= 1 passes and MinTRL = 1;
    with z > 0, squaring is safe and gives T >= 1 + term (z / (SR - SR*))^2.
    Hence z is replaced by max(z, 0). The paper's formula squares z, which
    is right only for thresholds above 0.5.

    [DECISION] For SR <= SR* the spec's "not reachable" is kept. With a
    threshold of 0.5 or below, PSR can still pass there (it is 0.5 at
    SR = SR*, and at short T when SR < SR*), so "T >= MinTRL exactly when
    DSR passes" holds only for SR > SR*. A question for Ciaran.
    """
    z = inverse_cdf(threshold)
    if sharpe <= sr_star:
        return None
    term = _variance_term(sharpe, skewness, kurtosis)
    ratio = max(z, 0.0) / (sharpe - sr_star)
    return 1.0 + term * (ratio * ratio)

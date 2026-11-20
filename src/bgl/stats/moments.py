"""Net excess returns and the moment estimators every other statistic uses.

Spec, "Statistics": r_t = g_t - tau_t c - f_t; the Sharpe ratio is mean over
standard deviation with divisor T-1; skewness and kurtosis are the population
values with divisor T, and the kurtosis is not excess (a normal gives 3).
"""

import dataclasses
import math
import sys
from collections.abc import Hashable, Mapping
from decimal import (
    Context,
    Decimal,
    DivisionByZero,
    Inexact,
    InvalidOperation,
    Overflow,
    localcontext,
)
from typing import Final, TypeVar

import numpy as np
import numpy.typing as npt

from bgl.stats import NotComputable

FloatArray = npt.NDArray[np.float64]
Key = TypeVar("Key", bound=Hashable)

BPS_PER_UNIT: Final[int] = 10_000  # cost_bps / 10^4 is the cost per unit turnover


def as_returns(values: npt.ArrayLike, what: str = "returns") -> FloatArray:
    """A one-dimensional float array of finite values.

    [DECISION] Non-zero values below the smallest normal float (about
    2.2e-308) are not computable: such subnormal numbers carry too few bits,
    and no real return is that small. Zero is fine.
    """
    array = np.asarray(values, dtype=np.float64)
    if array.ndim != 1:
        raise ValueError(f"{what} must be one-dimensional, got shape {array.shape}")
    if not np.all(np.isfinite(array)):
        raise NotComputable(f"{what} hold nan or inf")
    magnitude = np.abs(array)
    if np.any((magnitude > 0) & (magnitude < sys.float_info.min)):
        raise NotComputable(f"{what} hold non-zero values too small to compute with")
    return array


# Enough digits for any exact sum or product of two floats' shortest texts
# (exponents span about 650 decimal places); Inexact is trapped regardless.
_EXACT: Final[Context] = Context(
    prec=2000, traps=[Inexact, InvalidOperation, DivisionByZero, Overflow]
)


def _decimal(value: float) -> Decimal:
    return Decimal(repr(float(value)))


def net_excess(
    gross: npt.ArrayLike,
    turnover: npt.ArrayLike,
    *,
    cost_bps: float,
    risk_free: npt.ArrayLike | None,
) -> FloatArray:
    """r_t = g_t - tau_t c - f_t, with c = cost_bps / 10^4 (spec, decision 5).

    risk_free is the locked file's per-period values on the same periods, or
    None for risk_free = "none", which subtracts zero. It is a required
    argument, so a forgotten risk-free series cannot silently become zero.

    [DECISION] Each input is taken as its shortest round-trip decimal text
    (repr), which is exactly the series file's 10-decimal text, C19's text
    for cost_bps and the risk-free file's text (up to 15 significant
    digits). r_t is computed exactly in decimal and rounded once to a float.
    So a series that is constant in decimal (say gross = 0.01 + tau * 5 bps,
    or gross = risk-free + 0.03) is exactly constant, s = 0, and has no
    Sharpe ratio (N34), instead of a float residue giving s of about 1e-18.
    """
    g = as_returns(gross, "gross returns")
    tau = as_returns(turnover, "turnover")
    if tau.shape != g.shape:
        raise ValueError("gross returns and turnover differ in length")
    if not math.isfinite(cost_bps) or cost_bps < 0:
        raise ValueError(f"cost_bps must be finite and zero or more, got {cost_bps}")
    f: FloatArray | None = None
    if risk_free is not None:
        f = as_returns(risk_free, "risk-free returns")
        if f.shape != g.shape:
            raise ValueError("risk-free returns and gross returns differ in length")
    with localcontext(_EXACT):
        cost = _decimal(cost_bps) / BPS_PER_UNIT
        exact = [
            _decimal(g[i])
            - _decimal(tau[i]) * cost
            - (0 if f is None else _decimal(f[i]))
            for i in range(len(g))
        ]
    return as_returns([float(value) for value in exact], "net excess returns")


def _centred(values: npt.ArrayLike) -> tuple[int, float, FloatArray]:
    """(T, mean, deviations from the mean).

    [DECISION] Sums use math.fsum, which is exactly rounded, so a statistic
    does not depend on numpy's summation order or the CPU's vector unit. The
    remaining differences across systems are left to N42's rounding.
    """
    r = as_returns(values)
    t = len(r)
    if t == 0:
        raise NotComputable("the series is empty")
    mean = math.fsum(r.tolist()) / t
    return t, mean, r - mean


def _constant(values: FloatArray) -> bool:
    """True when every value is the same, which is exactly s = 0.

    [DECISION] N34 s = 0 (no Sharpe ratio) is decided by comparing the values,
    not by testing a computed s against zero: the floating mean of a constant
    series can leave deviations of about 1e-17, which would give a huge,
    meaningless Sharpe ratio. net_excess computes exactly in decimal, so a
    series that is constant in its decimal texts is constant here too.
    """
    return bool(np.all(values == values[0]))


def scaled_deviations(values: npt.ArrayLike) -> tuple[int, float, FloatArray, int]:
    """(T, mean, u, e) with deviations d = u * 2^e and max |u| in [0.5, 1).

    Scaling by a power of two is exact, so every ratio built from u (Sharpe
    up to the factor 2^-e, skewness, kurtosis, autocorrelations) equals the
    one built from d, but squares and fourth powers can no longer underflow.
    Not computable for a constant series (N34).
    """
    r = as_returns(values)
    t, centre, d = _centred(r)
    if _constant(r):
        raise NotComputable("the standard deviation is zero, so there is no Sharpe")
    exponent = math.frexp(float(np.max(np.abs(d))))[1]
    return t, centre, np.ldexp(d, -exponent), exponent


def mean(values: npt.ArrayLike) -> float:
    """The arithmetic mean."""
    return _centred(values)[1]


def std(values: npt.ArrayLike) -> float:
    """Standard deviation with divisor T-1 (spec, "Estimators")."""
    r = as_returns(values)
    if len(r) < 2:
        raise NotComputable("a standard deviation needs at least two periods")
    if _constant(r):
        return 0.0
    t, _, u, exponent = scaled_deviations(r)
    return math.ldexp(math.sqrt(math.fsum((u * u).tolist()) / (t - 1)), exponent)


def sharpe(values: npt.ArrayLike) -> float:
    """Per-period Sharpe ratio: mean / s, s with divisor T-1 (spec, decision D).

    [DECISION] N34 Not computable when s = 0 or T < 2: such a series has no
    Sharpe ratio. The caller lists it, or fails G6 if it is the selection.
    """
    r = as_returns(values)
    if len(r) < 2:
        raise NotComputable("a Sharpe ratio needs at least two periods")
    t, centre, u, exponent = scaled_deviations(r)
    s_scaled = math.sqrt(math.fsum((u * u).tolist()) / (t - 1))
    result = math.ldexp(centre, -exponent) / s_scaled
    if not math.isfinite(result):
        raise NotComputable("the Sharpe ratio is too large to represent")
    return result


def skewness(values: npt.ArrayLike) -> float:
    """Population skewness m3 / m2^(3/2), central moments with divisor T.

    Computed on the scaled deviations u (the ratio is scale-free), and
    m2^(3/2) as m2 * sqrt(m2): both correctly rounded, unlike pow().
    """
    t, _, u, _ = scaled_deviations(values)
    m2 = math.fsum((u * u).tolist()) / t
    m3 = math.fsum((u * u * u).tolist()) / t
    return m3 / (m2 * math.sqrt(m2))


def kurtosis(values: npt.ArrayLike) -> float:
    """Population kurtosis m4 / m2^2, divisor T, not excess (normal gives 3)."""
    t, _, u, _ = scaled_deviations(values)
    squares = u * u
    m2 = math.fsum(squares.tolist()) / t
    m4 = math.fsum((squares * squares).tolist()) / t
    return m4 / (m2 * m2)


@dataclasses.dataclass(frozen=True)
class Moments:
    """The estimators for one series, as PSR, DSR and MinTRL use them."""

    t: int
    mean: float
    std: float
    sharpe: float
    skewness: float
    kurtosis: float


def describe(values: npt.ArrayLike) -> Moments:
    """Every estimator for one series. Not computable when s = 0 or T < 2."""
    r = as_returns(values)
    return Moments(
        t=len(r),
        mean=mean(r),
        std=std(r),
        sharpe=sharpe(r),
        skewness=skewness(r),
        kurtosis=kurtosis(r),
    )


def trial_sharpes(
    series: Mapping[Key, npt.ArrayLike],
) -> tuple[dict[Key, float], dict[Key, str]]:
    """(Sharpe ratio by variant, reason by variant for those without one).

    N34: a series with s = 0 has no Sharpe ratio; it leaves V-hat and PBO and
    is listed. [DECISION] N34 A series too short for a standard deviation
    (T < 2) is treated the same way: it has no Sharpe ratio, so it is listed.
    """
    computed: dict[Key, float] = {}
    missing: dict[Key, str] = {}
    for key, values in series.items():
        try:
            computed[key] = sharpe(values)
        except NotComputable as exc:
            missing[key] = str(exc)
    return computed, missing

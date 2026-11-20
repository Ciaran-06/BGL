"""Stationary bootstrap band for a live Sharpe ratio (stage 4, reported only).

Sources:

- The stationary bootstrap: Politis and Romano (1994), "The stationary
  bootstrap", JASA 89(428). Blocks start at a uniform random period, have
  geometric lengths with mean 1/p, and wrap around the end of the series.
- The mean block length: Politis and White (2004), "Automatic block-length
  selection for the dependent bootstrap", Econometric Reviews 23(1), eqs. (6)
  to (9), as corrected by Patton, Politis and White (2009), Econometric
  Reviews 28(4), items 1 and 4: b = (2 G^2 / D_SB)^(1/3) N^(1/3), with
  D_SB = 2 g(0)^2.

N41: the seed is the first 8 bytes of the lock row's hash; uniform draws come
from PCG64's raw output; quantiles use linear interpolation; no outside
package.
"""

import dataclasses
import math
from collections.abc import Sequence
from typing import Final

import numpy as np
import numpy.typing as npt

from bgl.canonical import HASH_HEX
from bgl.stats import NotComputable
from bgl.stats.lo import autocorrelations
from bgl.stats.moments import as_returns, scaled_deviations

REPLICATIONS: Final[int] = 10_000  # spec, "Bootstrap band"
LEVEL: Final[float] = 0.95  # spec: a two-sided 95% band
_CHUNK: Final[int] = 500  # replications drawn together; bounds memory only
_UNIT: Final[float] = 2.0**-53

# [DECISION] N41 Politis and White's (2004) constants, from memory and not yet
# checked against the paper (docs/PLAN.md, section 6, item 4): c = 2,
# K_N = max(5, ceil(sqrt(log10 N))), at most ceil(sqrt(N)) + K_N lags,
# M = 2 m-hat, and b capped at ceil(min(3 sqrt(N), N / 3)) as in Patton's
# reference code. The formula itself is checked against the 2009 Table 1;
# lag_window's choice of M is pinned by tests on given autocorrelations.
_C: Final[float] = 2.0


def _flat_top(s: float) -> float:
    """Politis and White's flat-top (trapezoid) lag window lambda(s)."""
    s = abs(s)
    if s <= 0.5:
        return 1.0
    if s <= 1.0:
        return 2.0 * (1.0 - s)
    return 0.0


def sb_block_length(autocovariances: Sequence[float], n: int) -> float:
    """b = (2 G^2 / D_SB)^(1/3) N^(1/3) from R(0) .. R(M), before any cap.

    G = sum_{|k|<=M} lambda(k/M) |k| R(k), g(0) = sum_{|k|<=M} lambda(k/M) R(k),
    D_SB = 2 g(0)^2 (Politis and White 2004, eqs. (6) to (9); Patton,
    Politis and White 2009, items 1 and 4).
    """
    big_m = len(autocovariances) - 1
    if big_m < 1:
        raise ValueError("the block length needs R(0) and at least R(1)")
    acov = [float(value) for value in autocovariances]
    weights = [_flat_top(k / big_m) for k in range(big_m + 1)]
    g_big = 2.0 * math.fsum(weights[k] * k * acov[k] for k in range(1, big_m + 1))
    g_zero = acov[0] + 2.0 * math.fsum(
        weights[k] * acov[k] for k in range(1, big_m + 1)
    )
    d_sb = 2.0 * g_zero * g_zero
    if not d_sb > 0:
        raise NotComputable("the estimated spectral density at zero is zero")
    return float((2.0 * g_big * g_big / d_sb) ** (1.0 / 3.0) * n ** (1.0 / 3.0))


@dataclasses.dataclass(frozen=True)
class BlockLength:
    """The chosen mean block length and how it was reached."""

    value: float
    m_hat: int
    lag_window: int  # M
    capped: bool


def lag_count(n: int) -> tuple[int, int]:
    """(K_N, m_max): the run length and the most lags examined, for N periods."""
    k_n = max(5, math.ceil(math.sqrt(math.log10(n))))
    return k_n, math.ceil(math.sqrt(n)) + k_n


def lag_window(rho: Sequence[float], n: int) -> tuple[int, int]:
    """(m-hat, M) from rho_1 .. rho_{m_max} (rho[0] is rho_1) and N.

    m-hat is the smallest positive integer m such that |rho_{m+k}| <
    c sqrt(log10 N / N) for k = 1 .. K_N (Politis and White 2004, section 3.2
    and its footnote: "smallest positive integer"), so M = 2 m-hat >= 2.

    [DECISION] N41 When no run of K_N small autocorrelations is found, m-hat is
    the last significant lag (Patton's reference code). M is at most m_max.
    """
    k_n, m_max = lag_count(n)
    if len(rho) < m_max:
        raise ValueError(f"the lag window needs {m_max} autocorrelations")
    limit = _C * math.sqrt(math.log10(n) / n)
    small = [abs(value) < limit for value in rho[:m_max]]
    m_hat: int | None = None
    for m in range(1, m_max - k_n + 1):
        if all(small[m : m + k_n]):
            m_hat = m
            break
    if m_hat is None:
        m_hat = max(k + 1 for k, ok in enumerate(small) if not ok)
    return m_hat, min(2 * m_hat, m_max)


def block_length(values: npt.ArrayLike) -> BlockLength:
    """Automatic mean block length for the stationary bootstrap.

    M comes from lag_window; R(k) uses divisor N (Politis and White 2004).

    [DECISION] N41 b is kept at 1 or more, since a block holds at least one
    period; with little autocorrelation the formula gives b near 1 (the
    ordinary bootstrap) or below.
    """
    r = as_returns(values)
    n = len(r)
    if n < 3:
        raise NotComputable("the block length needs at least three periods")
    _, m_max = lag_count(n)
    if m_max >= n:
        raise NotComputable(
            f"{n} periods are too few for the block-length rule's {m_max} lags"
        )
    b_max = math.ceil(min(3.0 * math.sqrt(n), n / 3.0))
    m_hat, big_m = lag_window(autocorrelations(r, m_max), n)
    # Autocovariances of the deviations scaled by an exact power of two: b
    # depends only on G / g(0), which the scale leaves unchanged.
    _, _, d, _ = scaled_deviations(r)
    acov = [math.fsum((d * d).tolist()) / n] + [
        math.fsum((d[k:] * d[:-k]).tolist()) / n for k in range(1, big_m + 1)
    ]
    b = sb_block_length(acov, n)
    if b > b_max:
        return BlockLength(float(b_max), m_hat, big_m, True)
    return BlockLength(max(b, 1.0), m_hat, big_m, False)


def seed_from_lock_hash(lock_hash: str) -> int:
    """N41: the first 8 bytes of the lock row's hash, as an unsigned integer.

    [DECISION] N41 The bytes are read big-endian, the order they appear in the
    hex text, so the seed is the integer value of the first 16 hex digits.
    """
    if not HASH_HEX.fullmatch(lock_hash):
        raise ValueError("the lock hash must be 64 lowercase hex digits")
    return int.from_bytes(bytes.fromhex(lock_hash)[:8], "big")


def _uniforms(generator: np.random.PCG64, count: int) -> npt.NDArray[np.float64]:
    """count uniforms in [0, 1) from PCG64's raw 64-bit output (N41).

    The top 53 bits of each draw, times 2^-53. numpy's Generator methods are
    not used, since their algorithms may change between numpy versions.
    """
    raw = np.asarray(generator.random_raw(count), dtype=np.uint64)
    uniforms: npt.NDArray[np.float64] = (raw >> np.uint64(11)).astype(
        np.float64
    ) * _UNIT
    return uniforms


def resample_indices(
    generator: np.random.PCG64, *, t: int, length: int, block: float, count: int
) -> npt.NDArray[np.intp]:
    """count stationary-bootstrap index paths of the given length over 0 .. t-1.

    Politis and Romano (1994): each period starts a new block with probability
    p = 1 / block, at a uniform random period; otherwise it takes the period
    after the last one, wrapping from t-1 to 0. The first period always starts
    a block.

    [DECISION] N41 Each period consumes exactly two draws, in order: the
    new-block draw (u < p starts a block; ignored for the first period) and
    the start draw (floor(u t)). Fixed consumption keeps every replication's
    position in the stream independent of the others.
    """
    if t < 1 or length < 1 or count < 0:
        raise ValueError("t and length must be at least 1")
    if not math.isfinite(block) or block < 1.0:
        raise ValueError(f"the mean block length must be at least 1, got {block}")
    probability = 1.0 / block
    draws = _uniforms(generator, count * length * 2).reshape(count, length, 2)
    new = draws[:, :, 0] < probability
    new[:, 0] = True
    starts = np.minimum(np.floor(draws[:, :, 1] * t).astype(np.intp), t - 1)
    steps = np.arange(length, dtype=np.intp)
    last_new = np.maximum.accumulate(np.where(new, steps, 0), axis=1)
    first = np.take_along_axis(starts, last_new, axis=1)
    indices: npt.NDArray[np.intp] = (first + (steps - last_new)) % t
    return indices


def linear_quantile(values: npt.ArrayLike, q: float) -> float:
    """Quantile by linear interpolation between order statistics (N41).

    h = (n - 1) q; the result is x_(floor h) + (h - floor h)(x_(floor h + 1) -
    x_(floor h)), counting from 0 in sorted order (Hyndman and Fan's type 7).
    """
    ordered = np.sort(np.asarray(values, dtype=np.float64))
    n = len(ordered)
    if n == 0 or not 0.0 <= q <= 1.0:
        raise ValueError("a quantile needs values and 0 <= q <= 1")
    h = (n - 1) * q
    low = math.floor(h)
    if low >= n - 1:
        return float(ordered[-1])
    return float(ordered[low] + (h - low) * (ordered[low + 1] - ordered[low]))


@dataclasses.dataclass(frozen=True)
class Band:
    """A two-sided band for a live per-period Sharpe ratio."""

    low: float
    high: float
    level: float
    length: int  # live periods each replication holds
    block: float  # mean block length used
    replications: int
    seed: int


def band(
    values: npt.ArrayLike,
    *,
    length: int,
    block: float,
    replications: int,
    level: float,
    seed: int,
) -> Band:
    """Band for the Sharpe ratio of `length` periods resampled from the design
    series by the stationary bootstrap (spec, "Bootstrap band").

    Every argument is required: the spec's values are REPLICATIONS and LEVEL,
    the seed comes from seed_from_lock_hash and the block from block_length.

    [DECISION] A replication whose resampled series is constant has no Sharpe
    ratio; rather than drop it silently, the band is then not computable.
    """
    r = as_returns(values)
    if length < 2:
        raise NotComputable("a live Sharpe ratio needs at least two periods")
    if replications < 1 or not 0.0 < level < 1.0:
        raise ValueError("replications must be at least 1 and 0 < level < 1")
    generator = np.random.PCG64(seed)
    sharpes: list[npt.NDArray[np.float64]] = []
    for start in range(0, replications, _CHUNK):
        count = min(_CHUNK, replications - start)
        index = resample_indices(
            generator, t=len(r), length=length, block=block, count=count
        )
        sample = r[index]
        if np.any(np.all(sample == sample[:, :1], axis=1)):
            raise NotComputable("a bootstrap replication was constant")
        spread = sample.std(axis=1, ddof=1)
        if np.any(spread == 0):
            raise NotComputable("a bootstrap replication's deviations underflowed")
        sharpes.append(sample.mean(axis=1) / spread)
    every = np.concatenate(sharpes)
    tail = (1.0 - level) / 2.0
    return Band(
        low=linear_quantile(every, tail),
        high=linear_quantile(every, 1.0 - tail),
        level=level,
        length=length,
        block=block,
        replications=replications,
        seed=seed,
    )

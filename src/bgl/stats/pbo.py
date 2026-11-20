"""Probability of backtest overfitting by combinatorially symmetric cross-validation.

Source: Bailey, Borwein, López de Prado and Zhu (2017), "The probability of
backtest overfitting", Journal of Computational Finance 20(4):

- the CSCV procedure: Algorithm 2.3, steps (a) to (g);
- the relative rank omega = rank / (N + 1): step (f); the logit
  lambda = ln(omega / (1 - omega)): step (g);
- PBO as the share of logits at or below zero: eq. (2.2) and section 3.1
  (BGL counts
  lambda = 0 as overfit, which is stricter than the paper's integral; see
  docs/PLAN.md, section 6, item 7);
- performance degradation and probability of loss: section 3.2.

The performance measure is the per-period Sharpe ratio (spec, "PBO").
"""

import dataclasses
import itertools
import math
from collections.abc import Hashable, Mapping, Sequence
from fractions import Fraction
from typing import Final, Generic, TypeVar

import numpy as np
import numpy.typing as npt

from bgl.stats import NotComputable

Key = TypeVar("Key", bound=Hashable)

MIN_BLOCKS: Final[int] = 4
MAX_BLOCKS: Final[int] = 20  # N37: C10 caps cscv_blocks at 20
_CHUNK: Final[int] = 512  # splits computed together; bounds memory, not results


def _check_blocks(blocks: int, what: str) -> None:
    if (
        isinstance(blocks, bool)
        or not isinstance(blocks, int)
        or blocks % 2
        or not MIN_BLOCKS <= blocks <= MAX_BLOCKS
    ):
        raise ValueError(
            f"{what} must be an even whole number from {MIN_BLOCKS} to "
            f"{MAX_BLOCKS}, got {blocks!r}"
        )


def choose_blocks(t: int, *, cscv_blocks: int, min_block_periods: int) -> int:
    """S: the number of CSCV blocks for T rows (spec, "Blocks").

    [DECISION] cscv_blocks is the most blocks used: S is the largest even S
    with 4 <= S <= cscv_blocks for which every block holds at least
    min_block_periods rows, a block holding floor(T / S) rows after N35 drops
    the remainder. Not computable when no such S exists (spec: stage 2 fails).
    """
    _check_blocks(cscv_blocks, "cscv_blocks")
    if isinstance(min_block_periods, bool) or min_block_periods < 1:
        raise ValueError(
            f"min_block_periods must be at least 1, got {min_block_periods}"
        )
    for blocks in range(cscv_blocks, MIN_BLOCKS - 1, -2):
        if t // blocks >= min_block_periods:
            return blocks
    raise NotComputable(
        f"{t} periods cannot make {MIN_BLOCKS} blocks of at least "
        f"{min_block_periods} periods each"
    )


@dataclasses.dataclass(frozen=True)
class CommonMatrix(Generic[Key]):
    """The PBO matrix: rows are the selected variant's periods, oldest first."""

    periods: tuple[str, ...]
    variants: tuple[Key, ...]  # one per column, in column order
    matrix: npt.NDArray[np.float64]
    excluded: Mapping[Key, str]  # variant -> why it is not in the matrix


def common_matrix(
    periods: Sequence[str], series: Mapping[Key, Mapping[str, float]]
) -> CommonMatrix[Key]:
    """Line the variants up on the selected variant's periods (N36).

    series maps each recorded design variant to its net excess returns by
    period. [DECISION] N36 The common range is the selected variant's periods;
    a variant missing any of them is excluded and listed (it stays in N, which
    is counted elsewhere). Periods a variant has outside the range are not
    used.

    [DECISION] N34 A variant whose returns are constant over the common
    periods has no Sharpe ratio there, so it leaves the matrix and is listed,
    as N34 does for a series with s = 0.
    """
    if not periods:
        raise NotComputable("the selected variant has no periods")
    if len(set(periods)) != len(periods):
        raise ValueError("periods repeat")
    columns: list[list[float]] = []
    included: list[Key] = []
    excluded: dict[Key, str] = {}
    for key, values in series.items():
        missing = [period for period in periods if period not in values]
        if missing:
            excluded[key] = (
                f"missing {len(missing)} of the selected variant's periods, "
                f"first {missing[0]}"
            )
            continue
        column = [float(values[period]) for period in periods]
        if not all(math.isfinite(value) for value in column):
            raise NotComputable(f"variant {key!r} holds nan or inf")
        if all(value == column[0] for value in column):
            excluded[key] = "constant over the common periods, so no Sharpe ratio"
            continue
        columns.append(column)
        included.append(key)
    matrix = np.array(columns, dtype=np.float64).T.reshape(len(periods), len(columns))
    return CommonMatrix(tuple(periods), tuple(included), matrix, excluded)


@dataclasses.dataclass(frozen=True)
class Pbo:
    """PBO and its companion measures, from one CSCV pass."""

    blocks: int  # S
    block_length: int  # rows per block
    dropped: int  # oldest rows dropped, T mod S (N35)
    trials: int  # N, the columns of the matrix
    splits: int  # C(S, S/2)
    overfit: int  # splits with lambda <= 0
    logits: tuple[float, ...]  # lambda for each split, in split order
    selected_is: tuple[float, ...]  # in-sample Sharpe of each split's pick
    selected_oos: tuple[float, ...]  # its out-of-sample Sharpe
    degradation_slope: float | None  # None when every pick's IS Sharpe is equal
    degradation_intercept: float | None
    probability_of_loss: Fraction

    @property
    def pbo(self) -> Fraction:
        """Share of splits with lambda <= 0, exactly."""
        return Fraction(self.overfit, self.splits)


def _column_shift(kept: npt.NDArray[np.float64]) -> npt.NDArray[np.float64]:
    """Each column's median element: an actual value, so for a nearly constant
    column x - shift is exact (Sterbenz: x and shift within a factor of 2) and
    the large common level never enters a subtraction of small differences.
    """
    shift: npt.NDArray[np.float64] = np.sort(kept, axis=0)[len(kept) // 2]
    return shift


def _block_stats(
    kept: npt.NDArray[np.float64],
    shift: npt.NDArray[np.float64],
    blocks: int,
    length: int,
) -> tuple[npt.NDArray[np.float64], ...]:
    """Per block and column: the mean of the shifted values, the within-block
    sum of squared deviations (two-pass, so no cancellation), and the minimum
    and maximum of the values themselves (to spot constant halves).

    Sums use math.fsum (exactly rounded), so the block figures, and the ranks
    built from them, do not depend on the platform's summation order.
    """
    k = kept.shape[1]
    y = kept - shift
    means = np.empty((blocks, k))
    within = np.empty((blocks, k))
    for b in range(blocks):
        part = y[b * length : (b + 1) * length]
        for j in range(k):
            column = part[:, j]
            centre = math.fsum(column.tolist()) / length
            deviations = column - centre
            means[b, j] = centre
            within[b, j] = math.fsum((deviations * deviations).tolist())
    shaped = kept.reshape(blocks, length, k)
    return means, within, shaped.min(axis=1), shaped.max(axis=1)


def _split_sharpes(
    combos: npt.NDArray[np.intp],
    stats: tuple[npt.NDArray[np.float64], ...],
    shift: npt.NDArray[np.float64],
    length: int,
) -> npt.NDArray[np.float64]:
    """Sharpe ratio of every column over each split's blocks, from block figures.

    The blocks are equal in length L, so over h blocks the mean is the mean of
    the block means, and the sum of squared deviations is
    sum_b W_b + L sum_b (m_b - mean)^2 (the pooled, or parallel, variance
    formula). Every term is non-negative, so nothing cancels, unlike
    sum x^2 - (sum x)^2 / n, which loses a nearly flat column to rounding.
    """
    means, within, low, high = stats
    h = combos.shape[1]
    rows = h * length
    total = means[combos[:, 0]]
    pooled = within[combos[:, 0]]
    lowest = low[combos[:, 0]]
    highest = high[combos[:, 0]]
    # Fixed left-to-right adds (not a reduction), so the order never varies.
    for position in range(1, h):
        total = total + means[combos[:, position]]
        pooled = pooled + within[combos[:, position]]
        lowest = np.minimum(lowest, low[combos[:, position]])
        highest = np.maximum(highest, high[combos[:, position]])
    # [DECISION] One variant constant over any S/2 blocks makes the whole PBO
    # not computable (the spec is silent). It fails closed: such a variant has
    # no Sharpe ratio in that half, so no rank exists for the split. The
    # alternative, excluding such variants and listing them, is a question
    # for Ciaran.
    constant = lowest == highest
    if np.any(constant):
        split, column = (int(i) for i in np.argwhere(constant)[0])
        raise NotComputable(
            f"column {column} is constant over the blocks "
            f"{combos[split].tolist()}, so it has no Sharpe ratio there"
        )
    grand = total / h
    between = np.zeros_like(grand)
    for position in range(h):
        gap = means[combos[:, position]] - grand
        between = between + gap * gap
    variance = (pooled + length * between) / (rows - 1)
    if np.any(variance <= 0):
        raise NotComputable("a split's variance underflowed to zero")
    result: npt.NDArray[np.float64] = (shift + grand) / np.sqrt(variance)
    return result


def _ranks_of(
    values: npt.NDArray[np.float64], picks: npt.NDArray[np.intp]
) -> list[int]:
    """Rank of each picked value: 1 + the number strictly below it.

    [DECISION] N35 Rank 1 is the worst out-of-sample Sharpe, and tied values
    share the lowest rank (the conservative reading for PBO).
    """
    return [1 + int(np.count_nonzero(values < values[pick])) for pick in picks]


def pbo(matrix: npt.ArrayLike, *, blocks: int, min_trials: int) -> Pbo:
    """PBO of a T x N matrix of per-period returns, oldest row first.

    blocks is S from choose_blocks. min_trials is the gate's pbo_min_trials:
    fewer columns than that is not computable (spec, "Not computable").
    """
    _check_blocks(blocks, "blocks")
    m = np.asarray(matrix, dtype=np.float64)
    if m.ndim != 2:
        raise ValueError(f"the PBO matrix must be two-dimensional, got {m.shape}")
    if not np.all(np.isfinite(m)):
        raise NotComputable("the PBO matrix holds nan or inf")
    t, k = m.shape
    # [DECISION] At least two columns are needed whatever pbo_min_trials says:
    # one variant always ranks (N+1)/2, which says nothing.
    if k < max(min_trials, 2):
        raise NotComputable(f"{k} variants, fewer than the {max(min_trials, 2)} needed")
    length = t // blocks
    if length < 1:
        raise NotComputable(f"{t} periods cannot fill {blocks} blocks")
    dropped = t % blocks
    # [DECISION] N35 Drop the oldest T mod S rows so every block has the same
    # length; the newest data, nearest the decision, is kept.
    kept = m[dropped:]
    shift = _column_shift(kept)
    stats = _block_stats(kept, shift, blocks, length)
    every = np.arange(blocks)
    combos = np.array(
        list(itertools.combinations(range(blocks), blocks // 2)), dtype=np.intp
    )
    logits: list[float] = []
    picks_is: list[float] = []
    picks_oos: list[float] = []
    overfit = 0
    for start in range(0, len(combos), _CHUNK):
        chunk = combos[start : start + _CHUNK]
        others = np.array([np.setdiff1d(every, row) for row in chunk], dtype=np.intp)
        in_sample = _split_sharpes(chunk, stats, shift, length)
        out_sample = _split_sharpes(others, stats, shift, length)
        for is_row, oos_row in zip(in_sample, out_sample, strict=True):
            best = np.flatnonzero(is_row == is_row.max())
            ranks = _ranks_of(oos_row, best)
            # [DECISION] N35 A tie for in-sample best takes the tied variant
            # with the lowest out-of-sample rank (the conservative pick).
            position = min(range(len(best)), key=lambda i: ranks[i])
            rank, pick = ranks[position], int(best[position])
            # Algorithm 2.3 step (f): omega = rank / (N + 1); step (g): the
            # logit lambda = ln(omega / (1 - omega)).
            omega = Fraction(rank, k + 1)
            logits.append(math.log(omega / (1 - omega)))
            # lambda <= 0 exactly when omega <= 1/2; compared in exact fractions.
            # [DECISION] lambda = 0 counts as overfit, as the spec's "lambda <= 0"
            # says. That is stricter than the paper's integral of the logit
            # density below zero (docs/PLAN.md, section 6, item 7).
            if omega <= Fraction(1, 2):
                overfit += 1
            picks_is.append(float(is_row[pick]))
            picks_oos.append(float(oos_row[pick]))
    slope, intercept = _degradation(picks_is, picks_oos)
    # [DECISION] A loss is an out-of-sample Sharpe strictly below zero; a
    # Sharpe of exactly zero is no loss (section 3.2: "a loss").
    losses = sum(1 for value in picks_oos if value < 0)
    return Pbo(
        blocks=blocks,
        block_length=length,
        dropped=dropped,
        trials=k,
        splits=len(combos),
        overfit=overfit,
        logits=tuple(logits),
        selected_is=tuple(picks_is),
        selected_oos=tuple(picks_oos),
        degradation_slope=slope,
        degradation_intercept=intercept,
        probability_of_loss=Fraction(losses, len(combos)),
    )


def _degradation(x: list[float], y: list[float]) -> tuple[float | None, float | None]:
    """Least-squares line of out-of-sample on in-sample Sharpe (section 3.2).

    [DECISION] When every pick has the same in-sample Sharpe the slope is
    undefined, and both figures are reported as None.
    """
    count = len(x)
    x_bar = math.fsum(x) / count
    y_bar = math.fsum(y) / count
    sxx = math.fsum((value - x_bar) * (value - x_bar) for value in x)
    if all(value == x[0] for value in x) or sxx == 0:
        return None, None
    sxy = math.fsum((a - x_bar) * (b - y_bar) for a, b in zip(x, y, strict=True))
    slope = sxy / sxx
    return slope, y_bar - slope * x_bar

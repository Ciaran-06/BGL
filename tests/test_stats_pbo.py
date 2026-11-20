"""PBO by CSCV: a hand-worked S = 4 case, a loop-only twin, and the N35-N37 edges."""

import itertools
import math
import statistics
from fractions import Fraction

import numpy as np
import pytest

from bgl.stats import NotComputable
from bgl.stats.pbo import choose_blocks, common_matrix, pbo

# The hand case: T = 4 rows (oldest first), N = 3 variants A, B, C, S = 4
# blocks of one row each, so 6 splits of 2 in-sample rows and 2 out.
#
#        A    B    C
#   t1   5    2    3
#   t2   2    1    4
#   t3   3   -2    1
#   t4   1    4    2
#
# Over two values x, y the Sharpe ratio is ((x+y)/2) / (|x-y|/sqrt 2)
# = q / sqrt 2 with q = (x+y)/|x-y|. sqrt 2 is common, so ranks use q.
#
# q for each pair of rows:
#   rows    A      B      C
#   1,2    7/3    3      7
#   1,3    4      0      2
#   1,4    3/2    3      5
#   2,3    5     -1/3    5/3
#   2,4    3      5/3    3
#   3,4    2      1/3    3
#
# Split (in-sample rows | out-of-sample rows), in itertools order:
#   1. IS 1,2: A 7/3, B 3, C 7 -> pick C.  OOS 3,4: A 2, B 1/3, C 3.
#      C is best of 3: rank 3, omega 3/4, lambda ln 3 > 0.         not overfit
#   2. IS 1,3: A 4, B 0, C 2 -> pick A.    OOS 2,4: A 3, B 5/3, C 3.
#      A ties C at 3; ties take the lower rank: rank 2, omega 1/2,
#      lambda 0, counted (lambda <= 0).                            overfit
#   3. IS 1,4: A 3/2, B 3, C 5 -> pick C.  OOS 2,3: A 5, B -1/3, C 5/3.
#      C rank 2, omega 1/2, lambda 0.                              overfit
#   4. IS 2,3: A 5, B -1/3, C 5/3 -> A.    OOS 1,4: A 3/2, B 3, C 5.
#      A rank 1, omega 1/4, lambda -ln 3.                          overfit
#   5. IS 2,4: A 3, B 5/3, C 3: A and C tie for best.
#      OOS 1,3: A 4, B 0, C 2 -> ranks A 3, B 1, C 2. The tie takes
#      the lower OOS rank: C, rank 2, omega 1/2, lambda 0.         overfit
#   6. IS 3,4: A 2, B 1/3, C 3 -> pick C.  OOS 1,2: A 7/3, B 3, C 7.
#      C rank 3, lambda ln 3.                                      not overfit
#
# PBO = 4/6. Picks' (IS q, OOS q): (7,3) (4,3) (5,5/3) (5,3/2) (3,2) (3,7).
HAND = [[5, 2, 3], [2, 1, 4], [3, -2, 1], [1, 4, 2]]
HAND_IS_Q = [Fraction(7), Fraction(4), Fraction(5), Fraction(5), Fraction(3), 3]
HAND_OOS_Q = [Fraction(3), 3, Fraction(5, 3), Fraction(3, 2), Fraction(2), 7]
LN3 = math.log(3)


class TestHandCase:
    def test_pbo(self) -> None:
        result = pbo(HAND, blocks=4, min_trials=2)
        assert result.splits == 6
        assert result.overfit == 4
        assert result.pbo == Fraction(2, 3)
        assert result.dropped == 0
        assert result.block_length == 1
        assert result.trials == 3

    def test_logits(self) -> None:
        result = pbo(HAND, blocks=4, min_trials=2)
        assert result.logits == pytest.approx([LN3, 0, 0, -LN3, 0, LN3], abs=1e-15)

    def test_picks(self) -> None:
        result = pbo(HAND, blocks=4, min_trials=2)
        root2 = math.sqrt(2)
        assert result.selected_is == pytest.approx(
            [float(q) / root2 for q in HAND_IS_Q], rel=1e-14
        )
        assert result.selected_oos == pytest.approx(
            [float(q) / root2 for q in HAND_OOS_Q], rel=1e-14
        )

    def test_degradation_and_loss(self) -> None:
        # Least squares of OOS q on IS q, in exact fractions:
        #   x-bar = 27/6, y-bar = 109/36, Sxx = 23/2, Sxy = -71/12
        #   slope = -71/138; the intercept in Sharpe units is (y-bar - slope
        #   x-bar) / sqrt 2. No pick loses out of sample, so P(loss) = 0.
        x = [Fraction(v) for v in HAND_IS_Q]
        y = [Fraction(v) for v in HAND_OOS_Q]
        x_bar, y_bar = sum(x) / 6, sum(y) / 6
        sxx = sum((a - x_bar) ** 2 for a in x)
        sxy = sum((a - x_bar) * (b - y_bar) for a, b in zip(x, y, strict=True))
        assert (x_bar, y_bar, sxx, sxy) == (
            Fraction(27, 6),
            Fraction(109, 36),
            Fraction(23, 2),
            Fraction(-71, 12),
        )
        slope = sxy / sxx
        result = pbo(HAND, blocks=4, min_trials=2)
        assert result.degradation_slope == pytest.approx(float(slope), rel=1e-12)
        intercept = float(y_bar - slope * x_bar) / math.sqrt(2)
        assert result.degradation_intercept == pytest.approx(intercept, rel=1e-12)
        assert result.probability_of_loss == 0

    def test_remainder_rows_dropped_from_the_oldest(self) -> None:
        # N35: T = 5, S = 4 drops T mod S = 1 row, the oldest.
        extra = [[100, -100, 50]]
        result = pbo(extra + HAND, blocks=4, min_trials=2)
        assert result.dropped == 1
        assert result.overfit == 4
        assert result.logits == pbo(HAND, blocks=4, min_trials=2).logits
        # Dropping the newest row instead would change the answer.
        assert pbo((extra + HAND)[:4], blocks=4, min_trials=2).overfit != 4

    def test_column_order(self) -> None:
        base = pbo(HAND, blocks=4, min_trials=2)
        for order in itertools.permutations(range(3)):
            matrix = [[row[i] for i in order] for row in HAND]
            again = pbo(matrix, blocks=4, min_trials=2)
            assert again.overfit == base.overfit
            assert again.logits == base.logits
            assert again.selected_oos == base.selected_oos


def loop_pbo(matrix: list[list[float]], blocks: int) -> tuple[int, list[float], int]:
    """A second PBO written with plain loops: (overfit, logits, losses).

    Follows Bailey et al. (2017) Algorithm 2.3 directly: drop the oldest
    T mod S rows, cut S blocks, and for every choice of S/2 in-sample blocks
    compute each variant's Sharpe ratio from the concatenated rows.
    """
    t, k = len(matrix), len(matrix[0])
    rows = matrix[t % blocks :]
    size = len(rows) // blocks
    cut = [rows[b * size : (b + 1) * size] for b in range(blocks)]

    def sharpe_of(chosen: tuple[int, ...], v: int) -> float:
        values = [row[v] for b in chosen for row in cut[b]]
        return statistics.mean(values) / statistics.stdev(values)

    overfit, losses, logits = 0, 0, []
    LOOP_PICKS.clear()
    for chosen in itertools.combinations(range(blocks), blocks // 2):
        other = tuple(b for b in range(blocks) if b not in chosen)
        in_s = [sharpe_of(chosen, v) for v in range(k)]
        out_s = [sharpe_of(other, v) for v in range(k)]
        best = max(in_s)
        ranks = {}
        for v in range(k):
            if in_s[v] == best:
                below = 0
                for w in range(k):
                    if out_s[w] < out_s[v]:
                        below += 1
                ranks[v] = below + 1
        pick = min(ranks, key=lambda v: ranks[v])
        omega = ranks[pick] / (k + 1)
        logits.append(math.log(omega / (1 - omega)))
        if omega <= 0.5:
            overfit += 1
        if out_s[pick] < 0:
            losses += 1
        LOOP_PICKS.append((in_s[pick], out_s[pick]))
    return overfit, logits, losses


# The loop twin's picks (in-sample, out-of-sample Sharpe), from its last call.
LOOP_PICKS: list[tuple[float, float]] = []


class TestAgainstLoops:
    @pytest.mark.parametrize(
        ("seed", "t", "k", "blocks"),
        [(1, 48, 5, 4), (2, 61, 7, 6), (3, 100, 4, 8), (4, 37, 10, 4), (5, 90, 3, 10)],
    )
    def test_random_matrices(self, seed: int, t: int, k: int, blocks: int) -> None:
        rng = np.random.default_rng(seed)
        # A small common drift plus noise, so picks win and lose.
        matrix = (rng.normal(0.0, 1.0, (t, k)) + rng.normal(0.05, 0.1, k)).tolist()
        overfit, logits, losses = loop_pbo(matrix, blocks)
        result = pbo(matrix, blocks=blocks, min_trials=2)
        assert result.overfit == overfit
        assert result.logits == pytest.approx(logits, abs=1e-12)
        assert result.probability_of_loss == Fraction(
            losses, math.comb(blocks, blocks // 2)
        )
        assert result.splits == math.comb(blocks, blocks // 2)

    @pytest.mark.parametrize("seed", [29, 30, 31, 32])
    @pytest.mark.parametrize("blocks", [4, 6, 8])
    def test_nearly_flat_columns(self, seed: int, blocks: int) -> None:
        # Columns that are flat but for ticks of the series file's smallest unit
        # (1e-10), at level 0 and at level 0.01. sum x^2 - (sum x)^2 / n loses
        # these to cancellation; the review's repro (seed 29, S = 4) gave 3/6
        # where the exact answer is 5/6.
        rng = np.random.default_rng(seed)
        m = rng.normal(0.001, 0.01, (96, 5)).round(10)
        m[:48, 1] = 0.0
        m[rng.integers(0, 24), 1] = 1e-10
        m[24 + rng.integers(0, 24), 1] = 2e-10
        m[48:, 1] = rng.normal(0.004, 0.01, 48).round(10)
        m[:, 3] = 0.01
        for row in rng.choice(96, size=30, replace=False):
            m[row, 3] = 0.0100000001 if row % 2 else 0.0099999999
        try:
            overfit, logits, losses = loop_pbo(m.tolist(), blocks)
        except statistics.StatisticsError:
            pytest.skip("a half is constant; the twin cannot rank it")
        result = pbo(m, blocks=blocks, min_trials=2)
        assert result.overfit == overfit
        assert result.logits == pytest.approx(logits, abs=1e-12)
        assert result.probability_of_loss == Fraction(
            losses, math.comb(blocks, blocks // 2)
        )
        # The picks' Sharpe ratios agree with the exact (statistics-module)
        # twin to 12 digits, including the nearly flat column at level 0.01.
        assert result.selected_is == pytest.approx(
            [p[0] for p in LOOP_PICKS], rel=1e-12
        )
        assert result.selected_oos == pytest.approx(
            [p[1] for p in LOOP_PICKS], rel=1e-12
        )

    def test_review_repro(self) -> None:
        rng = np.random.default_rng(29)
        m = rng.normal(0.001, 0.01, (80, 4)).round(10)
        m[:40, 1] = 0.0
        m[rng.integers(0, 20), 1] = 1e-10
        m[20 + rng.integers(0, 20), 1] = 2e-10
        m[40:, 1] = rng.normal(0.004, 0.01, 40).round(10)
        assert loop_pbo(m.tolist(), 4)[0] == 5
        assert pbo(m, blocks=4, min_trials=2).overfit == 5

    def test_loss_at_exactly_zero_is_no_loss(self) -> None:
        # X = 1, 10, -1, 12; Y = 5, 1, 4, 2. Split IS rows 2,4: X has q = 22/2,
        # Y has q = 3, so X is picked; out of sample (rows 1,3) X is 1, -1:
        # Sharpe exactly 0, which is not a loss.
        matrix = [[1, 5], [10, 1], [-1, 4], [12, 2]]
        result = pbo(matrix, blocks=4, min_trials=2)
        assert 0.0 in result.selected_oos
        losses = sum(1 for value in result.selected_oos if value < 0)
        assert result.probability_of_loss == Fraction(losses, 6)
        assert loop_pbo(matrix, 4)[2] == losses

    def test_column_order_on_random(self) -> None:
        rng = np.random.default_rng(9)
        matrix = rng.normal(0.01, 1.0, (64, 8))
        base = pbo(matrix, blocks=8, min_trials=2)
        for _ in range(5):
            order = rng.permutation(8)
            again = pbo(matrix[:, order], blocks=8, min_trials=2)
            assert again.overfit == base.overfit
            assert again.logits == base.logits


class TestRefusals:
    def test_fewer_variants_than_min_trials(self) -> None:
        with pytest.raises(NotComputable, match="fewer"):
            pbo(HAND, blocks=4, min_trials=4)

    def test_exactly_min_trials(self) -> None:
        assert pbo(HAND, blocks=4, min_trials=3).trials == 3

    def test_one_variant(self) -> None:
        with pytest.raises(NotComputable):
            pbo([[1], [2], [3], [5]], blocks=4, min_trials=1)

    def test_too_few_rows(self) -> None:
        with pytest.raises(NotComputable):
            pbo(HAND[:3], blocks=4, min_trials=2)

    def test_constant_half(self) -> None:
        matrix = [[1, 2], [1, 3], [1, 1], [2, 5]]  # variant 1 is flat on rows 1-3
        with pytest.raises(NotComputable, match="constant"):
            pbo(matrix, blocks=4, min_trials=2)

    @pytest.mark.parametrize("blocks", [2, 5, 22, True])
    def test_bad_blocks(self, blocks: int) -> None:
        with pytest.raises(ValueError):
            pbo(HAND, blocks=blocks, min_trials=2)

    def test_nan(self) -> None:
        with pytest.raises(NotComputable):
            pbo([[1.0, math.nan]] * 4, blocks=4, min_trials=2)


class TestChooseBlocks:
    @pytest.mark.parametrize(
        ("t", "cscv", "minimum", "expected"),
        [
            pytest.param(320, 16, 20, 16, id="exactly_fits"),
            pytest.param(319, 16, 20, 14, id="one_short_steps_down"),
            pytest.param(80, 16, 20, 4, id="four_exactly"),
            pytest.param(1000, 4, 1, 4, id="never_above_cscv_blocks"),
            pytest.param(10_000, 20, 20, 20, id="n37_max"),
        ],
    )
    def test_choice(self, t: int, cscv: int, minimum: int, expected: int) -> None:
        assert choose_blocks(t, cscv_blocks=cscv, min_block_periods=minimum) == expected

    def test_none_fits(self) -> None:
        with pytest.raises(NotComputable):
            choose_blocks(79, cscv_blocks=16, min_block_periods=20)

    @pytest.mark.parametrize("cscv", [2, 3, 5, 22, True])
    def test_bad_cscv_blocks(self, cscv: int) -> None:
        # N37: at most 20; C10: at least 4 and even.
        with pytest.raises(ValueError):
            choose_blocks(1000, cscv_blocks=cscv, min_block_periods=1)

    def test_bad_min_block_periods(self) -> None:
        with pytest.raises(ValueError):
            choose_blocks(1000, cscv_blocks=4, min_block_periods=0)


class TestCommonMatrix:
    PERIODS = ["2020-01-02", "2020-01-03", "2020-01-06"]

    def test_n36_selected_periods_are_the_range(self) -> None:
        series = {
            1: {"2020-01-02": 0.1, "2020-01-03": 0.2, "2020-01-06": -0.1},
            # Extra periods outside the range are not used.
            2: {
                "2020-01-01": 9.0,
                "2020-01-02": 0.3,
                "2020-01-03": 0.1,
                "2020-01-06": 0.0,
            },
            # Missing one period: excluded and listed.
            3: {"2020-01-02": 0.3, "2020-01-06": 0.0},
        }
        result = common_matrix(self.PERIODS, series)
        assert result.variants == (1, 2)
        assert result.matrix.tolist() == [[0.1, 0.3], [0.2, 0.1], [-0.1, 0.0]]
        assert set(result.excluded) == {3}
        assert "2020-01-03" in result.excluded[3]

    def test_n34_constant_on_the_range_leaves(self) -> None:
        series = {
            1: {"2020-01-02": 0.1, "2020-01-03": 0.2, "2020-01-06": -0.1},
            2: {
                "2019-12-31": 0.5,
                "2020-01-02": 0.0,
                "2020-01-03": 0.0,
                "2020-01-06": 0.0,
            },
        }
        result = common_matrix(self.PERIODS, series)
        assert result.variants == (1,)
        assert "constant" in result.excluded[2]

    def test_nothing_left(self) -> None:
        result = common_matrix(self.PERIODS, {1: {"2020-01-02": 0.1}})
        assert result.variants == ()
        assert result.matrix.shape == (3, 0)
        with pytest.raises(NotComputable):
            pbo(result.matrix, blocks=4, min_trials=2)

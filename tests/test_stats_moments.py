"""Net excess returns and the estimators, checked against hand calculations."""

import math
from fractions import Fraction

import numpy as np
import pytest

from bgl.stats import NotComputable
from bgl.stats.moments import (
    describe,
    kurtosis,
    mean,
    net_excess,
    sharpe,
    skewness,
    std,
    trial_sharpes,
)

# The five-point series of docs/PLAN.md section 3, worked in exact fractions.
#   r = -2, -1, 0, 1, 7; T = 5; mean = 5/5 = 1
#   deviations d = -3, -2, -1, 0, 6
#   sum d^2 = 9 + 4 + 1 + 0 + 36 = 50
#   sum d^3 = -27 - 8 - 1 + 0 + 216 = 180
#   sum d^4 = 81 + 16 + 1 + 0 + 1296 = 1394
#   s^2 (divisor T-1) = 50/4 = 25/2, so SR = 1/sqrt(25/2) and SR^2 = 2/25
#   m2 = 50/5 = 10, m3 = 180/5 = 36, m4 = 1394/5
#   skew = m3 / m2^(3/2) = 36 / 10^(3/2), so skew^2 = 1296/1000 = 162/125
#   kurt = m4 / m2^2 = (1394/5)/100 = 697/250 = 2.788 (not excess)
FIVE = [-2.0, -1.0, 0.0, 1.0, 7.0]
SR_SQUARED = Fraction(2, 25)
SKEW_SQUARED = Fraction(162, 125)
KURT = Fraction(697, 250)


class TestFivePoint:
    def test_mean(self) -> None:
        assert mean(FIVE) == 1.0

    def test_std_divisor_t_minus_1(self) -> None:
        assert std(FIVE) ** 2 == pytest.approx(12.5, abs=1e-12)
        # With divisor T it would be 10.
        assert std(FIVE) ** 2 != pytest.approx(10.0)

    def test_sharpe(self) -> None:
        value = sharpe(FIVE)
        assert value > 0
        assert value**2 == pytest.approx(float(SR_SQUARED), abs=1e-15)
        # Divisor T would give SR^2 = 1/10.
        assert value**2 != pytest.approx(0.1)

    def test_skewness_divisor_t(self) -> None:
        value = skewness(FIVE)
        assert value > 0
        assert value**2 == pytest.approx(float(SKEW_SQUARED), abs=1e-14)
        # Divisor T-1 would give 45 / 12.5^(3/2), about 1.018.
        assert value != pytest.approx(45 / 12.5**1.5)

    def test_kurtosis_divisor_t_not_excess(self) -> None:
        value = kurtosis(FIVE)
        assert value == pytest.approx(float(KURT), abs=1e-14)
        assert value != pytest.approx(float(KURT) - 3)  # excess would be -0.212
        assert value != pytest.approx((1394 / 4) / 12.5**2)  # divisor T-1

    def test_describe(self) -> None:
        moments = describe(FIVE)
        assert moments.t == 5
        assert moments.sharpe == sharpe(FIVE)
        assert moments.kurtosis == kurtosis(FIVE)

    def test_scale_free(self) -> None:
        scaled = [value / 100 for value in FIVE]
        assert sharpe(scaled) == pytest.approx(sharpe(FIVE), rel=1e-12)
        assert kurtosis(scaled) == pytest.approx(kurtosis(FIVE), rel=1e-12)


class TestNoSharpe:
    """N34: a series with s = 0 has no Sharpe ratio."""

    def test_constant_series(self) -> None:
        with pytest.raises(NotComputable, match="zero"):
            sharpe([0.01, 0.01, 0.01])

    def test_constant_whose_float_mean_is_off(self) -> None:
        # 0.1 * 3 / 3 is not exactly 0.1 in floats; s must still be zero.
        values = [0.1, 0.1, 0.1]
        assert math.fsum(values) / 3 != 0.1
        assert std(values) == 0.0
        with pytest.raises(NotComputable):
            sharpe(values)

    def test_constant_has_no_skew_or_kurtosis(self) -> None:
        with pytest.raises(NotComputable):
            skewness([0.2, 0.2])
        with pytest.raises(NotComputable):
            kurtosis([0.2, 0.2])

    def test_subnormal_values_not_computable(self) -> None:
        # A non-zero value below the smallest normal float: never a division
        # by zero or an infinite Sharpe ratio.
        values = [0.0, 0.0, 5e-324]
        with pytest.raises(NotComputable, match="too small"):
            sharpe(values)
        with pytest.raises(NotComputable, match="too small"):
            skewness(values)
        with pytest.raises(NotComputable, match="too small"):
            kurtosis(values)

    def test_tiny_values_do_not_underflow(self) -> None:
        # m2^2 and m2^(3/2) would underflow to 0 here; the deviations are scaled
        # by an exact power of two first, and the ratios are scale-free.
        #   [0, 0, 1]: mean 1/3, d = -1/3, -1/3, 2/3; m2 = 2/9, m4 = 2/27,
        #   kurtosis = (2/27) / (4/81) = 3/2.
        #   [0, 1, 3]: mean 4/3, d = -4/3, -1/3, 5/3; m2 = 14/9, m3 = 20/27,
        #   skewness^2 = m3^2 / m2^3 = (400/729) / (2744/729) = 50/343.
        assert kurtosis([0.0, 0.0, 1e-90]) == pytest.approx(1.5, rel=1e-14)
        skew = skewness([0.0, 1e-110, 3e-110])
        assert skew > 0
        assert skew**2 == pytest.approx(float(Fraction(50, 343)), rel=1e-14)
        assert sharpe([0.0, 0.0, 1e-200]) == pytest.approx(
            sharpe([0.0, 0.0, 1.0]), rel=1e-15
        )
        assert std([0.0, 1e-200]) == pytest.approx(1e-200 / math.sqrt(2), rel=1e-15)

    def test_scaling_changes_nothing(self) -> None:
        # Exact power-of-two scaling: the five-point results, bit for bit.
        values = [v / 3 for v in FIVE]
        assert sharpe([v * 2.0**-600 for v in values]) == sharpe(values)
        assert kurtosis([v * 2.0**-600 for v in values]) == kurtosis(values)

    def test_exactly_constant_net_of_cost(self) -> None:
        # gross = 0.01 + tau * 5 bps, so net of a 5 bps cost every period is
        # exactly 0.01 in decimal. Float arithmetic leaves s of about 1e-18 and
        # a Sharpe ratio near 9e15; the exact computation gives s = 0.
        turnover = [0.5, 1.0, 0.2, 3.7, 0.0]
        gross = [0.01025, 0.0105, 0.0101, 0.01185, 0.01]
        r = net_excess(gross, turnover, cost_bps=5.0, risk_free=None)
        assert r.tolist() == [0.01] * 5
        assert std(r) == 0.0
        with pytest.raises(NotComputable, match="zero"):
            sharpe(r)

    def test_exactly_constant_net_of_risk_free(self) -> None:
        # gross = risk-free + 0.03: net excess is exactly 0.03 every period.
        risk_free = [0.0001, 0.00002, 0.000141, 0.00019]
        gross = [0.0301, 0.03002, 0.030141, 0.03019]
        # In floats, 0.03002 - 0.00002 is not 0.03.
        assert [g - f for g, f in zip(gross, risk_free, strict=True)] != [0.03] * 4
        r = net_excess(gross, [0.0] * 4, cost_bps=5.0, risk_free=risk_free)
        assert r.tolist() == [0.03] * 4
        with pytest.raises(NotComputable):
            sharpe(r)

    def test_one_period(self) -> None:
        with pytest.raises(NotComputable):
            sharpe([0.01])

    def test_empty(self) -> None:
        with pytest.raises(NotComputable):
            mean([])

    def test_nan_refused(self) -> None:
        with pytest.raises(NotComputable):
            sharpe([0.01, math.nan, 0.02])

    def test_trial_sharpes_lists_the_constant_variant(self) -> None:
        computed, missing = trial_sharpes(
            {1: FIVE, 2: [0.0, 0.0, 0.0], 3: [0.5], 4: [1.0, 3.0]}
        )
        assert set(computed) == {1, 4}
        assert computed[4] == pytest.approx(2.0 / math.sqrt(2.0))
        assert set(missing) == {2, 3}
        assert "zero" in missing[2]


class TestNetExcess:
    # Hand case: c = 5 bps = 0.0005.
    #   t1: 0.01 - 0.5 * 0.0005 - 0.0001 = 0.00965
    #   t2: -0.02 - 1.0 * 0.0005 - 0.0001 = -0.0206
    #   t3: 0.03 - 0.0 * 0.0005 - 0.0001 = 0.0299
    GROSS = [0.01, -0.02, 0.03]
    TURNOVER = [0.5, 1.0, 0.0]
    RISK_FREE = [0.0001, 0.0001, 0.0001]

    def test_hand_case(self) -> None:
        r = net_excess(
            self.GROSS, self.TURNOVER, cost_bps=5.0, risk_free=self.RISK_FREE
        )
        assert r.tolist() == pytest.approx([0.00965, -0.0206, 0.0299], abs=1e-15)

    def test_doubling_the_cost(self) -> None:
        # c = 10 bps: t1 = 0.01 - 0.0005 - 0.0001 = 0.0094; t2 = -0.0211.
        r = net_excess(
            self.GROSS, self.TURNOVER, cost_bps=10.0, risk_free=self.RISK_FREE
        )
        assert r.tolist() == pytest.approx([0.0094, -0.0211, 0.0299], abs=1e-15)

    def test_no_risk_free(self) -> None:
        r = net_excess(self.GROSS, self.TURNOVER, cost_bps=5.0, risk_free=None)
        assert r.tolist() == pytest.approx([0.00975, -0.0205, 0.03], abs=1e-15)

    def test_zero_cost_leaves_gross(self) -> None:
        r = net_excess(self.GROSS, self.TURNOVER, cost_bps=0.0, risk_free=None)
        assert r.tolist() == self.GROSS

    @pytest.mark.parametrize(
        ("turnover", "risk_free", "cost"),
        [
            pytest.param([0.5, 1.0], None, 5.0, id="turnover_length"),
            pytest.param([0.5, 1.0, 0.0], [0.0], 5.0, id="risk_free_length"),
            pytest.param([0.5, 1.0, 0.0], None, -1.0, id="negative_cost"),
            pytest.param([0.5, 1.0, 0.0], None, math.inf, id="infinite_cost"),
        ],
    )
    def test_refusals(
        self, turnover: list[float], risk_free: list[float] | None, cost: float
    ) -> None:
        with pytest.raises(ValueError):
            net_excess(self.GROSS, turnover, cost_bps=cost, risk_free=risk_free)

    def test_two_dimensional_refused(self) -> None:
        with pytest.raises(ValueError):
            net_excess(np.zeros((2, 2)), np.zeros((2, 2)), cost_bps=0, risk_free=None)

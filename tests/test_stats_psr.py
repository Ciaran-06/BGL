"""PSR, the DSR hurdle, the trial variance and MinTRL."""

import math

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from bgl.stats import NotComputable
from bgl.stats.psr import (
    EULER_GAMMA,
    dsr_hurdle,
    expected_max,
    min_trl,
    psr,
    trial_variance,
)


def erf_cdf(x: float) -> float:
    """Phi from math.erf, independent of the code under test."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


class TestPsrHandCases:
    def test_normal_small_sample(self) -> None:
        # SR = 0.5, skew 0, kurt 3, T = 5, SR* = 0:
        #   term = 1 - 0 + (3 - 1)/4 * 0.25 = 1.125
        #   z = 0.5 * sqrt(4) / sqrt(1.125) = 1 / sqrt(1.125) = 0.9428090415820634
        z = 1.0 / math.sqrt(1.125)
        value = psr(sharpe=0.5, skewness=0.0, kurtosis=3.0, t=5, sr_star=0.0)
        assert value == pytest.approx(erf_cdf(z), abs=1e-14)
        # sqrt(T) in place of sqrt(T - 1) would give Phi(0.5 sqrt(5)/sqrt(1.125)).
        assert value != pytest.approx(erf_cdf(0.5 * math.sqrt(5) / math.sqrt(1.125)))

    def test_skew_term_sign(self) -> None:
        # SR = 0.3, skew +2, kurt 6, T = 26, SR* = 0.1:
        #   term = 1 - 2 * 0.3 + 5/4 * 0.09 = 1 - 0.6 + 0.1125 = 0.5125
        #   z = 0.2 * 5 / sqrt(0.5125)
        z = 0.2 * 5.0 / math.sqrt(0.5125)
        value = psr(sharpe=0.3, skewness=2.0, kurtosis=6.0, t=26, sr_star=0.1)
        assert value == pytest.approx(erf_cdf(z), abs=1e-14)
        # With the skew term's sign flipped: term = 1.7125.
        assert value != pytest.approx(erf_cdf(1.0 / math.sqrt(1.7125)))

    def test_excess_kurtosis_would_differ(self) -> None:
        # Plain kurtosis 3 gives term 1 + 0.5 SR^2; excess (0) would give
        # 1 - 0.25 SR^2.
        value = psr(sharpe=1.0, skewness=0.0, kurtosis=3.0, t=2, sr_star=0.0)
        assert value == pytest.approx(erf_cdf(1.0 / math.sqrt(1.5)), abs=1e-14)
        assert value != pytest.approx(erf_cdf(1.0 / math.sqrt(0.75)))

    def test_term_not_positive_is_not_computable(self) -> None:
        # term = 1 - 2 * 1 + (2 - 1)/4 * 1 = -0.75
        with pytest.raises(NotComputable):
            psr(sharpe=1.0, skewness=2.0, kurtosis=2.0, t=10, sr_star=0.0)

    def test_term_exactly_zero_is_not_computable(self) -> None:
        # SR = 1, skew 2, kurt 5 (= 1 + skew^2, a two-point law):
        #   term = 1 - 2 + (5 - 1)/4 = 0 exactly.
        with pytest.raises(NotComputable):
            psr(sharpe=1.0, skewness=2.0, kurtosis=5.0, t=10, sr_star=0.0)
        with pytest.raises(NotComputable):
            min_trl(sharpe=1.0, skewness=2.0, kurtosis=5.0, sr_star=0.0, threshold=0.95)

    def test_one_period_not_computable(self) -> None:
        with pytest.raises(NotComputable):
            psr(sharpe=1.0, skewness=0.0, kurtosis=3.0, t=1, sr_star=0.0)


class TestDsrPaperExample:
    """Bailey and López de Prado (2014), the numerical example.

    N = 100 trials, V = 1/2 (annualised), T = 1250 daily periods at 250 a
    year, skew -3, kurtosis 10, annualised Sharpe 2.5. Published: SR* = 0.1132
    per period and DSR = 0.9004; N = 46 gives 0.9505; normal returns with
    N = 88 give about 0.95. (The inputs are from memory; see docs/PLAN.md,
    section 6, item 2. They reproduce all three printed results.)

    Working, for N = 100:
      per-period V = 0.5 / 250 = 0.002, sqrt(V) = 0.0447214
      Phi^-1(1 - 1/100) = 2.326348; Phi^-1(1 - 1/(100 e)) = 2.680210
      E[max] = 0.4227843 * 2.326348 + 0.5772157 * 2.680210 = 2.530603
      SR* = 0.0447214 * 2.530603 = 0.113172
      SR = 2.5 / sqrt(250) = 0.1581139
      term = 1 + 3 * 0.1581139 + 9/4 * 0.025 = 1.5305916
      z = (0.1581139 - 0.113172) * sqrt(1249) / sqrt(1.5305916) = 1.283816
      DSR = Phi(1.283816) = 0.900397
    """

    PER_YEAR = 250
    SR = 2.5 / math.sqrt(PER_YEAR)
    V = 0.5 / PER_YEAR

    def dsr(self, n: int, skew: float, kurt: float) -> float:
        star = dsr_hurdle(n=n, variance=self.V)
        return psr(sharpe=self.SR, skewness=skew, kurtosis=kurt, t=1250, sr_star=star)

    def test_hurdle(self) -> None:
        assert dsr_hurdle(n=100, variance=self.V) == pytest.approx(0.1132, abs=5e-5)

    def test_dsr_n_100(self) -> None:
        assert self.dsr(100, -3.0, 10.0) == pytest.approx(0.9004, abs=5e-5)
        # The working above, to six places.
        assert self.dsr(100, -3.0, 10.0) == pytest.approx(0.900397, abs=5e-7)

    def test_dsr_n_46(self) -> None:
        assert self.dsr(46, -3.0, 10.0) == pytest.approx(0.9505, abs=5e-5)

    def test_normal_n_88(self) -> None:
        assert round(self.dsr(88, 0.0, 3.0), 2) == 0.95

    def test_expected_max_by_hand(self) -> None:
        # Independent inverse normals: Phi(x) from erf, solved by bisection.
        def inverse(p: float) -> float:
            low, high = -10.0, 10.0
            for _ in range(200):
                middle = (low + high) / 2
                low, high = (middle, high) if erf_cdf(middle) < p else (low, middle)
            return (low + high) / 2

        n = 100
        hand = (1 - 0.5772156649015329) * inverse(1 - 1 / n) + 0.5772156649015329 * (
            inverse(1 - 1 / (n * math.e))
        )
        assert expected_max(n) == pytest.approx(hand, abs=1e-9)


def exact_expected_max(n: int) -> float:
    """E[max of N standard normals] = int x N phi(x) Phi(x)^(N-1) dx, by quadrature.

    Trapezoid rule on [-12, 12] with step 5e-5, Phi from math.erf.
    """
    x = np.linspace(-12.0, 12.0, 480_001)
    density = np.exp(-x * x / 2.0) / math.sqrt(2.0 * math.pi)
    phi = 0.5 * (1.0 + np.vectorize(math.erf)(x / math.sqrt(2.0)))
    integrand = x * n * density * phi ** (n - 1)
    return float(np.trapezoid(integrand, x))


class TestExpectedMax:
    @pytest.mark.parametrize("n", [2, 3, 5, 10, 100, 1000, 10**6])
    def test_within_005_of_exact(self, n: int) -> None:
        # The spec keeps the paper's approximation because it stays within
        # about 0.05 of the exact value (docs/PLAN.md, section 6, item 5).
        assert abs(expected_max(n) - exact_expected_max(n)) < 0.05

    def test_quadrature_is_right(self) -> None:
        # Known exact values: E[max of 2] = 1/sqrt(pi); E[max of 3] = 3/(2 sqrt(pi)).
        assert exact_expected_max(2) == pytest.approx(1 / math.sqrt(math.pi), 1e-9)
        assert exact_expected_max(3) == pytest.approx(1.5 / math.sqrt(math.pi), 1e-9)

    def test_n_1_is_zero(self) -> None:
        assert expected_max(1) == 0.0
        assert dsr_hurdle(n=1, variance=0.3) == 0.0

    def test_n_1_gives_psr_0(self) -> None:
        kwargs = {"sharpe": 0.1, "skewness": -0.5, "kurtosis": 4.0, "t": 200}
        star = dsr_hurdle(n=1, variance=0.01)
        assert psr(**kwargs, sr_star=star) == psr(**kwargs, sr_star=0.0)

    @pytest.mark.parametrize("n", [0, -1, True, 2.0])
    def test_bad_n(self, n: object) -> None:
        with pytest.raises(ValueError):
            expected_max(n)  # type: ignore[arg-type]

    def test_euler_gamma(self) -> None:
        # OEIS A001620: 0.5772156649015328606...
        assert EULER_GAMMA == pytest.approx(0.5772156649015329, abs=1e-16)

    @given(st.integers(min_value=1, max_value=10**7))
    @settings(max_examples=200, deadline=None)
    def test_dsr_never_rises_with_n(self, n: int) -> None:
        assert expected_max(n + 1) >= expected_max(n)
        kwargs = {"sharpe": 0.08, "skewness": -1.0, "kurtosis": 5.0, "t": 500}
        lower = psr(**kwargs, sr_star=dsr_hurdle(n=n + 1, variance=0.004))
        higher = psr(**kwargs, sr_star=dsr_hurdle(n=n, variance=0.004))
        assert lower <= higher


class TestTrialVariance:
    def test_floor_applies(self) -> None:
        # Sharpes 0.1, 0.2, 0.3: V-hat = (0.01 + 0 + 0.01) / 2 = 0.01.
        # T = 11: floor 1/10 = 0.1 > 0.01, so V = 0.1.
        result = trial_variance([0.1, 0.2, 0.3], t=11)
        assert result.v_hat == pytest.approx(0.01, abs=1e-15)
        assert result.value == 0.1
        assert result.floor_applied
        assert result.trials == 3

    def test_divisor_k_minus_1(self) -> None:
        # Sharpes 0, 1, 2: V-hat = (1 + 0 + 1) / 2 = 1 (divisor K would give 2/3).
        result = trial_variance([0.0, 1.0, 2.0], t=11)
        assert result.v_hat == 1.0
        assert result.value == 1.0
        assert not result.floor_applied

    def test_equal_to_floor(self) -> None:
        # Sharpes 0, 1: V-hat = 0.5; T = 3: floor 1/2. Same value either way.
        result = trial_variance([0.0, 1.0], t=3)
        assert result.value == 0.5
        assert not result.floor_applied

    @pytest.mark.parametrize("sharpes", [[], [0.4]])
    def test_fewer_than_two(self, sharpes: list[float]) -> None:
        result = trial_variance(sharpes, t=101)
        assert result.value == 0.01
        assert result.v_hat is None
        assert result.floor_applied

    @given(
        st.floats(min_value=-5, max_value=5, allow_nan=False),
        st.integers(min_value=2, max_value=50),
        st.integers(min_value=2, max_value=10_000),
    )
    @settings(max_examples=100, deadline=None)
    def test_identical_variants_take_the_floor(
        self, value: float, k: int, t: int
    ) -> None:
        result = trial_variance([value] * k, t=t)
        assert result.v_hat == 0.0
        assert result.value == 1.0 / (t - 1)
        assert result.floor_applied

    def test_short_series(self) -> None:
        with pytest.raises(NotComputable):
            trial_variance([0.1, 0.2], t=1)


class TestMinTrl:
    def test_hand_case(self) -> None:
        # SR = 0.2, SR* = 0.1, skew 0, kurt 3, threshold 0.95:
        #   z = 1.6448536269514722, term = 1 + 0.5 * 0.04 = 1.02
        #   MinTRL = 1 + 1.02 * (z / 0.1)^2 = 1 + 1.02 * 270.554345... = 276.965432...
        z = 1.6448536269514722
        expected = 1 + 1.02 * (z / 0.1) ** 2
        value = min_trl(
            sharpe=0.2, skewness=0.0, kurtosis=3.0, sr_star=0.1, threshold=0.95
        )
        assert value == pytest.approx(expected, rel=1e-12)
        assert value == pytest.approx(276.9654, abs=1e-4)

    @pytest.mark.parametrize("sharpe", [0.1, 0.05])
    def test_not_reachable_at_or_below_hurdle(self, sharpe: float) -> None:
        assert (
            min_trl(
                sharpe=sharpe, skewness=0.0, kurtosis=3.0, sr_star=0.1, threshold=0.95
            )
            is None
        )

    @pytest.mark.parametrize("threshold", [0.05, 0.3, 0.5])
    def test_threshold_at_or_below_half(self, threshold: float) -> None:
        # z <= 0: with SR > SR*, PSR is above 1/2 at every T, so MinTRL = 1.
        # (Squaring z, as the paper's formula does, would give 1 + term z^2 /
        # (SR - SR*)^2 > 1 for thresholds below 0.5.)
        value = min_trl(
            sharpe=0.2, skewness=0.0, kurtosis=3.0, sr_star=0.1, threshold=threshold
        )
        assert value == 1.0

    @pytest.mark.parametrize("threshold", [0.05, 0.3, 0.5, 0.8, 0.95, 0.99])
    @pytest.mark.parametrize("skew", [-2.0, 0.0, 1.0])
    def test_grid_agrees_with_psr(self, threshold: float, skew: float) -> None:
        # T >= MinTRL exactly when PSR(SR*) >= the threshold, for SR > SR*.
        kurt = 3.0 + skew * skew
        checked = 0
        for sharpe in (0.02, 0.05, 0.1, 0.3):
            for star in (0.0, 0.01, 0.04):
                if sharpe <= star:
                    continue
                needed = min_trl(
                    sharpe=sharpe,
                    skewness=skew,
                    kurtosis=kurt,
                    sr_star=star,
                    threshold=threshold,
                )
                assert needed is not None
                for t in range(2, 3000, 7):
                    if abs(t - needed) < 1e-6:
                        continue  # too close to call in floats
                    value = psr(
                        sharpe=sharpe, skewness=skew, kurtosis=kurt, t=t, sr_star=star
                    )
                    assert (t >= needed) == (value >= threshold)
                    checked += 1
        assert checked > 1000

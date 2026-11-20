"""Stationary bootstrap, its block length and its band (N41)."""

import math
import statistics

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from bgl.stats import NotComputable
from bgl.stats.bootstrap import (
    LEVEL,
    REPLICATIONS,
    band,
    block_length,
    lag_count,
    lag_window,
    linear_quantile,
    resample_indices,
    sb_block_length,
    seed_from_lock_hash,
)

LOCK_HASH = "0123456789abcdef" + "0" * 48


def ar1(phi: float, n: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    noise = rng.standard_normal(n)
    x = np.empty(n)
    x[0] = noise[0] / math.sqrt(1 - phi * phi)
    for i in range(1, n):
        x[i] = phi * x[i - 1] + noise[i]
    return x


class TestTable1:
    """Patton, Politis and White (2009), Table 1: the optimal stationary
    bootstrap block length for AR(1) with coefficient rho.

    With unit innovations R(k) = rho^|k| / (1 - rho^2), so the formula's
    G^2 / g(0)^2 = (2 rho / (1 - rho^2))^2 and b = (2 rho/(1-rho^2))^(2/3) N^(1/3).
    For rho = 0.7, N = 200: (1.4/0.51)^(2/3) * 200^(1/3) = 1.96047 * 5.84804 = 11.465.
    """

    @pytest.mark.parametrize(
        ("rho", "n", "published"),
        [
            (0.7, 200, 11.47),
            (0.7, 800, 18.20),
            (0.1, 200, 2.01),
            (0.1, 800, 3.20),
            (-0.4, 200, 5.66),
            (-0.4, 800, 8.99),
        ],
    )
    def test_table_1(self, rho: float, n: int, published: float) -> None:
        # M = 2000: the flat-top window is 1 out to lag 1000, where rho^k is
        # below 1e-150, so the truncation is exact to double precision.
        acov = [rho**k / (1 - rho * rho) for k in range(2001)]
        assert round(sb_block_length(acov, n), 2) == published

    def test_hand_formula(self) -> None:
        rho, n = 0.7, 200
        acov = [rho**k / (1 - rho * rho) for k in range(2001)]
        hand = (2 * rho / (1 - rho * rho)) ** (2 / 3) * n ** (1 / 3)
        assert sb_block_length(acov, n) == pytest.approx(hand, rel=1e-12)


class TestBlockLength:
    def test_ar1_estimate_near_theory(self) -> None:
        # rho = 0.7, N = 20000: theory (1.4/0.51)^(2/3) * 20000^(1/3) = 53.2.
        estimate = block_length(ar1(0.7, 20_000, seed=5)).value
        theory = (1.4 / 0.51) ** (2 / 3) * 20_000 ** (1 / 3)
        assert abs(estimate / theory - 1) < 0.25

    def test_white_noise_gives_short_blocks(self) -> None:
        # m-hat is at least 1 (the paper's "smallest positive integer"), so
        # M = 2, and white noise gives a block near the ordinary bootstrap's 1.
        result = block_length(np.random.default_rng(1).standard_normal(5000))
        assert result.m_hat == 1
        assert result.lag_window == 2
        assert 1.0 <= result.value < 3.0

    def test_persistence_lengthens_blocks(self) -> None:
        short = block_length(ar1(0.1, 2000, seed=2)).value
        long = block_length(ar1(0.8, 2000, seed=2)).value
        assert long > 3 * short

    def test_scale_and_shift_free(self) -> None:
        x = ar1(0.5, 1000, seed=3)
        base = block_length(x).value
        assert block_length(x * 1000 + 7).value == pytest.approx(base, rel=1e-9)

    def test_cap(self) -> None:
        # A strictly alternating series of 200 has a tiny long-run variance
        # g(0), so the formula's b is huge; it stops at
        # ceil(min(3 sqrt 200, 200/3)) = ceil(42.43) = 43.
        result = block_length((-1.0) ** np.arange(200))
        assert result.capped
        assert result.value == 43.0

    def test_underflowing_deviations(self) -> None:
        # Found by Hypothesis: one subnormal among zeros squares to 0.
        with pytest.raises(NotComputable):
            block_length([0.0] * 49 + [5e-324])

    def test_too_short(self) -> None:
        with pytest.raises(NotComputable):
            block_length([0.1, 0.2, 0.3, 0.1, 0.0, 0.2])

    @given(
        st.lists(
            st.floats(
                min_value=-1, max_value=1, allow_nan=False, allow_subnormal=False
            ),
            min_size=40,
            max_size=300,
        ).filter(lambda values: float(np.var(values)) > 1e-200)
    )
    @settings(max_examples=60, deadline=None)
    def test_always_between_1_and_the_cap(self, values: list[float]) -> None:
        result = block_length(values)
        n = len(values)
        assert 1.0 <= result.value <= math.ceil(min(3 * math.sqrt(n), n / 3))


class TestLagWindow:
    """m-hat and M from given autocorrelations, N = 100.

    K_N = max(5, ceil(sqrt(log10 100))) = 5; m_max = ceil(sqrt 100) + 5 = 15.
    The bound c sqrt(log10 N / N) = 2 sqrt(0.02) = 0.2828 (it would be 0.2121
    with c = 1.5, 0.4243 with c = 3, and 0.4292 with the natural log).
    """

    def test_lag_count(self) -> None:
        assert lag_count(100) == (5, 15)
        assert lag_count(10_000) == (5, 105)

    def test_pins_c_and_log10(self) -> None:
        # rho_1 = 0.9 and rho_2 = 0.35 are significant, rho_3 = 0.25 and later
        # are not: m-hat = 2 (rho_3 .. rho_7 small), M = 4. With c = 1.5
        # rho_3 is significant (m-hat 3); with c = 3 or ln, rho_2 is not
        # (m-hat 1).
        rho = [0.9, 0.35, 0.25] + [0.0] * 12
        assert lag_window(rho, 100) == (2, 4)

    def test_smallest_positive_m(self) -> None:
        # No significant autocorrelation: the paper's m-hat is 1, not 0.
        assert lag_window([0.0] * 15, 100) == (1, 2)

    def test_no_quiet_run(self) -> None:
        # Never K_N small values in a row: m-hat is the last significant lag,
        # and M = min(2 m-hat, m_max).
        assert lag_window([0.9] * 15, 100) == (15, 15)
        assert lag_window([0.9] * 6 + [0.0] * 9, 100) == (6, 12)


class TestSeedAndDraws:
    def test_seed_is_first_8_bytes(self) -> None:
        assert seed_from_lock_hash(LOCK_HASH) == 0x0123456789ABCDEF

    @pytest.mark.parametrize("text", ["0123", "G" * 64, "A" * 64, ""])
    def test_bad_hash(self, text: str) -> None:
        with pytest.raises(ValueError):
            seed_from_lock_hash(text)

    def test_pcg64_stream_is_pinned(self) -> None:
        # Regression vector: numpy promises PCG64's stream for a given seed is
        # stable; if it ever changes, every stored band changes, so fail here.
        generator = np.random.PCG64(0x0123456789ABCDEF)
        assert [int(x) for x in generator.random_raw(3)] == [
            13120626929746913276,
            17671978889722661604,
            9241423605842732577,
        ]

    def test_p_1_is_the_ordinary_bootstrap(self) -> None:
        # Mean block 1: p = 1, so every period starts a block at floor(u t),
        # u the second draw of each pair (recomputed here from the raw stream).
        t, length = 7, 50
        indices = resample_indices(
            np.random.PCG64(11), t=t, length=length, block=1.0, count=1
        )
        raw = np.random.PCG64(11).random_raw(2 * length)
        # floor(u t) with u = (raw >> 11) / 2^53, in exact integers.
        expected = [((int(raw[2 * i + 1]) >> 11) * t) >> 53 for i in range(length)]
        assert indices[0].tolist() == expected

    def test_wrap_around(self) -> None:
        # A huge mean block: one block for the whole path, wrapping from t-1 to 0.
        indices = resample_indices(
            np.random.PCG64(3), t=3, length=10, block=1e12, count=4
        )
        for path in indices.tolist():
            for earlier, later in zip(path, path[1:], strict=False):
                assert later == (earlier + 1) % 3

    def test_mean_block_length(self) -> None:
        # Geometric block lengths with mean 1/p = 5.
        indices = resample_indices(
            np.random.PCG64(8), t=10_000, length=2000, block=5.0, count=50
        )
        breaks = np.count_nonzero(np.diff(indices, axis=1) % 10_000 != 1)
        blocks = breaks + 50  # every path's first period starts a block
        assert indices.size / blocks == pytest.approx(5.0, rel=0.03)

    def test_bad_block(self) -> None:
        with pytest.raises(ValueError):
            resample_indices(np.random.PCG64(1), t=5, length=5, block=0.5, count=1)


class TestQuantile:
    @pytest.mark.parametrize(
        ("values", "q", "expected"),
        [
            ([1.0, 2.0, 3.0, 4.0], 0.5, 2.5),
            ([0.0, 10.0], 0.25, 2.5),
            ([4.0, 1.0, 3.0], 0.0, 1.0),
            ([4.0, 1.0, 3.0], 1.0, 4.0),
            ([5.0], 0.3, 5.0),
        ],
    )
    def test_hand_cases(self, values: list[float], q: float, expected: float) -> None:
        assert linear_quantile(values, q) == expected

    def test_matches_numpy_linear(self) -> None:
        values = np.random.default_rng(2).normal(size=1001)
        for q in (0.025, 0.5, 0.975):
            assert linear_quantile(values, q) == pytest.approx(
                float(np.quantile(values, q, method="linear")), rel=1e-15
            )


class TestBand:
    def series(self) -> np.ndarray:
        return 0.0005 + 0.01 * ar1(0.2, 500, seed=6)

    def run(self, seed: int, **changes: object) -> object:
        arguments: dict[str, object] = {
            "length": 63,
            "block": 3.0,
            "replications": REPLICATIONS,
            "level": LEVEL,
            "seed": seed,
        }
        arguments.update(changes)
        return band(self.series(), **arguments)  # type: ignore[arg-type]

    def test_same_seed_same_band(self) -> None:
        seed = seed_from_lock_hash(LOCK_HASH)
        assert self.run(seed) == self.run(seed)

    def test_other_seed_other_band(self) -> None:
        assert self.run(1) != self.run(2)

    def test_band_brackets_the_sharpe(self) -> None:
        result = band(
            self.series(),
            length=63,
            block=3.0,
            replications=REPLICATIONS,
            level=LEVEL,
            seed=7,
        )
        sharpe = self.series().mean() / self.series().std(ddof=1)
        assert result.low < sharpe < result.high
        # A Sharpe over 63 periods has a standard error near 1/sqrt(63) = 0.126,
        # so a 95% band is roughly 0.5 wide.
        assert 0.3 < result.high - result.low < 0.8

    @pytest.mark.parametrize("level", [0.95, 0.9])
    def test_recomputed_independently(self, level: float) -> None:
        # The same index paths (drawn in one call, where band draws in chunks
        # of 500), each Sharpe by the statistics module (divisor T-1), and the
        # (1 - level)/2 and (1 + level)/2 quantiles by numpy's linear method.
        series = self.series()
        result = band(
            series, length=63, block=3.0, replications=2000, level=level, seed=12
        )
        paths = resample_indices(
            np.random.PCG64(12), t=len(series), length=63, block=3.0, count=2000
        )
        sharpes = []
        for path in paths:
            values = series[path].tolist()
            sharpes.append(statistics.mean(values) / statistics.stdev(values))
        low, high = np.quantile(sharpes, [(1 - level) / 2, (1 + level) / 2])
        assert result.low == pytest.approx(float(low), rel=1e-12)
        assert result.high == pytest.approx(float(high), rel=1e-12)

    def test_constant_replication_not_computable(self) -> None:
        values = [0.0] * 99 + [1.0]
        with pytest.raises(NotComputable):
            band(values, length=2, block=1.0, replications=100, level=LEVEL, seed=1)

    def test_one_period_not_computable(self) -> None:
        with pytest.raises(NotComputable):
            band(
                self.series(), length=1, block=1.0, replications=10, level=LEVEL, seed=1
            )

    def test_spec_constants(self) -> None:
        assert REPLICATIONS == 10_000
        assert LEVEL == 0.95

import math

import pytest

from bgl.stats import NotComputable
from bgl.stats.normal import cdf, inverse_cdf


def erf_cdf(x: float) -> float:
    """Phi from math.erf, independent of statistics.NormalDist."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


class TestPublishedValues:
    # docs/PLAN.md section 3: Phi^-1(0.99) = 2.326348 and Phi(1.959964) = 0.975.
    def test_inverse_at_099(self) -> None:
        assert inverse_cdf(0.99) == pytest.approx(2.326348, abs=5e-7)

    def test_cdf_at_1959964(self) -> None:
        assert cdf(1.959964) == pytest.approx(0.975, abs=5e-7)

    @pytest.mark.parametrize("x", [-3.0, -1.0, 0.0, 0.5, 2.0])
    def test_cdf_matches_erf(self, x: float) -> None:
        assert cdf(x) == pytest.approx(erf_cdf(x), abs=1e-14)

    @pytest.mark.parametrize("p", [0.001, 0.3, 0.5, 0.9, 0.999999])
    def test_inverse_round_trips(self, p: float) -> None:
        assert erf_cdf(inverse_cdf(p)) == pytest.approx(p, rel=1e-9)


class TestRefusals:
    @pytest.mark.parametrize("p", [0.0, 1.0, -0.1, 1.5, math.nan])
    def test_inverse_outside_open_interval(self, p: float) -> None:
        with pytest.raises(NotComputable):
            inverse_cdf(p)

    def test_cdf_of_nan(self) -> None:
        with pytest.raises(NotComputable):
            cdf(math.nan)

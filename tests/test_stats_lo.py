"""Lo (2002): eta(q) = q / sqrt(q + 2 sum_{k=1}^{q-1} (q-k) rho_k)."""

import math

import pytest

from bgl.stats import NotComputable
from bgl.stats.lo import autocorrelations, eta, lo_sharpe
from bgl.stats.moments import sharpe


class TestEta:
    @pytest.mark.parametrize("q", [1, 2, 12, 52, 252])
    def test_no_autocorrelation_gives_root_q(self, q: int) -> None:
        assert eta([0.0] * (q - 1), q) == pytest.approx(math.sqrt(q), rel=1e-15)

    def test_q2_rho_half(self) -> None:
        # 2 / sqrt(2 + 2 * 1 * 0.5) = 2 / sqrt 3 = sqrt(4/3)
        assert eta([0.5], 2) == pytest.approx(math.sqrt(4 / 3), rel=1e-15)

    @pytest.mark.parametrize(("phi", "q"), [(0.5, 12), (-0.3, 12), (0.2, 252)])
    def test_ar1_closed_form(self, phi: float, q: int) -> None:
        # With rho_k = phi^k:
        #   sum_{k=1}^{q-1} (q-k) phi^k = phi/(1-phi) * (q - (1 - phi^q)/(1 - phi))
        # (check q = 2: phi/(1-phi) * (2 - (1+phi)) = phi). So
        #   eta = q / sqrt(q + 2 phi/(1-phi) (q - (1-phi^q)/(1-phi))).
        closed = q / math.sqrt(q + 2 * phi / (1 - phi) * (q - (1 - phi**q) / (1 - phi)))
        rho = [phi**k for k in range(1, q)]
        assert eta(rho, q) == pytest.approx(closed, rel=1e-12)

    def test_term_not_positive(self) -> None:
        # q = 2, rho_1 = -1: 2 + 2 * (-1) = 0.
        with pytest.raises(NotComputable):
            eta([-1.0], 2)

    def test_too_few_autocorrelations(self) -> None:
        with pytest.raises(ValueError):
            eta([0.1], 3)

    @pytest.mark.parametrize("q", [0, True])
    def test_bad_q(self, q: int) -> None:
        with pytest.raises(ValueError):
            eta([], q)


class TestLoSharpe:
    def test_hand_case(self) -> None:
        # r = 1, 3, 2, 4 (T = 4 > q = 2); mean 2.5; d = -1.5, 0.5, -0.5, 1.5
        #   sum d^2 = 5; lag-1 products: -0.75, -0.25, -0.75, sum -1.75
        #   rho_1 = -0.35; eta = 2 / sqrt(2 - 0.7) = 2 / sqrt 1.3
        #   SR = 2.5 / sqrt(5/3); annualised = eta * SR
        r = [1.0, 3.0, 2.0, 4.0]
        assert autocorrelations(r, 1) == pytest.approx([-0.35], abs=1e-15)
        result = lo_sharpe(r, periods_per_year=2)
        assert result.eta == pytest.approx(2 / math.sqrt(1.3), rel=1e-14)
        assert result.sharpe_per_period == pytest.approx(2.5 / math.sqrt(5 / 3))
        assert result.sharpe_annualised == pytest.approx(
            2 / math.sqrt(1.3) * 2.5 / math.sqrt(5 / 3), rel=1e-14
        )

    def test_n40_needs_more_than_q_periods(self) -> None:
        r = [1.0, 3.0, 2.0, 4.0, 2.5]
        with pytest.raises(NotComputable, match="N40"):
            lo_sharpe(r[:4], periods_per_year=4)  # T = q
        assert lo_sharpe(r, periods_per_year=4).q == 4  # T = q + 1

    def test_q_1_is_the_plain_sharpe(self) -> None:
        r = [1.0, 3.0, 2.0, 4.0]
        result = lo_sharpe(r, periods_per_year=1)
        assert result.eta == 1.0
        assert result.sharpe_annualised == sharpe(r)

    def test_subnormal_values(self) -> None:
        with pytest.raises(NotComputable, match="too small"):
            autocorrelations([0.0, 0.0, 0.0, 5e-324], 1)

    def test_tiny_values_are_scale_free(self) -> None:
        # The hand case scaled by 1e-160: its squares would underflow.
        r = [1.0, 3.0, 2.0, 4.0]
        assert autocorrelations([v * 1e-160 for v in r], 1) == pytest.approx(
            [-0.35], abs=1e-15
        )

    def test_constant_series(self) -> None:
        with pytest.raises(NotComputable):
            lo_sharpe([0.1] * 10, periods_per_year=2)

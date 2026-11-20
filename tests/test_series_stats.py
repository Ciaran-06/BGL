"""Reading series and risk-free files, and the close file's statistics (N38, N39)."""

import datetime
import math
from typing import Any

import pytest

from bgl.canonical import canonical_json
from bgl.series import (
    SeriesFileError,
    check_series,
    close_summary,
    read_risk_free,
    read_series,
    risk_free_on,
    series_bytes,
)
from bgl.stats import NotComputable
from bgl.stats.rounding import decimal_text

D1, D2, D3 = "2020-01-02", "2020-01-03", "2020-01-06"
HEADER = b"period,variant,gross,turnover\n"


def make(
    gross: dict[int, dict[str, float]], turnover: dict[int, dict[str, float]]
) -> Any:
    return check_series(
        gross,
        turnover,
        variants=len(gross),
        sample_start=datetime.date(2020, 1, 1),
        sample_end=datetime.date(2020, 12, 31),
    )


TWO = make(
    {1: {D1: 0.01, D2: -0.02, D3: 0.03}, 2: {D1: 0.0, D2: 1 / 3, D3: -0.5}},
    {1: {D1: 0.5, D2: 1.0, D3: 0.0}, 2: {D1: 0.0, D2: 1.0, D3: 2.0}},
)


class TestReadSeries:
    def test_round_trip(self) -> None:
        data = series_bytes(TWO)
        back = read_series(data)
        assert back.periods == TWO.periods
        assert back.gross[1] == TWO.gross[1]
        assert back.gross[2][1] == 0.3333333333  # the file's rounded value
        assert series_bytes(back) == data

    def test_intraday(self) -> None:
        early, late = "2020-01-02T09:00:00Z", "2020-01-02T09:00:00.500000Z"
        data = make({1: {early: 0.1, late: 0.2}}, {1: {early: 0, late: 0}})
        assert read_series(series_bytes(data)).periods == (early, late)

    @pytest.mark.parametrize(
        ("data", "message"),
        [
            pytest.param(b"", "line feed", id="empty"),
            pytest.param(HEADER, "no rows", id="header_only"),
            pytest.param(b"period,gross\n", "header", id="header"),
            pytest.param(
                HEADER + b"2020-01-02,1,0.0100000000,0.5000000000",
                "line feed",
                id="no_lf",
            ),
            pytest.param(
                HEADER.replace(b"\n", b"\r\n")
                + b"2020-01-02,1,0.0100000000,0.5000000000\r\n",
                "header",
                id="crlf",
            ),
            pytest.param(
                HEADER + b"2020-01-02,1,0.01,0.5\n", "canonical", id="decimals"
            ),
            pytest.param(
                HEADER + b"2020-01-02,1,-0.0000000000,0.0000000000\n",
                "canonical",
                id="negative_zero",
            ),
            pytest.param(
                HEADER + b"2020-01-02,1,1e-2,0.5000000000\n", "canonical", id="exponent"
            ),
            pytest.param(
                HEADER + b"2020-01-02,1,abc,0.5000000000\n", "not a number", id="text"
            ),
            pytest.param(HEADER + b"2020-01-02,1,nan,0.5000000000\n", "nan", id="nan"),
            pytest.param(
                HEADER + b"2020-01-02,01,0.0100000000,0.5000000000\n",
                "malformed",
                id="variant_leading_zero",
            ),
            pytest.param(
                HEADER + b"2020-01-02,0,0.0100000000,0.5000000000\n",
                "malformed",
                id="variant_zero",
            ),
            pytest.param(
                HEADER + b"2020-01-02,2,0.0100000000,0.5000000000\n",
                "1 to K",
                id="variant_gap",
            ),
            pytest.param(
                HEADER + b"2020-01-02,1,0.0100000000\n", "malformed", id="three_fields"
            ),
            pytest.param(
                HEADER + b"2020-1-2,1,0.0100000000,0.5000000000\n",
                "series file",
                id="period_not_iso",
            ),
            pytest.param(
                HEADER
                + b"2020-01-03,1,0.0100000000,0.5000000000\n"
                + b"2020-01-02,1,0.0100000000,0.5000000000\n",
                "increasing",
                id="period_order",
            ),
            pytest.param(
                HEADER
                + b"2020-01-02,1,0.0100000000,0.5000000000\n"
                + b"2020-01-02,1,0.0100000000,0.5000000000\n",
                "increasing",
                id="period_twice",
            ),
            pytest.param(
                HEADER
                + b"2020-01-02,1,0.0100000000,0.5000000000\n"
                + b"2020-01-03T00:00:00Z,1,0.0100000000,0.5000000000\n",
                "mixes",
                id="mixed_kinds",
            ),
            pytest.param(
                HEADER
                + b"2020-01-02,1,0.0100000000,0.5000000000\n"
                + b"2020-01-03,2,0.0100000000,0.5000000000\n",
                "variant 1's periods",
                id="variant_off_grid",
            ),
            pytest.param(
                HEADER
                + b"2020-01-02,2,0.0100000000,0.5000000000\n"
                + b"2020-01-02,1,0.0100000000,0.5000000000\n",
                "canonical",
                id="variant_order",
            ),
            pytest.param(
                HEADER + b"2020-01-02,1,0.0100000000,-0.5000000000\n",
                "negative",
                id="negative_turnover",
            ),
            pytest.param(
                HEADER + b"2020-01-02,1,0.0100000000,0.5000000000\n\n",
                "malformed",
                id="blank_line",
            ),
            pytest.param(b"\xff\xfe", "UTF-8", id="not_utf8"),
        ],
    )
    def test_refused(self, data: bytes, message: str) -> None:
        with pytest.raises(SeriesFileError, match=message):
            read_series(data)


RF_HEADER = b"period,risk_free\n"


class TestReadRiskFree:
    def test_valid(self) -> None:
        data = RF_HEADER + b"2020-01-03,0.0002\n2020-01-02,0.0001\n"
        assert read_risk_free(data) == {D1: 0.0001, D2: 0.0002}

    def test_crlf_and_no_final_line_end(self) -> None:
        data = b"period,risk_free\r\n2020-01-02,0.0001\r\n2020-01-03,-0.00005"
        assert read_risk_free(data) == {D1: 0.0001, D2: -0.00005}

    def test_intraday(self) -> None:
        data = RF_HEADER + b"2020-01-02T09:00:00Z,0\n"
        assert read_risk_free(data) == {"2020-01-02T09:00:00Z": 0.0}

    @pytest.mark.parametrize(
        ("data", "message"),
        [
            pytest.param(b"", "first line", id="empty"),
            pytest.param(b"date,rate\n2020-01-02,0.1\n", "first line", id="header"),
            pytest.param(b"\xef\xbb\xbf" + RF_HEADER, "byte-order", id="bom"),
            pytest.param(RF_HEADER + b"2020-01-02,1e-4\n", "decimal", id="exponent"),
            pytest.param(RF_HEADER + b"2020-01-02,nan\n", "decimal", id="nan"),
            pytest.param(RF_HEADER + b"2020-01-02,inf\n", "decimal", id="inf"),
            pytest.param(RF_HEADER + b"2020-01-02,+0.1\n", "decimal", id="plus"),
            pytest.param(RF_HEADER + b"2020-01-02,.1\n", "decimal", id="no_lead_digit"),
            pytest.param(RF_HEADER + b"2020-01-02,\n", "decimal", id="empty_value"),
            pytest.param(RF_HEADER + b"2020-01-02, 0.1\n", "decimal", id="space"),
            pytest.param(RF_HEADER + b"2020-01-02,0.1,x\n", "expected", id="fields"),
            pytest.param(RF_HEADER + b"\n2020-01-02,0.1\n", "expected", id="blank"),
            pytest.param(RF_HEADER + b"2020-1-2,0.1\n", "risk-free", id="bad_date"),
            pytest.param(
                RF_HEADER + b"2020-01-02T09:00:00.000000Z,0.1\n",
                "canonical",
                id="not_canonical",
            ),
            pytest.param(
                RF_HEADER + b"2020-01-02,0.1\n2020-01-02,0.2\n", "twice", id="twice"
            ),
            pytest.param(
                RF_HEADER + b"2020-01-02,0.1\n2020-01-03T00:00:00Z,0.2\n",
                "mixes",
                id="mixed",
            ),
            pytest.param(
                RF_HEADER + b"2020-01-02," + b"9" * 400 + b"\n", "big", id="huge"
            ),
            pytest.param(b"\xff", "UTF-8", id="not_utf8"),
        ],
    )
    def test_refused(self, data: bytes, message: str) -> None:
        with pytest.raises(SeriesFileError, match=message):
            read_risk_free(data)

    def test_missing_period_is_not_zero(self) -> None:
        # N38: a missing period makes the statistic not computable, never zero.
        with pytest.raises(NotComputable, match=D2):
            risk_free_on([D1, D2], {D1: 0.0001})
        assert risk_free_on([D2, D1], {D1: 0.1, D2: 0.2}) == [0.2, 0.1]


def hand_stats(r: list[float]) -> dict[str, str]:
    """mean, std (T-1), Sharpe, skewness and kurtosis (divisor T) without bgl."""
    t = len(r)
    m = math.fsum(r) / t
    d = [x - m for x in r]
    s = math.sqrt(math.fsum(x * x for x in d) / (t - 1))
    m2 = math.fsum(x * x for x in d) / t
    m3 = math.fsum(x**3 for x in d) / t
    m4 = math.fsum(x**4 for x in d) / t
    return {
        "mean": decimal_text(m),
        "std": decimal_text(s),
        "sharpe": decimal_text(m / s),
        "skewness": decimal_text(m3 / m2**1.5),
        "kurtosis": decimal_text(m4 / m2**2),
    }


class TestCloseSummary:
    def test_n39_no_gate_table_uses_zero_cost(self) -> None:
        record = close_summary(TWO, cost_bps=None, risk_free=None)
        assert record["cost_bps"] is None
        assert record["risk_free"] == "none"
        first = record["statistics"][0]
        assert first["variant"] == 1
        assert first["T"] == "3"
        expected = hand_stats([0.01, -0.02, 0.03])
        for key, text in expected.items():
            # Same text, or one unit apart in the 12th digit (floats differ in
            # the last place between the two computations).
            assert abs(float(first[key]) - float(text)) <= abs(float(text)) * 1e-11
        assert first["not_computable"] is None

    def test_with_cost_and_risk_free(self) -> None:
        # Variant 1, c = 5 bps, f = 0.0001 each period:
        #   0.01 - 0.5 * 0.0005 - 0.0001 = 0.00965
        #   -0.02 - 1.0 * 0.0005 - 0.0001 = -0.0206
        #   0.03 - 0 - 0.0001 = 0.0299
        rf = {D1: 0.0001, D2: 0.0001, D3: 0.0001}
        record = close_summary(TWO, cost_bps=5.0, risk_free=rf)
        assert record["cost_bps"] == "5.0"
        assert record["risk_free"] == "file"
        first = record["statistics"][0]
        expected = hand_stats([0.00965, -0.0206, 0.0299])
        for key, text in expected.items():
            assert abs(float(first[key]) - float(text)) <= abs(float(text)) * 1e-11
        # Doubling the cost changes the mean by 0.5 * (1.5 / 3) bps exactly.
        doubled = close_summary(TWO, cost_bps=10.0, risk_free=rf)["statistics"][0]
        assert float(first["mean"]) - float(doubled["mean"]) == pytest.approx(
            0.0005 * 1.5 / 3, rel=1e-9
        )

    def test_uses_the_files_rounded_values(self) -> None:
        # The series file keeps 10 decimals: 0.123456789012 is stored as
        # 0.1234567890, and the mean is computed from that.
        data = make(
            {1: {D1: 0.123456789012, D2: 0.0, D3: 0.0}},
            {1: {D1: 0.0, D2: 0.0, D3: 0.0}},
        )
        record = close_summary(data, cost_bps=None, risk_free=None)
        assert record["statistics"][0]["mean"] == decimal_text(0.1234567890 / 3)
        assert record["statistics"][0]["mean"] != decimal_text(0.123456789012 / 3)

    def test_missing_risk_free_period(self) -> None:
        record = close_summary(TWO, cost_bps=5.0, risk_free={D1: 0.0, D2: 0.0})
        for item in record["statistics"]:
            assert item["mean"] is None
            assert item["sharpe"] is None
            assert "2020-01-06" in item["not_computable"]

    def test_constant_variant(self) -> None:
        data = make(
            {1: {D1: 0.01, D2: 0.01, D3: 0.01}, 2: {D1: 0.0, D2: 0.1, D3: -0.1}},
            {1: {D1: 0.0, D2: 0.0, D3: 0.0}, 2: {D1: 0.0, D2: 0.0, D3: 0.0}},
        )
        record = close_summary(data, cost_bps=None, risk_free=None)
        flat = record["statistics"][0]
        assert flat["mean"] == "0.01"
        assert flat["std"] == "0"
        assert flat["sharpe"] is None and flat["kurtosis"] is None
        assert "zero" in flat["not_computable"]
        assert record["statistics"][1]["sharpe"] == "0"

    def test_one_period(self) -> None:
        data = make({1: {D1: 0.01}}, {1: {D1: 0.0}})
        record = close_summary(data, cost_bps=None, risk_free=None)
        assert record["statistics"][0]["mean"] == "0.01"
        assert record["statistics"][0]["std"] is None
        assert record["statistics"][0]["not_computable"]

    def test_hashable_and_keeps_shape(self) -> None:
        record = close_summary(TWO, cost_bps=5.0, risk_free=None)
        canonical_json(record)  # no floats anywhere
        assert (record["variants"], record["periods"]) == (2, 3)
        assert (record["first"], record["last"]) == (D1, D3)

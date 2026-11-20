import datetime
from typing import Any

import pytest

from bgl.errors import RunError
from bgl.series import check_series, period_text, series_bytes

D1, D2 = "2020-01-02", "2020-01-03"
START, END = datetime.date(2020, 1, 1), datetime.date(2020, 12, 31)


def check(gross: Any, turnover: Any, variants: int = 1, **kwargs: Any) -> Any:
    kwargs.setdefault("sample_start", START)
    kwargs.setdefault("sample_end", END)
    return check_series(gross, turnover, variants=variants, **kwargs)


def one(values: dict[Any, Any]) -> dict[int, dict[Any, Any]]:
    return {1: values}


class TestCheckSeries:
    def test_valid_two_variants(self) -> None:
        data = check(
            {1: {D2: 0.02, D1: 0.01}, 2: {D1: 0.0, D2: 0.03}},
            {1: {D1: 0.5, D2: 0.1}, 2: {D1: 0, D2: 1}},
            variants=2,
        )
        assert data.periods == (D1, D2)
        assert data.gross[1] == (0.01, 0.02)
        assert data.variants == (1, 2)

    def test_list_form_numbers_variants_from_one(self) -> None:
        data = check([{D1: 0.1}, {D1: 0.2}], [{D1: 0}, {D1: 0}], variants=2)
        assert data.gross[2] == (0.2,)

    def test_list_of_pairs_and_date_objects(self) -> None:
        day = datetime.date(2020, 1, 2)
        data = check(one([(day, 0.1)]), one([(day, 0.0)]))
        assert data.periods == (D1,)

    @pytest.mark.parametrize(
        ("gross", "turnover", "variants", "message"),
        [
            pytest.param(one({D1: 0.1}), one({D1: 0}), 2, "declared 2", id="too_few"),
            pytest.param(
                {1: {D1: 0.1}, 2: {D1: 0.1}},
                {1: {D1: 0}, 2: {D1: 0}},
                1,
                "declared 1",
                id="too_many",
            ),
            pytest.param(
                {0: {D1: 0.1}}, {0: {D1: 0}}, 1, "declared 1", id="zero_based"
            ),
            pytest.param(
                {True: {D1: 0.1}}, one({D1: 0}), 1, "whole number", id="bool_variant"
            ),
            pytest.param(
                {"a": {D1: 0.1}}, one({D1: 0}), 1, "whole number", id="text_variant"
            ),
            pytest.param(
                one({D1: 0.1, D2: 0.1}),
                one({D1: 0}),
                1,
                "same periods",
                id="turnover_off_grid",
            ),
            pytest.param(
                {1: {D1: 0.1}, 2: {D2: 0.1}},
                {1: {D1: 0}, 2: {D2: 0}},
                2,
                "same periods as variant 1",
                id="variants_off_grid",
            ),
            pytest.param(one({}), one({}), 1, "empty", id="empty"),
            pytest.param(
                one({D1: float("nan")}), one({D1: 0}), 1, "nan", id="nan_return"
            ),
            pytest.param(
                one({D1: 0.1}),
                one({D1: float("inf")}),
                1,
                "nan and inf",
                id="inf_turnover",
            ),
            pytest.param(one({D1: "x"}), one({D1: 0}), 1, "not a number", id="text"),
            pytest.param(one({D1: True}), one({D1: 0}), 1, "not a number", id="bool"),
            pytest.param(
                one({D1: 0.1}), one({D1: -0.5}), 1, "negative", id="negative_turnover"
            ),
            pytest.param(
                one({"2019-12-31": 0.1}),
                one({"2019-12-31": 0}),
                1,
                "before the declared sample_start",
                id="before_sample",
            ),
            pytest.param(
                one({"2021-01-01": 0.1}),
                one({"2021-01-01": 0}),
                1,
                "after the declared sample_end",
                id="after_sample",
            ),
            pytest.param(
                one({D1: 0.1, "2020-01-03T10:00:00Z": 0.1}),
                one({D1: 0, "2020-01-03T10:00:00Z": 0}),
                1,
                "mixes dates and datetimes",
                id="mixed_kinds",
            ),
            pytest.param(
                one([(D1, 0.1), (datetime.date(2020, 1, 2), 0.2)]),
                one([(D1, 0), (D2, 0)]),
                1,
                "appears twice",
                id="duplicate_period",
            ),
            pytest.param(one(5), one({D1: 0}), 1, "must map each period", id="scalar"),
            pytest.param(5, one({D1: 0}), 1, "one series per variant", id="not_series"),
        ],
    )
    def test_refused(
        self, gross: Any, turnover: Any, variants: int, message: str
    ) -> None:
        with pytest.raises(RunError, match=message):
            check(gross, turnover, variants)

    def test_boundary_dates_are_inside_the_sample(self) -> None:
        check(
            one({"2020-01-01": 0.1, "2020-12-31": 0.1}),
            one({"2020-01-01": 0, "2020-12-31": 0}),
        )

    def test_pandas_frame_and_series(self) -> None:
        import pandas as pd  # dev group (N20): this test must run, never skip

        index = pd.date_range("2020-01-02", periods=3, freq="D")
        gross = pd.DataFrame({1: [0.1, 0.2, 0.3], 2: [0.0, 0.1, 0.2]}, index=index)
        turnover = pd.DataFrame({1: [0, 0, 0], 2: [1, 1, 1]}, index=index)
        data = check(gross, turnover, variants=2)
        assert data.periods == ("2020-01-02", "2020-01-03", "2020-01-04")
        assert data.gross[2] == (0.0, 0.1, 0.2)


class TestPeriodText:
    def test_date(self) -> None:
        assert period_text(datetime.date(2020, 1, 2))[0] == "2020-01-02"

    def test_aware_datetime_is_converted_to_utc(self) -> None:
        zone = datetime.timezone(datetime.timedelta(hours=2))
        moment = datetime.datetime(2020, 1, 2, 1, 30, tzinfo=zone)
        text, utc, is_datetime = period_text(moment)
        assert text == "2020-01-01T23:30:00Z"
        assert utc.date() == datetime.date(2020, 1, 1)
        assert is_datetime

    def test_naive_midnight_is_a_date(self) -> None:
        text, _, is_datetime = period_text(datetime.datetime(2020, 1, 2))
        assert (text, is_datetime) == ("2020-01-02", False)

    def test_naive_time_is_refused(self) -> None:
        with pytest.raises(RunError, match="no time zone"):
            period_text(datetime.datetime(2020, 1, 2, 9, 30))

    def test_utc_text_round_trips(self) -> None:
        assert period_text("2020-01-02T09:30:00Z")[0] == "2020-01-02T09:30:00Z"

    @pytest.mark.parametrize("bad", ["02/01/2020", "2020-01-02T09:30:00", "x", 5, None])
    def test_bad_period(self, bad: object) -> None:
        with pytest.raises(RunError):
            period_text(bad)


class TestSeriesBytes:
    def test_exact_bytes(self) -> None:
        data = check(
            {1: {D1: 0.01, D2: -0.02}, 2: {D1: 0, D2: 1 / 3}},
            {1: {D1: 0.5, D2: 0.1}, 2: {D1: 0, D2: 1}},
            variants=2,
        )
        assert series_bytes(data) == (
            b"period,variant,gross,turnover\n"
            b"2020-01-02,1,0.0100000000,0.5000000000\n"
            b"2020-01-03,1,-0.0200000000,0.1000000000\n"
            b"2020-01-02,2,0.0000000000,0.0000000000\n"
            b"2020-01-03,2,0.3333333333,1.0000000000\n"
        )

    def test_same_series_in_any_order_gives_same_bytes(self) -> None:
        first = check(one({D1: 0.1, D2: 0.2}), one({D1: 0, D2: 0}))
        second = check(one({D2: 0.2, D1: 0.1}), one({D2: 0, D1: 0}))
        assert series_bytes(first) == series_bytes(second)

    def test_negative_zero_has_one_spelling(self) -> None:
        data = check(one({D1: -0.0, D2: -1e-12}), one({D1: 0, D2: 0}))
        text = series_bytes(data).decode()
        assert "-0.0000000000" not in text

    def test_intraday_sorts_by_time_not_text(self) -> None:
        early, late = "2020-01-02T09:00:00Z", "2020-01-02T09:00:00.500000Z"
        data = check(one({late: 0.2, early: 0.1}), one({late: 0, early: 0}))
        assert data.periods == (early, late)
        assert data.gross[1] == (0.1, 0.2)

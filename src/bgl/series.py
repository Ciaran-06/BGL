"""Return series (run.returns): checking them and writing the series file.

The file is long format, period,variant,gross,turnover, sorted by variant and
then period, with fixed decimals, so the same series always gives the same
bytes on every platform.
"""

import dataclasses
import datetime
import math
import operator
from collections.abc import Iterable, Mapping
from typing import Any, Final

from bgl.errors import RunError

DECIMALS: Final[int] = 10  # fixed by format_version 1
HEADER: Final[str] = "period,variant,gross,turnover"


@dataclasses.dataclass(frozen=True)
class SeriesData:
    """Checked series: periods shared by every variant, and each variant's values."""

    periods: tuple[str, ...]
    gross: Mapping[int, tuple[float, ...]]
    turnover: Mapping[int, tuple[float, ...]]

    @property
    def variants(self) -> tuple[int, ...]:
        return tuple(sorted(self.gross))


def _pairs(series: object, what: str) -> Iterable[tuple[object, object]]:
    """(period, value) pairs from a mapping, a pandas Series, or a list of pairs."""
    items = getattr(series, "items", None)
    if callable(items):
        return list(items())
    if isinstance(series, (list, tuple)):
        return list(series)
    raise RunError(
        f"{what} must map each period to a value (a dict, a pandas Series or a "
        f"list of pairs), got {type(series).__name__}"
    )


def _by_variant(data: object, what: str) -> dict[int, object]:
    """Variant number -> series, from a mapping or a list (first item is variant 1).

    [DECISION] Variants are numbered 1 to the declared count, matching
    selects = {"variant": k}. A list's first series is variant 1.
    """
    items = getattr(data, "items", None)
    if callable(items):
        result: dict[int, object] = {}
        for key, series in items():
            if isinstance(key, bool):
                raise RunError(f"{what}: variant {key!r} is not a whole number")
            try:
                variant = operator.index(key)
            except TypeError:
                raise RunError(
                    f"{what}: variant {key!r} is not a whole number"
                ) from None
            if variant in result:
                raise RunError(f"{what}: variant {variant} appears twice")
            result[variant] = series
        return result
    if isinstance(data, (list, tuple)):
        return {index + 1: series for index, series in enumerate(data)}
    raise RunError(
        f"{what} must hold one series per variant (a dict keyed by variant "
        f"number, a list, or a pandas DataFrame), got {type(data).__name__}"
    )


def period_text(period: object) -> tuple[str, datetime.datetime, bool]:
    """(canonical text, the moment as naive UTC, is_datetime) for one period.

    Dates are written as YYYY-MM-DD, datetimes as UTC ending in Z.

    [DECISION] A datetime without a time zone is accepted only at exactly
    midnight, and is then read as a date; that is what a daily pandas index
    holds. Any other time needs a time zone, so nothing is guessed.
    """
    if isinstance(period, str):
        try:
            if len(period) == 10:
                period = datetime.date.fromisoformat(period)
            elif period.endswith("Z"):
                period = datetime.datetime.fromisoformat(period[:-1] + "+00:00")
            else:
                raise ValueError
        except ValueError:
            raise RunError(
                f"period {period!r} is not YYYY-MM-DD or a UTC time ending in Z"
            ) from None
    if isinstance(period, datetime.datetime):
        if period.tzinfo is None:
            if period.time() != datetime.time(0):
                raise RunError(
                    f"period {period!r} has a time but no time zone; give UTC"
                )
            period = period.date()
        else:
            utc = period.astimezone(datetime.UTC).replace(tzinfo=None)
            spec = "microseconds" if utc.microsecond else "seconds"
            return utc.isoformat(timespec=spec) + "Z", utc, True
    if isinstance(period, datetime.date):
        moment = datetime.datetime.combine(period, datetime.time(0))
        return period.isoformat(), moment, False
    raise RunError(f"period {period!r} is not a date or a datetime")


def _number(value: object, what: str) -> float:
    if isinstance(value, bool):
        raise RunError(f"{what}: {value!r} is not a number")
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise RunError(f"{what}: {value!r} is not a number") from None
    if not math.isfinite(number):
        raise RunError(f"{what}: nan and inf are not returns")
    return number


def _one_series(
    series: object, what: str
) -> tuple[list[str], list[datetime.date], list[bool], list[float]]:
    """One variant's periods (text), their dates, kinds and values, in time order."""
    rows = []
    for pair in _pairs(series, what):
        if not isinstance(pair, (tuple, list)) or len(pair) != 2:
            raise RunError(f"{what}: expected (period, value) pairs")
        text, moment, is_datetime = period_text(pair[0])
        value = _number(pair[1], f"{what} at {text}")
        rows.append((moment, text, is_datetime, value))
    # Sort by the moment, not the text: "…00.5Z" sorts before "…00Z" as text.
    rows.sort(key=lambda row: row[0])
    for earlier, later in zip(rows, rows[1:], strict=False):
        if earlier[0] == later[0]:
            raise RunError(f"{what}: period {later[1]} appears twice")
    return (
        [row[1] for row in rows],
        [row[0].date() for row in rows],
        [row[2] for row in rows],
        [row[3] for row in rows],
    )


def check_series(
    gross: object,
    turnover: object,
    *,
    variants: int,
    sample_start: datetime.date | None,
    sample_end: datetime.date | None,
) -> SeriesData:
    """Check a run's series and return them in canonical order.

    Refuses a variant count different from the declared one, variants on
    different periods, mixed dates and datetimes, periods outside the declared
    sample, and anything that is not a finite number.

    [DECISION] "On the study's period grid" is enforced here as: every variant
    of the run has exactly the same periods, unique, all dates or all UTC
    datetimes, inside the declared sample. Whether different runs share one
    grid is checked where it matters, when PBO lines the trials up.
    """
    gross_by = _by_variant(gross, "gross")
    turnover_by = _by_variant(turnover, "turnover")
    expected = list(range(1, variants + 1))
    for name, found in (("gross", gross_by), ("turnover", turnover_by)):
        if sorted(found) != expected:
            raise RunError(
                f"{name} holds variants {sorted(found)}, but the run declared "
                f"{variants} (numbered 1 to {variants})"
            )
    periods: list[str] | None = None
    gross_values: dict[int, tuple[float, ...]] = {}
    turnover_values: dict[int, tuple[float, ...]] = {}
    for variant in expected:
        texts, days, kinds, values = _one_series(gross_by[variant], f"gross[{variant}]")
        t_texts, _, _, t_values = _one_series(
            turnover_by[variant], f"turnover[{variant}]"
        )
        if not texts:
            raise RunError(f"gross[{variant}] is empty")
        if t_texts != texts:
            raise RunError(
                f"turnover[{variant}] is not on the same periods as gross[{variant}]"
            )
        if len(set(kinds)) != 1:
            raise RunError(f"gross[{variant}] mixes dates and datetimes")
        if periods is None:
            periods = texts
            for day in days:
                if sample_start is not None and day < sample_start:
                    raise RunError(
                        f"period {day} is before the declared sample_start "
                        f"{sample_start}"
                    )
                if sample_end is not None and day > sample_end:
                    raise RunError(
                        f"period {day} is after the declared sample_end {sample_end}"
                    )
        elif texts != periods:
            raise RunError(f"variant {variant} is not on the same periods as variant 1")
        if any(value < 0 for value in t_values):
            raise RunError(f"turnover[{variant}] has a negative value")
        gross_values[variant] = tuple(values)
        turnover_values[variant] = tuple(t_values)
    assert periods is not None
    return SeriesData(tuple(periods), gross_values, turnover_values)


def _fixed(value: float) -> str:
    text = f"{value:.{DECIMALS}f}"
    # -0.0000000000 and 0.0000000000 are the same return; write one spelling.
    return text[1:] if text.startswith("-") and float(text) == 0 else text


def series_bytes(data: SeriesData) -> bytes:
    """The series file's exact bytes: UTF-8, LF line endings, fixed decimals."""
    lines = [HEADER]
    for variant in data.variants:
        gross, turnover = data.gross[variant], data.turnover[variant]
        for index, period in enumerate(data.periods):
            lines.append(
                f"{period},{variant},{_fixed(gross[index])},{_fixed(turnover[index])}"
            )
    return ("\n".join(lines) + "\n").encode("utf-8")


def summary(data: SeriesData) -> dict[str, Any]:
    """What the close file records about the series until build step 11.

    [DECISION] The spec's per-variant statistics (mean, Sharpe, skewness,
    kurtosis on net excess returns) need the cost model, the risk-free file and
    numpy, which arrive with the statistics step. Until then the close file
    records only the series' shape; the series file itself holds every value.
    """
    return {
        "variants": len(data.variants),
        "periods": len(data.periods),
        "first": data.periods[0],
        "last": data.periods[-1],
    }

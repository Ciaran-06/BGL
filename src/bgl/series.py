"""Return series (run.returns): checking them and writing the series file.

The file is long format, period,variant,gross,turnover, sorted by variant and
then period, with fixed decimals, so the same series always gives the same
bytes on every platform.

Also here: read_series (the file back into values, refusing any other
spelling), read_risk_free (N38) and close_summary, the close file's
per-variant statistics (N39).
"""

import dataclasses
import datetime
import math
import operator
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Final

from bgl.config import canonical_number
from bgl.errors import BglError, RunError
from bgl.stats import NotComputable, moments
from bgl.stats.moments import net_excess
from bgl.stats.rounding import decimal_text

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
    """The series' shape, which the close file records today.

    [DECISION] Two pieces of N38/N39 are deferred, because run.py is outside
    build step 11's files (and step 8a is changing it):
    1. run.py still calls this. close_summary below is the full N39 record
       (per-variant statistics on net excess returns); switching run.py to it
       needs the study's cost model and risk-free file at close.
    2. N38's "each open manifest records the risk-free file's hash" is not
       implemented; the gate's listing of distinct hashes needs it.
    Both belong to step 12 (the gate), or to whichever step next changes
    run.py and manifest.py, if that comes first. Until then the series file
    itself holds every value.
    """
    return {
        "variants": len(data.variants),
        "periods": len(data.periods),
        "first": data.periods[0],
        "last": data.periods[-1],
    }


class SeriesFileError(BglError):
    """A return series file or risk-free file is not in its exact format."""


_VARIANT_TEXT: Final[re.Pattern[str]] = re.compile(r"[1-9][0-9]*")


def read_series(data: bytes) -> SeriesData:
    """Read a return series file's bytes back into a SeriesData.

    The file must be exactly what series_bytes writes: the header, rows sorted
    by variant and then period, variants numbered 1 to K, every variant on the
    same periods in time order, canonical period texts, fixed decimals, LF line
    endings. Anything else is refused, so a file has one valid spelling.
    """
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise SeriesFileError("the series file is not UTF-8") from None
    if not text.endswith("\n"):
        raise SeriesFileError("the series file does not end with a line feed")
    lines = text[:-1].split("\n")
    if lines[0] != HEADER:
        raise SeriesFileError(f"the series file's header is not {HEADER!r}")
    rows: dict[int, list[tuple[str, float, float]]] = {}
    for number, line in enumerate(lines[1:], start=2):
        fields = line.split(",")
        if len(fields) != 4 or not _VARIANT_TEXT.fullmatch(fields[1]):
            raise SeriesFileError(f"series file line {number} is malformed")
        try:
            gross, turnover = float(fields[2]), float(fields[3])
        except ValueError:
            raise SeriesFileError(
                f"series file line {number} holds a value that is not a number"
            ) from None
        rows.setdefault(int(fields[1]), []).append((fields[0], gross, turnover))
    if not rows:
        raise SeriesFileError("the series file holds no rows")
    if sorted(rows) != list(range(1, len(rows) + 1)):
        raise SeriesFileError("the series file's variants are not numbered 1 to K")
    periods = tuple(period for period, _, _ in rows[1])
    previous: datetime.datetime | None = None
    kinds = set()
    for period in periods:
        try:
            canonical, moment, is_datetime = period_text(period)
        except RunError as exc:
            raise SeriesFileError(f"series file: {exc}") from None
        if canonical != period:
            raise SeriesFileError(f"series file period {period!r} is not canonical")
        if previous is not None and moment <= previous:
            raise SeriesFileError(
                f"series file periods are not in strictly increasing order at {period}"
            )
        previous = moment
        kinds.add(is_datetime)
    if len(kinds) != 1:
        raise SeriesFileError("the series file mixes dates and datetimes")
    gross_by: dict[int, tuple[float, ...]] = {}
    turnover_by: dict[int, tuple[float, ...]] = {}
    for variant, values in rows.items():
        if tuple(period for period, _, _ in values) != periods:
            raise SeriesFileError(
                f"series file variant {variant} is not on variant 1's periods"
            )
        if any(not math.isfinite(g) or not math.isfinite(t) for _, g, t in values):
            raise SeriesFileError(f"series file variant {variant} holds nan or inf")
        if any(t < 0 for _, _, t in values):
            raise SeriesFileError(
                f"series file variant {variant} has negative turnover"
            )
        gross_by[variant] = tuple(g for _, g, _ in values)
        turnover_by[variant] = tuple(t for _, _, t in values)
    result = SeriesData(periods, gross_by, turnover_by)
    if series_bytes(result) != data:
        raise SeriesFileError(
            "the series file is not in canonical form (row order, decimals or "
            "spelling differ from what BGL writes)"
        )
    return result


RISK_FREE_HEADER: Final[str] = "period,risk_free"
_DECIMAL_TEXT: Final[re.Pattern[str]] = re.compile(r"-?[0-9]+(\.[0-9]+)?")


def read_risk_free(data: bytes) -> dict[str, float]:
    """Read a risk-free file (N38): CSV period,risk_free, decimal text,
    per-period returns. Returns each canonical period text's value.

    [DECISION] N38 Rows may come in any order. Lines end in LF or CRLF (the
    file is hashed as bytes, so line endings cannot change evidence); a final
    line ending is optional. Refused: a byte-order mark, blank lines, a period
    in non-canonical form (dates as YYYY-MM-DD, times in UTC ending in Z), a
    period given twice, mixed dates and datetimes, and any value that is not
    plain decimal text (no exponent, no sign other than a leading minus, no
    nan or inf).
    """
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise SeriesFileError("the risk-free file is not UTF-8") from None
    if text.startswith("\ufeff"):
        raise SeriesFileError("the risk-free file starts with a byte-order mark")
    lines = text.split("\n")
    if lines[-1] == "":
        lines.pop()
    lines = [line[:-1] if line.endswith("\r") else line for line in lines]
    if not lines or lines[0] != RISK_FREE_HEADER:
        raise SeriesFileError(
            f"the risk-free file's first line is not {RISK_FREE_HEADER!r}"
        )
    values: dict[str, float] = {}
    kinds = set()
    for number, line in enumerate(lines[1:], start=2):
        fields = line.split(",")
        if len(fields) != 2:
            raise SeriesFileError(
                f"risk-free file line {number}: expected period,risk_free"
            )
        period, value = fields
        try:
            canonical, _, is_datetime = period_text(period)
        except RunError as exc:
            raise SeriesFileError(f"risk-free file line {number}: {exc}") from None
        if canonical != period:
            raise SeriesFileError(
                f"risk-free file line {number}: period {period!r} is not canonical "
                f"(write {canonical!r})"
            )
        if not _DECIMAL_TEXT.fullmatch(value):
            raise SeriesFileError(
                f"risk-free file line {number}: {value!r} is not decimal text"
            )
        if period in values:
            raise SeriesFileError(
                f"risk-free file line {number}: period {period} appears twice"
            )
        number_value = float(value)
        if not math.isfinite(number_value):
            raise SeriesFileError(f"risk-free file line {number}: {value!r} is too big")
        values[period] = number_value
        kinds.add(is_datetime)
    if len(kinds) > 1:
        raise SeriesFileError("the risk-free file mixes dates and datetimes")
    return values


def risk_free_on(periods: Sequence[str], table: Mapping[str, float]) -> list[float]:
    """The risk-free return for each period, in order (N38).

    A period missing from the file makes every statistic that needs it not
    computable; it is never read as zero.
    """
    missing = [period for period in periods if period not in table]
    if missing:
        raise NotComputable(
            f"the risk-free file has no value for {len(missing)} period(s), "
            f"first {missing[0]} (N38)"
        )
    return [table[period] for period in periods]


def close_summary(
    data: SeriesData,
    *,
    cost_bps: float | None,
    risk_free: Mapping[str, float] | None,
) -> dict[str, Any]:
    """The close file's "series" record: shape and per-variant statistics.

    cost_bps is the locked cost model's value, or None for a study with no
    gate table (N39: computed with c = 0 and recorded as null). risk_free is
    the parsed risk-free file, or None for risk_free = "none". Both are
    required arguments, so neither can be left out by accident.

    N39: the statistics come from the series file's rounded values (the
    floats read back from series_bytes), so verify can recompute them from
    the file alone. Each value is N42 text (12 significant digits).

    [DECISION] N39 Field names: "T", "mean", "std", "sharpe", "skewness",
    "kurtosis", all as strings as the spec's close file says, plus
    "not_computable" (null, or why some are null). The record also holds the
    cost_bps used and whether a risk-free file was subtracted.
    """
    stored = read_series(series_bytes(data))
    reason: str | None = None
    rf: list[float] | None = None
    if risk_free is not None:
        try:
            rf = risk_free_on(stored.periods, risk_free)
        except NotComputable as exc:
            reason = str(exc)
    statistics = []
    for variant in stored.variants:
        record: dict[str, Any] = {
            "variant": variant,
            "T": str(len(stored.periods)),
            "mean": None,
            "std": None,
            "sharpe": None,
            "skewness": None,
            "kurtosis": None,
            "not_computable": reason,
        }
        if reason is None:
            r = net_excess(
                stored.gross[variant],
                stored.turnover[variant],
                cost_bps=0.0 if cost_bps is None else cost_bps,
                risk_free=rf,
            )
            record["mean"] = decimal_text(moments.mean(r))
            try:
                record["std"] = decimal_text(moments.std(r))
                values = moments.describe(r)
            except NotComputable as exc:
                record["not_computable"] = str(exc)
            else:
                record["sharpe"] = decimal_text(values.sharpe)
                record["skewness"] = decimal_text(values.skewness)
                record["kurtosis"] = decimal_text(values.kurtosis)
        statistics.append(record)
    return {
        **summary(stored),
        "cost_bps": None if cost_bps is None else canonical_number(cost_bps),
        "risk_free": "none" if risk_free is None else "file",
        "statistics": statistics,
    }

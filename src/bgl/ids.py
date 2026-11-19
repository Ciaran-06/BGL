"""Run IDs (R16): <prefix>-<letter><counter>-<CODE>, for example AL-R007-CG1."""

import re
from collections.abc import Iterable
from typing import Final

# The letter says what kind of row sequence the ID belongs to.
LETTER_FOR_TYPE: Final[dict[str, str]] = {
    "diagnostic": "R",
    "confirmatory": "R",
    "exploratory": "X",
    "data": "D",
    "replay": "P",
}
REFUSED_LETTER: Final[str] = "F"
DRY_LETTER: Final[str] = "T"
IMPORTED_LETTER: Final[str] = "I"

_RUN_ID: Final[re.Pattern[str]] = re.compile(
    r"([A-Z]{2,4})-([RXDPFTI])(\d{3,})-([A-Z][A-Z0-9]{1,5})"
)
# [DECISION] Counters are zero-padded to at least 3 digits and grow past 999.
_MIN_DIGITS: Final[int] = 3


def format_run_id(prefix: str, letter: str, counter: int, code: str) -> str:
    return f"{prefix}-{letter}{counter:0{_MIN_DIGITS}d}-{code}"


def parse_run_id(run_id: object) -> tuple[str, str, int, str] | None:
    """(prefix, letter, counter, code), or None if run_id is not a run ID."""
    if not isinstance(run_id, str):
        return None
    match = _RUN_ID.fullmatch(run_id)
    if match is None:
        return None
    prefix, letter, digits, code = match.groups()
    return prefix, letter, int(digits), code


def next_run_id(prefix: str, letter: str, code: str, used: Iterable[object]) -> str:
    """The next unused ID in the (prefix, letter, code) sequence.

    used holds every ID that must never be reused: the IDs in the writer's own
    chain and the IDs of files already in runs_dir (orphans included, R13).
    Anything in used that is not an ID of this sequence is ignored.
    """
    highest = 0
    for candidate in used:
        parsed = parse_run_id(candidate)
        if parsed is not None and (parsed[0], parsed[1], parsed[3]) == (
            prefix,
            letter,
            code,
        ):
            highest = max(highest, parsed[2])
    return format_run_id(prefix, letter, highest + 1, code)

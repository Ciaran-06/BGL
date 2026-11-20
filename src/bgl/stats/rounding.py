"""N42: decision statistics are rounded to 12 significant digits, stored as that
text, and compared as that value.

Floats are not bit-identical across systems (docs/PLAN.md, section 6, item 9);
twelve significant digits absorbs the last-place differences, so every machine
stores the same text and reaches the same verdict.
"""

import math
from decimal import ROUND_HALF_EVEN, Context, Decimal
from typing import Final

SIGNIFICANT_DIGITS: Final[int] = 12
_CONTEXT: Final[Context] = Context(prec=SIGNIFICANT_DIGITS, rounding=ROUND_HALF_EVEN)


def rounded(value: float) -> Decimal:
    """The float's exact binary value rounded to 12 significant digits.

    [DECISION] N42 Rounding is half-even on the float's exact value (not on
    its shortest repr), so the result never depends on how a float prints.
    """
    if not math.isfinite(value):
        raise ValueError(f"only finite values are rounded, got {value!r}")
    result = _CONTEXT.plus(Decimal(value))
    return result if result else Decimal(0)  # one zero, never -0


def decimal_text(value: float) -> str:
    """The stored text: 12 significant digits, plain notation, no exponent,
    trailing zeros removed, "0" for zero (for example "0.900421337171").

    [DECISION] N42 Plain notation without trailing zeros gives each value one
    spelling, like C19's canonical decimals.
    """
    number = rounded(value).normalize(_CONTEXT)
    return format(number, "f")


def text_value(text: str) -> Decimal:
    """The value a stored text stands for, for comparison (N42)."""
    return Decimal(text)

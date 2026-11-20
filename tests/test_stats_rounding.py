from decimal import Decimal

import pytest

from bgl.stats.rounding import decimal_text, rounded, text_value


class TestDecimalText:
    @pytest.mark.parametrize(
        ("value", "text"),
        [
            pytest.param(0.9003968344493904, "0.900396834449", id="twelve_digits"),
            pytest.param(0.5, "0.5", id="trailing_zeros_removed"),
            pytest.param(0.0, "0", id="zero"),
            pytest.param(-0.0, "0", id="negative_zero"),
            pytest.param(-1.25, "-1.25", id="negative"),
            pytest.param(1e-20, "0.00000000000000000001", id="no_exponent_small"),
            pytest.param(123456789012345.0, "123456789012000", id="no_exponent_big"),
            pytest.param(100.0, "100", id="whole"),
            # Exactly representable ties round half to even.
            pytest.param(1000000000005.0, "1000000000000", id="tie_down_to_even"),
            pytest.param(1000000000015.0, "1000000000020", id="tie_up_to_even"),
        ],
    )
    def test_text(self, value: float, text: str) -> None:
        assert decimal_text(value) == text

    def test_rounds_exact_binary_value(self) -> None:
        # 0.1 is 0.1000000000000000055511151231257827... in binary; twelve
        # significant digits give 0.1 exactly.
        assert rounded(0.1) == Decimal("0.100000000000")

    def test_rounds_exact_value_not_repr(self) -> None:
        # repr is "0.9500000000005", a tie that half-even would round to
        # 0.95; the float's exact value is 0.95000000000050000004137..., just
        # above the tie, so it rounds up.
        assert repr(0.9500000000005) == "0.9500000000005"
        assert decimal_text(0.9500000000005) == "0.950000000001"

    def test_twelve_significant_digits(self) -> None:
        text = decimal_text(2.0 / 3.0)
        assert text == "0.666666666667"
        assert len(text.replace("0.", "", 1)) == 12

    @pytest.mark.parametrize("value", [float("nan"), float("inf")])
    def test_non_finite_refused(self, value: float) -> None:
        with pytest.raises(ValueError):
            decimal_text(value)

    def test_value_of_text(self) -> None:
        assert text_value(decimal_text(1 / 7)) == Decimal("0.142857142857")

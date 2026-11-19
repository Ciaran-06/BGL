import datetime
import hashlib
import types
from typing import Any

import pytest

from bgl.canonical import (
    GENESIS_HASH,
    HASH_HEX,
    canonical_bytes,
    canonical_json,
    line_bytes,
    row_hash,
)


class TestCanonicalBytes:
    def test_known_bytes(self) -> None:
        assert canonical_bytes({"b": 1, "a": "é"}) == b'{"a":"\xc3\xa9","b":1}'

    def test_key_order_does_not_matter(self) -> None:
        assert canonical_bytes({"a": 1, "b": 2}) == canonical_bytes({"b": 2, "a": 1})

    def test_hash_and_sig_are_left_out(self) -> None:
        assert canonical_bytes({"a": 1, "hash": "x", "sig": "y"}) == b'{"a":1}'

    def test_nested_keys_are_sorted(self) -> None:
        assert canonical_bytes({"m": {"z": 1, "a": [2, {"y": 3, "b": 4}]}}) == (
            b'{"m":{"a":[2,{"b":4,"y":3}],"z":1}}'
        )

    def test_bool_and_null(self) -> None:
        assert canonical_bytes({"x": True, "y": None}) == b'{"x":true,"y":null}'

    def test_tuple_is_written_as_list(self) -> None:
        assert canonical_bytes({"t": ("a", 1)}) == canonical_bytes({"t": ["a", 1]})

    def test_read_only_top_level_is_accepted(self) -> None:
        row = types.MappingProxyType({"a": 1})
        assert canonical_bytes(row) == b'{"a":1}'

    def test_does_not_change_the_row(self) -> None:
        row = {"a": 1, "hash": "x", "sig": "y"}
        canonical_bytes(row)
        assert row == {"a": 1, "hash": "x", "sig": "y"}

    @pytest.mark.parametrize(
        ("row", "path"),
        [
            pytest.param({"x": 0.5}, "row.x", id="top_level_float"),
            pytest.param(
                {"lock": {"gate": {"cost_bps": 5.0}}},
                "row.lock.gate.cost_bps",
                id="nested_float",
            ),
            pytest.param({"claims": ["a", 1.5]}, "row.claims[1]", id="float_in_list"),
            pytest.param({"x": float("nan")}, "row.x", id="nan"),
            pytest.param({"x": datetime.date(2025, 1, 1)}, "row.x", id="date"),
            pytest.param({"x": b"x"}, "row.x", id="bytes"),
            pytest.param({"x": {1, 2}}, "row.x", id="set"),
            pytest.param(
                {"x": types.MappingProxyType({})}, "row.x", id="read_only_nested"
            ),
            pytest.param({"m": {1: "a"}}, "row.m", id="non_string_key"),
        ],
    )
    def test_refused(self, row: dict[str, Any], path: str) -> None:
        with pytest.raises(ValueError, match=path.replace("[", r"\[")):
            canonical_bytes(row)

    def test_lone_surrogate_is_refused(self) -> None:
        with pytest.raises(ValueError, match="Unicode"):
            canonical_bytes({"s": "\ud800"})


class TestRowHash:
    def test_known_hash(self) -> None:
        assert row_hash({"b": 1, "a": "é"}) == (
            "aa58fba8483623bed37c1b02edfccbdd9a53123837c20bfa4cb4049993a2872e"
        )

    def test_empty_row(self) -> None:
        assert row_hash({}) == hashlib.sha256(b"{}").hexdigest()

    def test_hash_and_sig_do_not_change_it(self) -> None:
        assert row_hash({"a": 1}) == row_hash({"a": 1, "hash": "h", "sig": "s"})

    def test_other_fields_do(self) -> None:
        assert row_hash({"a": 1}) != row_hash({"a": 2})

    def test_is_lowercase_hex(self) -> None:
        assert HASH_HEX.fullmatch(row_hash({"a": 1}))


class TestCanonicalJson:
    def test_keeps_hash_and_sig(self) -> None:
        assert canonical_json({"hash": "h", "a": 1}) == b'{"a":1,"hash":"h"}'

    def test_float_refused(self) -> None:
        with pytest.raises(ValueError, match=r"value\.x"):
            canonical_json({"x": 1.0})


class TestLineBytes:
    def test_full_row_and_newline(self) -> None:
        assert line_bytes({"b": 1, "hash": "h", "sig": "s"}) == (
            b'{"b":1,"hash":"h","sig":"s"}\n'
        )

    def test_float_refused(self) -> None:
        with pytest.raises(ValueError):
            line_bytes({"x": 1.5})


def test_genesis_is_64_zeros() -> None:
    assert GENESIS_HASH == "0" * 64
    assert HASH_HEX.fullmatch(GENESIS_HASH)

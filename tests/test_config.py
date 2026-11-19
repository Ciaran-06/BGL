import base64
import datetime
import json
import math
import os
import sys
import types
from pathlib import Path
from typing import Any

import pytest
from helpers import (
    FULL_VALID,
    MINIMAL_VALID,
    SECOND_STUDY,
    assert_has_error,
    assert_no_error,
    load_error,
    replace,
    write,
    write_bytes,
)

from bgl.config import (
    Config,
    ConfigError,
    ConfigErrorGroup,
    ErrorCollector,
    Mode,
    canonical_number,
    canonical_registry,
    canonical_study,
    check_https_url,
    check_https_url_list,
    check_mode,
    check_number,
    check_path,
    check_path_list,
    check_placeholder,
    check_project,
    check_public_key,
    check_registry,
    check_string,
    check_table,
    check_unknown_keys,
    check_whole_number,
    check_witness,
    key_path,
    load_config,
    read_toml,
)


class TestReadToml:
    def test_c8_missing_file_points_to_bgl_init(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError, match="bgl init") as info:
            read_toml(tmp_path / "bgl.toml")
        assert info.value.rule == "C8"

    def test_c8_folder_instead_of_file(self, tmp_path: Path) -> None:
        folder_path = tmp_path / "bgl.toml"
        folder_path.mkdir()
        with pytest.raises(ConfigError) as info:
            read_toml(folder_path)
        assert info.value.rule == "C8"
        assert info.value.file == folder_path

    @pytest.mark.skipif(
        sys.platform.startswith("win") or os.geteuid() == 0,
        reason="file permissions cannot block reading on Windows or for root",
    )
    def test_c8_unreadable_file(self, tmp_path: Path) -> None:
        path = write(tmp_path, "x = 20\n")
        path.chmod(0o000)
        try:
            with pytest.raises(ConfigError) as info:
                read_toml(path)
            assert info.value.rule == "C8"
            assert info.value.file == path
        finally:
            path.chmod(0o644)

    def test_c9_invalid_utf8(self, tmp_path: Path) -> None:
        path = write_bytes(tmp_path, b'x="\xff"\n')
        with pytest.raises(ConfigError) as info:
            read_toml(path)
        assert info.value.rule == "C9"

    def test_c9_byte_order_mark(self, tmp_path: Path) -> None:
        path = write_bytes(tmp_path, b"\xef\xbb\xbfx = 20\n")
        with pytest.raises(ConfigError) as info:
            read_toml(path)
        assert info.value.rule == "C9"

    def test_c9_line_number_appears_once(self, tmp_path: Path) -> None:
        path = write(tmp_path, 'a = 1\nb = "two"\nc =\n')
        with pytest.raises(ConfigError) as info:
            read_toml(path)
        assert info.value.line == 3
        assert info.value.rule == "C9"
        assert str(info.value).count("line 3") == 1

    def test_c9_invalid_toml_reports_file_and_line(self, tmp_path: Path) -> None:
        path = write(tmp_path, '[project\nname = "x"\n')
        with pytest.raises(ConfigError) as info:
            read_toml(path)
        assert info.value.rule == "C9"
        assert info.value.line == 1
        assert "bgl.toml" in str(info.value)

    def test_rejects_non_path(self, tmp_path: Path) -> None:
        with pytest.raises(TypeError):
            read_toml(str(tmp_path / "bgl.toml"))

    def test_decimal_loads_as_float(self, tmp_path: Path) -> None:
        path = write(tmp_path, "x = 0.95\n")
        data = read_toml(path)
        assert type(data["x"]) is float
        assert data["x"] == 0.95

    def test_integer_stays_int(self, tmp_path: Path) -> None:
        path = write(tmp_path, "x = 20\n")
        data = read_toml(path)
        assert type(data["x"]) is int

    def test_nan_loads_without_error(self, tmp_path: Path) -> None:
        path = write(tmp_path, "x = nan\n")
        data = read_toml(path)
        assert type(data["x"]) is float
        assert math.isnan(data["x"])

    def test_empty_file_returns_empty_dict(self, tmp_path: Path) -> None:
        path = write(tmp_path, "")
        data = read_toml(path)
        assert data == {}


FILE = Path("bgl.toml")


def error(rule: str, key: str | None = None) -> ConfigError:
    return ConfigError(FILE, rule, "something", key=key, found=1)


class TestConfigErrorGroup:
    def test_holds_the_errors_given(self) -> None:
        first, second = error("C3"), error("C4")
        group = ConfigErrorGroup(FILE, [first, second])
        assert group.exceptions == (first, second)
        assert group.file == FILE

    def test_is_an_exception_group(self) -> None:
        assert isinstance(ConfigErrorGroup(FILE, [error("C3")]), ExceptionGroup)

    def test_subgroup_keeps_type(self) -> None:
        group = ConfigErrorGroup(FILE, [error("C3"), error("C4")])
        smaller = group.subgroup(
            lambda e: isinstance(e, ConfigError) and e.rule == "C3"
        )
        assert isinstance(smaller, ConfigErrorGroup)
        assert smaller.file == FILE
        assert [e.rule for e in smaller.exceptions] == ["C3"]
        assert smaller.message == "bgl.toml has 1 problem"

    def test_message_counts_problems(self) -> None:
        group = ConfigErrorGroup(FILE, [error("C3"), error("C4")])
        assert group.message == "bgl.toml has 2 problems"

    def test_empty_list_is_refused(self) -> None:
        with pytest.raises(ValueError):
            ConfigErrorGroup(FILE, [])

    def test_non_config_error_is_refused(self) -> None:
        with pytest.raises(TypeError):
            ConfigErrorGroup(FILE, [ValueError("x")])  # type: ignore[list-item]


class TestErrorCollector:
    def test_new_collector_has_no_errors(self) -> None:
        assert not ErrorCollector(FILE).has_errors()

    def test_raise_if_any_does_nothing_when_empty(self) -> None:
        ErrorCollector(FILE).raise_if_any()

    def test_added_error_carries_file_rule_key_and_found(self) -> None:
        collector = ErrorCollector(FILE)
        collector.add("C3", "a whole number", key="studies.alpha.budget", found=-1)
        [added] = collector.errors
        assert added.file == FILE
        assert added.rule == "C3"
        assert added.key == "studies.alpha.budget"
        assert added.found == -1
        assert added.expected == "a whole number"

    def test_raises_group_with_all_errors(self) -> None:
        collector = ErrorCollector(FILE)
        collector.add("C3", "a whole number", key="studies.alpha.budget", found=-1)
        collector.add("C4", "2 to 4 uppercase letters", key="studies.alpha.prefix")
        with pytest.raises(ConfigErrorGroup) as info:
            collector.raise_if_any()
        assert len(info.value.exceptions) == 2

    def test_keeps_order_added(self) -> None:
        collector = ErrorCollector(FILE)
        for rule in ("C4", "C1", "C3"):
            collector.add(rule, "something", key="k")
        with pytest.raises(ConfigErrorGroup) as info:
            collector.raise_if_any()
        assert [e.rule for e in info.value.exceptions] == ["C4", "C1", "C3"]

    def test_missing_value_shows_as_missing(self) -> None:
        collector = ErrorCollector(FILE)
        collector.add("C13", "a whole number", key="studies.alpha.prior_trials")
        assert "found missing" in str(collector.errors[0])


class TestLoadConfig:
    def test_minimal_valid_loads(self, tmp_path: Path) -> None:
        config = load_config(write(tmp_path, MINIMAL_VALID))
        assert config.project.name == "example_project"
        assert config.studies["alpha"].budget == 20


class TestC1ProjectTable:
    @pytest.mark.parametrize(
        "name",
        [
            pytest.param('""', id="empty"),
            pytest.param("5", id="not_a_string"),
        ],
    )
    def test_bad_name(self, tmp_path: Path, name: str) -> None:
        bad = replace(MINIMAL_VALID, 'name = "example_project"', f"name = {name}")
        assert_has_error(load_error(tmp_path, bad), "C1", "project.name")

    def test_missing_name(self, tmp_path: Path) -> None:
        bad = replace(MINIMAL_VALID, 'name = "example_project"\n', "")
        assert_has_error(load_error(tmp_path, bad), "C1", "project.name")

    def test_empty_file_gives_two_c1_errors(self, tmp_path: Path) -> None:
        found = load_error(tmp_path, "")
        assert_has_error(found, "C1", "project")
        assert_has_error(found, "C1", "studies")

    def test_empty_studies_table_is_valid(self, tmp_path: Path) -> None:
        text = MINIMAL_VALID.split("[studies.alpha]")[0] + "[studies]\n"
        load_config(write(tmp_path, text))


class TestC2Paths:
    @pytest.mark.parametrize(
        "ledger",
        [
            pytest.param('"/etc/ledger"', id="posix_absolute"),
            pytest.param('"C:/ledger"', id="windows_drive"),
            pytest.param('"../outside"', id="parent_escape"),
            pytest.param('"bgl/../../outside"', id="nested_escape"),
            pytest.param("'bgl\\ledger'", id="backslash"),
        ],
    )
    def test_bad_ledger_path(self, tmp_path: Path, ledger: str) -> None:
        bad = replace(MINIMAL_VALID, 'ledger = "bgl/ledger"', f"ledger = {ledger}")
        assert_has_error(load_error(tmp_path, bad), "C2", "project.ledger")


class TestC3Budget:
    @pytest.mark.parametrize(
        "value",
        [
            pytest.param("-1", id="negative"),
            pytest.param('"20"', id="string"),
            pytest.param("2.5", id="float"),
            pytest.param("true", id="boolean"),
        ],
    )
    def test_bad_budget(self, tmp_path: Path, value: str) -> None:
        bad = replace(MINIMAL_VALID, "budget = 20", f"budget = {value}")
        assert_has_error(load_error(tmp_path, bad), "C3", "studies.alpha.budget")

    def test_zero_is_valid(self, tmp_path: Path) -> None:
        load_config(
            write(tmp_path, replace(MINIMAL_VALID, "budget = 20", "budget = 0"))
        )


class TestC4Prefix:
    @pytest.mark.parametrize(
        "value",
        [
            pytest.param('"A"', id="too_short"),
            pytest.param('"al"', id="lowercase"),
            pytest.param('"ALPHA"', id="too_long"),
            pytest.param('"A1"', id="digit"),
            pytest.param('""', id="empty"),
            pytest.param('"AL\\n"', id="trailing_newline"),
        ],
    )
    def test_bad_prefix(self, tmp_path: Path, value: str) -> None:
        bad = replace(MINIMAL_VALID, 'prefix = "AL"', f"prefix = {value}")
        assert_has_error(load_error(tmp_path, bad), "C4", "studies.alpha.prefix")

    @pytest.mark.parametrize(
        "value",
        [pytest.param("AB", id="two_letters"), pytest.param("ABCD", id="four_letters")],
    )
    def test_boundary_prefix_is_valid(self, tmp_path: Path, value: str) -> None:
        good = replace(MINIMAL_VALID, 'prefix = "AL"', f'prefix = "{value}"')
        load_config(write(tmp_path, good))


class TestC5Claims:
    @pytest.mark.parametrize(
        ("value", "key"),
        [
            pytest.param(
                '["effect_exists", "effect_exists"]',
                "studies.alpha.claims[1]",
                id="duplicate",
            ),
            pytest.param('[""]', "studies.alpha.claims[0]", id="empty_string"),
            pytest.param('"effect_exists"', "studies.alpha.claims", id="not_a_list"),
            pytest.param("[1]", "studies.alpha.claims[0]", id="not_strings"),
            pytest.param('["Effect"]', "studies.alpha.claims[0]", id="upper_case"),
            pytest.param(
                '["a" ]'.replace('"a"', '"' + "a" * 33 + '"'),
                "studies.alpha.claims[0]",
                id="33_chars",
            ),
            pytest.param('["2x"]', "studies.alpha.claims[0]", id="starts_with_digit"),
        ],
    )
    def test_bad_claims(self, tmp_path: Path, value: str, key: str) -> None:
        bad = replace(MINIMAL_VALID, 'claims = ["effect_exists"]', f"claims = {value}")
        assert_has_error(load_error(tmp_path, bad), "C5", key)

    def test_empty_list_is_valid(self, tmp_path: Path) -> None:
        good = replace(MINIMAL_VALID, 'claims = ["effect_exists"]', "claims = []")
        load_config(write(tmp_path, good))


class TestC6UniquePrefixes:
    def test_shared_prefix(self, tmp_path: Path) -> None:
        bad = replace(MINIMAL_VALID + SECOND_STUDY, 'prefix = "BE"', 'prefix = "AL"')
        assert_has_error(load_error(tmp_path, bad), "C6")

    def test_invalid_prefixes_are_not_compared(self, tmp_path: Path) -> None:
        two = MINIMAL_VALID + SECOND_STUDY
        bad = replace(
            replace(two, 'prefix = "AL"', 'prefix = "al"'),
            'prefix = "BE"',
            'prefix = "al"',
        )
        found = load_error(tmp_path, bad)
        assert_has_error(found, "C4")
        assert_no_error(found, "C6")


class TestC7UnknownKeys:
    def test_misspelt_key(self, tmp_path: Path) -> None:
        bad = replace(MINIMAL_VALID, "budget = 20", "budgt = 20")
        found = load_error(tmp_path, bad)
        assert_has_error(found, "C7", "studies.alpha.budgt")
        assert_has_error(found, "C3", "studies.alpha.budget")

    def test_unknown_top_level_table(self, tmp_path: Path) -> None:
        bad = MINIMAL_VALID + "\n[extra]\nx = 1\n"
        assert_has_error(load_error(tmp_path, bad), "C7", "extra")


class TestGroupBehaviour:
    def test_two_mistakes_in_different_tables_are_both_reported(
        self, tmp_path: Path
    ) -> None:
        bad = replace(MINIMAL_VALID, 'name = "example_project"', 'name = ""')
        bad = replace(bad, "budget = 20", "budget = -1")
        found = load_error(tmp_path, bad)
        assert_has_error(found, "C1", "project.name")
        assert_has_error(found, "C3", "studies.alpha.budget")


# --- Shared phase 2 checks ---

TABLE_PATH = "studies.alpha"


def new_collector() -> ErrorCollector:
    return ErrorCollector(FILE)


def only_error(found: ErrorCollector) -> tuple[str, str | None, object]:
    """The (rule, key, found) of the single error collected."""
    [error] = found.errors
    return error.rule, error.key, error.found


class TestKeyPath:
    def test_joins_with_dot(self) -> None:
        assert key_path("studies.alpha", "budget") == "studies.alpha.budget"

    def test_top_level_has_no_leading_dot(self) -> None:
        assert key_path("", "project") == "project"


class TestCheckUnknownKeys:
    def test_known_keys_pass(self) -> None:
        found = new_collector()
        check_unknown_keys(found, {"budget": 1}, frozenset({"budget"}), TABLE_PATH)
        assert not found.has_errors()

    def test_unknown_key_is_c7_with_its_path(self) -> None:
        found = new_collector()
        table = {"budget": 1, "budgt": 2}
        check_unknown_keys(found, table, frozenset({"budget"}), TABLE_PATH)
        assert only_error(found) == ("C7", "studies.alpha.budgt", 2)

    def test_several_unknown_keys_reported_sorted(self) -> None:
        found = new_collector()
        check_unknown_keys(found, {"zz": 1, "aa": 2}, frozenset(), TABLE_PATH)
        assert [e.key for e in found.errors] == ["studies.alpha.aa", "studies.alpha.zz"]

    def test_top_level_unknown_table(self) -> None:
        found = new_collector()
        table = {"project": {}, "extra": {}}
        check_unknown_keys(found, table, frozenset({"project", "studies"}), "")
        assert only_error(found) == ("C7", "extra", {})


class TestCheckTable:
    def test_present_table_is_returned(self) -> None:
        found = new_collector()
        assert check_table(found, {"returns": {"a": 1}}, "returns", "x", "C16") == {
            "a": 1
        }
        assert not found.has_errors()

    def test_missing_table(self) -> None:
        found = new_collector()
        assert check_table(found, {}, "returns", TABLE_PATH, "C16") is None
        assert only_error(found) == ("C16", "studies.alpha.returns", None)

    def test_value_that_is_not_a_table(self) -> None:
        found = new_collector()
        assert (
            check_table(found, {"witness": True}, "witness", "project", "C17") is None
        )
        assert only_error(found) == ("C17", "project.witness", True)


class TestCheckWholeNumber:
    @pytest.mark.parametrize(
        "value",
        [pytest.param(0, id="zero_at_minimum"), pytest.param(20, id="positive")],
    )
    def test_valid(self, value: int) -> None:
        found = new_collector()
        result = check_whole_number(
            found, {"budget": value}, "budget", TABLE_PATH, "C3", minimum=0
        )
        assert result == value
        assert not found.has_errors()

    @pytest.mark.parametrize(
        "value",
        [
            pytest.param(-1, id="below_minimum"),
            pytest.param(True, id="boolean"),
            pytest.param(2.5, id="float"),
            pytest.param(20.0, id="whole_float"),
            pytest.param("20", id="string"),
        ],
    )
    def test_invalid(self, value: Any) -> None:
        found = new_collector()
        result = check_whole_number(
            found, {"budget": value}, "budget", TABLE_PATH, "C3", minimum=0
        )
        assert result is None
        assert only_error(found) == ("C3", "studies.alpha.budget", value)

    def test_missing(self) -> None:
        found = new_collector()
        assert (
            check_whole_number(found, {}, "budget", TABLE_PATH, "C3", minimum=0) is None
        )
        assert only_error(found) == ("C3", "studies.alpha.budget", None)

    @pytest.mark.parametrize(
        ("value", "ok"),
        [
            pytest.param(4, True, id="four"),
            pytest.param(16, True, id="sixteen"),
            pytest.param(5, False, id="odd"),
            pytest.param(2, False, id="even_below_minimum"),
        ],
    )
    def test_even(self, value: int, ok: bool) -> None:
        found = new_collector()
        result = check_whole_number(
            found,
            {"cscv_blocks": value},
            "cscv_blocks",
            "studies.alpha.gate",
            "C10",
            minimum=4,
            even=True,
        )
        assert (result == value) is ok
        assert found.has_errors() is not ok


class TestCheckNumber:
    def check(self, value: Any, **bounds: float) -> tuple[float | None, ErrorCollector]:
        found = new_collector()
        result = check_number(found, {"x": value}, "x", "gate", "C10", **bounds)
        return result, found

    def test_integer_becomes_float(self) -> None:
        result, found = self.check(5, at_least=0)
        assert result == 5.0
        assert type(result) is float
        assert not found.has_errors()

    @pytest.mark.parametrize(
        "value",
        [
            pytest.param(math.nan, id="nan"),
            pytest.param(math.inf, id="inf"),
            pytest.param(-math.inf, id="minus_inf"),
            pytest.param(True, id="boolean"),
            pytest.param("0.5", id="string"),
        ],
    )
    def test_not_a_finite_number(self, value: Any) -> None:
        result, found = self.check(value, at_least=0)
        assert result is None
        rule, key, _ = only_error(found)
        assert (rule, key) == ("C10", "gate.x")

    def test_missing(self) -> None:
        found = new_collector()
        assert check_number(found, {}, "x", "gate", "C10", at_least=0) is None
        assert only_error(found) == ("C10", "gate.x", None)

    @pytest.mark.parametrize(
        ("value", "ok"),
        [
            pytest.param(0.0, False, id="at_exclusive_low"),
            pytest.param(0.5, True, id="inside"),
            pytest.param(1.0, False, id="at_exclusive_high"),
        ],
    )
    def test_strictly_between(self, value: float, ok: bool) -> None:
        result, found = self.check(value, above=0, below=1)
        assert (result is not None) is ok
        assert found.has_errors() is not ok

    @pytest.mark.parametrize(
        ("value", "ok"),
        [
            pytest.param(0.5, True, id="at_inclusive_high"),
            pytest.param(0.51, False, id="just_above"),
            pytest.param(0.0, False, id="at_exclusive_low"),
        ],
    )
    def test_above_and_at_most(self, value: float, ok: bool) -> None:
        result, found = self.check(value, above=0, at_most=0.5)
        assert (result is not None) is ok
        assert found.has_errors() is not ok

    def test_zero_or_more_includes_zero(self) -> None:
        result, found = self.check(0, at_least=0)
        assert result == 0.0
        assert not found.has_errors()


class TestCheckString:
    def test_valid(self) -> None:
        found = new_collector()
        assert check_string(found, {"name": "x"}, "name", "project", "C1") == "x"
        assert not found.has_errors()

    @pytest.mark.parametrize(
        "value",
        [
            pytest.param("", id="empty"),
            pytest.param(5, id="not_a_string"),
            pytest.param(None, id="missing"),
        ],
    )
    def test_invalid(self, value: Any) -> None:
        found = new_collector()
        table = {} if value is None else {"name": value}
        assert check_string(found, table, "name", "project", "C1") is None
        assert only_error(found) == ("C1", "project.name", value)


class TestCheckPath:
    @pytest.mark.parametrize(
        ("value", "tidied"),
        [
            pytest.param("bgl/ledger", "bgl/ledger", id="simple"),
            pytest.param("bgl/./ledger", "bgl/ledger", id="dot_segment"),
            pytest.param("a/b/../c", "a/c", id="dotdot_stays_inside"),
            pytest.param("bgl//ledger/", "bgl/ledger", id="double_and_trailing_slash"),
            pytest.param("./bgl", "bgl", id="leading_dot"),
            pytest.param(
                "data/raw/prices.parquet", "data/raw/prices.parquet", id="file"
            ),
        ],
    )
    def test_valid_returns_tidied_path(self, value: str, tidied: str) -> None:
        found = new_collector()
        assert check_path(found, {"ledger": value}, "ledger", "project") == tidied
        assert not found.has_errors()

    @pytest.mark.parametrize(
        "value",
        [
            pytest.param("/etc/ledger", id="posix_absolute"),
            pytest.param("//server/share", id="unc_forward"),
            pytest.param("C:/ledger", id="windows_drive"),
            pytest.param("c:ledger", id="drive_relative"),
            pytest.param("bgl\\ledger", id="backslash"),
            pytest.param("../outside", id="parent_escape"),
            pytest.param("bgl/../../outside", id="nested_escape"),
            pytest.param("", id="empty"),
            pytest.param(5, id="not_a_string"),
        ],
    )
    def test_invalid(self, value: Any) -> None:
        found = new_collector()
        assert check_path(found, {"ledger": value}, "ledger", "project") is None
        assert only_error(found) == ("C2", "project.ledger", value)

    def test_missing(self) -> None:
        found = new_collector()
        assert check_path(found, {}, "ledger", "project") is None
        assert only_error(found) == ("C2", "project.ledger", None)


class TestCheckPathList:
    def check(
        self, value: Any, *, allow_empty: bool
    ) -> tuple[tuple[str, ...] | None, ErrorCollector]:
        found = new_collector()
        result = check_path_list(
            found,
            {"holdout_files": value},
            "holdout_files",
            TABLE_PATH,
            "C11",
            allow_empty=allow_empty,
        )
        return result, found

    def test_valid_list_becomes_tuple(self) -> None:
        result, found = self.check(["a/x.parquet", "b/y.parquet"], allow_empty=False)
        assert result == ("a/x.parquet", "b/y.parquet")
        assert not found.has_errors()

    def test_items_are_tidied(self) -> None:
        result, found = self.check(["a/./x.parquet", "b//y.parquet"], allow_empty=False)
        assert result == ("a/x.parquet", "b/y.parquet")
        assert not found.has_errors()

    @pytest.mark.parametrize(
        "second",
        [
            pytest.param("a/x.parquet", id="exact"),
            pytest.param("a/./x.parquet", id="after_tidying"),
        ],
    )
    def test_repeat_is_owning_rule_at_later_index(self, second: str) -> None:
        result, found = self.check(["a/x.parquet", second], allow_empty=False)
        assert result is None
        assert only_error(found) == ("C11", "studies.alpha.holdout_files[1]", second)

    def test_empty_allowed(self) -> None:
        result, found = self.check([], allow_empty=True)
        assert result == ()
        assert not found.has_errors()

    def test_empty_refused_under_owning_rule(self) -> None:
        result, found = self.check([], allow_empty=False)
        assert result is None
        assert only_error(found) == ("C11", "studies.alpha.holdout_files", [])

    def test_not_a_list_under_owning_rule(self) -> None:
        result, found = self.check("a/x.parquet", allow_empty=True)
        assert result is None
        assert only_error(found) == (
            "C11",
            "studies.alpha.holdout_files",
            "a/x.parquet",
        )

    def test_bad_item_is_c2_with_index(self) -> None:
        result, found = self.check(["a/x.parquet", "/abs/y"], allow_empty=False)
        assert result is None
        assert only_error(found) == ("C2", "studies.alpha.holdout_files[1]", "/abs/y")

    def test_every_bad_item_reported(self) -> None:
        _, found = self.check(["/a", "ok/b", "../c"], allow_empty=False)
        assert [e.key for e in found.errors] == [
            "studies.alpha.holdout_files[0]",
            "studies.alpha.holdout_files[2]",
        ]


class TestCheckPlaceholder:
    @pytest.mark.parametrize(
        "value",
        [
            pytest.param("https://rekor.example", id="url"),
            pytest.param("PBO < 0.5 per Bailey et al. (2017)", id="less_than"),
            pytest.param("x > y", id="greater_than"),
            pytest.param("<cited source>", id="angle_brackets"),
            pytest.param("todo(bgl) lower case", id="lower_case_marker"),
        ],
    )
    def test_real_text_passes(self, value: str) -> None:
        found = new_collector()
        assert check_placeholder(found, value, "studies.alpha.gate.basis.costs.text")
        assert not found.has_errors()

    @pytest.mark.parametrize(
        "value",
        [
            pytest.param(
                "ed25519:TODO(bgl): paste the public key printed by bgl join",
                id="template_key",
            ),
            pytest.param("TODO(bgl)", id="marker_only"),
            pytest.param("Bailey (2014) TODO(bgl) add section", id="half_edited"),
        ],
    )
    def test_marker_is_c20(self, value: str) -> None:
        found = new_collector()
        assert not check_placeholder(
            found, value, "studies.alpha.gate.basis.costs.text"
        )
        assert only_error(found) == (
            "C20",
            "studies.alpha.gate.basis.costs.text",
            value,
        )


# --- Project table, registry and witness (block 6) ---


def key_text(seed: int) -> str:
    """A valid key text whose 32 bytes start at seed, so each seed is distinct."""
    raw = bytes((seed + index) % 256 for index in range(32))
    return "ed25519:" + base64.b64encode(raw).decode("ascii")


KEY_TEXT = key_text(0)
TEMPLATE_KEY = "ed25519:TODO(bgl): paste the public key printed by bgl join"


def valid_project(**changes: Any) -> dict[str, Any]:
    """A valid team [project] table as tomllib returns it, with changes applied."""
    project: dict[str, Any] = {
        "name": "example_project",
        "mode": "team",
        "ledger": "bgl/ledger",
        "runs_dir": "bgl/runs",
        "chains": {
            "CG1": {"person": "researcher_a", "key": KEY_TEXT},
            "HC1": {"person": "custodian_a", "key": key_text(1)},
        },
        "witness": {
            "rekor_url": "https://rekor.example",
            "ots_calendars": ["https://a.example", "https://b.example"],
        },
    }
    project.update(changes)
    return project


def rules_and_keys(found: ErrorCollector) -> list[tuple[str, str | None]]:
    return [(error.rule, error.key) for error in found.errors]


class TestCheckProject:
    def test_valid_team_project_builds(self) -> None:
        found = new_collector()
        parts = check_project(found, {"project": valid_project()})
        assert not found.has_errors()
        project = parts.build()
        assert project is not None
        assert project.name == "example_project"
        assert project.mode == "team"
        assert project.ledger == "bgl/ledger"
        assert project.runs_dir == "bgl/runs"
        assert set(project.writers) == {"CG1", "HC1"}
        assert project.witness is not None
        assert project.witness.ots_calendars == (
            "https://a.example",
            "https://b.example",
        )

    def test_valid_solo_without_witness_builds(self) -> None:
        found = new_collector()
        parts = check_project(
            found, {"project": valid_project(mode="solo", witness=False)}
        )
        assert not found.has_errors()
        project = parts.build()
        assert project is not None
        assert project.witness is None

    def test_missing_project_is_c1_and_nothing_else(self) -> None:
        found = new_collector()
        parts = check_project(found, {})
        assert only_error(found) == ("C1", "project", None)
        assert parts.build() is None

    def test_unknown_key_is_c7(self) -> None:
        found = new_collector()
        check_project(found, {"project": valid_project(ledgr="bgl/ledger")})
        assert rules_and_keys(found) == [("C7", "project.ledgr")]

    @pytest.mark.parametrize(
        ("change", "rule", "key"),
        [
            pytest.param({"name": ""}, "C1", "project.name", id="empty_name"),
            pytest.param({"name": 3}, "C1", "project.name", id="name_not_string"),
            pytest.param({"ledger": "/abs"}, "C2", "project.ledger", id="abs_ledger"),
            pytest.param(
                {"runs_dir": "../out"}, "C2", "project.runs_dir", id="runs_dir_escapes"
            ),
            pytest.param({"mode": "Team"}, "C14", "project.mode", id="mode_case"),
        ],
    )
    def test_one_bad_field(self, change: dict[str, Any], rule: str, key: str) -> None:
        found = new_collector()
        parts = check_project(found, {"project": valid_project(**change)})
        assert rules_and_keys(found) == [(rule, key)]
        assert parts.build() is None

    @pytest.mark.parametrize(
        "key", ["name", "mode", "ledger", "runs_dir", "chains", "witness"]
    )
    def test_every_key_is_required(self, key: str) -> None:
        project = valid_project()
        del project[key]
        found = new_collector()
        parts = check_project(found, {"project": project})
        assert [k for _, k in rules_and_keys(found)] == [f"project.{key}"]
        assert parts.build() is None

    def test_bad_name_keeps_mode_and_writers_for_study_checks(self) -> None:
        found = new_collector()
        parts = check_project(found, {"project": valid_project(name="")})
        assert parts.mode == "team"
        assert parts.writers is not None
        assert parts.build() is None

    def test_errors_follow_key_order(self) -> None:
        found = new_collector()
        check_project(
            found,
            {"project": valid_project(name="", mode="x", ledger="", chains={})},
        )
        assert [k for _, k in rules_and_keys(found)] == [
            "project.name",
            "project.mode",
            "project.ledger",
            "project.chains",
        ]


class TestCheckMode:
    @pytest.mark.parametrize("value", ["solo", "team"])
    def test_valid(self, value: str) -> None:
        found = new_collector()
        assert check_mode(found, {"mode": value}) == value
        assert not found.has_errors()

    @pytest.mark.parametrize(
        "value",
        [
            pytest.param("Solo", id="capitalised"),
            pytest.param("solo ", id="trailing_space"),
            pytest.param("", id="empty"),
            pytest.param(True, id="bool"),
            pytest.param(None, id="missing"),
        ],
    )
    def test_invalid_is_c14(self, value: Any) -> None:
        found = new_collector()
        table = {} if value is None else {"mode": value}
        assert check_mode(found, table) is None
        assert only_error(found) == ("C14", "project.mode", value)


class TestCheckRegistry:
    def test_returns_read_only_writers(self) -> None:
        found = new_collector()
        writers = check_registry(found, valid_project())
        assert writers is not None
        assert isinstance(writers, types.MappingProxyType)
        assert writers["CG1"].person == "researcher_a"
        assert writers["CG1"].public_key == bytes(range(32))
        with pytest.raises(TypeError):
            writers["XX1"] = writers["CG1"]  # type: ignore[index]

    def test_missing_chains_is_c12(self) -> None:
        found = new_collector()
        project = valid_project()
        del project["chains"]
        assert check_registry(found, project) is None
        assert only_error(found) == ("C12", "project.chains", None)

    def test_empty_registry_is_c12(self) -> None:
        found = new_collector()
        assert check_registry(found, valid_project(chains={})) is None
        assert only_error(found) == ("C12", "project.chains", {})

    @pytest.mark.parametrize(
        "code",
        [
            pytest.param("AB", id="two_chars"),
            pytest.param("ABCDEF", id="six_chars"),
            pytest.param("A1", id="letter_then_digit"),
            pytest.param("CG1", id="example"),
        ],
    )
    def test_valid_code(self, code: str) -> None:
        found = new_collector()
        chains = {code: {"person": "p", "key": KEY_TEXT}}
        assert check_registry(found, valid_project(chains=chains)) is not None
        assert not found.has_errors()

    @pytest.mark.parametrize(
        "code",
        [
            pytest.param("A", id="one_char"),
            pytest.param("ABCDEFG", id="seven_chars"),
            pytest.param("1AB", id="starts_with_digit"),
            pytest.param("cg1", id="lower_case"),
            pytest.param("C-1", id="hyphen"),
            pytest.param("CG1\n", id="trailing_newline"),
        ],
    )
    def test_bad_code_is_c12(self, code: str) -> None:
        found = new_collector()
        chains = {code: {"person": "p", "key": KEY_TEXT}}
        assert check_registry(found, valid_project(chains=chains)) is None
        assert only_error(found) == ("C12", f"project.chains.{code}", code)

    def test_entry_not_a_table(self) -> None:
        found = new_collector()
        assert check_registry(found, valid_project(chains={"CG1": "x"})) is None
        assert only_error(found) == ("C12", "project.chains.CG1", "x")

    def test_entry_problems_all_reported(self) -> None:
        found = new_collector()
        chains = {"CG1": {"person": "", "key": "rsa:abc", "email": "a@b"}}
        assert check_registry(found, valid_project(chains=chains)) is None
        assert rules_and_keys(found) == [
            ("C7", "project.chains.CG1.email"),
            ("C12", "project.chains.CG1.person"),
            ("C12", "project.chains.CG1.key"),
        ]

    def test_one_bad_writer_does_not_hide_another(self) -> None:
        found = new_collector()
        chains = {
            "CG1": {"person": "", "key": KEY_TEXT},
            "HC1": {"person": "q", "key": "nope"},
        }
        assert check_registry(found, valid_project(chains=chains)) is None
        assert rules_and_keys(found) == [
            ("C12", "project.chains.CG1.person"),
            ("C12", "project.chains.HC1.key"),
        ]

    def test_same_key_twice_is_c12_at_the_later_entry(self) -> None:
        found = new_collector()
        chains = {
            "CG1": {"person": "researcher_a", "key": KEY_TEXT},
            "CG2": {"person": "researcher_b", "key": key_text(1)},
            "HC1": {"person": "custodian_a", "key": KEY_TEXT},
        }
        assert check_registry(found, valid_project(chains=chains)) is None
        assert only_error(found) == ("C12", "project.chains.HC1.key", KEY_TEXT)

    def test_template_keys_are_not_compared(self) -> None:
        found = new_collector()
        chains = {
            "CG1": {"person": "researcher_a", "key": TEMPLATE_KEY},
            "CG2": {"person": "researcher_b", "key": TEMPLATE_KEY},
        }
        assert check_registry(found, valid_project(chains=chains)) is None
        assert rules_and_keys(found) == [
            ("C20", "project.chains.CG1.key"),
            ("C20", "project.chains.CG2.key"),
        ]

    def test_one_person_may_have_two_chains(self) -> None:
        found = new_collector()
        chains = {
            "CG1": {"person": "researcher_a", "key": KEY_TEXT},
            "CG2": {"person": "researcher_a", "key": key_text(1)},
        }
        assert check_registry(found, valid_project(chains=chains)) is not None
        assert not found.has_errors()


class TestCheckPublicKey:
    PATH = "project.chains.CG1"

    def test_valid_key_returns_raw_bytes(self) -> None:
        found = new_collector()
        assert check_public_key(found, {"key": KEY_TEXT}, self.PATH) == bytes(range(32))
        assert not found.has_errors()

    @pytest.mark.parametrize(
        "value",
        [
            pytest.param(None, id="missing"),
            pytest.param(5, id="not_string"),
            pytest.param("", id="empty"),
            pytest.param(KEY_TEXT.replace("ed25519:", "ED25519:"), id="prefix_case"),
            pytest.param(KEY_TEXT.replace("ed25519:", ""), id="no_prefix"),
            pytest.param(
                "ed25519:" + base64.b64encode(bytes(31)).decode(), id="31_bytes"
            ),
            pytest.param(
                "ed25519:" + base64.b64encode(bytes(33)).decode(), id="33_bytes"
            ),
            pytest.param(KEY_TEXT.rstrip("="), id="missing_padding"),
            pytest.param(KEY_TEXT + " ", id="trailing_space"),
            pytest.param(
                "ed25519:" + base64.urlsafe_b64encode(bytes([251] * 32)).decode(),
                id="url_safe_alphabet",
            ),
            pytest.param("ed25519:" + "A" * 42 + "B=", id="non_canonical_bits"),
        ],
    )
    def test_bad_key_is_c12(self, value: Any) -> None:
        found = new_collector()
        entry = {} if value is None else {"key": value}
        assert check_public_key(found, entry, self.PATH) is None
        assert only_error(found) == ("C12", f"{self.PATH}.key", value)

    def test_template_key_is_c20_only(self) -> None:
        found = new_collector()
        assert check_public_key(found, {"key": TEMPLATE_KEY}, self.PATH) is None
        assert only_error(found) == ("C20", f"{self.PATH}.key", TEMPLATE_KEY)


class TestCheckWitness:
    def witness(self, **changes: Any) -> dict[str, Any]:
        table: dict[str, Any] = {
            "rekor_url": "https://rekor.example",
            "ots_calendars": ["https://a.example"],
        }
        table.update(changes)
        return table

    @pytest.mark.parametrize("mode", ["solo", "team"])
    def test_table_is_valid_in_either_mode(self, mode: Mode) -> None:
        found = new_collector()
        valid, witness = check_witness(found, {"witness": self.witness()}, mode)
        assert valid
        assert witness is not None
        assert witness.rekor_url == "https://rekor.example"
        assert not found.has_errors()

    def test_false_is_valid_in_solo(self) -> None:
        found = new_collector()
        assert check_witness(found, {"witness": False}, "solo") == (True, None)
        assert not found.has_errors()

    def test_false_is_c17_in_team(self) -> None:
        found = new_collector()
        assert check_witness(found, {"witness": False}, "team") == (False, None)
        assert only_error(found) == ("C17", "project.witness", False)

    def test_false_with_bad_mode_adds_nothing(self) -> None:
        found = new_collector()
        assert check_witness(found, {"witness": False}, None) == (False, None)
        assert not found.has_errors()

    @pytest.mark.parametrize(
        "value",
        [
            pytest.param(None, id="missing"),
            pytest.param(True, id="true"),
            pytest.param("on", id="string"),
        ],
    )
    def test_not_a_table_or_false_is_c17(self, value: Any) -> None:
        found = new_collector()
        project = {} if value is None else {"witness": value}
        assert check_witness(found, project, "solo") == (False, None)
        assert only_error(found) == ("C17", "project.witness", value)

    def test_unknown_key_is_c7(self) -> None:
        found = new_collector()
        table = self.witness(rekor="https://x.example")
        valid, _ = check_witness(found, {"witness": table}, "team")
        assert valid
        assert rules_and_keys(found) == [("C7", "project.witness.rekor")]

    def test_both_bad_fields_reported(self) -> None:
        found = new_collector()
        table = self.witness(rekor_url="http://rekor.example", ots_calendars=[])
        assert check_witness(found, {"witness": table}, "team") == (False, None)
        assert rules_and_keys(found) == [
            ("C17", "project.witness.rekor_url"),
            ("C17", "project.witness.ots_calendars"),
        ]


class TestCheckHttpsUrl:
    PATH = "project.witness"

    @pytest.mark.parametrize(
        "value",
        [
            pytest.param("https://rekor.example", id="plain"),
            pytest.param("https://rekor.example:8443/api", id="port_and_path"),
            pytest.param("HTTPS://rekor.example", id="upper_case_scheme"),
            pytest.param("https://a.example/x>y", id="angle_bracket_allowed"),
        ],
    )
    def test_valid(self, value: str) -> None:
        found = new_collector()
        assert (
            check_https_url(found, {"rekor_url": value}, "rekor_url", self.PATH, "C17")
            == value
        )
        assert not found.has_errors()

    @pytest.mark.parametrize(
        "value",
        [
            pytest.param(None, id="missing"),
            pytest.param("", id="empty"),
            pytest.param(7, id="not_string"),
            pytest.param("http://rekor.example", id="http"),
            pytest.param("rekor.example", id="no_scheme"),
            pytest.param("https://", id="no_host"),
            pytest.param("https://rekor.example:99999", id="bad_port"),
            pytest.param("https://rekor .example", id="space"),
            pytest.param("https://rekor.example\n", id="newline"),
            pytest.param("https://[::1", id="bad_ipv6"),
        ],
    )
    def test_invalid_is_rule(self, value: Any) -> None:
        found = new_collector()
        table = {} if value is None else {"rekor_url": value}
        assert check_https_url(found, table, "rekor_url", self.PATH, "C17") is None
        assert only_error(found) == ("C17", f"{self.PATH}.rekor_url", value)

    def test_placeholder_is_c20_only(self) -> None:
        found = new_collector()
        value = "https://TODO(bgl).example"
        table = {"rekor_url": value}
        assert check_https_url(found, table, "rekor_url", self.PATH, "C17") is None
        assert only_error(found) == ("C20", f"{self.PATH}.rekor_url", value)


class TestCheckHttpsUrlList:
    PATH = "project.witness"

    def test_valid_list_becomes_tuple(self) -> None:
        found = new_collector()
        table = {"ots_calendars": ["https://a.example", "https://b.example"]}
        assert check_https_url_list(
            found, table, "ots_calendars", self.PATH, "C17"
        ) == ("https://a.example", "https://b.example")
        assert not found.has_errors()

    @pytest.mark.parametrize(
        "value",
        [
            pytest.param(None, id="missing"),
            pytest.param([], id="empty"),
            pytest.param("https://a.example", id="string_not_list"),
        ],
    )
    def test_bad_shape_is_rule(self, value: Any) -> None:
        found = new_collector()
        table = {} if value is None else {"ots_calendars": value}
        assert (
            check_https_url_list(found, table, "ots_calendars", self.PATH, "C17")
            is None
        )
        assert only_error(found) == ("C17", f"{self.PATH}.ots_calendars", value)

    def test_each_bad_item_reported_at_its_index(self) -> None:
        found = new_collector()
        table = {
            "ots_calendars": [
                "https://a.example",
                "http://b.example",
                "https://TODO(bgl)",
            ]
        }
        assert (
            check_https_url_list(found, table, "ots_calendars", self.PATH, "C17")
            is None
        )
        assert rules_and_keys(found) == [
            ("C17", f"{self.PATH}.ots_calendars[1]"),
            ("C20", f"{self.PATH}.ots_calendars[2]"),
        ]


# --- Studies, gate, loading and canonical tables (sprint) ---


def load(tmp_path: Path, text: str) -> Config:
    return load_config(write(tmp_path, text))


def found_pairs(error: ConfigError | ConfigErrorGroup) -> list[tuple[str, str | None]]:
    errors = list(error.exceptions) if isinstance(error, ConfigErrorGroup) else [error]
    return [(e.rule, e.key) for e in errors]


def without_line(text: str, line: str) -> str:
    return replace(text, line + "\n", "")


SOLO_HOLDOUT = replace(
    MINIMAL_VALID,
    "holdout = false\n",
    'holdout_start = 2025-01-01\nholdout_files = ["data/h.parquet"]\n'
    "restricted_files = []\n",
)


class TestLoadConfigRecords:
    def test_full_valid_loads_every_part(self, tmp_path: Path) -> None:
        config = load(tmp_path, FULL_VALID)
        assert config.project.mode == "team"
        assert set(config.project.writers) == {"CG1", "CG2", "HC1"}
        study = config.studies["alpha"]
        assert study.name == "alpha"
        assert study.claims == ("effect_exists", "effect_persists")
        assert study.holdout is not None
        assert study.holdout.start == datetime.date(2025, 1, 1)
        assert study.holdout.custodian == "custodian_a"
        assert study.returns.risk_free == "data/rates/rf_daily.csv"
        assert study.returns.risk_free_reasoning is None
        assert study.gate is not None
        assert study.gate.cscv_blocks == 16
        assert study.gate.costs.cost_bps == 5.0
        assert isinstance(study.gate.costs.cost_bps, float)
        assert study.gate.basis["costs"].kind == "reasoning"
        assert set(study.gate.basis) == {
            "dsr_threshold",
            "psr_threshold",
            "pbo_threshold",
            "pbo_min_trials",
            "cscv_blocks",
            "min_block_periods",
            "min_live_days",
            "live_witness_lag_days",
            "costs",
        }

    def test_minimal_study_has_no_holdout_or_gate(self, tmp_path: Path) -> None:
        study = load(tmp_path, MINIMAL_VALID).studies["alpha"]
        assert study.holdout is None
        assert study.gate is None
        assert study.returns.risk_free is None
        assert study.returns.risk_free_reasoning == "Self-financing long-short book"

    def test_studies_are_read_only(self, tmp_path: Path) -> None:
        config = load(tmp_path, MINIMAL_VALID + SECOND_STUDY)
        assert set(config.studies) == {"alpha", "beta"}
        with pytest.raises(TypeError):
            config.studies["gamma"] = config.studies["alpha"]  # type: ignore[index]

    def test_phase_1_error_is_a_single_config_error(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError) as info:
            load_config(tmp_path / "bgl.toml")
        assert info.value.rule == "C8"

    def test_paths_are_stored_tidied(self, tmp_path: Path) -> None:
        text = replace(
            FULL_VALID, '"docs/studies/alpha.md"', '"docs/./studies//alpha.md"'
        )
        assert load(tmp_path, text).studies["alpha"].spec_files == (
            "docs/studies/alpha.md",
        )


class TestC1StudyNames:
    @pytest.mark.parametrize(
        "name",
        [
            pytest.param("a", id="one_char"),
            pytest.param("a" * 32, id="32_chars"),
            pytest.param("alpha_2", id="underscore_digit"),
        ],
    )
    def test_valid(self, tmp_path: Path, name: str) -> None:
        text = replace(MINIMAL_VALID, "[studies.alpha]", f"[studies.{name}]")
        text = replace(text, "[studies.alpha.returns]", f"[studies.{name}.returns]")
        assert set(load(tmp_path, text).studies) == {name}

    @pytest.mark.parametrize(
        ("header", "key"),
        [
            pytest.param("Alpha", "studies.Alpha", id="capital"),
            pytest.param("1alpha", "studies.1alpha", id="starts_with_digit"),
            pytest.param("al-pha", "studies.al-pha", id="hyphen"),
            pytest.param('"my.study"', "studies.my.study", id="dot"),
            pytest.param("a" * 33, "studies." + "a" * 33, id="33_chars"),
        ],
    )
    def test_invalid_is_c1(self, tmp_path: Path, header: str, key: str) -> None:
        text = replace(MINIMAL_VALID, "[studies.alpha]", f"[studies.{header}]")
        text = replace(text, "[studies.alpha.returns]", f"[studies.{header}.returns]")
        assert_has_error(load_error(tmp_path, text), "C1", key)

    def test_study_that_is_not_a_table(self, tmp_path: Path) -> None:
        text = MINIMAL_VALID.split("[studies.alpha]")[0] + "[studies]\nalpha = 3\n"
        assert found_pairs(load_error(tmp_path, text)) == [("C1", "studies.alpha")]


class TestC2StudyPaths:
    @pytest.mark.parametrize(
        ("old", "new", "key"),
        [
            pytest.param(
                '"data/rates/rf_daily.csv"',
                '"/rates.csv"',
                "studies.alpha.returns.risk_free",
                id="risk_free",
            ),
            pytest.param(
                '"docs/studies/alpha.md"',
                '"../alpha.md"',
                "studies.alpha.spec_files[0]",
                id="spec_file",
            ),
            pytest.param(
                '"data/holdout/alpha_holdout.parquet"',
                '"C:/h.parquet"',
                "studies.alpha.holdout_files[0]",
                id="holdout_file",
            ),
        ],
    )
    def test_bad_path_is_c2(self, tmp_path: Path, old: str, new: str, key: str) -> None:
        assert found_pairs(load_error(tmp_path, replace(FULL_VALID, old, new))) == [
            ("C2", key)
        ]


GATE = "studies.alpha.gate"


class TestC10Gate:
    @pytest.mark.parametrize(
        ("line", "value", "ok"),
        [
            pytest.param("dsr_threshold = 0.95", "0", False, id="dsr_0"),
            pytest.param("dsr_threshold = 0.95", "1", False, id="dsr_1"),
            pytest.param("dsr_threshold = 0.95", "0.999", True, id="dsr_0_999"),
            pytest.param("dsr_threshold = 0.95", "nan", False, id="dsr_nan"),
            pytest.param("psr_threshold = 0.95", "1.0", False, id="psr_1"),
            pytest.param("psr_threshold = 0.95", "0.001", True, id="psr_small"),
            pytest.param("pbo_threshold = 0.05", "0.5", True, id="pbo_0_5"),
            pytest.param("pbo_threshold = 0.05", "0.5000001", False, id="pbo_over"),
            pytest.param("pbo_threshold = 0.05", "0", False, id="pbo_0"),
            pytest.param("pbo_threshold = 0.05", "inf", False, id="pbo_inf"),
            pytest.param("pbo_min_trials = 10", "2", True, id="trials_2"),
            pytest.param("pbo_min_trials = 10", "1", False, id="trials_1"),
            pytest.param("cscv_blocks = 16", "4", True, id="blocks_4"),
            pytest.param("cscv_blocks = 16", "2", False, id="blocks_2"),
            pytest.param("cscv_blocks = 16", "5", False, id="blocks_odd"),
            pytest.param("cscv_blocks = 16", "16.0", False, id="blocks_float"),
            pytest.param("min_block_periods = 20", "1", True, id="block_periods_1"),
            pytest.param("min_block_periods = 20", "0", False, id="block_periods_0"),
            pytest.param("min_live_days = 90", "0", False, id="live_days_0"),
            pytest.param("live_witness_lag_days = 3", "1", True, id="lag_1"),
            pytest.param("live_witness_lag_days = 3", "0", False, id="lag_0"),
            pytest.param("live_witness_lag_days = 3", "true", False, id="lag_bool"),
        ],
    )
    def test_ranges(self, tmp_path: Path, line: str, value: str, ok: bool) -> None:
        key = line.split(" = ")[0]
        text = replace(FULL_VALID, line + "\n", f"{key} = {value}\n")
        if ok:
            load(tmp_path, text)
        else:
            assert found_pairs(load_error(tmp_path, text)) == [("C10", f"{GATE}.{key}")]

    @pytest.mark.parametrize(
        "line",
        [
            "dsr_threshold = 0.95",
            "psr_threshold = 0.95",
            "pbo_threshold = 0.05",
            "pbo_min_trials = 10",
            "cscv_blocks = 16",
            "min_block_periods = 20",
            "min_live_days = 90",
            "live_witness_lag_days = 3",
        ],
    )
    def test_every_value_is_required(self, tmp_path: Path, line: str) -> None:
        key = line.split(" = ")[0]
        text = without_line(FULL_VALID, line)
        assert found_pairs(load_error(tmp_path, text)) == [("C10", f"{GATE}.{key}")]

    def test_gate_is_optional(self, tmp_path: Path) -> None:
        assert load(tmp_path, MINIMAL_VALID).studies["alpha"].gate is None

    def test_gate_not_a_table(self, tmp_path: Path) -> None:
        text = replace(
            MINIMAL_VALID, "spec_files = []\n", "spec_files = []\ngate = 1\n"
        )
        assert found_pairs(load_error(tmp_path, text)) == [("C10", GATE)]

    def test_unknown_gate_key_is_c7(self, tmp_path: Path) -> None:
        text = replace(
            FULL_VALID, "cscv_blocks = 16\n", "cscv_blocks = 16\nmintrl = 1\n"
        )
        assert found_pairs(load_error(tmp_path, text)) == [("C7", f"{GATE}.mintrl")]

    def test_missing_costs_and_basis(self, tmp_path: Path) -> None:
        text = FULL_VALID.split("[studies.alpha.gate.costs]")[0]
        assert found_pairs(load_error(tmp_path, text)) == [
            ("C10", f"{GATE}.costs"),
            ("C10", f"{GATE}.basis"),
        ]

    @pytest.mark.parametrize(
        ("old", "new", "pair"),
        [
            pytest.param(
                'model = "flat"',
                'model = "linear"',
                ("C10", f"{GATE}.costs.model"),
                id="model",
            ),
            pytest.param(
                "cost_bps = 5",
                "cost_bps = -0.5",
                ("C10", f"{GATE}.costs.cost_bps"),
                id="negative_cost",
            ),
            pytest.param(
                "cost_bps = 5",
                "cost_bps = 5\nslippage = 1",
                ("C7", f"{GATE}.costs.slippage"),
                id="unknown_cost_key",
            ),
        ],
    )
    def test_bad_costs(
        self, tmp_path: Path, old: str, new: str, pair: tuple[str, str]
    ) -> None:
        assert found_pairs(load_error(tmp_path, replace(FULL_VALID, old, new))) == [
            pair
        ]

    def test_zero_cost_is_valid(self, tmp_path: Path) -> None:
        config = load(tmp_path, replace(FULL_VALID, "cost_bps = 5", "cost_bps = 0"))
        gate = config.studies["alpha"].gate
        assert gate is not None
        assert gate.costs.cost_bps == 0.0

    def test_missing_basis_entry(self, tmp_path: Path) -> None:
        text = without_line(
            FULL_VALID,
            'costs = { kind = "reasoning", text = "Typical all-in cost for liquid '
            'large caps" }',
        )
        assert found_pairs(load_error(tmp_path, text)) == [
            ("C10", f"{GATE}.basis.costs")
        ]

    @pytest.mark.parametrize(
        ("new", "pair"),
        [
            pytest.param(
                '{ kind = "opinion", text = "x" }',
                ("C10", f"{GATE}.basis.cscv_blocks.kind"),
                id="bad_kind",
            ),
            pytest.param(
                '{ kind = "citation", text = "" }',
                ("C10", f"{GATE}.basis.cscv_blocks.text"),
                id="empty_text",
            ),
            pytest.param(
                '{ kind = "citation", text = "TODO(bgl): cite a source" }',
                ("C20", f"{GATE}.basis.cscv_blocks.text"),
                id="placeholder",
            ),
            pytest.param(
                '{ kind = "citation", text = "x", page = 3 }',
                ("C7", f"{GATE}.basis.cscv_blocks.page"),
                id="unknown_entry_key",
            ),
            pytest.param(
                '"Bailey et al. (2017)"',
                ("C10", f"{GATE}.basis.cscv_blocks"),
                id="not_a_table",
            ),
        ],
    )
    def test_bad_basis_entry(
        self, tmp_path: Path, new: str, pair: tuple[str, str]
    ) -> None:
        old = 'cscv_blocks = { kind = "citation", text = "Bailey et al. (2017)" }'
        text = replace(FULL_VALID, old, f"cscv_blocks = {new}")
        assert found_pairs(load_error(tmp_path, text)) == [pair]

    def test_unknown_basis_key_is_c7(self, tmp_path: Path) -> None:
        extra = 'capital = { kind = "reasoning", text = "x" }\n'
        text = FULL_VALID + extra
        assert found_pairs(load_error(tmp_path, text)) == [
            ("C7", f"{GATE}.basis.capital")
        ]


ALPHA = "studies.alpha"


class TestC11Holdout:
    def test_both_start_and_flag(self, tmp_path: Path) -> None:
        text = replace(
            FULL_VALID,
            "holdout_start = 2025-01-01\n",
            "holdout_start = 2025-01-01\nholdout = false\n",
        )
        assert found_pairs(load_error(tmp_path, text)) == [("C11", f"{ALPHA}.holdout")]

    def test_neither_start_nor_flag(self, tmp_path: Path) -> None:
        text = without_line(MINIMAL_VALID, "holdout = false")
        assert found_pairs(load_error(tmp_path, text)) == [
            ("C11", f"{ALPHA}.holdout_start")
        ]

    def test_holdout_true(self, tmp_path: Path) -> None:
        text = replace(MINIMAL_VALID, "holdout = false", "holdout = true")
        assert found_pairs(load_error(tmp_path, text)) == [("C11", f"{ALPHA}.holdout")]

    @pytest.mark.parametrize(
        "value",
        [
            pytest.param('"2025-01-01"', id="quoted"),
            pytest.param("2025-01-01T00:00:00", id="datetime"),
            pytest.param("2025-01-01 09:30:00Z", id="datetime_utc"),
            pytest.param("09:30:00", id="time"),
        ],
    )
    def test_start_must_be_a_plain_date(self, tmp_path: Path, value: str) -> None:
        text = replace(
            FULL_VALID, "holdout_start = 2025-01-01", f"holdout_start = {value}"
        )
        assert found_pairs(load_error(tmp_path, text)) == [
            ("C11", f"{ALPHA}.holdout_start")
        ]

    @pytest.mark.parametrize(
        ("old", "new", "key"),
        [
            pytest.param(
                'holdout_files = ["data/holdout/alpha_holdout.parquet"]',
                "holdout_files = []",
                f"{ALPHA}.holdout_files",
                id="empty_holdout_files",
            ),
            pytest.param(
                'holdout_files = ["data/holdout/alpha_holdout.parquet"]\n',
                "",
                f"{ALPHA}.holdout_files",
                id="missing_holdout_files",
            ),
            pytest.param(
                'restricted_files = ["data/raw/prices.parquet"]\n',
                "",
                f"{ALPHA}.restricted_files",
                id="missing_restricted_files",
            ),
            pytest.param(
                'restricted_files = ["data/raw/prices.parquet"]',
                'restricted_files = ["data/holdout/alpha_holdout.parquet"]',
                f"{ALPHA}.restricted_files[0]",
                id="overlap",
            ),
            pytest.param(
                'restricted_files = ["data/raw/prices.parquet"]',
                'restricted_files = ["data/holdout/./alpha_holdout.parquet"]',
                f"{ALPHA}.restricted_files[0]",
                id="overlap_after_tidying",
            ),
            pytest.param(
                'holdout_files = ["data/holdout/alpha_holdout.parquet"]',
                'holdout_files = ["data/h.parquet", "data/h.parquet"]',
                f"{ALPHA}.holdout_files[1]",
                id="repeat",
            ),
        ],
    )
    def test_bad_files(self, tmp_path: Path, old: str, new: str, key: str) -> None:
        text = replace(FULL_VALID, old, new)
        assert found_pairs(load_error(tmp_path, text)) == [("C11", key)]

    def test_empty_restricted_files_is_valid(self, tmp_path: Path) -> None:
        text = replace(
            FULL_VALID,
            'restricted_files = ["data/raw/prices.parquet"]',
            "restricted_files = []",
        )
        holdout = load(tmp_path, text).studies["alpha"].holdout
        assert holdout is not None
        assert holdout.restricted_files == ()

    @pytest.mark.parametrize(
        "line",
        [
            'holdout_files = ["data/h.parquet"]',
            "restricted_files = []",
            'custodian = "researcher_a"',
        ],
    )
    def test_no_holdout_refuses_holdout_keys(self, tmp_path: Path, line: str) -> None:
        key = line.split(" = ")[0]
        text = replace(MINIMAL_VALID, "holdout = false\n", f"holdout = false\n{line}\n")
        assert found_pairs(load_error(tmp_path, text)) == [("C11", f"{ALPHA}.{key}")]


class TestC13PriorTrials:
    def test_missing(self, tmp_path: Path) -> None:
        text = without_line(MINIMAL_VALID, "prior_trials = 0")
        assert found_pairs(load_error(tmp_path, text)) == [
            ("C13", f"{ALPHA}.prior_trials")
        ]

    def test_negative(self, tmp_path: Path) -> None:
        text = replace(MINIMAL_VALID, "prior_trials = 0", "prior_trials = -1")
        assert found_pairs(load_error(tmp_path, text)) == [
            ("C13", f"{ALPHA}.prior_trials")
        ]


class TestC14C15Custodian:
    def test_team_holdout_needs_custodian(self, tmp_path: Path) -> None:
        text = without_line(FULL_VALID, 'custodian = "custodian_a"')
        assert found_pairs(load_error(tmp_path, text)) == [
            ("C14", f"{ALPHA}.custodian")
        ]

    def test_solo_holdout_may_omit_custodian(self, tmp_path: Path) -> None:
        holdout = load(tmp_path, SOLO_HOLDOUT).studies["alpha"].holdout
        assert holdout is not None
        assert holdout.custodian is None

    def test_bad_mode_hides_the_custodian_rule(self, tmp_path: Path) -> None:
        text = replace(FULL_VALID, 'mode = "team"', 'mode = "both"')
        text = without_line(text, 'custodian = "custodian_a"')
        assert found_pairs(load_error(tmp_path, text)) == [("C14", "project.mode")]

    def test_custodian_not_in_registry(self, tmp_path: Path) -> None:
        text = replace(FULL_VALID, 'custodian = "custodian_a"', 'custodian = "nobody"')
        assert found_pairs(load_error(tmp_path, text)) == [
            ("C15", f"{ALPHA}.custodian")
        ]

    def test_empty_custodian(self, tmp_path: Path) -> None:
        text = replace(FULL_VALID, 'custodian = "custodian_a"', 'custodian = ""')
        assert found_pairs(load_error(tmp_path, text)) == [
            ("C15", f"{ALPHA}.custodian")
        ]

    def test_invalid_registry_skips_c15(self, tmp_path: Path) -> None:
        text = replace(FULL_VALID, 'person = "custodian_a"', 'person = ""')
        found = load_error(tmp_path, text)
        assert found_pairs(found) == [("C12", "project.chains.HC1.person")]

    def test_solo_custodian_is_still_checked(self, tmp_path: Path) -> None:
        text = replace(
            SOLO_HOLDOUT,
            "restricted_files = []\n",
            'restricted_files = []\ncustodian = "nobody"\n',
        )
        assert found_pairs(load_error(tmp_path, text)) == [
            ("C15", f"{ALPHA}.custodian")
        ]


RETURNS = "studies.alpha.returns"


class TestC16Returns:
    def test_missing_returns(self, tmp_path: Path) -> None:
        text = MINIMAL_VALID.split("[studies.alpha.returns]")[0]
        assert found_pairs(load_error(tmp_path, text)) == [("C16", RETURNS)]

    @pytest.mark.parametrize(
        ("old", "new", "pair"),
        [
            pytest.param(
                "periods_per_year = 252",
                "periods_per_year = 0",
                ("C16", f"{RETURNS}.periods_per_year"),
                id="periods_0",
            ),
            pytest.param(
                "periods_per_year = 252",
                "periods_per_year = 252.0",
                ("C16", f"{RETURNS}.periods_per_year"),
                id="periods_float",
            ),
            pytest.param(
                'basis = "Daily close-to-close portfolio return"',
                'basis = ""',
                ("C16", f"{RETURNS}.basis"),
                id="empty_basis",
            ),
            pytest.param(
                'basis = "Daily close-to-close portfolio return"',
                'basis = "TODO(bgl): describe the return"',
                ("C20", f"{RETURNS}.basis"),
                id="placeholder_basis",
            ),
            pytest.param(
                'risk_free = "none"',
                "risk_free = 0",
                ("C16", f"{RETURNS}.risk_free"),
                id="risk_free_number",
            ),
            pytest.param(
                'risk_free = "none"',
                'risk_free = ""',
                ("C16", f"{RETURNS}.risk_free"),
                id="risk_free_empty",
            ),
            pytest.param(
                'risk_free_reasoning = "Self-financing long-short book"',
                'risk_free_reasoning = "TODO(bgl): why no risk-free rate"',
                ("C20", f"{RETURNS}.risk_free_reasoning"),
                id="placeholder_reasoning",
            ),
            pytest.param(
                'risk_free_reasoning = "Self-financing long-short book"',
                'risk_free_reasoning = "x"\nleverage = 2',
                ("C7", f"{RETURNS}.leverage"),
                id="unknown_key",
            ),
        ],
    )
    def test_bad_returns(
        self, tmp_path: Path, old: str, new: str, pair: tuple[str, str]
    ) -> None:
        text = replace(MINIMAL_VALID, old, new)
        assert found_pairs(load_error(tmp_path, text)) == [pair]

    def test_none_needs_reasoning(self, tmp_path: Path) -> None:
        text = without_line(
            MINIMAL_VALID, 'risk_free_reasoning = "Self-financing long-short book"'
        )
        assert found_pairs(load_error(tmp_path, text)) == [
            ("C16", f"{RETURNS}.risk_free_reasoning")
        ]

    def test_path_refuses_reasoning(self, tmp_path: Path) -> None:
        text = replace(MINIMAL_VALID, 'risk_free = "none"', 'risk_free = "data/rf.csv"')
        assert found_pairs(load_error(tmp_path, text)) == [
            ("C16", f"{RETURNS}.risk_free_reasoning")
        ]

    def test_missing_risk_free(self, tmp_path: Path) -> None:
        text = without_line(MINIMAL_VALID, 'risk_free = "none"')
        assert found_pairs(load_error(tmp_path, text)) == [
            ("C16", f"{RETURNS}.risk_free")
        ]


class TestC17Loader:
    def test_team_cannot_turn_witness_off(self, tmp_path: Path) -> None:
        text = replace(MINIMAL_VALID, 'mode = "solo"', 'mode = "team"')
        assert found_pairs(load_error(tmp_path, text)) == [("C17", "project.witness")]


class TestC18SpecFiles:
    @pytest.mark.parametrize(
        ("new", "key"),
        [
            pytest.param("", f"{ALPHA}.spec_files", id="missing"),
            pytest.param('spec_files = "a.md"\n', f"{ALPHA}.spec_files", id="string"),
            pytest.param(
                'spec_files = ["a.md", "./a.md"]\n',
                f"{ALPHA}.spec_files[1]",
                id="repeat",
            ),
        ],
    )
    def test_bad_spec_files(self, tmp_path: Path, new: str, key: str) -> None:
        text = replace(MINIMAL_VALID, "spec_files = []\n", new)
        assert found_pairs(load_error(tmp_path, text)) == [("C18", key)]


class TestC6Keys:
    def test_shared_prefix_reported_at_later_study(self, tmp_path: Path) -> None:
        text = replace(MINIMAL_VALID + SECOND_STUDY, 'prefix = "BE"', 'prefix = "AL"')
        assert found_pairs(load_error(tmp_path, text)) == [
            ("C6", "studies.beta.prefix")
        ]


def _floats_in(value: Any) -> list[Any]:
    if isinstance(value, float):
        return [value]
    if isinstance(value, dict):
        return [f for item in value.values() for f in _floats_in(item)]
    if isinstance(value, list):
        return [f for item in value for f in _floats_in(item)]
    return []


class TestC19Canonical:
    def test_full_study_has_no_floats(self, tmp_path: Path) -> None:
        table = canonical_study(load(tmp_path, FULL_VALID).studies["alpha"])
        assert _floats_in(table) == []
        json.dumps(table)
        assert table["holdout_start"] == "2025-01-01"
        assert table["gate"]["dsr_threshold"] == "0.95"
        assert table["gate"]["costs"]["cost_bps"] == "5.0"
        assert table["gate"]["cscv_blocks"] == 16
        assert table["custodian"] == "custodian_a"

    def test_minimal_study(self, tmp_path: Path) -> None:
        table = canonical_study(load(tmp_path, MINIMAL_VALID).studies["alpha"])
        assert table["holdout"] is False
        assert "gate" not in table
        assert "holdout_start" not in table
        assert table["returns"]["risk_free"] == "none"
        assert (
            table["returns"]["risk_free_reasoning"] == "Self-financing long-short book"
        )

    @pytest.mark.parametrize(
        ("old", "new"),
        [
            pytest.param("cost_bps = 5", "cost_bps = 5.0", id="int_vs_float"),
            pytest.param("cost_bps = 5", "cost_bps = 5.00", id="trailing_zero"),
            pytest.param("dsr_threshold = 0.95", "dsr_threshold = 0.950", id="dsr"),
            pytest.param(
                '"docs/studies/alpha.md"', '"docs/./studies/alpha.md"', id="untidy_path"
            ),
        ],
    )
    def test_harmless_reformat_gives_same_table(
        self, tmp_path: Path, old: str, new: str
    ) -> None:
        first = canonical_study(load(tmp_path, FULL_VALID).studies["alpha"])
        second = canonical_study(
            load(tmp_path, replace(FULL_VALID, old, new)).studies["alpha"]
        )
        assert first == second

    def test_real_change_gives_different_table(self, tmp_path: Path) -> None:
        first = canonical_study(load(tmp_path, FULL_VALID).studies["alpha"])
        second = canonical_study(
            load(tmp_path, replace(FULL_VALID, "cost_bps = 5", "cost_bps = 6")).studies[
                "alpha"
            ]
        )
        assert first != second

    def test_registry_matches_file_text(self, tmp_path: Path) -> None:
        registry = canonical_registry(load(tmp_path, FULL_VALID).project.writers)
        assert registry["HC1"] == {
            "person": "custodian_a",
            "key": "ed25519:AwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwM=",
        }

    @pytest.mark.parametrize(
        ("value", "text"),
        [
            pytest.param(0.95, "0.95", id="plain"),
            pytest.param(5, "5.0", id="int"),
            pytest.param(0.1 + 0.2, "0.30000000000000004", id="round_trip"),
            pytest.param(1e-7, "1e-07", id="small"),
        ],
    )
    def test_canonical_number(self, value: float, text: str) -> None:
        assert canonical_number(value) == text
        assert float(canonical_number(value)) == float(value)

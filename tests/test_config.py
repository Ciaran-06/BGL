import math
import os
import sys
from pathlib import Path
from typing import Any

import pytest
from helpers import (
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
    ConfigError,
    ConfigErrorGroup,
    ErrorCollector,
    check_number,
    check_path,
    check_path_list,
    check_placeholder,
    check_string,
    check_table,
    check_unknown_keys,
    check_whole_number,
    key_path,
    load_config,
    read_toml,
)

PHASE_2_NOT_WRITTEN = (
    "phase 2 validators not written yet; "
    "switch these to rule and key assertions when they are"
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


@pytest.mark.skip(reason=PHASE_2_NOT_WRITTEN)
class TestLoadConfig:
    def test_minimal_valid_loads(self, tmp_path: Path) -> None:
        config = load_config(write(tmp_path, MINIMAL_VALID))
        assert config.project.name == "example_project"
        assert config.studies["alpha"].budget == 20


@pytest.mark.skip(reason=PHASE_2_NOT_WRITTEN)
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


@pytest.mark.skip(reason=PHASE_2_NOT_WRITTEN)
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


@pytest.mark.skip(reason=PHASE_2_NOT_WRITTEN)
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


@pytest.mark.skip(reason=PHASE_2_NOT_WRITTEN)
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


@pytest.mark.skip(reason=PHASE_2_NOT_WRITTEN)
class TestC5Claims:
    @pytest.mark.parametrize(
        "value",
        [
            pytest.param('["effect_exists", "effect_exists"]', id="duplicate"),
            pytest.param('[""]', id="empty_string"),
            pytest.param('"effect_exists"', id="not_a_list"),
            pytest.param("[1]", id="not_strings"),
        ],
    )
    def test_bad_claims(self, tmp_path: Path, value: str) -> None:
        bad = replace(MINIMAL_VALID, 'claims = ["effect_exists"]', f"claims = {value}")
        assert_has_error(load_error(tmp_path, bad), "C5", "studies.alpha.claims")

    def test_empty_list_is_valid(self, tmp_path: Path) -> None:
        good = replace(MINIMAL_VALID, 'claims = ["effect_exists"]', "claims = []")
        load_config(write(tmp_path, good))


@pytest.mark.skip(reason=PHASE_2_NOT_WRITTEN)
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


@pytest.mark.skip(reason=PHASE_2_NOT_WRITTEN)
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
    @pytest.mark.skip(reason=PHASE_2_NOT_WRITTEN)
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
        "value",
        [
            pytest.param("bgl/ledger", id="simple"),
            pytest.param("bgl/./ledger", id="dot_segment"),
            pytest.param("a/b/../c", id="dotdot_stays_inside"),
            pytest.param("data/raw/prices.parquet", id="file"),
        ],
    )
    def test_valid(self, value: str) -> None:
        found = new_collector()
        assert check_path(found, {"ledger": value}, "ledger", "project") == value
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
    def test_real_text_passes(self) -> None:
        found = new_collector()
        assert check_placeholder(
            found, "https://rekor.example", "project.witness.rekor_url"
        )
        assert not found.has_errors()

    @pytest.mark.parametrize(
        "value",
        [
            pytest.param("ed25519:<public key printed by bgl join>", id="template_key"),
            pytest.param("<cited source>", id="template_basis"),
            pytest.param("x > y", id="greater_than"),
        ],
    )
    def test_angle_brackets_are_c20(self, value: str) -> None:
        found = new_collector()
        assert not check_placeholder(
            found, value, "studies.alpha.gate.basis.costs.text"
        )
        assert only_error(found) == (
            "C20",
            "studies.alpha.gate.basis.costs.text",
            value,
        )

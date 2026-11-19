from pathlib import Path

import pytest
from helpers import (
    assert_has_error,
    assert_no_error,
    replace,
    write,
    write_bytes,
)

from bgl.config import ConfigError, ConfigErrorGroup

FILE = Path("bgl.toml")


def error(rule: str, key: str | None = None) -> ConfigError:
    return ConfigError(FILE, rule, "something", key=key, found=1)


def group(*errors: ConfigError) -> ConfigErrorGroup:
    return ConfigErrorGroup(FILE, errors)


class TestWrite:
    def test_writes_bgl_toml_in_folder(self, tmp_path: Path) -> None:
        path = write(tmp_path, "a = 1\n")
        assert path == tmp_path / "bgl.toml"
        assert path.read_text(encoding="utf-8") == "a = 1\n"

    def test_uses_lf_line_endings_on_every_os(self, tmp_path: Path) -> None:
        path = write(tmp_path, "a = 1\nb = 2\n")
        assert path.read_bytes() == b"a = 1\nb = 2\n"

    def test_writes_utf8(self, tmp_path: Path) -> None:
        path = write(tmp_path, 'name = "Bailey and López de Prado"\n')
        assert path.read_bytes() == 'name = "Bailey and López de Prado"\n'.encode()


class TestWriteBytes:
    def test_writes_exact_bytes(self, tmp_path: Path) -> None:
        data = b"\xef\xbb\xbfa = 1\r\n\xff"
        path = write_bytes(tmp_path, data)
        assert path == tmp_path / "bgl.toml"
        assert path.read_bytes() == data


class TestReplace:
    def test_replaces_single_occurrence(self) -> None:
        assert replace("a = 1\nb = 2\n", "a = 1", "a = 9") == "a = 9\nb = 2\n"

    @pytest.mark.parametrize(
        ("fixture_text", "old_text", "count"),
        [
            pytest.param("a = 1\nb = 2\n", "c = 3", 0, id="missing"),
            pytest.param("x = 1\nx = 1\n", "x = 1", 2, id="twice"),
        ],
    )
    def test_wrong_count_raises(
        self, fixture_text: str, old_text: str, count: int
    ) -> None:
        with pytest.raises(ValueError, match=f"found {count} times"):
            replace(fixture_text, old_text, "new")

    def test_substring_match_is_replaced(self) -> None:
        # Documents the trap: include the whole value in the old text.
        assert replace("budget = 20\n", "budget = 2", "budget = 5") == "budget = 50\n"


class TestAssertHasError:
    def test_passes_on_matching_rule_in_group(self) -> None:
        assert_has_error(group(error("C4"), error("C3")), "C3")

    def test_passes_on_matching_rule_and_key(self) -> None:
        found = group(error("C3", "studies.alpha.budget"))
        assert_has_error(found, "C3", "studies.alpha.budget")

    def test_passes_on_single_config_error(self) -> None:
        assert_has_error(error("C8"), "C8")

    def test_fails_on_wrong_key(self) -> None:
        found = group(error("C3", "studies.alpha.budget"))
        with pytest.raises(AssertionError):
            assert_has_error(found, "C3", "studies.beta.budget")

    def test_fails_on_missing_rule(self) -> None:
        with pytest.raises(AssertionError):
            assert_has_error(group(error("C3")), "C7")

    def test_failure_lists_errors_found(self) -> None:
        found = group(error("C3", "studies.alpha.budget"), error("C4", "x.prefix"))
        with pytest.raises(AssertionError, match="C3 at studies.alpha.budget") as info:
            assert_has_error(found, "C7")
        assert "C4 at x.prefix" in str(info.value)

    def test_rejects_other_exception_types(self) -> None:
        with pytest.raises(TypeError):
            assert_has_error(ValueError("x"), "C3")


class TestAssertNoError:
    def test_passes_when_rule_absent(self) -> None:
        assert_no_error(group(error("C4")), "C6")

    def test_fails_when_rule_present(self) -> None:
        with pytest.raises(AssertionError, match="C6"):
            assert_no_error(group(error("C6", "studies.beta.prefix")), "C6")

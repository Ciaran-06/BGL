from pathlib import Path

import pytest
from helpers import replace, write, write_bytes


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

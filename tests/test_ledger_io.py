from pathlib import Path

import pytest

from bgl.config import Project
from bgl.ledger_io import (
    append_line,
    chain_files,
    chain_path,
    ledger_dir,
    read_chain,
    truncate_tail,
)

PROJECT = Project(
    name="p",
    mode="solo",
    ledger="bgl/ledger",
    runs_dir="bgl/runs",
    writers={},
    witness=None,
)


def test_paths(tmp_path: Path) -> None:
    assert ledger_dir(tmp_path, PROJECT) == tmp_path / "bgl" / "ledger"
    assert chain_path(tmp_path, PROJECT, "CG1") == (
        tmp_path / "bgl" / "ledger" / "CG1.jsonl"
    )


class TestReadChain:
    @pytest.mark.parametrize(
        ("data", "lines", "tail"),
        [
            pytest.param(b"", [], None, id="empty"),
            pytest.param(b"a\n", [b"a"], None, id="one_line"),
            pytest.param(b"a\nb\n", [b"a", b"b"], None, id="two_lines"),
            pytest.param(b"a\nb", [b"a"], b"b", id="torn_tail"),
            pytest.param(b"b", [], b"b", id="only_a_tail"),
            pytest.param(b"a\n\nb\n", [b"a", b"", b"b"], None, id="blank_line_kept"),
            pytest.param(b"a\r\n", [b"a\r"], None, id="crlf_kept_for_verify"),
        ],
    )
    def test_split(
        self, tmp_path: Path, data: bytes, lines: list[bytes], tail: bytes | None
    ) -> None:
        path = tmp_path / "CG1.jsonl"
        path.write_bytes(data)
        assert read_chain(path) == (lines, tail)

    def test_missing_file_is_empty(self, tmp_path: Path) -> None:
        assert read_chain(tmp_path / "CG1.jsonl") == ([], None)


class TestAppendLine:
    def test_creates_folder_and_file(self, tmp_path: Path) -> None:
        path = tmp_path / "bgl" / "ledger" / "CG1.jsonl"
        append_line(path, b"a\n")
        append_line(path, b"b\n")
        assert path.read_bytes() == b"a\nb\n"

    @pytest.mark.parametrize(
        "line",
        [
            pytest.param(b"a", id="no_newline"),
            pytest.param(b"a\nb\n", id="two_lines"),
            pytest.param(b"", id="empty"),
        ],
    )
    def test_refuses_anything_but_one_line(self, tmp_path: Path, line: bytes) -> None:
        with pytest.raises(ValueError):
            append_line(tmp_path / "CG1.jsonl", line)


class TestTruncateTail:
    def test_removes_tail(self, tmp_path: Path) -> None:
        path = tmp_path / "CG1.jsonl"
        path.write_bytes(b"a\nbroken")
        truncate_tail(path, 6)
        assert path.read_bytes() == b"a\n"

    def test_refuses_to_remove_a_complete_line(self, tmp_path: Path) -> None:
        path = tmp_path / "CG1.jsonl"
        path.write_bytes(b"a\nbroken")
        with pytest.raises(ValueError, match="complete line"):
            truncate_tail(path, 7)
        assert path.read_bytes() == b"a\nbroken"

    @pytest.mark.parametrize("length", [0, -1, 100])
    def test_bad_length(self, tmp_path: Path, length: int) -> None:
        path = tmp_path / "CG1.jsonl"
        path.write_bytes(b"a\nb")
        with pytest.raises(ValueError):
            truncate_tail(path, length)
        assert path.read_bytes() == b"a\nb"


def test_chain_files(tmp_path: Path) -> None:
    folder = tmp_path / "bgl" / "ledger"
    assert chain_files(tmp_path, PROJECT) == []
    folder.mkdir(parents=True)
    for name in ("HC1.jsonl", "CG1.jsonl", "notes.txt"):
        (folder / name).write_bytes(b"")
    (folder / "witness").mkdir()
    assert [p.name for p in chain_files(tmp_path, PROJECT)] == [
        "CG1.jsonl",
        "HC1.jsonl",
    ]

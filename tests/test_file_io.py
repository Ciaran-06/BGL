import os
import sys
from pathlib import Path

import pytest
from bgl.file_io import atomic_write, create_exclusive, fsync_dir

from bgl import file_io


class TestAtomicWrite:
    def test_writes_new_file(self, tmp_path: Path) -> None:
        path = tmp_path / "a.json"
        atomic_write(path, b"one")
        assert path.read_bytes() == b"one"

    def test_replaces_existing_file(self, tmp_path: Path) -> None:
        path = tmp_path / "a.json"
        path.write_bytes(b"old")
        atomic_write(path, b"new")
        assert path.read_bytes() == b"new"

    def test_leaves_no_temporary_file(self, tmp_path: Path) -> None:
        atomic_write(tmp_path / "a.json", b"x")
        assert [p.name for p in tmp_path.iterdir()] == ["a.json"]

    def test_failure_keeps_old_file_and_removes_temp(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "a.json"
        path.write_bytes(b"old")

        def broken_replace(src: object, dst: object) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(file_io.os, "replace", broken_replace)
        with pytest.raises(OSError, match="disk full"):
            atomic_write(path, b"new")
        assert path.read_bytes() == b"old"
        assert [p.name for p in tmp_path.iterdir()] == ["a.json"]

    def test_missing_folder_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            atomic_write(tmp_path / "nope" / "a.json", b"x")


class TestCreateExclusive:
    def test_creates(self, tmp_path: Path) -> None:
        path = tmp_path / "k"
        create_exclusive(path, b"secret")
        assert path.read_bytes() == b"secret"

    def test_never_overwrites(self, tmp_path: Path) -> None:
        path = tmp_path / "k"
        path.write_bytes(b"first")
        with pytest.raises(FileExistsError):
            create_exclusive(path, b"second")
        assert path.read_bytes() == b"first"

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
    def test_mode(self, tmp_path: Path) -> None:
        path = tmp_path / "k"
        create_exclusive(path, b"x", mode=0o600)
        assert path.stat().st_mode & 0o777 == 0o600


def test_fsync_dir_runs(tmp_path: Path) -> None:
    fsync_dir(tmp_path)
    assert os.path.isdir(tmp_path)

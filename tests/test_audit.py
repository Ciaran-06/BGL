import importlib.util
import os
import runpy
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from bgl import audit
from bgl.audit import Watch, loaded_repo_modules, python_prefixes


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "data").mkdir(parents=True)
    (root / "data" / "declared.csv").write_text("d\n")
    (root / "data" / "other.csv").write_text("o\n")
    (root / "data" / "holdout.csv").write_text("h\n")
    (root / "mod.py").write_text("VALUE = 1\n")
    return root


@pytest.fixture
def watching(repo: Path) -> Iterator[Watch]:
    watch = Watch(
        repo,
        declared=[repo / "data" / "declared.csv"],
        guarded={repo / "data" / "holdout.csv": "holdout"},
    )
    audit.start_watching(watch)
    try:
        yield watch
    finally:
        audit.stop_watching()


def read(path: Path) -> None:
    with open(path) as handle:
        handle.read()


class TestFilesOpened:
    def test_declared_input_is_not_recorded(self, repo: Path, watching: Watch) -> None:
        read(repo / "data" / "declared.csv")
        assert list(watching.files_opened) == []

    def test_undeclared_file_is_recorded_once_relative(
        self, repo: Path, watching: Watch
    ) -> None:
        read(repo / "data" / "other.csv")
        read(repo / "data" / "other.csv")
        assert list(watching.files_opened) == ["data/other.csv"]

    def test_file_outside_repo_is_recorded_absolute(
        self, tmp_path: Path, watching: Watch
    ) -> None:
        outside = tmp_path / "outside.csv"
        outside.write_text("x\n")
        read(outside)
        assert list(watching.files_opened) == [os.path.realpath(outside)]

    def test_python_installation_is_not_recorded(self, watching: Watch) -> None:
        # A data file (not .py) that ships inside site-packages.
        read(Path(pytest.__file__).parent / "py.typed")
        assert list(watching.files_opened) == []

    def test_os_open_and_pathlib_are_seen(self, repo: Path, watching: Watch) -> None:
        os.close(os.open(repo / "data" / "other.csv", os.O_RDONLY))
        (repo / "data" / "holdout.csv").read_text()
        assert "data/other.csv" in watching.files_opened
        assert "data/holdout.csv" in watching.files_opened

    def test_nothing_recorded_after_stop(self, repo: Path, watching: Watch) -> None:
        audit.stop_watching()
        read(repo / "data" / "other.csv")
        assert list(watching.files_opened) == []

    def test_pause_hides_bgls_own_reads(self, repo: Path, watching: Watch) -> None:
        with watching.pause():
            read(repo / "data" / "other.csv")
        assert list(watching.files_opened) == []
        read(repo / "data" / "other.csv")
        assert list(watching.files_opened) == ["data/other.csv"]

    def test_list_is_capped(
        self, repo: Path, watching: Watch, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(audit, "MAX_FILES_RECORDED", 1)
        read(repo / "data" / "other.csv")
        read(repo / "data" / "holdout.csv")
        assert len(watching.files_opened) == 1
        assert watching.files_truncated

    def test_no_hook_errors(self, repo: Path, watching: Watch) -> None:
        read(repo / "data" / "other.csv")
        os.close(os.open(repo / "data" / "other.csv", os.O_RDONLY))
        assert watching.hook_errors == 0


class TestGuardedFiles:
    def test_opening_a_guarded_file_is_a_violation(
        self, repo: Path, watching: Watch
    ) -> None:
        read(repo / "data" / "holdout.csv")
        assert watching.violations == [
            "R20: the run opened the holdout file data/holdout.csv"
        ]

    def test_allowed_after_run_holdout(self, repo: Path, watching: Watch) -> None:
        watching.allow_guarded(repo / "data" / "holdout.csv")
        read(repo / "data" / "holdout.csv")
        assert watching.violations == []

    def test_declared_guarded_file_is_still_a_violation(self, repo: Path) -> None:
        target = repo / "data" / "holdout.csv"
        watch = Watch(repo, declared=[target], guarded={target: "restricted"})
        audit.start_watching(watch)
        try:
            read(target)
        finally:
            audit.stop_watching()
        assert watch.violations == [
            "R20: the run opened the restricted file data/holdout.csv"
        ]


class TestCodeFiles:
    """The six ways of loading code that the spike tried (N9)."""

    def test_reading_source(self, repo: Path, watching: Watch) -> None:
        read(repo / "mod.py")
        assert list(watching.code_files) == ["mod.py"]
        assert list(watching.files_opened) == []

    def test_exec_of_source_text(self, repo: Path, watching: Watch) -> None:
        exec((repo / "mod.py").read_text())
        assert "mod.py" in watching.code_files

    def test_runpy(self, repo: Path, watching: Watch) -> None:
        runpy.run_path(str(repo / "mod.py"))
        assert "mod.py" in watching.code_files

    def test_importlib_without_sys_modules(self, repo: Path, watching: Watch) -> None:
        spec = importlib.util.spec_from_file_location("x_mod", repo / "mod.py")
        assert spec is not None and spec.loader is not None
        spec.loader.exec_module(importlib.util.module_from_spec(spec))
        assert "mod.py" in watching.code_files
        assert "x_mod" not in sys.modules

    def test_exec_event_alone_names_the_file(self, repo: Path, watching: Watch) -> None:
        code = compile("VALUE = 2\n", str(repo / "never_opened.py"), "exec")
        exec(code)
        assert "never_opened.py" in watching.code_files

    def test_cached_pyc_maps_to_its_source(self, repo: Path, watching: Watch) -> None:
        cache = repo / "__pycache__"
        cache.mkdir()
        pyc = Path(importlib.util.cache_from_source(str(repo / "mod.py")))
        pyc.write_bytes(b"")
        read(pyc)
        assert list(watching.code_files) == ["mod.py"]

    def test_code_outside_repo_is_ignored(
        self, tmp_path: Path, watching: Watch
    ) -> None:
        outside = tmp_path / "ext.py"
        outside.write_text("A = 1\n")
        runpy.run_path(str(outside))
        assert list(watching.code_files) == []


class TestDryRun:
    @pytest.fixture
    def dry(self, repo: Path, tmp_path: Path) -> Iterator[tuple[Watch, Path]]:
        fixture = tmp_path / "fixture.csv"
        fixture.write_text("f\n")
        watch = Watch(
            repo, declared=[repo / "data" / "declared.csv"], dry_allowed=[fixture]
        )
        audit.start_watching(watch)
        try:
            yield watch, fixture
        finally:
            audit.stop_watching()

    def test_fixture_and_python_files_open(
        self, repo: Path, dry: tuple[Watch, Path]
    ) -> None:
        read(dry[1])
        read(repo / "mod.py")
        read(Path(os.__file__))
        read(Path(os.devnull))

    def test_declared_input_itself_is_blocked(
        self, repo: Path, dry: tuple[Watch, Path]
    ) -> None:
        with pytest.raises(PermissionError, match="R25"):
            read(repo / "data" / "declared.csv")

    def test_any_other_file_is_blocked(
        self, repo: Path, dry: tuple[Watch, Path]
    ) -> None:
        with pytest.raises(PermissionError, match="R25"):
            read(repo / "data" / "other.csv")
        with pytest.raises(PermissionError):
            os.open(repo / "data" / "other.csv", os.O_RDONLY)

    def test_writing_is_blocked_too(self, repo: Path, dry: tuple[Watch, Path]) -> None:
        with pytest.raises(PermissionError):
            open(repo / "out.csv", "w")  # noqa: SIM115
        assert not (repo / "out.csv").exists()


class TestProcesses:
    def test_subprocess_start_is_counted(self, watching: Watch) -> None:
        subprocess.run([sys.executable, "-c", "pass"], check=True)
        record = watching.process_record()
        assert record["starts"].get("subprocess.Popen") == 1

    def test_worker_modules_reported(self, watching: Watch) -> None:
        import multiprocessing  # noqa: F401

        assert "multiprocessing" in watching.process_record()["worker_modules_loaded"]


class TestLoadedRepoModules:
    def test_finds_imported_module_and_ignores_site_packages(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.syspath_prepend(str(repo))
        import mod  # type: ignore[import-not-found]  # noqa: F401

        try:
            assert loaded_repo_modules(repo) == ["mod.py"]
        finally:
            del sys.modules["mod"]
        assert loaded_repo_modules(repo) == []

    def test_site_packages_inside_the_root_are_left_out(self) -> None:
        # pytest lives in site-packages; treat the folder above it as the "repo".
        site_root = Path(pytest.__file__).resolve().parent.parent
        assert loaded_repo_modules(site_root) == []


def test_python_prefixes_end_with_separator() -> None:
    prefixes = python_prefixes()
    assert prefixes
    assert all(prefix.endswith(os.sep) for prefix in prefixes)

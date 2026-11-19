import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from conftest import Project, run_git

from bgl import setup
from bgl.home import create_machine_id
from bgl.verify import _safe_relative, verify_project


def checks(project: Project, **kwargs: object) -> list[tuple[str, str]]:
    report = verify_project(project.root, **kwargs)  # type: ignore[arg-type]
    return [(f.check, f.message) for f in report.failures]


def failing(project: Project) -> list[str]:
    return [check for check, _ in checks(project)]


def warning_checks(project: Project) -> list[str]:
    return [f.check for f in verify_project(project.root).warnings]


def test_clean_project_passes(project: Project) -> None:
    project.run()
    with project.start(run_type="exploratory") as run:
        run.returns(
            gross={1: {"2020-01-02": 0.1}, 2: {"2020-01-02": 0.2}},
            turnover={1: {"2020-01-02": 0}, 2: {"2020-01-02": 0}},
        )
        run.result("done")
    report = verify_project(project.root)
    assert report.ok, report.failures
    assert report.warnings == ()


def test_empty_project_passes(project: Project) -> None:
    assert verify_project(project.root).ok


class TestChains:
    def test_broken_chain(self, project: Project) -> None:
        project.run()
        path = project.root / "bgl" / "ledger" / "CG1.jsonl"
        path.write_bytes(path.read_bytes().replace(b"Lookback grid", b"Lookback grit"))
        assert ("chain") in failing(project)

    def test_unregistered_chain_file(self, project: Project) -> None:
        project.run()
        (project.root / "bgl" / "ledger" / "XX9.jsonl").write_bytes(b"")
        messages = checks(project)
        assert any("XX9.jsonl" in m for c, m in messages if c == "chain")


class TestPairing:
    def append(self, project: Project, **fields: object) -> None:
        from bgl.config import load_config
        from bgl.ledger import append_row
        from bgl.writers import load_identity

        config = load_config(project.root / "bgl.toml")
        identity = load_identity(project.root, config.project)
        append_row(
            project.root,
            config.project,
            identity.writer,
            identity.private_key,
            {"machine_id": identity.machine_id, **fields},
        )

    def test_hard_killed_run_is_unclosed(self, project: Project) -> None:
        script = textwrap.dedent(
            f"""
            import os
            from bgl import start_run
            with start_run(study="alpha", run_type="exploratory",
                           script={str(project.root / "scripts" / "run.py")!r},
                           what_changed="w", sample="design",
                           sample_start="2020-01-01", sample_end="2020-12-31",
                           variants=1):
                os._exit(0)
            """
        )
        subprocess.run([sys.executable, "-c", script], check=True, env=os.environ)
        assert project.events() == ["open"]
        messages = checks(project)
        assert any(
            c == "unclosed" and "AL-X001-CG1" in m and "abandon" in m
            for c, m in messages
        )

    def test_run_in_progress_is_not_a_failure(self, project: Project) -> None:
        with project.start() as run:
            report = verify_project(project.root)
            assert report.in_progress == ("AL-R001-CG1",)
            assert report.ok
            run.result("done")

    def test_open_from_another_machine_cannot_be_told(self, project: Project) -> None:
        with project.start() as run:
            (project.home / "machine_id").unlink()
            create_machine_id(project.home)
            messages = checks(project)
            assert any("another machine" in m for c, m in messages if c == "unclosed")
            run.result("done")

    def test_synced_folder_cannot_be_told(
        self, project: Project, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        with project.start() as run:
            monkeypatch.setattr(setup, "_is_network", lambda path: True)
            assert "unclosed" in failing(project)
            monkeypatch.undo()
            run.result("done")

    def test_close_without_open(self, project: Project) -> None:
        self.append(project, event="close", id="AL-R009-CG1", status="ok")
        assert any("no open row" in m for _, m in checks(project))

    def test_two_closes(self, project: Project) -> None:
        run_id = project.run()
        self.append(project, event="close", id=run_id, status="ok")
        assert any("2 close rows" in m for _, m in checks(project))

    def test_late_close_until_acknowledged(self, project: Project) -> None:
        run_id = project.run()
        self.append(project, event="close", id=run_id, status="abandoned")
        assert "late close" in failing(project)
        self.append(project, event="ack", id=run_id, reason="reviewed")
        assert "late close" not in failing(project)


class TestFiles:
    def runs(self, project: Project) -> Path:
        return project.root / "bgl" / "runs"

    def counted_run_with_series(self, project: Project) -> str:
        with project.start() as run:
            print("output")
            run.returns(
                gross={1: {"2020-01-02": 0.1}, 2: {"2020-01-02": 0.2}},
                turnover={1: {"2020-01-02": 0}, 2: {"2020-01-02": 0}},
            )
            run.result("done")
            return run.id

    @pytest.mark.parametrize("suffix", [".json", ".close.json"])
    def test_edited_committed_file_fails(self, project: Project, suffix: str) -> None:
        run_id = project.run()
        path = self.runs(project) / f"{run_id}{suffix}"
        path.write_bytes(path.read_bytes().replace(b"}", b" }", 1))
        assert "hash" in failing(project)

    @pytest.mark.parametrize("suffix", [".json", ".close.json"])
    def test_missing_committed_file_fails(self, project: Project, suffix: str) -> None:
        run_id = project.run()
        (self.runs(project) / f"{run_id}{suffix}").unlink()
        assert "missing" in failing(project)

    @pytest.mark.parametrize(
        "relative", ["returns/AL-R001-CG1.csv", "output/AL-R001-CG1.stdout"]
    )
    def test_edited_ignored_file_fails(self, project: Project, relative: str) -> None:
        self.counted_run_with_series(project)
        path = self.runs(project) / relative
        path.write_bytes(path.read_bytes() + b"x")
        assert "hash" in failing(project)

    def test_missing_ignored_files_are_not_checkable(self, project: Project) -> None:
        self.counted_run_with_series(project)
        shutil.rmtree(self.runs(project) / "returns")
        shutil.rmtree(self.runs(project) / "output")
        report = verify_project(project.root)
        assert report.ok
        assert [f.check for f in report.warnings] == ["not checkable"] * 3

    @pytest.mark.parametrize(
        "name",
        ["AL-R009-CG1.json", "AL-R009-CG1.close.json"],
    )
    def test_orphans_are_warnings(self, project: Project, name: str) -> None:
        project.run()
        (self.runs(project) / name).write_text("{}\n")
        report = verify_project(project.root)
        assert report.ok
        assert [f.check for f in report.warnings] == ["orphan"]


class TestCommits:
    def test_unreachable_commit_fails(self, project: Project) -> None:
        run_git(project.root, "checkout", "-q", "-b", "feature")
        project.write("src/feature.py", "x = 1\n")
        project.commit("feature")
        project.run()
        project.commit("ledger on feature")
        run_git(project.root, "checkout", "-q", "main")
        # Bring the ledger and run files over without the feature commits.
        run_git(project.root, "checkout", "feature", "--", "bgl")
        run_git(project.root, "branch", "-q", "-D", "feature")
        messages = checks(project)
        assert any("not reachable" in m for c, m in messages if c == "commit")

    def test_shallow_clone_fails_with_advice(
        self, project: Project, tmp_path: Path
    ) -> None:
        project.run()
        project.commit("ledger")
        project.commit("one more")
        clone = tmp_path / "clone"
        run_git(
            tmp_path, "clone", "-q", "--depth", "1", project.root.as_uri(), str(clone)
        )
        messages = [m for c, m in checks_at(clone) if c == "commit"]
        assert messages and "unshallow" in messages[0]


def checks_at(root: Path) -> list[tuple[str, str]]:
    return [(f.check, f.message) for f in verify_project(root).failures]


class TestLocks:
    def test_changed_locked_value_fails(self, project: Project) -> None:
        project.run()
        project.edit_toml("budget = 3", "budget = 9")
        project.commit("budget")
        messages = checks(project)
        assert any("budget" in m for c, m in messages if c == "lock")

    def test_changed_spec_file_is_a_warning(self, project: Project) -> None:
        project.run()
        project.write("docs/alpha.md", "revised\n")
        project.commit("revise")
        report = verify_project(project.root)
        assert report.ok
        assert [f.check for f in report.warnings] == ["lock"]

    def test_locked_study_removed_from_config(self, project: Project) -> None:
        project.run(study="beta")
        text = (project.root / "bgl.toml").read_text()
        start = text.index("[studies.beta]")
        (project.root / "bgl.toml").write_text(text[:start])
        project.commit("drop beta")
        messages = checks(project)
        assert any("no longer" in m for c, m in messages if c == "lock")


class TestContains:
    def test_found(self, project: Project) -> None:
        project.run()
        head = project.last()["hash"]
        report = verify_project(project.root, contains=head)
        assert report.ok
        assert report.contains == ("CG1 line 3",)

    def test_not_found_fails(self, project: Project) -> None:
        project.run()
        assert "contains" in [c for c, _ in checks(project, contains="0" * 64)]

    def test_bad_hash(self, project: Project) -> None:
        with pytest.raises(ValueError, match="hex hash"):
            verify_project(project.root, contains="xyz")


@pytest.mark.parametrize(
    ("path", "ok"),
    [
        ("bgl/runs/AL-R001-CG1.json", True),
        ("bgl/./runs/x.json", True),
        ("../x.json", False),
        ("/etc/passwd", False),
        ("C:/x.json", False),
        ("bgl\\runs\\x.json", False),
        ("", False),
        (None, False),
    ],
)
def test_safe_relative(path: object, ok: bool) -> None:
    assert (_safe_relative(path) is not None) == ok

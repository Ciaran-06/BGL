from pathlib import Path

import pytest
from conftest import Project, run_git

from bgl.cli import EXIT_FAILED, EXIT_OK, EXIT_USAGE, main
from bgl.home import HOME_ENV


def run_cli(monkeypatch: pytest.MonkeyPatch, cwd: Path, *argv: str) -> int:
    monkeypatch.chdir(cwd)
    return main(list(argv))


def test_init_then_status_and_verify(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    git_env: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv(HOME_ENV, str(tmp_path / "home"))
    repo = tmp_path / "repo"
    repo.mkdir()
    run_git(repo, "init", "-q")
    assert run_cli(monkeypatch, repo, "init", "CG1", "Ciaran") == EXIT_OK
    out = capsys.readouterr().out
    assert "Created bgl.toml" in out and "Back up your private key" in out
    assert run_cli(monkeypatch, repo, "status") == EXIT_OK
    assert "No studies declared yet" in capsys.readouterr().out
    assert run_cli(monkeypatch, repo, "verify") == EXIT_OK
    assert "verify passed" in capsys.readouterr().out
    assert run_cli(monkeypatch, repo, "init", "CG1", "Ciaran") == EXIT_FAILED
    assert "bgl join" in capsys.readouterr().err


def test_join_prints_the_entry(
    project: Project,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (project.root / ".bgl" / "writer").unlink()
    assert run_cli(monkeypatch, project.root, "join", "CG2", "Bea") == EXIT_OK
    out = capsys.readouterr().out
    assert "[project.chains.CG2]" in out and 'person = "Bea"' in out


def test_verify_fails_with_exit_code_1(
    project: Project,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    project.run()
    chain = project.root / "bgl" / "ledger" / "CG1.jsonl"
    chain.write_bytes(chain.read_bytes() + b'{"torn"')
    assert run_cli(monkeypatch, project.root, "verify") == EXIT_FAILED
    out = capsys.readouterr().out
    assert "FAIL (chain)" in out and "torn tail" in out


def test_repair(
    project: Project,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    project.run()
    chain = project.root / "bgl" / "ledger" / "CG1.jsonl"
    chain.write_bytes(chain.read_bytes() + b'{"torn"')
    assert run_cli(monkeypatch, project.root, "repair") == EXIT_OK
    assert "7 bytes" in capsys.readouterr().out
    assert project.last()["event"] == "repair"
    assert run_cli(monkeypatch, project.root, "verify") == EXIT_OK
    assert run_cli(monkeypatch, project.root, "repair") == EXIT_FAILED
    assert "nothing to repair" in capsys.readouterr().err


def test_status_shows_studies_and_chains(
    project: Project,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    project.run()
    assert run_cli(monkeypatch, project.root, "status") == EXIT_OK
    out = capsys.readouterr().out
    assert "Study alpha (locked)" in out
    assert "Budget: 1 of 3 counted runs used, 2 left" in out
    assert "N: 2 trials (0 prior)" in out
    assert "Chain CG1: 3 rows" in out
    assert "uncommitted ledger changes" in out


def test_invalid_config_lists_every_error(
    project: Project,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    project.edit_toml("budget = 3", "budget = -1")
    project.edit_toml('prefix = "AL"', 'prefix = "al"')
    assert run_cli(monkeypatch, project.root, "status") == EXIT_FAILED
    err = capsys.readouterr().err
    assert "C3" in err and "C4" in err


def test_outside_a_repository(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    git_env: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert run_cli(monkeypatch, tmp_path, "status") == EXIT_FAILED
    assert "not a git repository" in capsys.readouterr().err


def test_usage_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for argv in ([], ["fly"], ["init", "CG1"]):
        with pytest.raises(SystemExit) as info:
            run_cli(monkeypatch, tmp_path, *argv)
        assert info.value.code == EXIT_USAGE

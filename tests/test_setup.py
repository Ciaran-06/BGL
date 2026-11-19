import sys
from pathlib import Path

import pytest
from conftest import Project, run_git
from helpers import assert_has_error

from bgl import setup
from bgl.config import ConfigErrorGroup, load_config
from bgl.errors import SetupError
from bgl.home import HOME_ENV, load_machine_id, load_private_key
from bgl.setup import init_project, join_writer, registry_entry, sync_warning


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, git_env: None) -> Path:
    """An empty git repository and an empty home folder."""
    monkeypatch.setenv(HOME_ENV, str(tmp_path / "home"))
    root = tmp_path / "my_project"
    root.mkdir()
    run_git(root, "init", "-q", "-b", "main")
    return root


class TestInit:
    def test_creates_everything_and_loads(self, repo: Path) -> None:
        result = init_project(repo, "CG1", "Ciaran Gaffney")
        config = load_config(repo / "bgl.toml")
        assert config.project.name == "my_project"
        assert config.project.mode == "solo"
        assert config.project.witness is None
        assert dict(config.studies) == {}
        writer = config.project.writers["CG1"]
        assert writer.person == "Ciaran Gaffney"
        load_private_key(Path(str(repo.parent / "home")), writer)
        assert (repo / "bgl" / "ledger" / "CG1.jsonl").read_bytes() == b""
        assert (repo / ".bgl" / "writer").read_text() == "CG1\n"
        assert (repo / "scratch").is_dir()
        assert result.created == ("bgl.toml", "bgl/ledger/CG1.jsonl", ".bgl/writer")
        assert result.registry_entry in (repo / "bgl.toml").read_text()

    def test_ignore_and_attribute_entries(self, repo: Path) -> None:
        init_project(repo, "CG1", "p")
        assert (repo / ".gitignore").read_text().splitlines() == [
            ".bgl/",
            "scratch/",
            "bgl/runs/returns/",
            "bgl/runs/output/",
            "*.sealed",
        ]
        assert (repo / ".gitattributes").read_text().splitlines() == [
            "bgl/ledger/** -text",
            "bgl/runs/** -text",
        ]

    def test_appends_only_missing_lines(self, repo: Path) -> None:
        (repo / ".gitignore").write_text("__pycache__/\nscratch/")  # no newline
        result = init_project(repo, "CG1", "p")
        lines = (repo / ".gitignore").read_text().splitlines()
        assert lines.count("scratch/") == 1
        assert lines[:2] == ["__pycache__/", "scratch/"]
        assert ".gitignore: scratch/" not in result.appended
        assert ".gitignore: .bgl/" in result.appended

    def test_example_study_loads_once_filled_in(self, repo: Path) -> None:
        init_project(repo, "CG1", "p")
        path = repo / "bgl.toml"
        text = path.read_text()
        start = text.index("# [studies.alpha]")
        body = "\n".join(
            line[2:] if line.startswith("# ") else line
            for line in text[start:].splitlines()
        )
        path.write_text(text[:start] + body + "\n")
        with pytest.raises(ConfigErrorGroup) as info:
            load_config(path)
        assert_has_error(info.value, "C20", "studies.alpha.returns.basis")
        path.write_text(path.read_text().replace("TODO(bgl)", "Filled in"))
        assert "alpha" in load_config(path).studies

    def test_runs_at_the_repository_root_from_a_subfolder(self, repo: Path) -> None:
        (repo / "sub").mkdir()
        init_project(repo / "sub", "CG1", "p")
        assert (repo / "bgl.toml").exists()

    def test_never_overwrites_bgl_toml(self, repo: Path) -> None:
        (repo / "bgl.toml").write_text("mine\n")
        with pytest.raises(SetupError, match="bgl join"):
            init_project(repo, "CG1", "p")
        assert (repo / "bgl.toml").read_text() == "mine\n"
        assert not (repo / ".bgl").exists()

    def test_never_overwrites_the_writer_file(self, repo: Path) -> None:
        (repo / ".bgl").mkdir()
        (repo / ".bgl" / "writer").write_text("XX1\n")
        with pytest.raises(SetupError, match="already exists"):
            init_project(repo, "CG1", "p")
        assert not (repo / "bgl.toml").exists()

    @pytest.mark.parametrize("code", ["cg1", "C", "1AB", "ABCDEFG", "C-1"])
    def test_bad_code(self, repo: Path, code: str) -> None:
        with pytest.raises(SetupError, match="chain code"):
            init_project(repo, code, "p")
        assert not (repo / "bgl.toml").exists()

    @pytest.mark.parametrize("person", ["", "   ", "two\nlines"])
    def test_bad_person(self, repo: Path, person: str) -> None:
        with pytest.raises(SetupError, match="person"):
            init_project(repo, "CG1", person)

    def test_awkward_names_are_escaped(self, repo: Path) -> None:
        name = 'Ann "the quant" O\'Neil \\ é 😀'
        init_project(repo, "CG1", name)
        config = load_config(repo / "bgl.toml")
        assert config.project.writers["CG1"].person == name

    def test_reuses_an_existing_machine_id(self, repo: Path) -> None:
        home = repo.parent / "home"
        init_project(repo, "CG1", "p")
        first = load_machine_id(home)
        other = repo.parent / "other"
        other.mkdir()
        run_git(other, "init", "-q")
        init_project(other, "CG1", "p")
        assert load_machine_id(home) == first

    def test_not_a_git_repository(self, tmp_path: Path, git_env: None) -> None:
        plain = tmp_path / "plain"
        plain.mkdir()
        with pytest.raises(Exception, match="not a git repository"):
            init_project(plain, "CG1", "p")


class TestJoin:
    def test_prints_entry_and_sets_up_writer(self, project: Project) -> None:
        (project.root / ".bgl" / "writer").unlink()
        result = join_writer(project.root, "CG2", "researcher_b")
        assert result.created == (".bgl/writer",)
        assert (project.root / ".bgl" / "writer").read_text() == "CG2\n"
        assert result.registry_entry.startswith("[project.chains.CG2]\n")
        assert 'person = "researcher_b"' in result.registry_entry
        assert "CG2" not in (project.root / "bgl.toml").read_text()

    def test_entry_once_added_lets_the_writer_run(self, project: Project) -> None:
        (project.root / ".bgl" / "writer").unlink()
        result = join_writer(project.root, "CG2", "researcher_b")
        project.edit_toml(
            "[studies.alpha]", result.registry_entry + "\n[studies.alpha]"
        )
        project.commit("register CG2")
        project.run(run_type="exploratory")
        assert project.rows("CG2")[0]["chain"] == "CG2"

    def test_registered_code_is_refused(self, project: Project) -> None:
        (project.root / ".bgl" / "writer").unlink()
        with pytest.raises(SetupError, match="already registered"):
            join_writer(project.root, "CG1", "researcher_a")

    def test_existing_writer_file_is_refused(self, project: Project) -> None:
        with pytest.raises(SetupError, match="already has a writer"):
            join_writer(project.root, "CG2", "researcher_b")
        assert (project.root / ".bgl" / "writer").read_text() == "CG1\n"

    def test_needs_a_valid_config(self, project: Project) -> None:
        (project.root / ".bgl" / "writer").unlink()
        (project.root / "bgl.toml").write_text("not = [valid")
        with pytest.raises(Exception, match="C9"):
            join_writer(project.root, "CG2", "p")


def test_registry_entry_format() -> None:
    entry = registry_entry("CG1", "p", bytes(32))
    assert entry == (
        "[project.chains.CG1]\n"
        'person = "p"\n'
        'key = "ed25519:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="\n'
    )


class TestSyncWarning:
    @pytest.mark.parametrize(
        "folder",
        [
            "OneDrive",
            "OneDrive - University",
            "Dropbox",
            "Google Drive",
            "My Drive",
            "iCloud Drive",
        ],
    )
    def test_synced_folder_names(self, tmp_path: Path, folder: str) -> None:
        path = tmp_path / folder / "project"
        path.mkdir(parents=True)
        warning = sync_warning(path)
        assert warning is not None and "synced" in warning

    def test_ordinary_folder(self, tmp_path: Path) -> None:
        assert sync_warning(tmp_path) is None

    def test_network_drive(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(setup, "_is_network", lambda path: True)
        warning = sync_warning(tmp_path)
        assert warning is not None and "network" in warning

    @pytest.mark.skipif(sys.platform != "linux", reason="reads /proc/mounts")
    def test_local_linux_disk_is_not_network(self, tmp_path: Path) -> None:
        assert not setup._is_network(tmp_path)

    def test_init_reports_the_warning(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(setup, "_is_network", lambda path: True)
        assert init_project(repo, "CG1", "p").warnings


def test_starter_witness_table_loads_once_uncommented(repo: Path) -> None:
    init_project(repo, "CG1", "p")
    path = repo / "bgl.toml"
    text = path.read_text()
    start = text.index("# [project.witness]")
    end = text.index("witness = false\n")
    table = "\n".join(line[2:] for line in text[start:end].strip().splitlines())
    path.write_text(
        text[:start] + text[end + len("witness = false\n") :] + "\n" + table + "\n"
    )
    witness = load_config(path).project.witness
    assert witness is not None
    assert witness.rekor_url == "https://rekor.sigstore.dev"
    assert len(witness.ots_calendars) == 2

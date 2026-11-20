import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from bgl.canonical import canonical_json
from bgl.errors import GitError, GitMissingError
from bgl.git import (
    changed_paths,
    code_fingerprint,
    commit_exists,
    commit_signature,
    git_user,
    head_commit,
    is_reachable,
    is_shallow,
    is_tracked,
    python_blobs,
    repo_root,
    uncommitted_changes,
    untracked_python,
)

EXEMPT = ("bgl/ledger", "bgl/runs")


@pytest.fixture(autouse=True)
def isolated_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the developer's own git config (signing, hooks, user) out of tests."""
    config = tmp_path / "gitconfig"
    config.write_text("")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        monkeypatch.delenv(name, raising=False)


def git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def write(root: Path, path: str, text: str = "x\n") -> None:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def commit_all(root: Path, message: str = "c") -> str:
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", message)
    return git(root, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.name", "Test User")
    git(root, "config", "user.email", "test@example.com")
    git(root, "config", "commit.gpgsign", "false")
    write(root, "src/model.py", "a = 1\n")
    write(root, "data/prices.csv", "p\n")
    write(root, "bgl/ledger/CG1.jsonl", "{}\n")
    write(root, "bgl/runs/AL-R001-CG1.json", "{}\n")
    commit_all(root, "first")
    return root


class TestRepoBasics:
    def test_repo_root_from_subfolder(self, repo: Path) -> None:
        assert repo_root(repo / "src").resolve() == repo.resolve()

    def test_not_a_repo(self, tmp_path: Path) -> None:
        outside = tmp_path / "plain"
        outside.mkdir()
        with pytest.raises(GitError):
            repo_root(outside)

    def test_head_commit(self, repo: Path) -> None:
        assert head_commit(repo) == git(repo, "rev-parse", "HEAD")
        assert len(head_commit(repo)) == 40

    def test_no_commits_yet(self, tmp_path: Path) -> None:
        empty = tmp_path / "empty"
        empty.mkdir()
        git(empty, "init", "-q")
        with pytest.raises(GitError, match="no commits"):
            head_commit(empty)

    def test_git_missing_is_never_a_no(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PATH", "")
        for call in (
            lambda: head_commit(repo),
            lambda: git_user(repo),
            lambda: commit_exists(repo, "0" * 40),
        ):
            with pytest.raises(GitMissingError):
                call()


class TestGitUser:
    def test_configured(self, repo: Path) -> None:
        assert git_user(repo) == "Test User <test@example.com>"

    @pytest.mark.parametrize("key", ["user.name", "user.email"])
    def test_missing_is_refused(self, repo: Path, key: str) -> None:
        git(repo, "config", "--unset", key)
        with pytest.raises(GitError, match="git config user.name"):
            git_user(repo)


class TestUncommittedChanges:
    def test_clean(self, repo: Path) -> None:
        assert uncommitted_changes(repo, EXEMPT) == []

    def test_modified_tracked_file(self, repo: Path) -> None:
        write(repo, "src/model.py", "a = 2\n")
        assert uncommitted_changes(repo, EXEMPT) == ["src/model.py"]

    def test_staged_new_file(self, repo: Path) -> None:
        write(repo, "src/new.py")
        git(repo, "add", "src/new.py")
        assert uncommitted_changes(repo, EXEMPT) == ["src/new.py"]

    def test_deleted_tracked_file(self, repo: Path) -> None:
        (repo / "data" / "prices.csv").unlink()
        assert uncommitted_changes(repo, EXEMPT) == ["data/prices.csv"]

    def test_untracked_files_are_allowed(self, repo: Path) -> None:
        write(repo, "data/new.csv")
        write(repo, "notes.txt")
        assert uncommitted_changes(repo, EXEMPT) == []

    def test_bgl_folders_are_exempt(self, repo: Path) -> None:
        write(repo, "bgl/ledger/CG1.jsonl", "{}\n{}\n")
        write(repo, "bgl/runs/AL-R001-CG1.json", "[]\n")
        assert uncommitted_changes(repo, EXEMPT) == []

    def test_similar_folder_name_is_not_exempt(self, repo: Path) -> None:
        write(repo, "bgl/ledgerx/a.txt")
        git(repo, "add", "bgl/ledgerx/a.txt")
        assert uncommitted_changes(repo, EXEMPT) == ["bgl/ledgerx/a.txt"]

    def test_rename_lists_both_paths(self, repo: Path) -> None:
        git(repo, "mv", "src/model.py", "src/renamed.py")
        assert uncommitted_changes(repo, EXEMPT) == ["src/model.py", "src/renamed.py"]

    def test_unusual_file_name(self, repo: Path) -> None:
        name = "src/my model é.py"
        write(repo, name)
        commit_all(repo)
        write(repo, name, "changed\n")
        assert uncommitted_changes(repo, EXEMPT) == [name]

    @pytest.mark.parametrize("bad", [".", ""])
    def test_repository_root_cannot_be_exempt(self, repo: Path, bad: str) -> None:
        with pytest.raises(ValueError):
            uncommitted_changes(repo, (bad,))


class TestChangedPaths:
    def test_new_and_modified(self, repo: Path) -> None:
        write(repo, "bgl/ledger/CG1.jsonl", "{}\n{}\n")
        write(repo, "bgl/ledger/witness/CG1/a.json")
        write(repo, "src/model.py", "a = 3\n")  # outside the folder
        assert changed_paths(repo, "bgl/ledger") == [
            "bgl/ledger/CG1.jsonl",
            "bgl/ledger/witness/CG1/a.json",
        ]

    def test_rename_lists_both_paths_whole(self, repo: Path) -> None:
        """F16: the rename's second path is read as a path, not a record."""
        git(repo, "mv", "bgl/ledger/CG1.jsonl", "bgl/ledger/CG2.jsonl")
        assert changed_paths(repo, "bgl/ledger") == [
            "bgl/ledger/CG1.jsonl",
            "bgl/ledger/CG2.jsonl",
        ]

    def test_clean(self, repo: Path) -> None:
        assert changed_paths(repo, "bgl/ledger") == []


class TestIsTracked:
    def test_tracked(self, repo: Path) -> None:
        assert is_tracked(repo, "src/model.py")

    def test_untracked(self, repo: Path) -> None:
        write(repo, "src/other.py")
        assert not is_tracked(repo, "src/other.py")

    def test_folder_is_not_a_tracked_file(self, repo: Path) -> None:
        assert not is_tracked(repo, "src")

    def test_missing(self, repo: Path) -> None:
        assert not is_tracked(repo, "src/nope.py")


class TestUntrackedPython:
    def test_lists_untracked_python_only(self, repo: Path) -> None:
        write(repo, "try.py")
        write(repo, "notebooks/explore.py")
        write(repo, "data/new.csv")
        write(repo, "scratch/ignored.py")
        write(repo, ".gitignore", "scratch/\n")
        assert untracked_python(repo) == ["notebooks/explore.py", "try.py"]

    def test_none(self, repo: Path) -> None:
        assert untracked_python(repo) == []


class TestCodeFingerprint:
    def test_python_blobs_from_commit(self, repo: Path) -> None:
        write(repo, "src/a.py", "b = 2\n")
        write(repo, "bgl/ledger/tool.py", "c = 3\n")
        commit = commit_all(repo)
        blobs = python_blobs(repo, commit, EXEMPT)
        assert [path for path, _ in blobs] == ["src/a.py", "src/model.py"]
        assert blobs[0][1] == git(repo, "rev-parse", f"{commit}:src/a.py")

    def test_fingerprint_is_sha256_of_canonical_json(self, repo: Path) -> None:
        commit = head_commit(repo)
        expected = hashlib.sha256(
            canonical_json(python_blobs(repo, commit, EXEMPT))
        ).hexdigest()
        assert code_fingerprint(repo, commit, EXEMPT) == expected

    def test_python_change_changes_it(self, repo: Path) -> None:
        before = code_fingerprint(repo, head_commit(repo), EXEMPT)
        write(repo, "src/model.py", "a = 99\n")
        assert code_fingerprint(repo, commit_all(repo), EXEMPT) != before

    def test_data_change_does_not(self, repo: Path) -> None:
        before = code_fingerprint(repo, head_commit(repo), EXEMPT)
        write(repo, "data/prices.csv", "q\n")
        assert code_fingerprint(repo, commit_all(repo), EXEMPT) == before

    def test_uncommitted_edit_does_not(self, repo: Path) -> None:
        before = code_fingerprint(repo, head_commit(repo), EXEMPT)
        write(repo, "src/model.py", "a = 99\n")
        assert code_fingerprint(repo, head_commit(repo), EXEMPT) == before

    def test_older_commit_is_reproducible(self, repo: Path) -> None:
        first = head_commit(repo)
        before = code_fingerprint(repo, first, EXEMPT)
        write(repo, "src/model.py", "a = 99\n")
        commit_all(repo)
        assert code_fingerprint(repo, first, EXEMPT) == before


class TestReachability:
    def test_head_is_reachable(self, repo: Path) -> None:
        assert is_reachable(repo, head_commit(repo))

    def test_commit_on_deleted_branch(self, repo: Path) -> None:
        git(repo, "checkout", "-q", "-b", "feature")
        write(repo, "src/f.py")
        lost = commit_all(repo)
        git(repo, "checkout", "-q", "main")
        git(repo, "branch", "-q", "-D", "feature")
        assert commit_exists(repo, lost)
        assert not is_reachable(repo, lost)

    def test_tag_keeps_it_reachable(self, repo: Path) -> None:
        git(repo, "checkout", "-q", "-b", "feature")
        write(repo, "src/f.py")
        kept = commit_all(repo)
        git(repo, "tag", "keep")
        git(repo, "checkout", "-q", "main")
        git(repo, "branch", "-q", "-D", "feature")
        assert is_reachable(repo, kept)

    def test_remote_branch_keeps_it_reachable(self, repo: Path) -> None:
        git(repo, "checkout", "-q", "-b", "feature")
        write(repo, "src/f.py")
        kept = commit_all(repo)
        git(repo, "update-ref", "refs/remotes/origin/feature", kept)
        git(repo, "checkout", "-q", "main")
        git(repo, "branch", "-q", "-D", "feature")
        assert is_reachable(repo, kept)

    def test_unknown_commit(self, repo: Path) -> None:
        assert not commit_exists(repo, "0" * 40)
        assert not is_reachable(repo, "0" * 40)

    def test_shallow_clone(self, repo: Path, tmp_path: Path) -> None:
        write(repo, "src/model.py", "a = 2\n")
        commit_all(repo)
        clone = tmp_path / "clone"
        git(tmp_path, "clone", "-q", "--depth", "1", repo.as_uri(), str(clone))
        assert is_shallow(clone)
        assert not is_shallow(repo)


class TestCommitSignature:
    def test_unsigned(self, repo: Path) -> None:
        assert commit_signature(repo, head_commit(repo)) == "N"


@pytest.fixture
def gpg_home(monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    if shutil.which("gpg") is None or sys.platform == "win32":
        pytest.skip("gpg not available")
    # A short path: gpg-agent's socket path has a length limit on macOS.
    home = Path(tempfile.mkdtemp(prefix="bglg", dir="/tmp"))
    os.chmod(home, 0o700)
    monkeypatch.setenv("GNUPGHOME", str(home))
    subprocess.run(
        [
            "gpg",
            "--batch",
            "--quiet",
            "--passphrase",
            "",
            "--quick-gen-key",
            "Test User <test@example.com>",
            "ed25519",
            "sign",
            "never",
        ],
        check=True,
        capture_output=True,
    )
    yield home
    subprocess.run(["gpgconf", "--kill", "gpg-agent"], check=False, capture_output=True)
    shutil.rmtree(home, ignore_errors=True)


class TestSignedCommits:
    def signed_commit(self, repo: Path) -> str:
        git(repo, "config", "user.signingkey", "test@example.com")
        write(repo, "src/signed.py")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-S", "-m", "signed")
        return git(repo, "rev-parse", "HEAD")

    def test_good(self, repo: Path, gpg_home: Path) -> None:
        assert commit_signature(repo, self.signed_commit(repo)) == "G"

    def test_bad(self, repo: Path, gpg_home: Path) -> None:
        commit = self.signed_commit(repo)
        body = git(repo, "cat-file", "commit", commit).replace("signed", "forged")
        tampered = subprocess.run(
            ["git", "-C", str(repo), "hash-object", "-t", "commit", "-w", "--stdin"],
            input=body + "\n",
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        assert commit_signature(repo, tampered) == "B"

    def test_unknown_key(
        self, repo: Path, gpg_home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        commit = self.signed_commit(repo)
        stranger = Path(tempfile.mkdtemp(prefix="bglh", dir="/tmp"))
        os.chmod(stranger, 0o700)
        monkeypatch.setenv("GNUPGHOME", str(stranger))
        try:
            assert commit_signature(repo, commit) == "E"
        finally:
            subprocess.run(
                ["gpgconf", "--kill", "gpg-agent"], check=False, capture_output=True
            )
            shutil.rmtree(stranger, ignore_errors=True)

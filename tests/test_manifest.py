import hashlib
import os
from pathlib import Path

import pytest

from bgl.canonical import canonical_json
from bgl.manifest import (
    canonical_config,
    environment,
    file_bytes,
    hash_file,
    inputs_hash,
    repo_relative,
)


class TestRepoRelative:
    def test_relative_is_from_the_root_not_the_cwd(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / "sub").mkdir()
        monkeypatch.chdir(tmp_path / "sub")
        assert repo_relative(tmp_path, "data/x.csv") == "data/x.csv"

    def test_absolute_inside(self, tmp_path: Path) -> None:
        assert repo_relative(tmp_path, tmp_path / "data" / "x.csv") == "data/x.csv"

    def test_tidies(self, tmp_path: Path) -> None:
        assert repo_relative(tmp_path, "data/./a/../x.csv") == "data/x.csv"

    @pytest.mark.parametrize("bad", ["../x.csv", "data/../../x.csv"])
    def test_outside_is_refused(self, tmp_path: Path, bad: str) -> None:
        root = tmp_path / "repo"
        root.mkdir()
        with pytest.raises(ValueError, match="outside the repository"):
            repo_relative(root, bad)

    def test_absolute_outside_is_refused(self, tmp_path: Path) -> None:
        root = tmp_path / "repo"
        root.mkdir()
        with pytest.raises(ValueError):
            repo_relative(root, tmp_path / "elsewhere.csv")

    def test_sibling_with_same_prefix_is_outside(self, tmp_path: Path) -> None:
        root = tmp_path / "repo"
        root.mkdir()
        with pytest.raises(ValueError):
            repo_relative(root, tmp_path / "repo2" / "x.csv")

    @pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
    def test_root_reached_through_a_symlink(self, tmp_path: Path) -> None:
        root = tmp_path / "repo"
        (root / "data").mkdir(parents=True)
        link = tmp_path / "link"
        link.symlink_to(root)
        assert repo_relative(root, link / "data" / "x.csv") == "data/x.csv"


def test_hash_file(tmp_path: Path) -> None:
    path = tmp_path / "x.bin"
    path.write_bytes(b"abc" * 1000)
    assert hash_file(path) == (3000, hashlib.sha256(b"abc" * 1000).hexdigest())


class TestCanonicalConfig:
    def test_floats_become_text_and_tuples_lists(self) -> None:
        config = {"a": 0.5, "b": (1, 2.0), "c": {"d": None, "e": True}}
        assert canonical_config(config) == {
            "a": "0.5",
            "b": [1, "2.0"],
            "c": {"d": None, "e": True},
        }

    def test_result_is_hashable_by_canonical_json(self) -> None:
        canonical_json(canonical_config({"x": [0.1, {"y": 1e-9}]}))

    @pytest.mark.parametrize(
        ("config", "path"),
        [
            pytest.param({"a": float("nan")}, "config.a", id="nan"),
            pytest.param({"a": [1, float("inf")]}, r"config.a\[1\]", id="inf"),
            pytest.param({"a": {1: 2}}, "config.a", id="non_text_key"),
            pytest.param({"a": {"b": object()}}, "config.a.b", id="object"),
            pytest.param({"a": b"x"}, "config.a", id="bytes"),
        ],
    )
    def test_refused_with_path(self, config: dict[str, object], path: str) -> None:
        with pytest.raises(ValueError, match=path):
            canonical_config(config)


def test_inputs_hash_ignores_order_and_size() -> None:
    a = {"path": "a.csv", "sha256": "1" * 64, "size": 1}
    b = {"path": "b.csv", "sha256": "2" * 64, "size": 2}
    assert inputs_hash([a, b]) == inputs_hash([b, {**a, "size": 99}])
    assert inputs_hash([a, b]) != inputs_hash([a, {**b, "sha256": "3" * 64}])
    assert inputs_hash([a]) != inputs_hash([{**a, "path": "c.csv"}])


class TestEnvironment:
    def test_lock_file_hash_when_present(self, tmp_path: Path) -> None:
        (tmp_path / "uv.lock").write_bytes(b"lock")
        info = environment(tmp_path)
        assert info["lock_file"] == {
            "path": "uv.lock",
            "sha256": hashlib.sha256(b"lock").hexdigest(),
        }
        assert info["packages"] is None

    def test_package_list_otherwise(self, tmp_path: Path) -> None:
        info = environment(tmp_path)
        assert info["lock_file"] is None
        assert any(item.startswith("pytest==") for item in info["packages"])
        assert info["packages"] == sorted(info["packages"])


def test_file_bytes_is_canonical_json_and_newline() -> None:
    assert file_bytes({"b": 1, "a": 2}) == b'{"a":2,"b":1}\n'

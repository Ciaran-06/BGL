import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from bgl.config import Writer
from bgl.errors import KeyFileError
from bgl.home import (
    HOME_ENV,
    create_machine_id,
    create_private_key,
    home_dir,
    load_machine_id,
    load_private_key,
    private_key_path,
)


def writer_for(key: Ed25519PrivateKey, code: str = "CG1") -> Writer:
    return Writer(code=code, person="p", public_key=key.public_key().public_bytes_raw())


class TestHomeDir:
    def test_env_override(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(HOME_ENV, str(tmp_path))
        assert home_dir() == tmp_path

    def test_default_ends_in_bgl(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(HOME_ENV, raising=False)
        assert home_dir().name == "bgl"


class TestMachineId:
    def test_create_then_load(self, tmp_path: Path) -> None:
        created = create_machine_id(tmp_path)
        assert len(created) == 32
        assert load_machine_id(tmp_path) == created

    def test_never_replaced(self, tmp_path: Path) -> None:
        first = create_machine_id(tmp_path)
        with pytest.raises(KeyFileError, match="never replaced"):
            create_machine_id(tmp_path)
        assert load_machine_id(tmp_path) == first

    def test_missing_names_bgl_join(self, tmp_path: Path) -> None:
        with pytest.raises(KeyFileError, match="bgl join"):
            load_machine_id(tmp_path)

    @pytest.mark.parametrize("text", ["", "xyz", "0" * 31, "G" * 32])
    def test_malformed(self, tmp_path: Path, text: str) -> None:
        (tmp_path / "machine_id").write_text(text)
        with pytest.raises(KeyFileError, match="malformed"):
            load_machine_id(tmp_path)


class TestPrivateKey:
    def test_create_then_load(self, tmp_path: Path) -> None:
        key = create_private_key(tmp_path, "CG1")
        loaded = load_private_key(tmp_path, writer_for(key))
        assert loaded.private_bytes_raw() == key.private_bytes_raw()

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
    def test_file_is_private(self, tmp_path: Path) -> None:
        key = create_private_key(tmp_path, "CG1")
        path = private_key_path(tmp_path, "CG1", key.public_key().public_bytes_raw())
        assert path.stat().st_mode & 0o777 == 0o600

    def test_two_projects_same_code_do_not_collide(self, tmp_path: Path) -> None:
        first = create_private_key(tmp_path, "CG1")
        second = create_private_key(tmp_path, "CG1")
        assert load_private_key(tmp_path, writer_for(first)).private_bytes_raw() == (
            first.private_bytes_raw()
        )
        assert load_private_key(tmp_path, writer_for(second)).private_bytes_raw() == (
            second.private_bytes_raw()
        )

    def test_missing_names_bgl_join(self, tmp_path: Path) -> None:
        key = Ed25519PrivateKey.generate()
        with pytest.raises(KeyFileError, match="bgl join"):
            load_private_key(tmp_path, writer_for(key))

    def test_wrong_key_in_file_is_r19(self, tmp_path: Path) -> None:
        registered = Ed25519PrivateKey.generate()
        writer = writer_for(registered)
        path = private_key_path(tmp_path, "CG1", writer.public_key)
        path.parent.mkdir(parents=True)
        path.write_bytes(Ed25519PrivateKey.generate().private_bytes_raw())
        with pytest.raises(KeyFileError, match="R19"):
            load_private_key(tmp_path, writer)

    def test_truncated_key_file(self, tmp_path: Path) -> None:
        key = Ed25519PrivateKey.generate()
        writer = writer_for(key)
        path = private_key_path(tmp_path, "CG1", writer.public_key)
        path.parent.mkdir(parents=True)
        path.write_bytes(key.private_bytes_raw()[:31])
        with pytest.raises(KeyFileError, match="32-byte"):
            load_private_key(tmp_path, writer)

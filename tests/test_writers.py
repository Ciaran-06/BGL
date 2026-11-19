import re

import pytest
from conftest import Project

from bgl.config import load_config
from bgl.errors import KeyFileError, WriterError
from bgl.home import load_machine_id
from bgl.rowmeta import base_fields, utc_now
from bgl.writers import load_identity, read_writer_code


class TestReadWriterCode:
    def test_reads_the_code(self, project: Project) -> None:
        assert read_writer_code(project.root) == "CG1"

    def test_missing_names_bgl_join(self, project: Project) -> None:
        (project.root / ".bgl" / "writer").unlink()
        with pytest.raises(WriterError, match="bgl join"):
            read_writer_code(project.root)

    @pytest.mark.parametrize("text", ["", "\n", "CG1\nHC1\n"])
    def test_malformed(self, project: Project, text: str) -> None:
        project.write(".bgl/writer", text)
        with pytest.raises(WriterError, match="one chain code"):
            read_writer_code(project.root)


class TestLoadIdentity:
    def test_registered_writer(self, project: Project) -> None:
        config = load_config(project.root / "bgl.toml")
        identity = load_identity(project.root, config.project)
        assert identity.writer.code == "CG1"
        assert identity.writer.person == "researcher_a"
        assert identity.machine_id == load_machine_id(project.home)
        public = identity.private_key.public_key().public_bytes_raw()
        assert public == identity.writer.public_key

    def test_unregistered_code(self, project: Project) -> None:
        config = load_config(project.root / "bgl.toml")
        project.use_writer("ZZ9")
        with pytest.raises(WriterError, match="not in the registry"):
            load_identity(project.root, config.project)

    def test_missing_machine_id(self, project: Project) -> None:
        config = load_config(project.root / "bgl.toml")
        (project.home / "machine_id").unlink()
        with pytest.raises(KeyFileError, match="bgl join"):
            load_identity(project.root, config.project)


def test_utc_now_format() -> None:
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", utc_now())


def test_base_fields(project: Project) -> None:
    config = load_config(project.root / "bgl.toml")
    fields = base_fields(load_identity(project.root, config.project))
    assert set(fields) == {"machine_id", "host", "pid"}
    assert isinstance(fields["pid"], int)

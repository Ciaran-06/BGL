import math
import os
import sys
from pathlib import Path

import pytest
from helpers import MINIMAL_VALID, SECOND_STUDY, replace, write, write_bytes

from bgl.config import ConfigError, load_config, read_toml

PHASE_2_NOT_WRITTEN = (
    "phase 2 validators not written yet; "
    "switch these to rule and key assertions when they are"
)


class TestReadToml:
    def test_c8_missing_file_points_to_bgl_init(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError, match="bgl init") as info:
            read_toml(tmp_path / "bgl.toml")
        assert info.value.rule == "C8"

    def test_c8_folder_instead_of_file(self, tmp_path: Path) -> None:
        folder_path = tmp_path / "bgl.toml"
        folder_path.mkdir()
        with pytest.raises(ConfigError) as info:
            read_toml(folder_path)
        assert info.value.rule == "C8"
        assert info.value.file == folder_path

    @pytest.mark.skipif(
        sys.platform.startswith("win") or os.geteuid() == 0,
        reason="file permissions cannot block reading on Windows or for root",
    )
    def test_c8_unreadable_file(self, tmp_path: Path) -> None:
        path = write(tmp_path, "x = 20\n")
        path.chmod(0o000)
        try:
            with pytest.raises(ConfigError) as info:
                read_toml(path)
            assert info.value.rule == "C8"
            assert info.value.file == path
        finally:
            path.chmod(0o644)

    def test_c9_invalid_utf8(self, tmp_path: Path) -> None:
        path = write_bytes(tmp_path, b'x="\xff"\n')
        with pytest.raises(ConfigError) as info:
            read_toml(path)
        assert info.value.rule == "C9"

    def test_c9_byte_order_mark(self, tmp_path: Path) -> None:
        path = write_bytes(tmp_path, b"\xef\xbb\xbfx = 20\n")
        with pytest.raises(ConfigError) as info:
            read_toml(path)
        assert info.value.rule == "C9"

    def test_c9_line_number_appears_once(self, tmp_path: Path) -> None:
        path = write(tmp_path, 'a = 1\nb = "two"\nc =\n')
        with pytest.raises(ConfigError) as info:
            read_toml(path)
        assert info.value.line == 3
        assert info.value.rule == "C9"
        assert str(info.value).count("line 3") == 1

    def test_c9_invalid_toml_reports_file_and_line(self, tmp_path: Path) -> None:
        path = write(tmp_path, '[project\nname = "x"\n')
        with pytest.raises(ConfigError) as info:
            read_toml(path)
        assert info.value.rule == "C9"
        assert info.value.line == 1
        assert "bgl.toml" in str(info.value)

    def test_rejects_non_path(self, tmp_path: Path) -> None:
        with pytest.raises(TypeError):
            read_toml(str(tmp_path / "bgl.toml"))

    def test_decimal_loads_as_float(self, tmp_path: Path) -> None:
        path = write(tmp_path, "x = 0.95\n")
        data = read_toml(path)
        assert type(data["x"]) is float
        assert data["x"] == 0.95

    def test_integer_stays_int(self, tmp_path: Path) -> None:
        path = write(tmp_path, "x = 20\n")
        data = read_toml(path)
        assert type(data["x"]) is int

    def test_nan_loads_without_error(self, tmp_path: Path) -> None:
        path = write(tmp_path, "x = nan\n")
        data = read_toml(path)
        assert type(data["x"]) is float
        assert math.isnan(data["x"])

    def test_empty_file_returns_empty_dict(self, tmp_path: Path) -> None:
        path = write(tmp_path, "")
        data = read_toml(path)
        assert data == {}


@pytest.mark.skip(reason=PHASE_2_NOT_WRITTEN)
class TestLoadConfig:
    def test_minimal_valid_loads(self, tmp_path: Path) -> None:
        config = load_config(write(tmp_path, MINIMAL_VALID))
        assert config.project.name == "example_project"
        assert config.studies["alpha"].budget == 20


@pytest.mark.skip(reason=PHASE_2_NOT_WRITTEN)
class TestC1ProjectTable:
    @pytest.mark.parametrize(
        "name",
        [pytest.param('""', id="empty")],
    )
    def test_bad_name(self, tmp_path: Path, name: str) -> None:
        bad = replace(MINIMAL_VALID, 'name = "example_project"', f"name = {name}")
        with pytest.raises(ConfigError, match="name"):
            load_config(write(tmp_path, bad))


@pytest.mark.skip(reason=PHASE_2_NOT_WRITTEN)
class TestC2Paths:
    @pytest.mark.parametrize(
        "ledger",
        [
            pytest.param("/etc/ledger", id="posix_absolute"),
            pytest.param("C:/ledger", id="windows_drive"),
            pytest.param("../outside", id="parent_escape"),
        ],
    )
    def test_bad_ledger_path(self, tmp_path: Path, ledger: str) -> None:
        bad = replace(MINIMAL_VALID, 'ledger = "bgl/ledger"', f'ledger = "{ledger}"')
        with pytest.raises(ConfigError, match="ledger"):
            load_config(write(tmp_path, bad))


@pytest.mark.skip(reason=PHASE_2_NOT_WRITTEN)
class TestC3Budget:
    @pytest.mark.parametrize(
        "value",
        [
            pytest.param("-1", id="negative"),
            pytest.param('"20"', id="string"),
            pytest.param("2.5", id="float"),
        ],
    )
    def test_bad_budget(self, tmp_path: Path, value: str) -> None:
        bad = replace(MINIMAL_VALID, "budget = 20", f"budget = {value}")
        with pytest.raises(ConfigError, match="budget"):
            load_config(write(tmp_path, bad))


@pytest.mark.skip(reason=PHASE_2_NOT_WRITTEN)
class TestC4Prefix:
    @pytest.mark.parametrize(
        "value",
        [
            pytest.param("A", id="too_short"),
            pytest.param("al", id="lowercase"),
            pytest.param("ALPHA", id="too_long"),
            pytest.param("A1", id="digit"),
        ],
    )
    def test_bad_prefix(self, tmp_path: Path, value: str) -> None:
        bad = replace(MINIMAL_VALID, 'prefix = "AL"', f'prefix = "{value}"')
        with pytest.raises(ConfigError, match="prefix"):
            load_config(write(tmp_path, bad))


@pytest.mark.skip(reason=PHASE_2_NOT_WRITTEN)
class TestC5Claims:
    @pytest.mark.parametrize(
        "value",
        [
            pytest.param('["effect_exists", "effect_exists"]', id="duplicate"),
            pytest.param('[""]', id="empty_string"),
        ],
    )
    def test_bad_claims(self, tmp_path: Path, value: str) -> None:
        bad = replace(MINIMAL_VALID, 'claims = ["effect_exists"]', f"claims = {value}")
        with pytest.raises(ConfigError, match="claims"):
            load_config(write(tmp_path, bad))


@pytest.mark.skip(reason=PHASE_2_NOT_WRITTEN)
class TestC6UniquePrefixes:
    def test_shared_prefix(self, tmp_path: Path) -> None:
        two_studies = MINIMAL_VALID + SECOND_STUDY
        bad = replace(two_studies, 'prefix = "BE"', 'prefix = "AL"')
        with pytest.raises(ConfigError, match="prefix"):
            load_config(write(tmp_path, bad))


@pytest.mark.skip(reason=PHASE_2_NOT_WRITTEN)
class TestC7UnknownKeys:
    def test_misspelt_key(self, tmp_path: Path) -> None:
        bad = replace(MINIMAL_VALID, "budget = 20", "budgt = 20")
        with pytest.raises(ConfigError, match="budgt"):
            load_config(write(tmp_path, bad))

from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from bgl import ledger
from bgl.canonical import GENESIS_HASH, row_hash
from bgl.config import Project, Writer
from bgl.ledger import (
    BUILD_ONLY_FIELDS,
    EVENTS,
    FORMAT_VERSION,
    ROW_FIELDS,
    ChainProblem,
    ChainReport,
    LedgerReport,
    build_row,
    verify_all_chains,
)
from bgl.signing import verify_row_signature

KEY = Ed25519PrivateKey.from_private_bytes(bytes(32))
PUBLIC = KEY.public_key().public_bytes_raw()


def note(**extra: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {"event": "note", "chain": "CG1", "reason": "hello"}
    fields.update(extra)
    return fields


class TestConstants:
    def test_row_fields_match_spec_table(self) -> None:
        assert len(ROW_FIELDS) == 55

    def test_events(self) -> None:
        assert len(EVENTS) == 16

    def test_build_only_fields_are_row_fields(self) -> None:
        assert BUILD_ONLY_FIELDS <= ROW_FIELDS


class TestBuildRow:
    def test_every_field_present(self) -> None:
        row = build_row(note(), GENESIS_HASH, KEY)
        assert set(row) == ROW_FIELDS
        assert row["study"] is None
        assert row["reason"] == "hello"
        assert row["format_version"] == FORMAT_VERSION
        assert row["prev_hash"] == GENESIS_HASH

    def test_hash_and_signature_check_out(self) -> None:
        row = build_row(note(), GENESIS_HASH, KEY)
        assert row["hash"] == row_hash(row)
        assert verify_row_signature(PUBLIC, row["hash"], row["sig"])

    def test_does_not_change_fields(self) -> None:
        fields = note()
        build_row(fields, GENESIS_HASH, KEY)
        assert fields == note()

    def test_unknown_field(self) -> None:
        with pytest.raises(ValueError, match="unknown row fields: colour"):
            build_row(note(colour="red"), GENESIS_HASH, KEY)

    @pytest.mark.parametrize("field", sorted(BUILD_ONLY_FIELDS))
    def test_build_only_field_refused(self, field: str) -> None:
        with pytest.raises(ValueError, match="sets these itself"):
            build_row(note(**{field: "x"}), GENESIS_HASH, KEY)

    def test_unknown_event(self) -> None:
        with pytest.raises(ValueError, match="unknown event"):
            build_row(note(event="party"), GENESIS_HASH, KEY)

    def test_missing_chain(self) -> None:
        fields = note()
        del fields["chain"]
        with pytest.raises(ValueError, match="chain code"):
            build_row(fields, GENESIS_HASH, KEY)

    @pytest.mark.parametrize("prev", ["A" * 64, "0" * 63, ""])
    def test_bad_prev_hash(self, prev: str) -> None:
        with pytest.raises(ValueError, match="hex hash"):
            build_row(note(), prev, KEY)

    def test_float_refused(self) -> None:
        with pytest.raises(ValueError, match="row.value"):
            build_row(note(value=0.5), GENESIS_HASH, KEY)


def project(*codes: str) -> Project:
    writers = {code: Writer(code=code, person="p", public_key=PUBLIC) for code in codes}
    return Project(
        name="example_project",
        mode="solo",
        ledger="bgl/ledger",
        runs_dir="bgl/runs",
        writers=writers,
        witness=None,
    )


def fake_verify(lines: list[bytes], tail: bytes | None, writer: Writer) -> ChainReport:
    """Stands in for the hand-written verify_chain: passes unless there's a tail."""
    problem = None if tail is None else ChainProblem(len(lines) + 1, "torn tail")
    return ChainReport(code=writer.code, rows=(), head=GENESIS_HASH, problem=problem)


class TestVerifyAllChains:
    @pytest.fixture(autouse=True)
    def _fake_verify(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(ledger, "verify_chain", fake_verify)

    def test_every_registered_chain_reported(self, tmp_path: Path) -> None:
        report = verify_all_chains(tmp_path, project("CG1", "HC1"))
        assert set(report.chains) == {"CG1", "HC1"}
        assert report.unregistered == ()
        assert report.ok

    def test_unregistered_chain_file_fails(self, tmp_path: Path) -> None:
        folder = tmp_path / "bgl" / "ledger"
        folder.mkdir(parents=True)
        (folder / "CG1.jsonl").write_bytes(b"")
        (folder / "XX9.jsonl").write_bytes(b"")
        (folder / "notes.txt").write_bytes(b"")
        report = verify_all_chains(tmp_path, project("CG1"))
        assert report.unregistered == ("XX9.jsonl",)
        assert not report.ok

    def test_lower_case_copy_is_unregistered(self, tmp_path: Path) -> None:
        folder = tmp_path / "bgl" / "ledger"
        folder.mkdir(parents=True)
        (folder / "cg1.jsonl").write_bytes(b"")
        report = verify_all_chains(tmp_path, project("CG1"))
        assert report.unregistered == ("cg1.jsonl",)

    def test_broken_chain_fails(self, tmp_path: Path) -> None:
        folder = tmp_path / "bgl" / "ledger"
        folder.mkdir(parents=True)
        (folder / "CG1.jsonl").write_bytes(b"{}\n{")
        report = verify_all_chains(tmp_path, project("CG1"))
        assert report.chains["CG1"].problem == ChainProblem(2, "torn tail")
        assert not report.ok

    def test_takes_the_ledger_lock(self, tmp_path: Path) -> None:
        verify_all_chains(tmp_path, project("CG1"))
        assert (tmp_path / ".bgl" / "ledger.lock").exists()


def test_ledger_report_ok_needs_no_problems() -> None:
    good = ChainReport("CG1", (), GENESIS_HASH, None)
    assert LedgerReport({"CG1": good}, ()).ok

import base64
import hashlib
import json
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from hypothesis import given, settings
from hypothesis import strategies as st

from bgl import ledger
from bgl.canonical import GENESIS_HASH, line_bytes, row_hash
from bgl.config import Project, Writer
from bgl.errors import ChainBrokenError, NothingToRepairError, RowRefusedError
from bgl.ledger import (
    BUILD_ONLY_FIELDS,
    EVENTS,
    FORMAT_VERSION,
    ROW_FIELDS,
    ChainProblem,
    ChainReport,
    LedgerReport,
    append_row,
    build_row,
    repair_chain,
    verify_all_chains,
    verify_chain,
)
from bgl.signing import verify_row_signature

KEY = Ed25519PrivateKey.from_private_bytes(bytes(32))
PUBLIC = KEY.public_key().public_bytes_raw()


def note(**extra: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "event": "note",
        "chain": "CG1",
        "reason": "hello",
        "machine_id": "m1",
    }
    fields.update(extra)
    return fields


class TestConstants:
    def test_row_fields_match_spec_table(self) -> None:
        # Copied by hand from the spec's field table (File formats, Ledger).
        assert ROW_FIELDS == {
            "id",
            "legacy_id",
            "event",
            "chain",
            "study",
            "type",
            "claims",
            "selects",
            "variants",
            "sample",
            "sample_start",
            "sample_end",
            "script",
            "script_sha256",
            "code_sha256",
            "config_sha256",
            "inputs_sha256",
            "what_changed",
            "prediction",
            "status",
            "outcome",
            "result",
            "commit",
            "user",
            "commit_signature",
            "machine_id",
            "host",
            "pid",
            "lock_file",
            "started",
            "ended",
            "manifest",
            "manifest_sha256",
            "close_file",
            "close_sha256",
            "returns_sha256",
            "output_sha256",
            "other_heads",
            "reason",
            "fix_commit",
            "amend",
            "lock",
            "seal",
            "removed",
            "period",
            "value",
            "turnover",
            "cost",
            "position_sha256",
            "live_inputs",
            "report_sha256",
            "format_version",
            "prev_hash",
            "hash",
            "sig",
        }
        assert len(ROW_FIELDS) == 55

    def test_events(self) -> None:
        # Copied by hand from the spec's event list (File formats, Ledger).
        assert EVENTS == {
            "open",
            "close",
            "refused",
            "dry",
            "note",
            "ack",
            "void",
            "lock",
            "amend",
            "repair",
            "seal",
            "imported",
            "incubation",
            "live_return",
            "gate",
            "registration",
        }

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


# --- verify_chain, append_row, repair_chain ---

WRITER = Writer(code="CG1", person="p", public_key=PUBLIC)
OTHER_KEY = Ed25519PrivateKey.from_private_bytes(bytes([1]) * 32)


def good_rows(
    count: int, *, key: Ed25519PrivateKey = KEY, machines: tuple[str, ...] = ()
) -> list[dict[str, Any]]:
    """count valid rows in order. machines gives each row's machine_id (default m1)."""
    rows: list[dict[str, Any]] = []
    prev = GENESIS_HASH
    for index in range(count):
        machine = machines[index] if index < len(machines) else "m1"
        row = build_row(note(reason=f"row {index + 1}", machine_id=machine), prev, key)
        rows.append(row)
        prev = row["hash"]
    return rows


def as_line(row: dict[str, Any]) -> bytes:
    """A row as a chain line without its newline, in canonical form."""
    return line_bytes(row)[:-1]


def good_lines(count: int, **kwargs: Any) -> list[bytes]:
    return [as_line(row) for row in good_rows(count, **kwargs)]


def edited(line: bytes, **changes: Any) -> bytes:
    """Parse a line, apply changes (None deletes a field), write it canonically."""
    row = json.loads(line)
    for field, value in changes.items():
        if value is None:
            del row[field]
        else:
            row[field] = value
    return as_line(row)


def problem_of(report: ChainReport) -> tuple[int, str]:
    assert report.problem is not None, "expected a problem, found none"
    return report.problem.line, report.problem.reason


class TestVerifyChainPasses:
    def test_empty_chain(self) -> None:
        report = verify_chain([], None, WRITER)
        assert report.problem is None
        assert report.head == GENESIS_HASH
        assert report.rows == ()
        assert report.code == "CG1"

    def test_three_good_rows(self) -> None:
        rows = good_rows(3)
        report = verify_chain([as_line(r) for r in rows], None, WRITER)
        assert report.problem is None
        assert report.head == rows[2]["hash"]
        assert [r["hash"] for r in report.rows] == [r["hash"] for r in rows]


class TestVerifyChainProblems:
    @pytest.mark.parametrize(
        ("bad_line", "reason"),
        [
            pytest.param(b"\xff", "not UTF-8", id="not_utf8"),
            pytest.param(b"not json", "not valid JSON", id="not_json"),
            pytest.param(b"[1]", "not a JSON object", id="list"),
            pytest.param(b"", "not valid JSON", id="blank_line"),
        ],
    )
    def test_unreadable_line(self, bad_line: bytes, reason: str) -> None:
        lines = good_lines(3)
        lines[1] = bad_line
        assert problem_of(verify_chain(lines, None, WRITER)) == (2, reason)

    def test_spaces_are_not_canonical(self) -> None:
        lines = good_lines(3)
        lines[1] = json.dumps(json.loads(lines[1])).encode()
        line, reason = problem_of(verify_chain(lines, None, WRITER))
        assert (line, reason) == (2, "not in canonical form")

    def test_repeated_key_is_not_canonical(self) -> None:
        lines = good_lines(3)
        lines[1] = lines[1][:-1] + b',"reason":"forged"}'
        assert problem_of(verify_chain(lines, None, WRITER)) == (
            2,
            "not in canonical form",
        )

    def test_crlf_is_not_canonical(self) -> None:
        lines = good_lines(3)
        lines[1] = lines[1] + b"\r"
        assert problem_of(verify_chain(lines, None, WRITER)) == (
            2,
            "not in canonical form",
        )

    @pytest.mark.parametrize(
        "line",
        [
            pytest.param(b'{"x":1.5}', id="float"),
            pytest.param(b'{"x":NaN}', id="nan_literal"),
        ],
    )
    def test_unhashable_value(self, line: bytes) -> None:
        lines = good_lines(3)
        lines[1] = line
        assert problem_of(verify_chain(lines, None, WRITER)) == (
            2,
            "contains a float or other value that cannot be hashed",
        )

    def test_float_in_a_real_field(self) -> None:
        lines = good_lines(3)
        row = json.loads(lines[1])
        row["value"] = 1.5
        raw = json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        lines[1] = raw.encode()
        assert "float" in problem_of(verify_chain(lines, None, WRITER))[1]

    def test_missing_field(self) -> None:
        lines = good_lines(3)
        lines[1] = edited(lines[1], pid=None)
        assert problem_of(verify_chain(lines, None, WRITER)) == (
            2,
            "missing fields: pid",
        )

    def test_extra_field(self) -> None:
        lines = good_lines(3)
        lines[1] = edited(lines[1], colour="red")
        assert problem_of(verify_chain(lines, None, WRITER)) == (
            2,
            "extra fields: colour",
        )

    def test_missing_and_extra(self) -> None:
        lines = good_lines(3)
        lines[1] = edited(lines[1], pid=None, host=None, colour="red")
        assert problem_of(verify_chain(lines, None, WRITER)) == (
            2,
            "missing fields: host, pid; extra fields: colour",
        )

    @pytest.mark.parametrize(
        "version",
        [
            pytest.param(2, id="two"),
            pytest.param(True, id="true_equals_1_in_python"),
            pytest.param("1", id="string"),
        ],
    )
    def test_format_version(self, version: object) -> None:
        lines = good_lines(3)
        lines[1] = edited(lines[1], format_version=version)
        line, reason = problem_of(verify_chain(lines, None, WRITER))
        assert line == 2
        assert reason.startswith("format_version")

    def test_wrong_chain_code(self) -> None:
        lines = good_lines(3)
        lines[1] = edited(lines[1], chain="HC1")
        assert problem_of(verify_chain(lines, None, WRITER)) == (
            2,
            "chain 'HC1', expected CG1",
        )

    def test_removed_row(self) -> None:
        lines = good_lines(3)
        del lines[1]
        line, reason = problem_of(verify_chain(lines, None, WRITER))
        assert line == 2
        assert reason.startswith("prev_hash does not match")

    def test_swapped_rows(self) -> None:
        lines = good_lines(3)
        lines[1], lines[2] = lines[2], lines[1]
        line, reason = problem_of(verify_chain(lines, None, WRITER))
        assert line == 2
        assert reason.startswith("prev_hash does not match")

    def test_edited_row_with_old_hash(self) -> None:
        lines = good_lines(3)
        lines[1] = edited(lines[1], reason="forged")
        line, reason = problem_of(verify_chain(lines, None, WRITER))
        assert line == 2
        assert reason.startswith("hash does not match")

    def test_edited_row_with_recomputed_hash(self) -> None:
        lines = good_lines(3)
        row = json.loads(lines[1])
        row["reason"] = "forged"
        row["hash"] = row_hash(row)
        lines[1] = as_line(row)
        assert problem_of(verify_chain(lines, None, WRITER)) == (
            2,
            "signature does not verify with CG1's key",
        )

    def test_chain_signed_by_another_key(self) -> None:
        lines = good_lines(3, key=OTHER_KEY)
        assert problem_of(verify_chain(lines, None, WRITER)) == (
            1,
            "signature does not verify with CG1's key",
        )

    def test_second_machine(self) -> None:
        lines = good_lines(3, machines=("m1", "m1", "m2"))
        line, reason = problem_of(verify_chain(lines, None, WRITER))
        assert line == 3
        assert reason.startswith("machine_id differs")

    @pytest.mark.parametrize(
        ("machines", "line"),
        [
            pytest.param(("",), 1, id="empty_first_row"),
            pytest.param(("m1", ""), 2, id="empty_later_row"),
        ],
    )
    def test_empty_machine_id(self, machines: tuple[str, ...], line: int) -> None:
        lines = good_lines(len(machines), machines=machines)
        assert problem_of(verify_chain(lines, None, WRITER)) == (
            line,
            "machine_id missing",
        )

    def test_null_machine_id(self) -> None:
        fields = note()
        fields["machine_id"] = None
        row = build_row(fields, GENESIS_HASH, KEY)
        assert problem_of(verify_chain([as_line(row)], None, WRITER)) == (
            1,
            "machine_id missing",
        )

    def test_torn_tail_after_good_rows(self) -> None:
        rows = good_rows(3)
        report = verify_chain([as_line(r) for r in rows], b'{"id"', WRITER)
        line, reason = problem_of(report)
        assert line == 4
        assert reason.startswith("torn tail: 5 bytes")
        assert len(report.rows) == 3
        assert report.head == rows[2]["hash"]

    def test_first_problem_wins_over_torn_tail(self) -> None:
        lines = good_lines(3)
        lines[1] = b"not json"
        assert problem_of(verify_chain(lines, b"{", WRITER))[0] == 2

    def test_edit_in_the_middle_is_reported_there(self) -> None:
        rows = good_rows(5)
        lines = [as_line(r) for r in rows]
        lines[3] = edited(lines[3], reason="forged")
        report = verify_chain(lines, None, WRITER)
        assert problem_of(report)[0] == 4
        assert len(report.rows) == 3
        assert report.head == rows[2]["hash"]


def solo_project() -> Project:
    return Project(
        name="example_project",
        mode="solo",
        ledger="bgl/ledger",
        runs_dir="bgl/runs",
        writers={"CG1": WRITER},
        witness=None,
    )


def chain_file(root: Path) -> Path:
    return root / "bgl" / "ledger" / "CG1.jsonl"


def write_chain(root: Path, data: bytes) -> Path:
    path = chain_file(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def file_verifies(root: Path) -> ChainReport:
    lines = chain_file(root).read_bytes().split(b"\n")
    assert lines.pop() == b"", "chain file must end with a newline"
    report = verify_chain(lines, None, WRITER)
    assert report.problem is None, report.problem
    return report


class TestAppendRow:
    def test_first_row_points_at_genesis(self, tmp_path: Path) -> None:
        row = append_row(tmp_path, solo_project(), WRITER, KEY, note())
        assert row["prev_hash"] == GENESIS_HASH
        assert row["chain"] == "CG1"
        assert chain_file(tmp_path).read_bytes() == line_bytes(row)
        assert file_verifies(tmp_path).head == row["hash"]

    def test_two_rows_chain(self, tmp_path: Path) -> None:
        first = append_row(tmp_path, solo_project(), WRITER, KEY, note())
        second = append_row(tmp_path, solo_project(), WRITER, KEY, note())
        assert second["prev_hash"] == first["hash"]
        assert file_verifies(tmp_path).head == second["hash"]

    def test_chain_code_filled_in(self, tmp_path: Path) -> None:
        fields = note()
        del fields["chain"]
        assert append_row(tmp_path, solo_project(), WRITER, KEY, fields)["chain"] == (
            "CG1"
        )

    def test_other_chain_code_refused(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError):
            append_row(tmp_path, solo_project(), WRITER, KEY, note(chain="HC1"))
        assert not chain_file(tmp_path).exists()

    def test_broken_chain_refused_and_unchanged(self, tmp_path: Path) -> None:
        lines = good_lines(2)
        lines[1] = edited(lines[1], reason="forged")
        before = b"".join(line + b"\n" for line in lines)
        write_chain(tmp_path, before)
        with pytest.raises(ChainBrokenError) as info:
            append_row(tmp_path, solo_project(), WRITER, KEY, note())
        assert info.value.line == 2
        assert chain_file(tmp_path).read_bytes() == before

    def test_torn_tail_refused_with_repair_hint(self, tmp_path: Path) -> None:
        before = b"".join(line + b"\n" for line in good_lines(1)) + b'{"id"'
        write_chain(tmp_path, before)
        with pytest.raises(ChainBrokenError, match="bgl repair"):
            append_row(tmp_path, solo_project(), WRITER, KEY, note())
        assert chain_file(tmp_path).read_bytes() == before

    def test_takes_the_ledger_lock(self, tmp_path: Path) -> None:
        append_row(tmp_path, solo_project(), WRITER, KEY, note())
        assert (tmp_path / ".bgl" / "ledger.lock").exists()


APPENDER = textwrap.dedent(
    """
    import sys
    from pathlib import Path
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from bgl.config import Project, Writer
    from bgl.ledger import append_row

    root, count = Path(sys.argv[1]), int(sys.argv[2])
    key = Ed25519PrivateKey.from_private_bytes(bytes(32))
    writer = Writer("CG1", "p", key.public_key().public_bytes_raw())
    project = Project("p", "solo", "bgl/ledger", "bgl/runs", {"CG1": writer}, None)
    for index in range(count):
        fields = {"event": "note", "reason": f"{sys.argv[3]} {index}",
                  "machine_id": "m1"}
        append_row(root, project, writer, key, fields)
    """
)


def test_two_processes_appending_at_once_keep_one_chain(tmp_path: Path) -> None:
    """R18: both processes' rows chain onto the right head."""
    append_row(tmp_path, solo_project(), WRITER, KEY, note())
    processes = [
        subprocess.Popen([sys.executable, "-c", APPENDER, str(tmp_path), "20", name])
        for name in ("a", "b")
    ]
    for process in processes:
        assert process.wait(timeout=120) == 0
    report = file_verifies(tmp_path)
    assert len(report.rows) == 41


class TestRepairChain:
    def test_removes_tail_and_records_it(self, tmp_path: Path) -> None:
        rows = good_rows(3)
        tail = b'{"partial'
        write_chain(tmp_path, b"".join(line_bytes(r) for r in rows) + tail)
        row = repair_chain(tmp_path, solo_project(), WRITER, KEY, {"machine_id": "m1"})
        assert row["event"] == "repair"
        assert row["removed"] == {
            "length": len(tail),
            "sha256": hashlib.sha256(tail).hexdigest(),
        }
        assert row["prev_hash"] == rows[2]["hash"]
        report = file_verifies(tmp_path)
        assert len(report.rows) == 4
        assert report.head == row["hash"]

    def test_file_that_is_only_a_tail(self, tmp_path: Path) -> None:
        write_chain(tmp_path, b"{")
        row = repair_chain(tmp_path, solo_project(), WRITER, KEY, {"machine_id": "m1"})
        assert row["prev_hash"] == GENESIS_HASH
        assert row["removed"]["length"] == 1
        file_verifies(tmp_path)

    def test_clean_chain_has_nothing_to_repair(self, tmp_path: Path) -> None:
        before = b"".join(line_bytes(r) for r in good_rows(2))
        write_chain(tmp_path, before)
        with pytest.raises(NothingToRepairError):
            repair_chain(tmp_path, solo_project(), WRITER, KEY, {"machine_id": "m1"})
        assert chain_file(tmp_path).read_bytes() == before

    def test_broken_row_is_never_repaired(self, tmp_path: Path) -> None:
        lines = good_lines(3)
        lines[1] = edited(lines[1], reason="forged")
        before = b"".join(line + b"\n" for line in lines) + b"{"
        write_chain(tmp_path, before)
        with pytest.raises(ChainBrokenError):
            repair_chain(tmp_path, solo_project(), WRITER, KEY, {"machine_id": "m1"})
        assert chain_file(tmp_path).read_bytes() == before

    @pytest.mark.parametrize(
        "fields",
        [
            pytest.param({"machine_id": "m1", "event": "note"}, id="event"),
            pytest.param(
                {"machine_id": "m1", "removed": {"length": 1, "sha256": "x"}},
                id="removed",
            ),
            pytest.param({"machine_id": "m1", "chain": "HC1"}, id="other_chain"),
        ],
    )
    def test_caller_cannot_set_the_record(
        self, tmp_path: Path, fields: dict[str, Any]
    ) -> None:
        before = b"".join(line_bytes(r) for r in good_rows(1)) + b"{"
        write_chain(tmp_path, before)
        with pytest.raises(ValueError):
            repair_chain(tmp_path, solo_project(), WRITER, KEY, fields)
        assert chain_file(tmp_path).read_bytes() == before


class TestFieldTypes:
    """F12: fields readers group and compare by must have their table type."""

    @pytest.mark.parametrize(
        ("field", "value", "reason"),
        [
            ("id", ["AL-R001-CG1"], "id is not text or null"),
            ("id", {"run": 1}, "id is not text or null"),
            ("study", ["alpha"], "study is not text or null"),
            ("type", 1, "type is not text or null"),
            ("sample", {}, "sample is not text or null"),
            ("status", [], "status is not text or null"),
            ("variants", "2", "variants is not a whole number or null"),
            ("variants", True, "variants is not a whole number or null"),
        ],
    )
    def test_wrong_type_breaks_the_chain(
        self, field: str, value: object, reason: str
    ) -> None:
        row = build_row(note(**{field: value}), GENESIS_HASH, KEY)
        assert problem_of(verify_chain([as_line(row)], None, WRITER)) == (1, reason)

    def test_event_must_be_text(self) -> None:
        row = build_row(note(), GENESIS_HASH, KEY)
        row["event"] = ["note"]
        row["hash"] = row_hash(row)
        row["sig"] = ledger.sign_row_hash(KEY, row["hash"])
        assert problem_of(verify_chain([as_line(row)], None, WRITER)) == (
            1,
            "event is not text",
        )

    @pytest.mark.parametrize("value", [None, "AL-R001-CG1"])
    def test_text_or_null_passes(self, value: object) -> None:
        row = build_row(note(id=value, variants=2), GENESIS_HASH, KEY)
        assert verify_chain([as_line(row)], None, WRITER).problem is None

    def test_bgl_refuses_to_write_one(self, tmp_path: Path) -> None:
        with pytest.raises(RowRefusedError, match="id is not text or null"):
            append_row(tmp_path, solo_project(), WRITER, KEY, note(id=["x"]))
        assert not chain_file(tmp_path).exists()


class TestNoRowVerifyWouldReject:
    """F1: the chain is verified with the new row before anything is written."""

    @pytest.mark.parametrize(
        ("machine", "reason"),
        [
            pytest.param("m2", "machine_id differs", id="second_machine"),
            pytest.param(None, "machine_id missing", id="null"),
            pytest.param("", "machine_id missing", id="empty"),
            pytest.param(7, "machine_id missing", id="not_text"),
        ],
    )
    def test_append_refused_and_file_unchanged(
        self, tmp_path: Path, machine: object, reason: str
    ) -> None:
        before = b"".join(line_bytes(r) for r in good_rows(2))
        write_chain(tmp_path, before)
        with pytest.raises(RowRefusedError, match=reason) as info:
            append_row(tmp_path, solo_project(), WRITER, KEY, note(machine_id=machine))
        assert info.value.line == 3
        assert chain_file(tmp_path).read_bytes() == before

    def test_first_row_without_machine_id_is_refused(self, tmp_path: Path) -> None:
        fields = note()
        del fields["machine_id"]
        with pytest.raises(RowRefusedError, match="machine_id missing"):
            append_row(tmp_path, solo_project(), WRITER, KEY, fields)
        assert not chain_file(tmp_path).exists()

    def test_row_signed_by_another_key_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(RowRefusedError, match="signature"):
            append_row(tmp_path, solo_project(), WRITER, OTHER_KEY, note())
        assert not chain_file(tmp_path).exists()

    @pytest.mark.parametrize("machine", ["m2", None])
    def test_repair_refused_and_tail_kept(
        self, tmp_path: Path, machine: object
    ) -> None:
        before = b"".join(line_bytes(r) for r in good_rows(2)) + b'{"torn'
        write_chain(tmp_path, before)
        with pytest.raises(RowRefusedError):
            repair_chain(tmp_path, solo_project(), WRITER, KEY, {"machine_id": machine})
        assert chain_file(tmp_path).read_bytes() == before


# --- property tests (Hypothesis) ---------------------------------------------

_TEXT = st.text(alphabet=st.characters(blacklist_categories=("Cs",)), max_size=20)
_ROW_FIELDS = st.fixed_dictionaries(
    {"reason": _TEXT},
    optional={
        "pid": st.integers(min_value=-(2**40), max_value=2**40),
        "claims": st.lists(_TEXT, max_size=3),
        "amend": st.dictionaries(_TEXT, st.one_of(st.none(), st.booleans(), _TEXT)),
        "event": st.sampled_from(sorted(EVENTS)),
    },
)


def chain_bytes(field_sets: list[dict[str, Any]]) -> tuple[bytes, list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    prev = GENESIS_HASH
    for fields in field_sets:
        row = build_row(note(**fields), prev, KEY)
        rows.append(row)
        prev = row["hash"]
    return b"".join(line_bytes(row) for row in rows), rows


def verify_bytes(data: bytes) -> ChainReport:
    """verify_chain on file bytes, split exactly as ledger_io.read_chain does."""
    parts = data.split(b"\n")
    tail = parts.pop()
    return verify_chain(parts, tail or None, WRITER)


class TestProperties:
    @settings(max_examples=60, deadline=None)
    @given(st.lists(_ROW_FIELDS, max_size=6))
    def test_any_valid_sequence_verifies(
        self, field_sets: list[dict[str, Any]]
    ) -> None:
        data, rows = chain_bytes(field_sets)
        report = verify_bytes(data)
        assert report.problem is None
        assert [row["hash"] for row in report.rows] == [row["hash"] for row in rows]

    @settings(max_examples=15, deadline=None)
    @given(st.lists(_ROW_FIELDS, min_size=1, max_size=4))
    def test_any_valid_sequence_of_appends_verifies(
        self, field_sets: list[dict[str, Any]]
    ) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for fields in field_sets:
                append_row(root, solo_project(), WRITER, KEY, note(**fields))
            assert len(file_verifies(root).rows) == len(field_sets)

    @settings(max_examples=200, deadline=None)
    @given(
        st.lists(_ROW_FIELDS, min_size=1, max_size=4),
        st.data(),
    )
    def test_any_single_byte_change_fails(
        self, field_sets: list[dict[str, Any]], data: st.DataObject
    ) -> None:
        original, _ = chain_bytes(field_sets)
        kind = data.draw(st.sampled_from(["replace", "delete", "insert"]))
        at = data.draw(st.integers(0, len(original) - (kind != "insert")))
        if kind == "replace":
            byte = data.draw(st.integers(0, 255).filter(lambda b: b != original[at]))
            changed = original[:at] + bytes([byte]) + original[at + 1 :]
        elif kind == "delete":
            changed = original[:at] + original[at + 1 :]
        else:
            byte = data.draw(st.integers(0, 255))
            changed = original[:at] + bytes([byte]) + original[at:]
        assert verify_bytes(changed).problem is not None

    @settings(max_examples=100, deadline=None)
    @given(st.lists(_ROW_FIELDS, min_size=1, max_size=4), st.data())
    def test_any_respelling_of_the_same_row_fails(
        self, field_sets: list[dict[str, Any]], data: st.DataObject
    ) -> None:
        """Changes random bytes rarely hit: the row parses the same, spelt otherwise.

        A space after a separator, an escaped character, or a signature whose
        base64 has non-zero padding bits all decode to the same row and the
        same signature bytes. Each line has exactly one valid spelling.
        """
        original, rows = chain_bytes(field_sets)
        lines = original.split(b"\n")[:-1]
        index = data.draw(st.integers(0, len(lines) - 1))
        line = lines[index]
        kind = data.draw(st.sampled_from(["space", "escape", "sig_padding"]))
        if kind == "space":
            # Only spots that are structural whatever the strings hold: after
            # the opening brace, or before the closing one.
            at = data.draw(st.sampled_from([1, len(line) - 1]))
            line = line[:at] + b" " + line[at:]
            assert json.loads(line) == rows[index]
        elif kind == "escape":
            # The first letter of the "event" key, written as e.
            line = line.replace(b'"event"', b'"\\u0065vent"', 1)
            assert json.loads(line) == rows[index]
        else:
            sig = rows[index]["sig"]
            assert sig.endswith("==")
            alphabet = (
                "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
            )
            value = alphabet.index(sig[-3])
            low = data.draw(st.integers(1, 15))  # bits a decoder ignores
            respelt = sig[:-3] + alphabet[(value & ~15) | low] + "=="
            assert base64.b64decode(respelt) == base64.b64decode(sig)
            line = line.replace(sig.encode(), respelt.encode())
        lines[index] = line
        changed = b"".join(item + b"\n" for item in lines)
        assert verify_bytes(changed).problem is not None

    @settings(max_examples=200, deadline=None)
    @given(
        st.lists(_ROW_FIELDS, min_size=1, max_size=4),
        st.data(),
    )
    def test_any_single_field_change_fails(
        self, field_sets: list[dict[str, Any]], data: st.DataObject
    ) -> None:
        _, rows = chain_bytes(field_sets)
        index = data.draw(st.integers(0, len(rows) - 1))
        field = data.draw(st.sampled_from(sorted(ROW_FIELDS)))
        value = data.draw(
            st.one_of(st.none(), st.booleans(), st.integers(), _TEXT).filter(
                lambda v: json.dumps(v) != json.dumps(rows[index][field])
            )
        )
        rows[index] = {**rows[index], field: value}
        changed = b"".join(line_bytes(row) for row in rows)
        assert verify_bytes(changed).problem is not None

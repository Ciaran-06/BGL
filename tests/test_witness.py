"""Witnessing end to end: runs, files, status, verify and the discard check."""

import json
from pathlib import Path
from typing import Any

import pytest
from conftest import Project, Services, hard_kill, run_git

from bgl import rekor, witness
from bgl.canonical import canonical_json
from bgl.cli import EXIT_FAILED, EXIT_OK, main
from bgl.config import load_config
from bgl.errors import WitnessError
from bgl.ledger import append_row
from bgl.status import StudyStatus, project_status
from bgl.verify import verify_project
from bgl.writers import load_identity


def records(project: Project, kind: str = witness.WITNESS_DIR) -> list[Path]:
    folder = project.root / "bgl" / "ledger" / kind / "CG1"
    return sorted(folder.iterdir()) if folder.is_dir() else []


def identity(project: Project):  # type: ignore[no-untyped-def]
    config = load_config(project.root / "bgl.toml")
    return config, load_identity(project.root, config.project)


def alpha(project: Project) -> StudyStatus:
    return next(s for s in project_status(project.root).studies if s.name == "alpha")


def close_file(project: Project, run_id: str = "AL-X001-CG1") -> Any:
    return project.json(f"bgl/runs/{run_id}.close.json")


def stored_record(project: Project, head: str) -> Any:
    return json.loads(
        (
            project.root / "bgl" / "ledger" / "witness" / "CG1" / f"{head}.json"
        ).read_text()
    )


def append(project: Project, **fields: Any) -> dict[str, Any]:
    config, me = identity(project)
    return append_row(
        project.root,
        config.project,
        me.writer,
        me.private_key,
        {"machine_id": me.machine_id, **fields},
    )


class TestOpenWitness:
    """N10: the close file records the entry the R23 call returned."""

    def test_witnessed_open_stays_witnessed(
        self,
        witnessed: Project,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        witnessed.run(run_type="exploratory")
        open_head = witnessed.rows()[0]["hash"]
        record = stored_record(witnessed, open_head)
        assert close_file(witnessed)["open_witness"] == {
            "uuid": record["uuid"],
            "log_index": record["entry"]["logIndex"],
            "integrated_time": record["entry"]["integratedTime"],
            "log_id": record["entry"]["logID"],
            "signed_entry_timestamp": record["entry"]["verification"][
                "signedEntryTimestamp"
            ],
        }
        assert alpha(witnessed).unwitnessed == ()
        assert alpha(witnessed).witnessed_offline == ("AL-X001-CG1",)
        monkeypatch.chdir(witnessed.root)
        assert main(["witness"]) == EXIT_OK
        assert alpha(witnessed).witnessed_offline == ("AL-X001-CG1",)
        assert verify_project(witnessed.root).ok

    def test_status_labels_stored_records(
        self,
        witnessed: Project,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        witnessed.run(run_type="exploratory")
        monkeypatch.chdir(witnessed.root)
        assert main(["status"]) == EXIT_OK
        out = capsys.readouterr().out
        assert (
            "Witnessed opens: 1, from stored records, not checked against the log"
            in (out)
        )

    def test_only_the_r23_call_counts(self, witnessed: Project) -> None:
        """An entry for the open head made later in the run is never recorded."""
        services: Services = witnessed.services  # type: ignore[attr-defined]
        services.rekor.down = True
        with witnessed.start(run_type="exploratory") as run:
            services.rekor.down = False
            config, me = identity(witnessed)
            logged = witness.submit_pending(witnessed.root, config.project, me).logged
            assert logged == [witnessed.rows()[0]["hash"]]  # the open head
            run.result("done")
        assert close_file(witnessed)["open_witness"] is None
        assert alpha(witnessed).unwitnessed == ("AL-X001-CG1",)

    def test_r23_submits_the_open_head_itself(
        self, witnessed: Project, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Another process of this writer may append before the R23 call."""
        real = witness.submit_current_head
        calls: list[int] = []

        def other_process_first(*args: Any, **kwargs: Any) -> Any:
            calls.append(1)
            if len(calls) == 1:
                append(witnessed, event="note", reason="written by another process")
            return real(*args, **kwargs)

        monkeypatch.setattr(witness, "submit_current_head", other_process_first)
        witnessed.run(run_type="exploratory")
        open_row, note, _ = witnessed.rows()
        assert (open_row["event"], note["event"]) == ("open", "note")
        recorded = close_file(witnessed)["open_witness"]
        assert recorded == witness.open_witness(
            stored_record(witnessed, open_row["hash"])
        )
        assert alpha(witnessed).witnessed_offline == ("AL-X001-CG1",)

    def test_forged_open_witness_never_counts(self, witnessed: Project) -> None:
        """An offline open, a later entry, and open_witness edited to match it."""
        services: Services = witnessed.services  # type: ignore[attr-defined]
        services.rekor.down = True
        witnessed.run(run_type="exploratory")
        services.rekor.down = False
        config, me = identity(witnessed)
        witness.submit_pending(witnessed.root, config.project, me)
        open_head = witnessed.rows()[0]["hash"]
        path = witnessed.root / "bgl" / "runs" / "AL-X001-CG1.close.json"
        content = json.loads(path.read_text())
        content["open_witness"] = witness.open_witness(
            stored_record(witnessed, open_head)
        )
        path.write_bytes(canonical_json(content) + b"\n")
        assert alpha(witnessed).unwitnessed == ("AL-X001-CG1",)
        assert any(f.check == "hash" for f in verify_project(witnessed.root).failures)

    def test_edited_open_witness_fails_verify(self, witnessed: Project) -> None:
        witnessed.run(run_type="exploratory")
        path = witnessed.root / "bgl" / "runs" / "AL-X001-CG1.close.json"
        content = json.loads(path.read_text())
        content["open_witness"]["log_index"] += 1
        path.write_bytes(canonical_json(content) + b"\n")
        failures = verify_project(witnessed.root).failures
        assert any(
            f.check == "hash" and "close file of AL-X001-CG1" in f.message
            for f in failures
        )
        assert alpha(witnessed).unwitnessed == ("AL-X001-CG1",)

    def test_open_witness_must_match_the_stored_record(
        self, witnessed: Project
    ) -> None:
        witnessed.run(run_type="exploratory")
        open_head = witnessed.rows()[0]["hash"]
        record_file = records(witnessed)[0].with_name(f"{open_head}.json")
        record_file.unlink()
        assert alpha(witnessed).unwitnessed == ("AL-X001-CG1",)
        # A second, later entry for the same head is a valid record, but not
        # the one the R23 call returned.
        config, me = identity(witnessed)
        assert witness.submit_pending(witnessed.root, config.project, me).logged == [
            open_head
        ]
        later = witness.open_witness(stored_record(witnessed, open_head))
        recorded = close_file(witnessed)["open_witness"]
        assert later["log_index"] != recorded["log_index"]
        assert alpha(witnessed).unwitnessed == ("AL-X001-CG1",)

    def test_open_head_without_a_stamp_is_unwitnessed(self, witnessed: Project) -> None:
        witnessed.run(run_type="exploratory")
        open_head = witnessed.rows()[0]["hash"]
        proofs = witnessed.root / "bgl" / "ledger" / "proofs" / "CG1"
        (proofs / f"{open_head}.ots").unlink()
        assert alpha(witnessed).unwitnessed == ("AL-X001-CG1",)


class TestNoCloseFile:
    """N60: a run with no close file counts if the open head has an entry."""

    def test_in_progress_is_pending(self, witnessed: Project) -> None:
        with witnessed.start(run_type="exploratory") as run:
            status = alpha(witnessed)
            assert status.unwitnessed == ()
            assert status.witnessed_offline == ()
            run.result("done")

    def test_hard_killed_with_an_entry_is_witnessed(self, witnessed: Project) -> None:
        with witnessed.start(run_type="exploratory") as run:
            run_id = hard_kill(run)
        assert witnessed.events() == ["open"]
        assert alpha(witnessed).witnessed_offline == (run_id,)
        append(witnessed, event="close", id=run_id, status="abandoned", reason="dead")
        assert alpha(witnessed).witnessed_offline == (run_id,)

    def test_hard_killed_offline_counts_once_logged(self, witnessed: Project) -> None:
        services: Services = witnessed.services  # type: ignore[attr-defined]
        services.rekor.down = True
        with witnessed.start(run_type="exploratory") as run:
            run_id = hard_kill(run)
        assert alpha(witnessed).unwitnessed == (run_id,)
        services.rekor.down = False
        config, me = identity(witnessed)
        witness.submit_pending(witnessed.root, config.project, me)
        assert alpha(witnessed).witnessed_offline == (run_id,)

    def test_normal_close_without_a_close_file_gets_no_leniency(
        self, witnessed: Project
    ) -> None:
        """A hand-written ok close with no close file is not an N60 run."""
        with witnessed.start(run_type="exploratory") as run:
            run_id = hard_kill(run)
        assert alpha(witnessed).witnessed_offline == (run_id,)  # N60, so far
        append(witnessed, event="close", id=run_id, status="ok", result="done")
        assert alpha(witnessed).unwitnessed == (run_id,)
        assert any(
            "close_file is null exactly on abandoned closes" in f.message
            for f in verify_project(witnessed.root).failures
        )

    def test_close_file_is_read_only_where_bgl_writes_it(
        self, witnessed: Project
    ) -> None:
        """A second close naming another path is not read at the usual one."""
        witnessed.run(run_type="exploratory")
        assert alpha(witnessed).witnessed_offline == ("AL-X001-CG1",)
        real = witnessed.last()
        append(
            witnessed,
            event="close",
            id="AL-X001-CG1",
            status="ok",
            close_file="bgl/runs/elsewhere.close.json",
            close_sha256=real["close_sha256"],
        )
        assert alpha(witnessed).unwitnessed == ("AL-X001-CG1",)


class TestSubmitCurrentHead:
    def test_returns_the_record_for_the_given_head(self, witnessed: Project) -> None:
        append(witnessed, event="note", reason="first")
        head = witnessed.last()["hash"]
        append(witnessed, event="note", reason="second")
        config, me = identity(witnessed)
        record = witness.submit_current_head(witnessed.root, config.project, me, head)
        assert record is not None and record["head"] == head
        assert witness.open_witness(record)["uuid"] == record["uuid"]

    def test_none_when_the_head_already_had_a_record(self, witnessed: Project) -> None:
        append(witnessed, event="note", reason="first")
        config, me = identity(witnessed)
        assert witness.submit_current_head(witnessed.root, config.project, me)
        assert witness.submit_current_head(witnessed.root, config.project, me) is None

    def test_after_append_never_raises_exception(
        self, witnessed: Project, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def broken(*args: Any) -> None:
            raise RuntimeError("bug")

        monkeypatch.setattr(witness, "submit_heads", broken)
        config, me = identity(witnessed)
        assert witness.after_append(witnessed.root, config.project, me) is None

    def test_after_append_lets_ctrl_c_through(
        self, witnessed: Project, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def interrupted(*args: Any) -> None:
            raise KeyboardInterrupt

        monkeypatch.setattr(witness, "submit_heads", interrupted)
        config, me = identity(witnessed)
        with pytest.raises(KeyboardInterrupt):
            witness.after_append(witnessed.root, config.project, me, "a" * 64)

    def test_none_when_rekor_is_down(self, witnessed: Project) -> None:
        witnessed.services.rekor.down = True  # type: ignore[attr-defined]
        append(witnessed, event="note", reason="first")
        config, me = identity(witnessed)
        assert witness.after_append(witnessed.root, config.project, me) is None


class TestOpenWitnessFields:
    def record(self) -> dict[str, Any]:
        return {
            "uuid": "u",
            "entry": {
                "logIndex": 3,
                "integratedTime": 1700000000,
                "logID": "l",
                "verification": {"signedEntryTimestamp": "s"},
            },
        }

    def test_fields(self) -> None:
        assert witness.open_witness(self.record()) == {
            "uuid": "u",
            "log_index": 3,
            "integrated_time": 1700000000,
            "log_id": "l",
            "signed_entry_timestamp": "s",
        }

    @pytest.mark.parametrize(
        "change",
        [
            lambda r: r.pop("uuid"),
            lambda r: r["entry"].pop("verification"),
            lambda r: r["entry"].update(logIndex=True),
            lambda r: r["entry"].update(integratedTime="1"),
            lambda r: r.update(uuid=""),
            lambda r: r.update(entry=None),
        ],
    )
    def test_malformed(self, change: Any) -> None:
        record = self.record()
        change(record)
        with pytest.raises(WitnessError):
            witness.open_witness(record)


class TestDuringRuns:
    def test_open_and_close_heads_are_witnessed(self, witnessed: Project) -> None:
        witnessed.run(run_type="exploratory")
        heads = [row["hash"] for row in witnessed.rows()]
        assert [p.stem for p in records(witnessed)] == sorted(heads)
        assert [p.stem for p in records(witnessed, witness.PROOFS_DIR)] == sorted(heads)

    def test_open_is_witnessed_before_the_code_runs(self, witnessed: Project) -> None:
        with witnessed.start(run_type="exploratory") as run:
            open_head = witnessed.last()["hash"]
            assert (records(witnessed)[0].stem) == open_head
            run.result("done")

    def test_refusal_is_witnessed(self, witnessed: Project) -> None:
        from bgl import RunRefused

        with pytest.raises(RunRefused), witnessed.start(prediction=None):
            pass
        assert [p.stem for p in records(witnessed)] == [witnessed.last()["hash"]]

    def test_offline_open_stays_unwitnessed_after_the_backlog(
        self, witnessed: Project
    ) -> None:
        """N10: a later entry for the open head never witnesses a closed run."""
        services: Services = witnessed.services  # type: ignore[attr-defined]
        services.rekor.down = True
        for calendar in services.calendars:
            calendar.down = True
        witnessed.run(run_type="exploratory")
        assert witnessed.last()["status"] == "ok"
        assert records(witnessed) == []
        assert close_file(witnessed)["open_witness"] is None
        assert alpha(witnessed).unwitnessed == ("AL-X001-CG1",)
        services.rekor.down = False
        for calendar in services.calendars:
            calendar.down = False
        config, me = identity(witnessed)
        result = witness.submit_pending(witnessed.root, config.project, me)
        assert len(result.logged) == 2 and len(result.stamped) == 2
        assert alpha(witnessed).unwitnessed == ("AL-X001-CG1",)
        assert alpha(witnessed).witnessed_offline == ()

    def test_witness_off_writes_nothing(self, project: Project) -> None:
        project.run()
        assert not (project.root / "bgl" / "ledger" / "witness").exists()


class TestStatusAndVerify:
    def test_status_counts(self, witnessed: Project) -> None:
        witnessed.run(run_type="exploratory")
        chain = project_status(witnessed.root).chains[0]
        assert chain.witness == (
            "2/2 heads in Rekor, 2 stamped (0 confirmed in Bitcoin), "
            "from stored records, not checked against the log"  # N11
        )

    def test_verify_passes_with_good_records(self, witnessed: Project) -> None:
        witnessed.run(run_type="exploratory")
        assert verify_project(witnessed.root).ok

    def test_rewritten_chain_is_caught_offline(self, witnessed: Project) -> None:
        witnessed.run(run_type="exploratory")
        chain = witnessed.root / "bgl" / "ledger" / "CG1.jsonl"
        lines = chain.read_bytes().split(b"\n")
        chain.write_bytes(lines[0] + b"\n")  # throw the close row away
        messages = [
            f.message
            for f in verify_project(witnessed.root).failures
            if f.check == "witness"
        ]
        assert any(m.startswith("Rekor record") and "rewritten" in m for m in messages)
        assert any(m.startswith("stamp") and "rewritten" in m for m in messages)

    def test_edited_record_is_caught(self, witnessed: Project) -> None:
        witnessed.run(run_type="exploratory")
        path = records(witnessed)[0]
        record = json.loads(path.read_text())
        record["entry"]["integratedTime"] += 60
        path.write_text(json.dumps(record))
        assert any(
            f.check == "witness" for f in verify_project(witnessed.root).failures
        )

    def test_corrupt_stamp_is_caught(self, witnessed: Project) -> None:
        witnessed.run(run_type="exploratory")
        records(witnessed, witness.PROOFS_DIR)[0].write_bytes(b"junk")
        assert any(
            f.check == "witness" for f in verify_project(witnessed.root).failures
        )


class TestDiscardCheck:
    def test_clean_chain(self, witnessed: Project) -> None:
        witnessed.run(run_type="exploratory")
        config, me = identity(witnessed)
        heads = [row["hash"] for row in witnessed.rows()]
        report = witness.discard_check(config.project, me.writer, heads)
        assert report.ok
        assert (report.entries, report.matched) == (2, 2)

    def test_thrown_away_run_is_found_only_by_rekor(self, witnessed: Project) -> None:
        witnessed.run(run_type="exploratory")
        kept = witnessed.rows()
        witnessed.run(run_type="exploratory")  # the run the writer will hide
        thrown = [row["hash"] for row in witnessed.rows()[len(kept) :]]
        config, me = identity(witnessed)
        # A thorough cover-up: the run's rows and every local file of it go.
        chain = witnessed.root / "bgl" / "ledger" / "CG1.jsonl"
        lines = chain.read_bytes().split(b"\n")
        chain.write_bytes(b"\n".join(lines[: len(kept)]) + b"\n")
        for path in records(witnessed) + records(witnessed, witness.PROOFS_DIR):
            if path.stem in thrown:
                path.unlink()
        for path in (witnessed.root / "bgl" / "runs").rglob("AL-X002-CG1*"):
            path.unlink()
        assert verify_project(witnessed.root).ok  # nothing left locally to see
        heads = [row["hash"] for row in witnessed.rows()]
        report = witness.discard_check(config.project, me.writer, heads)
        assert not report.ok
        assert (report.entries, report.matched, len(report.unmatched)) == (4, 2, 2)

    def test_search_unavailable_raises(self, witnessed: Project) -> None:
        witnessed.services.rekor.search_works = False  # type: ignore[attr-defined]
        config, me = identity(witnessed)
        from bgl.errors import WitnessError

        with pytest.raises(WitnessError):
            witness.discard_check(config.project, me.writer, [])

    def test_entry_made_outside_bgl_counts_as_unmatched(
        self, witnessed: Project
    ) -> None:
        config, me = identity(witnessed)
        services: Services = witnessed.services  # type: ignore[attr-defined]
        services.rekor.add_entry_for(
            me.writer.public_key, rekor.witness_message("cd" * 32), me.private_key
        )
        report = witness.discard_check(config.project, me.writer, [])
        assert len(report.unmatched) == 1


class TestCommand:
    def test_witness_command_submits_and_upgrades(
        self,
        witnessed: Project,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        services: Services = witnessed.services  # type: ignore[attr-defined]
        services.rekor.down = True
        witnessed.run(run_type="exploratory")
        services.rekor.down = False
        services.confirm_all()
        monkeypatch.chdir(witnessed.root)
        assert main(["witness"]) == EXIT_OK
        out = capsys.readouterr().out
        assert "Logged 2 head(s) in Rekor" in out
        assert "Upgraded 2 stamp(s)" in out
        chain = project_status(witnessed.root).chains[0]
        assert "(2 confirmed in Bitcoin)" in chain.witness

    def test_failures_exit_1(
        self,
        witnessed: Project,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        services: Services = witnessed.services  # type: ignore[attr-defined]
        services.rekor.down = True
        witnessed.run(run_type="exploratory")
        monkeypatch.chdir(witnessed.root)
        assert main(["witness"]) == EXIT_FAILED
        assert "Warning: Rekor" in capsys.readouterr().out

    def test_witness_off(
        self,
        project: Project,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.chdir(project.root)
        assert main(["witness"]) == EXIT_OK
        assert "off" in capsys.readouterr().out


def test_records_are_committed_with_the_ledger(witnessed: Project) -> None:
    witnessed.run(run_type="exploratory")
    witnessed.commit("ledger")
    tracked = run_git(witnessed.root, "ls-files", "bgl/ledger")
    assert "bgl/ledger/witness/CG1/" in tracked
    assert "bgl/ledger/proofs/CG1/" in tracked


def test_head_in_rekor_but_never_stamped_is_unwitnessed(witnessed: Project) -> None:
    services: Services = witnessed.services  # type: ignore[attr-defined]
    for calendar in services.calendars:
        calendar.down = True
    witnessed.run(run_type="exploratory")
    assert records(witnessed)  # logged in Rekor
    alpha = next(s for s in project_status(witnessed.root).studies if s.name == "alpha")
    assert alpha.unwitnessed == ("AL-X001-CG1",)

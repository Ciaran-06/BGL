"""Witnessing end to end: runs, files, status, verify and the discard check."""

import json
from pathlib import Path

import pytest
from conftest import Project, Services, run_git

from bgl import rekor, witness
from bgl.cli import EXIT_FAILED, EXIT_OK, main
from bgl.config import load_config
from bgl.status import project_status
from bgl.verify import verify_project
from bgl.writers import load_identity


def records(project: Project, kind: str = witness.WITNESS_DIR) -> list[Path]:
    folder = project.root / "bgl" / "ledger" / kind / "CG1"
    return sorted(folder.iterdir()) if folder.is_dir() else []


def identity(project: Project):  # type: ignore[no-untyped-def]
    config = load_config(project.root / "bgl.toml")
    return config, load_identity(project.root, config.project)


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

    def test_offline_run_still_runs_and_stays_pending(self, witnessed: Project) -> None:
        services: Services = witnessed.services  # type: ignore[attr-defined]
        services.rekor.down = True
        for calendar in services.calendars:
            calendar.down = True
        witnessed.run(run_type="exploratory")
        assert witnessed.last()["status"] == "ok"
        assert records(witnessed) == []
        status = project_status(witnessed.root)
        alpha = next(s for s in status.studies if s.name == "alpha")
        assert alpha.unwitnessed == ("AL-X001-CG1",)
        services.rekor.down = False
        for calendar in services.calendars:
            calendar.down = False
        config, me = identity(witnessed)
        result = witness.submit_pending(witnessed.root, config.project, me)
        assert len(result.logged) == 2 and len(result.stamped) == 2
        status = project_status(witnessed.root)
        assert next(s for s in status.studies if s.name == "alpha").unwitnessed == ()

    def test_witness_off_writes_nothing(self, project: Project) -> None:
        project.run()
        assert not (project.root / "bgl" / "ledger" / "witness").exists()


class TestStatusAndVerify:
    def test_status_counts(self, witnessed: Project) -> None:
        witnessed.run(run_type="exploratory")
        chain = project_status(witnessed.root).chains[0]
        assert chain.witness == "2/2 heads in Rekor, 2 stamped (0 confirmed in Bitcoin)"

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
        assert chain.witness.endswith("(2 confirmed in Bitcoin)")

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

"""The run lifecycle: what an opened run writes, and how it closes."""

import contextlib
import hashlib
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from conftest import Project

from bgl import RunError, RunInvalid, budget, verify_ledger, witness
from bgl import run as run_module
from bgl.capture import OutputCapture
from bgl.errors import LockTimeoutError
from bgl.locks import file_lock
from bgl.verify import verify_project

SERIES = {
    "gross": {1: {"2020-01-02": 0.01}, 2: {"2020-01-02": 0.02}},
    "turnover": {1: {"2020-01-02": 0.5}, 2: {"2020-01-02": 0.0}},
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class TestOpenAndClose:
    def test_first_counted_run_writes_lock_open_close(self, project: Project) -> None:
        run_id = project.run()
        assert run_id == "AL-R001-CG1"
        assert project.events() == ["lock", "open", "close"]
        lock, opened, closed = project.rows()
        assert {lock["id"], opened["id"], closed["id"]} == {run_id}
        assert opened["status"] == "open"
        assert closed["status"] == "ok"
        assert closed["result"] == "done"

    def test_second_counted_run_writes_no_second_lock(self, project: Project) -> None:
        project.run()
        assert project.run() == "AL-R002-CG1"
        assert project.events().count("lock") == 1

    def test_uncounted_first_run_writes_no_lock(self, project: Project) -> None:
        project.run(run_type="exploratory")
        project.run(
            run_type="data",
            sample=None,
            variants=None,
            prediction=None,
            sample_start=None,
            sample_end=None,
        )
        assert project.events() == ["open", "close", "open", "close"]

    def test_id_letters_and_separate_counters(self, project: Project) -> None:
        ids = [
            project.run(),
            project.run(run_type="exploratory"),
            project.run(run_type="exploratory"),
            project.run(
                run_type="data",
                sample=None,
                variants=None,
                sample_start=None,
                sample_end=None,
            ),
            project.run(study="beta"),
        ]
        assert ids == [
            "AL-R001-CG1",
            "AL-X001-CG1",
            "AL-X002-CG1",
            "AL-D001-CG1",
            "BE-R001-CG1",
        ]

    def test_open_row_records_the_request_and_git_facts(self, project: Project) -> None:
        head = project.commit("again")
        project.run(config={"x": 0.5})
        opened = project.rows()[1]
        assert opened["study"] == "alpha"
        assert opened["type"] == "diagnostic"
        assert opened["variants"] == 2
        assert opened["sample"] == "design"
        assert (opened["sample_start"], opened["sample_end"]) == (
            "2020-01-01",
            "2024-12-31",
        )
        assert opened["script"] == "scripts/run.py"
        assert opened["commit"] == head
        assert opened["user"] == "Test User <test@example.com>"
        assert opened["commit_signature"] == "N"
        assert opened["prediction"] == "Sharpe rises with lookback"
        assert opened["lock_file"] == ".bgl/locks/AL-R001-CG1.lock"
        assert opened["other_heads"] == {}
        assert (
            opened["script_sha256"]
            == hashlib.sha256(b"# the research script\n").hexdigest()
        )

    def test_chain_verifies_after_runs(self, project: Project) -> None:
        project.run()
        project.run(run_type="exploratory")
        report = verify_ledger(root=project.root)
        assert report.ok
        assert len(report.chains["CG1"].rows) == 5

    def test_run_lock_is_held_while_open_and_released_after(
        self, project: Project
    ) -> None:
        lock = project.root / ".bgl" / "locks" / "AL-R001-CG1.lock"
        with project.start() as run:
            run.result("done")
            with pytest.raises(LockTimeoutError), file_lock(lock, timeout=0):
                pass
        with file_lock(lock, timeout=0):
            pass

    def test_other_heads_names_other_chains(self, project: Project) -> None:
        project.add_writer("HC1", "custodian_a")
        project.write_toml()
        project.commit("add writer")
        project.run(run_type="exploratory")
        first_head = project.last()["hash"]
        project.use_writer("HC1")
        project.run(run_type="exploratory")
        assert project.rows("HC1")[0]["other_heads"] == {"CG1": first_head}
        project.use_writer("CG1")
        project.run(run_type="exploratory")
        assert project.rows()[2]["other_heads"] == {"HC1": project.last("HC1")["hash"]}

    def test_absent_other_chain_is_null(self, project: Project) -> None:
        project.add_writer("HC1", "custodian_a")
        project.write_toml()
        project.commit("add writer")
        project.run(run_type="exploratory")
        assert project.rows()[0]["other_heads"] == {"HC1": None}


class TestManifestAndCloseFile:
    def test_manifest_matches_its_hash_and_holds_inputs(self, project: Project) -> None:
        project.run(config={"threshold": 0.5, "names": ("a", "b")})
        opened = project.rows()[1]
        path = project.root / opened["manifest"]
        assert sha(path) == opened["manifest_sha256"]
        manifest = project.json(opened["manifest"])
        assert manifest["id"] == "AL-R001-CG1"
        assert manifest["config"] == {"threshold": "0.5", "names": ["a", "b"]}
        design = project.root / "data" / "design.csv"
        assert manifest["inputs"] == [
            {
                "path": "data/design.csv",
                "size": design.stat().st_size,
                "sha256": sha(design),
            }
        ]
        assert manifest["git"]["branch"] == "main"
        assert manifest["git"]["commit"] == opened["commit"]
        assert manifest["costs"] is None
        assert manifest["environment"]["python"].count(".") == 2

    def test_close_file_matches_its_hash(self, project: Project) -> None:
        with project.start() as run:
            run.note("first remark")
            run.result("done")
        closed = project.last()
        assert sha(project.root / closed["close_file"]) == closed["close_sha256"]
        close = project.json(closed["close_file"])
        assert close["notes"] == ["first remark"]
        assert close["error"] is None
        assert close["hook_errors"] == 0
        assert close["untracked_modules"] == {"seen": [], "scan": []}

    def test_undeclared_reads_are_listed_declared_ones_are_not(
        self, project: Project
    ) -> None:
        with project.start() as run:
            (project.root / "data" / "design.csv").read_text()
            (project.root / "docs" / "alpha.md").read_text()
            run.result("done")
        close = project.json(project.last()["close_file"])
        assert close["files_opened"] == ["docs/alpha.md"]

    def test_sqlite_database_is_listed_as_opened(self, project: Project) -> None:
        import sqlite3

        with project.start() as run:
            connection = sqlite3.connect(project.root / "data" / "prices.db")
            connection.execute("create table t (x)")
            connection.close()
            sqlite3.connect(":memory:").close()
            run.result("done")
        close = project.json(project.last()["close_file"])
        assert close["files_opened"] == ["data/prices.db"]

    def test_outputs_are_hashed_at_close(self, project: Project) -> None:
        with project.start() as run:
            out = project.root / "results" / "table.csv"
            out.parent.mkdir()
            out.write_text("r\n")
            run.output(out)
            run.output("results/never_written.csv")
            run.result("done")
        close = project.json(project.last()["close_file"])
        assert close["outputs"] == [
            {"path": "results/table.csv", "sha256": sha(out), "tainted": False},
            {"path": "results/never_written.csv", "sha256": None, "tainted": False},
        ]

    def test_processes_started_by_the_run_are_recorded(self, project: Project) -> None:
        import subprocess

        with project.start() as run:
            subprocess.run([sys.executable, "-c", "pass"], check=True)
            run.result("done")
        close = project.json(project.last()["close_file"])
        assert close["processes"]["starts"]["subprocess.Popen"] == 1


class TestStatus:
    def test_no_result(self, project: Project) -> None:
        project.run(result=None)
        assert project.last()["status"] == "no_result"

    def test_exception_is_recorded_as_crashed_and_reraised(
        self, project: Project
    ) -> None:
        with pytest.raises(ZeroDivisionError), project.start() as run:
            run.result("about to fail")
            _ = 1 / 0
        closed = project.last()
        assert closed["status"] == "crashed"
        assert closed["reason"] == "ZeroDivisionError: division by zero"
        error = project.json(closed["close_file"])["error"]
        assert error["type"] == "ZeroDivisionError"
        assert "1 / 0" in error["traceback"]

    def test_ctrl_c_is_recorded_as_interrupted_and_reraised(
        self, project: Project
    ) -> None:
        with pytest.raises(KeyboardInterrupt), project.start():
            raise KeyboardInterrupt
        assert project.last()["status"] == "interrupted"

    @pytest.mark.parametrize(
        ("code", "status"), [(0, "ok"), (None, "ok"), (2, "crashed"), ("x", "crashed")]
    )
    def test_sys_exit(self, project: Project, code: object, status: str) -> None:
        with pytest.raises(SystemExit), project.start() as run:
            run.result("done")
            sys.exit(code)  # type: ignore[arg-type]
        assert project.last()["status"] == status

    def test_crashed_run_still_uses_budget(self, project: Project) -> None:
        with pytest.raises(RuntimeError), project.start():
            raise RuntimeError("boom")
        assert budget("alpha", root=project.root).used == 1


class TestFailureAfterTheOpenRow:
    """F3: anything that fails between the open row and the body closes the run."""

    def fail_first_call(
        self, monkeypatch: pytest.MonkeyPatch, target: Any, name: str, error: Any
    ) -> list[int]:
        calls: list[int] = []
        real = getattr(target, name)

        def patched(*args: Any, **kwargs: Any) -> Any:
            calls.append(1)
            if len(calls) == 1:
                raise error
            return real(*args, **kwargs)

        monkeypatch.setattr(target, name, patched)
        return calls

    def check_one_close(self, project: Project, status: str) -> dict[str, Any]:
        assert project.events() == ["lock", "open", "close"]
        closed = project.last()
        assert closed["status"] == status
        assert closed["id"] == "AL-R001-CG1"
        report = verify_project(project.root)
        assert report.ok, report.failures
        lock = project.root / ".bgl" / "locks" / "AL-R001-CG1.lock"
        with file_lock(lock, timeout=0):
            pass  # released
        assert budget("alpha", root=project.root).used == 1
        return closed

    def test_ctrl_c_in_the_witness_call(
        self, project: Project, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # after_append lets KeyboardInterrupt through: only Exception is caught.
        self.fail_first_call(
            monkeypatch, witness, "submit_current_head", KeyboardInterrupt()
        )
        with pytest.raises(KeyboardInterrupt), project.start():
            pytest.fail("the body must not run")
        self.check_one_close(project, "interrupted")

    def test_ctrl_c_while_releasing_the_ledger_lock(
        self, project: Project, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The open row is written; Ctrl+C lands as the ledger lock is let go."""
        real = run_module.ledger_lock

        @contextlib.contextmanager
        def interrupted_on_release(root: Path) -> Iterator[None]:
            with real(root):
                yield
            raise KeyboardInterrupt

        monkeypatch.setattr(run_module, "ledger_lock", interrupted_on_release)
        with pytest.raises(KeyboardInterrupt), project.start():
            pytest.fail("the body must not run")
        # Restore only this patch: undo() would also drop BGL_HOME and git_env.
        monkeypatch.setattr(run_module, "ledger_lock", real)
        self.check_one_close(project, "interrupted")

    def test_ctrl_c_while_the_open_row_is_appended(
        self, project: Project, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The row reached the file; Ctrl+C lands before the append returns."""
        real = run_module.append_row_unlocked

        def interrupted_after_write(*args: Any, **kwargs: Any) -> Any:
            row = real(*args, **kwargs)
            if row["event"] == "open":
                raise KeyboardInterrupt
            return row

        monkeypatch.setattr(run_module, "append_row_unlocked", interrupted_after_write)
        with pytest.raises(KeyboardInterrupt), project.start():
            pytest.fail("the body must not run")
        self.check_one_close(project, "interrupted")

    def test_ctrl_c_before_the_open_row_writes_no_close(
        self, project: Project, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        real = run_module.append_row_unlocked

        def interrupted_before_write(*args: Any, **kwargs: Any) -> Any:
            if args[-1]["event"] == "open":
                raise KeyboardInterrupt
            return real(*args, **kwargs)

        monkeypatch.setattr(run_module, "append_row_unlocked", interrupted_before_write)
        with pytest.raises(KeyboardInterrupt), project.start():
            pytest.fail("the body must not run")
        assert project.events() == ["lock"]  # no open row, so no close row
        lock = project.root / ".bgl" / "locks" / "AL-R001-CG1.lock"
        with file_lock(lock, timeout=0):
            pass  # released

    def test_the_close_head_is_witnessed(
        self, project: Project, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """_abort submits the close row's head like any close (R23's sibling)."""
        heads: list[object] = []
        real = witness.after_append

        def record(*args: Any) -> Any:
            heads.append(args[3] if len(args) > 3 else "newest")
            if len(heads) == 1:
                raise RuntimeError("x")
            return real(*args)

        monkeypatch.setattr(witness, "after_append", record)
        with pytest.raises(RuntimeError), project.start():
            pytest.fail("the body must not run")
        assert heads == [project.rows()[1]["hash"], "newest"]

    def test_error_in_the_witness_call(
        self, project: Project, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self.fail_first_call(monkeypatch, witness, "after_append", RuntimeError("x"))
        with pytest.raises(RuntimeError), project.start():
            pytest.fail("the body must not run")
        closed = self.check_one_close(project, "crashed")
        assert closed["reason"] == "failed after the open row: RuntimeError: x"
        error = project.json(closed["close_file"])["error"]
        assert error["type"] == "RuntimeError"

    def test_sys_exit_0_there_is_still_a_crash(
        self, project: Project, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self.fail_first_call(monkeypatch, witness, "after_append", SystemExit(0))
        with pytest.raises(SystemExit), project.start():
            pytest.fail("the body must not run")
        self.check_one_close(project, "crashed")

    def test_output_capture_failing_to_start(
        self, project: Project, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self.fail_first_call(monkeypatch, OutputCapture, "start", OSError("disk"))
        before = sys.stdout
        with pytest.raises(OSError, match="disk"), project.start():
            pytest.fail("the body must not run")
        assert sys.stdout is before
        closed = self.check_one_close(project, "crashed")
        output = project.json(closed["close_file"])["output"]
        assert output["stdout"]["sha256"] is None
        assert output["stdout"]["bytes"] is None

    def test_the_failed_run_is_never_closed_twice(
        self, project: Project, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self.fail_first_call(monkeypatch, witness, "after_append", RuntimeError("x"))
        run = project.start()
        with pytest.raises(RuntimeError):
            run.__enter__()
        run.__exit__(None, None, None)  # a caller that exits anyway
        with pytest.raises(RunError, match="not open"):
            run.result("done")
        assert project.events().count("close") == 1

    def test_dry_run_writes_only_its_dry_row(
        self, project: Project, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fixture = project.write("scratch/fixture.csv", "a,b\n0,0\n")
        self.fail_first_call(
            monkeypatch, witness, "submit_current_head", KeyboardInterrupt()
        )
        with (
            pytest.raises(KeyboardInterrupt),
            project.start(dry=True, dry_inputs={"data/design.csv": fixture}),
        ):
            pytest.fail("the body must not run")
        assert project.events() == ["dry"]
        (project.root / "data" / "design.csv").read_text()  # not watching


class TestReturns:
    def test_series_file_and_hash(self, project: Project) -> None:
        with project.start() as run:
            run.returns(**SERIES)
            run.result("done")
        closed = project.last()
        path = project.root / "bgl" / "runs" / "returns" / "AL-R001-CG1.csv"
        assert path.read_bytes() == (
            b"period,variant,gross,turnover\n"
            b"2020-01-02,1,0.0100000000,0.5000000000\n"
            b"2020-01-02,2,0.0200000000,0.0000000000\n"
        )
        assert closed["returns_sha256"] == sha(path)
        close = project.json(closed["close_file"])
        assert close["series"] == {
            "variants": 2,
            "periods": 1,
            "first": "2020-01-02",
            "last": "2020-01-02",
        }

    def test_no_returns_means_no_file_and_null_hash(self, project: Project) -> None:
        project.run()
        assert project.last()["returns_sha256"] is None
        assert not (project.root / "bgl" / "runs" / "returns").exists()

    def test_wrong_variant_count_is_refused_and_run_crashes(
        self, project: Project
    ) -> None:
        with pytest.raises(RunError, match="declared 2"), project.start() as run:
            run.returns(gross={1: {"2020-01-02": 0.1}}, turnover={1: {"2020-01-02": 0}})
        assert project.last()["status"] == "crashed"

    def test_period_outside_declared_sample_is_refused(self, project: Project) -> None:
        late = {1: {"2025-06-01": 0.1}, 2: {"2025-06-01": 0.1}}
        with pytest.raises(RunError, match="sample_end"), project.start() as run:
            run.returns(gross=late, turnover=late)

    def test_only_once(self, project: Project) -> None:
        with pytest.raises(RunError, match="already called"), project.start() as run:
            run.returns(**SERIES)
            run.returns(**SERIES)

    def test_data_run_records_no_returns(self, project: Project) -> None:
        arguments: dict[str, Any] = {
            "run_type": "data",
            "sample": None,
            "variants": None,
            "sample_start": None,
            "sample_end": None,
        }
        with (
            pytest.raises(RunError, match="no variants"),
            project.start(**arguments) as run,
        ):
            run.returns(**SERIES)

    def test_exploratory_run_may_record_returns(self, project: Project) -> None:
        with project.start(run_type="exploratory") as run:
            run.returns(**SERIES)
            run.result("done")
        assert project.last()["returns_sha256"] is not None


class TestOutputCapture:
    def test_counted_run_output_is_captured_and_hashed(
        self, project: Project, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with project.start() as run:
            print("to stdout")
            print("to stderr", file=sys.stderr)
            run.result("done")
        closed = project.last()
        out = project.root / "bgl" / "runs" / "output" / "AL-R001-CG1.stdout"
        err = out.with_suffix(".stderr")
        assert out.read_bytes() == b"to stdout\n"
        assert err.read_bytes() == b"to stderr\n"
        assert closed["output_sha256"] == {"stdout": sha(out), "stderr": sha(err)}
        assert capsys.readouterr().out == "to stdout\n"
        close = project.json(closed["close_file"])
        assert close["output"]["stdout"] == {
            "file": "bgl/runs/output/AL-R001-CG1.stdout",
            "sha256": sha(out),
            "bytes": len(b"to stdout\n"),  # F9
            "holdout_load": None,
        }
        assert close["output"]["stderr"]["bytes"] == len(b"to stderr\n")

    def test_uncounted_run_is_not_captured(self, project: Project) -> None:
        with project.start(run_type="exploratory") as run:
            print("hello")
            run.result("done")
        assert project.last()["output_sha256"] is None
        assert not (project.root / "bgl" / "runs" / "output").exists()

    def test_streams_restored_after_a_crash(self, project: Project) -> None:
        before = sys.stdout
        with pytest.raises(RuntimeError), project.start():
            raise RuntimeError("boom")
        assert sys.stdout is before


class TestRunObject:
    def test_cannot_be_entered_twice(self, project: Project) -> None:
        run = project.start()
        with run:
            run.result("done")
        with pytest.raises(RunError, match="only once"), run:
            pass

    def test_one_open_run_per_process(self, project: Project) -> None:
        with project.start() as run:
            with pytest.raises(RunError, match="already open"), project.start():
                pass
            run.result("done")
        assert project.events() == ["lock", "open", "close"]
        project.run()  # and a new run can start once the first has closed

    def test_methods_need_an_open_run(self, project: Project) -> None:
        run = project.start()
        with pytest.raises(RunError, match="not open"):
            run.result("x")
        with run:
            run.result("done")
        with pytest.raises(RunError, match="not open"):
            _ = run.id

    @pytest.mark.parametrize("text", ["", "   ", 5])
    def test_result_must_be_words(self, project: Project, text: Any) -> None:
        with pytest.raises(RunError, match="summary"), project.start() as run:
            run.result(text)

    def test_input_returns_the_declared_file(self, project: Project) -> None:
        with project.start() as run:
            assert run.input("data/design.csv") == project.root / "data" / "design.csv"
            with pytest.raises(RunError, match="not a declared input"):
                run.input("data/raw.csv")
            run.result("done")

    def test_positions_is_not_built_yet(self, project: Project) -> None:
        with pytest.raises(RunError, match="replay"), project.start() as run:
            run.positions("2020-01-02", {"A": 1.0})

    @pytest.mark.parametrize(
        ("change", "message"),
        [
            ({"run_type": "backtest"}, "run_type"),
            ({"sample": "test"}, "sample"),
            ({"variants": 2.0}, "variants"),
            ({"variants": True}, "variants"),
            ({"inputs": "data/design.csv"}, "list of paths"),
            ({"claims": "effect_exists"}, "claims"),
            ({"config": [1]}, "config"),
            ({"config": {"x": float("nan")}}, "config.x"),
            ({"sample_start": "01/02/2020"}, "sample_start"),
            ({"selects": {"run": "AL-R001-CG1"}}, "selects"),
            ({"dry_inputs": {"a": "b"}}, "dry=True"),
        ],
    )
    def test_arguments_of_the_wrong_kind_raise_before_anything_is_written(
        self, project: Project, change: dict[str, Any], message: str
    ) -> None:
        with pytest.raises(ValueError, match=message):
            project.start(**change)
        assert project.rows() == []

    def test_input_outside_the_repository_raises(self, project: Project) -> None:
        with (
            pytest.raises(ValueError, match="outside"),
            project.start(inputs=["../elsewhere.csv"]),
        ):
            pass
        assert project.rows() == []


class TestBudgetFunction:
    def test_counts_counted_opens_only(self, project: Project) -> None:
        project.run()
        project.run(run_type="exploratory")
        used = budget("alpha", root=project.root)
        assert (used.budget, used.used, used.left) == (3, 1, 2)
        assert budget("beta", root=project.root).used == 0

    def test_unknown_study(self, project: Project) -> None:
        with pytest.raises(RunError, match="unknown study"):
            budget("gamma", root=project.root)


class TestInvalid:
    """R17 and R20 while the run is open: the run closes as invalid."""

    def write_scratch_module(self, project: Project, name: str) -> Path:
        return project.write(f"scratch/{name}.py", "VALUE = 1\n")

    def test_untracked_import_closes_the_run_invalid(
        self, project: Project, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self.write_scratch_module(project, "helper_a")
        monkeypatch.syspath_prepend(str(project.root / "scratch"))
        try:
            with pytest.raises(RunInvalid, match="scratch/helper_a.py"):
                with project.start() as run:
                    import helper_a  # type: ignore[import-not-found]  # noqa: F401

                    run.result("done")
        finally:
            sys.modules.pop("helper_a", None)
        closed = project.last()
        assert closed["status"] == "invalid"
        assert closed["reason"].startswith("R17")
        close = project.json(closed["close_file"])
        assert "scratch/helper_a.py" in close["untracked_modules"]["seen"]
        assert budget("alpha", root=project.root).used == 1

    def test_exec_of_untracked_source_is_caught_by_the_hook(
        self, project: Project
    ) -> None:
        source = self.write_scratch_module(project, "helper_b")
        with pytest.raises(RunInvalid), project.start() as run:
            exec(source.read_text())
            run.result("done")
        close = project.json(project.last()["close_file"])
        assert close["untracked_modules"] == {
            "seen": ["scratch/helper_b.py"],
            "scan": [],
        }

    def test_tracked_code_is_fine(self, project: Project) -> None:
        with project.start() as run:
            exec((project.root / "scripts" / "run.py").read_text())
            run.result("done")
        assert project.last()["status"] == "ok"

    def test_invalid_wins_over_a_crash_and_the_crash_is_reraised(
        self, project: Project
    ) -> None:
        source = self.write_scratch_module(project, "helper_c")
        with pytest.raises(ZeroDivisionError), project.start():
            exec(source.read_text())
            _ = 1 / 0
        closed = project.last()
        assert closed["status"] == "invalid"
        assert project.json(closed["close_file"])["error"]["type"] == (
            "ZeroDivisionError"
        )

    def test_design_run_opening_a_holdout_file_is_invalid(
        self, project: Project
    ) -> None:
        with pytest.raises(RunInvalid, match="R20"), project.start(study="beta") as run:
            (project.root / "data" / "holdout.csv").read_text()
            run.result("done")
        assert project.last()["status"] == "invalid"

    def test_design_run_opening_a_restricted_file_is_invalid(
        self, project: Project
    ) -> None:
        with (
            pytest.raises(RunInvalid, match="restricted"),
            project.start(study="beta") as run,
        ):
            (project.root / "data" / "raw.csv").read_text()
            run.result("done")

    def test_split_run_before_the_lock_may_open_both(self, project: Project) -> None:
        arguments: dict[str, Any] = {
            "study": "beta",
            "run_type": "data",
            "sample": None,
            "variants": None,
            "sample_start": None,
            "sample_end": None,
            "inputs": ["data/raw.csv"],
        }
        with project.start(**arguments) as run:
            (project.root / "data" / "raw.csv").read_text()
            (project.root / "data" / "holdout.csv").write_text("a,b\n9,9\n")
            run.result("split done")
        assert project.last()["status"] == "ok"

    def test_sqlite_open_of_a_holdout_file_is_invalid(self, project: Project) -> None:
        """F8: sqlite opens files in C; its audit event is treated as an open."""
        import sqlite3

        with (
            pytest.raises(RunInvalid, match="R20: the run opened the holdout file"),
            project.start(study="beta") as run,
        ):
            sqlite3.connect(project.root / "data" / "holdout.csv").close()
            run.result("done")
        assert project.last()["status"] == "invalid"

    def test_other_studies_holdout_is_not_this_studys_business(
        self, project: Project
    ) -> None:
        with project.start() as run:  # study alpha has no holdout
            (project.root / "data" / "holdout.csv").read_text()
            run.result("done")
        assert project.last()["status"] == "ok"


class TestHoldoutRun:
    def open_holdout(self, project: Project) -> Any:
        selected = project.run(study="beta")
        return project.start(**project.holdout_arguments(selected))

    def test_holdout_load_and_taint(self, project: Project) -> None:
        with self.open_holdout(project) as run:
            print("before")
            path = run.holdout("data/holdout.csv")
            assert path == project.root / "data" / "holdout.csv"
            path.read_text()
            print("after")
            out = project.write("results/final.csv")
            run.output(out)
            run.outcome("effect_exists", "supported")
            run.result("done")
        closed = project.last()
        assert closed["status"] == "ok"
        assert closed["outcome"] == {"effect_exists": "supported"}
        close = project.json(closed["close_file"])
        assert close["output"]["stdout"]["holdout_load"] == len(b"before\n")
        assert close["output"]["stderr"]["holdout_load"] == 0
        assert close["outputs"][0]["tainted"] is True
        assert close["outcome"] == {"effect_exists": "supported"}

    def test_first_load_position_is_kept(self, project: Project) -> None:
        with self.open_holdout(project) as run:
            run.holdout("data/holdout.csv")
            print("x" * 10)
            run.holdout("data/holdout.csv")
            run.outcome("effect_exists", "inconclusive")
            run.result("done")
        close = project.json(project.last()["close_file"])
        assert close["output"]["stdout"]["holdout_load"] == 0

    def test_reading_the_holdout_without_run_holdout_is_invalid(
        self, project: Project
    ) -> None:
        with pytest.raises(RunInvalid, match="R20"):
            with self.open_holdout(project) as run:
                (project.root / "data" / "holdout.csv").read_text()
                run.result("done")

    def test_missing_outcome_is_no_result(self, project: Project) -> None:
        with self.open_holdout(project) as run:
            run.result("done")
        closed = project.last()
        assert closed["status"] == "no_result"
        assert closed["outcome"] == {"effect_exists": None}
        assert "effect_exists" in closed["reason"]

    def test_run_holdout_refusals(self, project: Project) -> None:
        with project.start(study="beta") as run:
            with pytest.raises(RunError, match="only for a confirmatory holdout"):
                run.holdout("data/holdout.csv")
            run.result("done")
        selected = project.rows()[1]["id"]
        with project.start(**project.holdout_arguments(selected)) as run:
            with pytest.raises(RunError, match="not a holdout file"):
                run.holdout("data/design.csv")
            with pytest.raises(RunError, match="not one of this run's claims"):
                run.outcome("other", "supported")
            with pytest.raises(RunError, match="one of supported"):
                run.outcome("effect_exists", "yes")
            run.outcome("effect_exists", "not_supported")
            run.result("done")

    def test_only_confirmatory_runs_declare_outcomes(self, project: Project) -> None:
        with project.start() as run:
            with pytest.raises(RunError, match="confirmatory"):
                run.outcome("effect_exists", "supported")
            run.result("done")


class TestDryRun:
    def fixture_file(self, project: Project) -> Path:
        return project.write("scratch/fixture.csv", "a,b\n0,0\n")

    def test_writes_one_dry_row_and_nothing_else(self, project: Project) -> None:
        fixture = self.fixture_file(project)
        with project.start(dry=True, dry_inputs={"data/design.csv": fixture}) as run:
            assert run.id == "AL-T001-CG1"
            assert run.input("data/design.csv") == fixture
            run.input("data/design.csv").read_text()
            run.returns(**SERIES)
            run.result("done")
        assert project.events() == ["dry"]
        row = project.last()
        assert (row["status"], row["id"], row["type"]) == (
            "dry",
            "AL-T001-CG1",
            "diagnostic",
        )
        assert not (project.root / "bgl" / "runs").exists()
        assert budget("alpha", root=project.root).used == 0

    def test_real_input_is_blocked(self, project: Project) -> None:
        fixture = self.fixture_file(project)
        with pytest.raises(PermissionError, match="R25"):
            with project.start(dry=True, dry_inputs={"data/design.csv": fixture}):
                (project.root / "data" / "design.csv").read_text()
        assert project.events() == ["dry"]

    def test_sqlite_open_is_blocked(self, project: Project) -> None:
        import sqlite3

        fixture = self.fixture_file(project)
        with pytest.raises(PermissionError, match="R25"):
            with project.start(dry=True, dry_inputs={"data/design.csv": fixture}):
                sqlite3.connect(project.root / "data" / "prices.db")
        assert not (project.root / "data" / "prices.db").exists()

    def test_block_ends_with_the_run(self, project: Project) -> None:
        fixture = self.fixture_file(project)
        with project.start(dry=True, dry_inputs={"data/design.csv": fixture}):
            pass
        (project.root / "data" / "design.csv").read_text()

    def test_fixtures_must_cover_the_inputs_exactly(self, project: Project) -> None:
        with pytest.raises(ValueError, match="one fixture for each"):
            with project.start(dry=True, dry_inputs={}):
                pass
        assert project.rows() == []

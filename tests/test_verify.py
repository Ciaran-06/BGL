import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest
from conftest import Project, hard_kill, run_git

from bgl import RunRefused, setup
from bgl.canonical import line_bytes
from bgl.config import load_config
from bgl.errors import AmendError
from bgl.home import create_machine_id
from bgl.ledger import append_row, build_row
from bgl.records import read_ledger
from bgl.status import project_status
from bgl.studylock import amend_study, effective_lock
from bgl.verify import _safe_relative, verify_project
from bgl.writers import load_identity


def checks(project: Project, **kwargs: object) -> list[tuple[str, str]]:
    report = verify_project(project.root, **kwargs)  # type: ignore[arg-type]
    return [(f.check, f.message) for f in report.failures]


def failing(project: Project) -> list[str]:
    return [check for check, _ in checks(project)]


def warning_checks(project: Project) -> list[str]:
    return [f.check for f in verify_project(project.root).warnings]


def append(project: Project, **fields: Any) -> dict[str, Any]:
    """Append one hand-made row to the current writer's chain."""
    config = load_config(project.root / "bgl.toml")
    identity = load_identity(project.root, config.project)
    return append_row(
        project.root,
        config.project,
        identity.writer,
        identity.private_key,
        {"machine_id": identity.machine_id, **fields},
    )


def second_writer(project: Project) -> None:
    project.add_writer("HC1", "custodian_a")
    project.write_toml()
    project.commit("add HC1")


def test_clean_project_passes(project: Project) -> None:
    project.run()
    with project.start(run_type="exploratory") as run:
        run.returns(
            gross={1: {"2020-01-02": 0.1}, 2: {"2020-01-02": 0.2}},
            turnover={1: {"2020-01-02": 0}, 2: {"2020-01-02": 0}},
        )
        run.result("done")
    report = verify_project(project.root)
    assert report.ok, report.failures
    assert report.warnings == ()


def test_empty_project_passes(project: Project) -> None:
    assert verify_project(project.root).ok


class TestChains:
    def test_broken_chain(self, project: Project) -> None:
        project.run()
        path = project.root / "bgl" / "ledger" / "CG1.jsonl"
        path.write_bytes(path.read_bytes().replace(b"Lookback grid", b"Lookback grit"))
        assert ("chain") in failing(project)

    def test_unregistered_chain_file(self, project: Project) -> None:
        project.run()
        (project.root / "bgl" / "ledger" / "XX9.jsonl").write_bytes(b"")
        messages = checks(project)
        assert any("XX9.jsonl" in m for c, m in messages if c == "chain")


class TestPairing:
    def append(self, project: Project, **fields: object) -> None:
        from bgl.config import load_config
        from bgl.ledger import append_row
        from bgl.writers import load_identity

        config = load_config(project.root / "bgl.toml")
        identity = load_identity(project.root, config.project)
        append_row(
            project.root,
            config.project,
            identity.writer,
            identity.private_key,
            {"machine_id": identity.machine_id, **fields},
        )

    def test_hard_killed_run_is_unclosed(self, project: Project) -> None:
        script = textwrap.dedent(
            f"""
            import os
            from bgl import start_run
            with start_run(study="alpha", run_type="exploratory",
                           script={str(project.root / "scripts" / "run.py")!r},
                           what_changed="w", sample="design",
                           sample_start="2020-01-01", sample_end="2020-12-31",
                           variants=1):
                os._exit(0)
            """
        )
        subprocess.run([sys.executable, "-c", script], check=True, env=os.environ)
        assert project.events() == ["open"]
        messages = checks(project)
        assert any(
            c == "unclosed" and "AL-X001-CG1" in m and "abandon" in m
            for c, m in messages
        )

    def test_run_in_progress_is_not_a_failure(self, project: Project) -> None:
        with project.start() as run:
            report = verify_project(project.root)
            assert report.in_progress == ("AL-R001-CG1",)
            assert report.ok
            run.result("done")

    def test_open_from_another_machine_cannot_be_told(self, project: Project) -> None:
        with project.start() as run:
            (project.home / "machine_id").unlink()
            create_machine_id(project.home)
            messages = checks(project)
            assert any("another machine" in m for c, m in messages if c == "unclosed")
            run.result("done")

    def test_synced_folder_cannot_be_told(
        self, project: Project, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        with project.start() as run:
            monkeypatch.setattr(setup, "_is_network", lambda path: True)
            assert "unclosed" in failing(project)
            monkeypatch.undo()
            run.result("done")

    def test_close_without_open(self, project: Project) -> None:
        self.append(project, event="close", id="AL-R009-CG1", status="ok")
        assert any("no open row" in m for _, m in checks(project))

    def test_two_closes(self, project: Project) -> None:
        run_id = project.run()
        self.append(project, event="close", id=run_id, status="ok")
        assert any("2 close rows" in m for _, m in checks(project))

    def test_late_close_until_acknowledged(self, project: Project) -> None:
        run_id = project.run()
        self.append(project, event="close", id=run_id, status="abandoned")
        assert "late close" in failing(project)
        self.append(project, event="ack", id=run_id, reason="reviewed")
        assert "late close" not in failing(project)

    def test_true_late_close(self, project: Project) -> None:
        """Abandoned first (while the run still ran), then the normal close."""
        second_writer(project)
        with project.start(run_type="exploratory") as run:
            project.use_writer("HC1")
            append(project, event="close", id=run.id, status="abandoned")
            project.use_writer("CG1")
            run.result("done")
        assert project.last()["status"] == "ok"
        assert failing(project) == ["late close"]
        append(project, event="ack", id="AL-X001-CG1", reason="reviewed")
        assert verify_project(project.root).ok

    def test_normal_close_outside_the_runs_chain(self, project: Project) -> None:
        """F12: only an abandon may close a run from another writer's chain."""
        second_writer(project)
        run_id = project.run(run_type="exploratory")
        project.use_writer("HC1")
        append(project, event="close", id=run_id, status="abandoned")
        assert not any("outside the chain" in m for _, m in checks(project))
        append(project, event="close", id=run_id, status="ok")
        assert any(
            c == "pairing" and "outside the chain that opened it" in m
            for c, m in checks(project)
        )

    @pytest.mark.parametrize(
        ("status", "close_file"),
        [("ok", None), ("abandoned", "bgl/runs/AL-X001-CG1.close.json")],
    )
    def test_close_file_exactly_on_normal_closes(
        self, project: Project, status: str, close_file: str | None
    ) -> None:
        """Spec: close_file is null on abandoned closes, and only there."""
        with project.start(run_type="exploratory") as run:
            hard_kill(run)
        append(
            project,
            event="close",
            id="AL-X001-CG1",
            status=status,
            close_file=close_file,
        )
        assert any(
            c == "pairing" and "close_file is null exactly on abandoned closes" in m
            for c, m in checks(project)
        )

    def test_duplicate_open_id(self, project: Project) -> None:
        """F12: a run ID is opened once, in any chain."""
        second_writer(project)
        run_id = project.run(run_type="exploratory")
        project.use_writer("HC1")
        append(project, event="open", id=run_id, status="open")
        append(project, event="close", id=run_id, status="abandoned")
        assert any(
            c == "pairing" and f"{run_id} has 2 open rows (CG1, HC1)" in m
            for c, m in checks(project)
        )


class TestMalformedContent:
    """F12: malformed lock or amend content is a finding, not a traceback."""

    @pytest.mark.parametrize("content", [{"junk": 1}, "locked", None, []])
    def test_malformed_lock(self, project: Project, content: object) -> None:
        append(project, event="lock", study="alpha", lock=content)
        messages = [m for c, m in checks(project) if c == "lock"]
        assert any("malformed content" in m for m in messages)
        with pytest.raises(RunRefused) as info, project.start():
            pass
        assert info.value.rule == "R22" and "malformed" in info.value.reason
        with pytest.raises(AmendError, match="malformed"):
            amend_study(project.root, "alpha", "budget", "why")

    def test_lock_row_with_no_study(self, project: Project) -> None:
        project.run()
        append(project, event="lock", study=None, lock={"junk": 1})
        assert "lock" in failing(project)

    @pytest.mark.parametrize(
        "content",
        ["budget", {"key": "budget"}, {"key": "prefix", "old": "AL", "new": "ZZ"}],
    )
    def test_malformed_amend(self, project: Project, content: object) -> None:
        project.run()
        row = append(project, event="amend", study="alpha", amend=content)
        messages = [m for c, m in checks(project) if c == "amend"]
        assert len(messages) == 1
        assert messages[0].startswith(
            f"amend row {row['hash']} in chain CG1 has malformed content"
        )
        project.run()  # never applied, so bgl.toml still matches the lock
        append(project, event="ack", study="alpha", amend={"row": row["hash"]})
        report = verify_project(project.root)
        assert report.ok, report.failures

    @pytest.mark.parametrize(
        ("amend", "run_id"),
        [({"row": "x"}, None), ({"row": "a" * 64, "extra": 1}, None), ("a", None)],
    )
    def test_malformed_ack(
        self, project: Project, amend: object, run_id: object
    ) -> None:
        append(project, event="ack", id=run_id, study="alpha", amend=amend)
        assert "amend" in failing(project)

    def test_ack_naming_a_row_must_have_null_id(self, project: Project) -> None:
        run_id = project.run()
        stale = append(
            project,
            event="amend",
            study="alpha",
            amend={"key": "budget", "old": 4, "new": 9},
        )
        ack = append(
            project, event="ack", id=run_id, study="alpha", amend={"row": stale["hash"]}
        )
        messages = [m for c, m in checks(project) if c == "amend"]
        assert any(f"ack row {ack['hash']}" in m and "malformed" in m for m in messages)
        assert any(stale["hash"] in m and "never fits" in m for m in messages)

    @pytest.mark.parametrize(
        ("event", "field", "value"),
        [
            ("open", "id", ["AL-X009-CG1"]),
            ("close", "id", {"run": 1}),
            ("lock", "study", ["alpha"]),
            ("amend", "study", {"name": "alpha"}),
        ],
    )
    def test_unhashable_id_or_study_is_a_finding(
        self, project: Project, event: str, field: str, value: object
    ) -> None:
        """Written by hand around F1, as a cheat or a bug elsewhere would."""
        project.run(run_type="exploratory")
        config = load_config(project.root / "bgl.toml")
        me = load_identity(project.root, config.project)
        chain = project.root / "bgl" / "ledger" / "CG1.jsonl"
        head = project.last()["hash"]
        row = build_row(
            {"event": event, "chain": "CG1", "machine_id": me.machine_id, field: value},
            head,
            me.private_key,
        )
        chain.write_bytes(chain.read_bytes() + line_bytes(row))
        messages = [m for c, m in checks(project) if c == "chain"]
        assert messages == [f"chain CG1 line 3: {field} is not text or null"]
        status = project_status(project.root)
        assert status.chains[0].problem == f"line 3: {field} is not text or null"

    @pytest.mark.parametrize("streams", ["x", ["stdout"], {"../../etc": "a" * 64}])
    def test_malformed_output_hashes(self, project: Project, streams: object) -> None:
        run_id = project.run(run_type="exploratory")
        append(
            project,
            event="close",
            id=run_id,
            status="abandoned",
            output_sha256=streams,
        )
        assert any("malformed output_sha256" in m for _, m in checks(project))


class TestAmendments:
    """N13 and N61: amendments apply by their recorded old; acks by row hash."""

    def test_two_chains_in_either_registry_order(self, project: Project) -> None:
        second_writer(project)
        project.run()  # CG1 locks alpha with budget 3
        project.edit_toml("budget = 3", "budget = 4")
        project.commit("budget 4")
        amend_study(project.root, "alpha", "budget", "four")  # CG1: 3 -> 4
        project.edit_toml("budget = 4", "budget = 6")
        project.commit("budget 6")
        project.use_writer("HC1")
        row = amend_study(project.root, "alpha", "budget", "six")
        assert row["amend"] == {"key": "budget", "old": 4, "new": 6}
        for order in (["CG1", "HC1"], ["HC1", "CG1"]):
            project.writers = {code: project.writers[code] for code in order}
            project.write_toml(lambda text: text.replace("budget = 3", "budget = 6"))
            project.commit(f"registry order {order}")
            config = load_config(project.root / "bgl.toml")
            assert list(config.project.writers) == order
            view = read_ledger(project.root, config.project)
            lock = view.events("lock", "alpha")[0]["lock"]
            state = effective_lock(lock, view.events("amend", "alpha"))
            assert state["study"]["budget"] == 6
            report = verify_project(project.root)
            assert report.ok, report.failures
            project.run(run_type="exploratory")  # R22 passes

    def test_non_fitting_amendment_fails_until_acknowledged(
        self, project: Project
    ) -> None:
        project.run()  # locks alpha with budget 3
        stale = append(
            project,
            event="amend",
            study="alpha",
            amend={"key": "budget", "old": 4, "new": 9},
            reason="written against a value never in force",
        )
        messages = [m for c, m in checks(project) if c == "amend"]
        assert len(messages) == 1
        assert stale["hash"] in messages[0] and "never fits" in messages[0]
        project.run()  # never applied: budget 3 still matches
        append(
            project,
            event="ack",
            study="alpha",
            amend={"row": stale["hash"]},
            reason="reviewed",
        )
        report = verify_project(project.root)
        assert report.ok, report.failures

    @pytest.mark.parametrize(
        ("study", "names_stale"),
        [("beta", True), ("alpha", False)],
        ids=["another_study", "another_row"],
    )
    def test_an_ack_must_name_an_amendment_of_its_study(
        self, project: Project, study: str, names_stale: bool
    ) -> None:
        project.run()
        stale = append(
            project,
            event="amend",
            study="alpha",
            amend={"key": "budget", "old": 4, "new": 9},
        )
        named = stale["hash"] if names_stale else "f" * 64
        ack = append(project, event="ack", study=study, amend={"row": named})
        messages = [m for c, m in checks(project) if c == "amend"]
        assert any(stale["hash"] in m and "never fits" in m for m in messages)
        assert any(
            f"ack row {ack['hash']}" in m and "which is no amend row" in m
            for m in messages
        )

    def test_a_disallowed_amendment_fails_until_acknowledged(
        self, project: Project
    ) -> None:
        """N44: re-checked against the typed rules, so a budget cut never applies."""
        project.run()
        cut = append(
            project,
            event="amend",
            study="alpha",
            amend={"key": "budget", "old": 3, "new": 1},
        )
        messages = [m for c, m in checks(project) if c == "amend"]
        assert len(messages) == 1 and "is not an allowed change" in messages[0]
        project.run()  # the cut never applied, so budget 3 still matches (R22)
        append(project, event="ack", study="alpha", amend={"row": cut["hash"]})
        assert verify_project(project.root).ok

    def test_every_lock_is_checked(self, project: Project) -> None:
        """Two writers locked alpha differently; an amendment must fit both."""
        second_writer(project)
        project.run()  # CG1 locks with budget 3
        content = dict(project.rows()[0]["lock"])
        content["study"] = {**content["study"], "budget": 4}
        project.use_writer("HC1")
        append(project, event="lock", study="alpha", lock=content)
        project.use_writer("CG1")
        row = append(
            project,
            event="amend",
            study="alpha",
            amend={"key": "budget", "old": 3, "new": 5},
        )
        messages = [m for c, m in checks(project) if c == "amend"]
        assert any(row["hash"] in m and "never fits" in m for m in messages)

    def test_amendment_of_a_study_with_no_lock(self, project: Project) -> None:
        append(
            project,
            event="amend",
            study="alpha",
            amend={"key": "budget", "old": 3, "new": 9},
        )
        assert "amend" in failing(project)

    def test_fitting_amendments_pass(self, project: Project) -> None:
        project.run()
        project.edit_toml("budget = 3", "budget = 4")
        project.commit("budget")
        amend_study(project.root, "alpha", "budget", "one more")
        assert verify_project(project.root).ok


class TestFiles:
    def runs(self, project: Project) -> Path:
        return project.root / "bgl" / "runs"

    def counted_run_with_series(self, project: Project) -> str:
        with project.start() as run:
            print("output")
            run.returns(
                gross={1: {"2020-01-02": 0.1}, 2: {"2020-01-02": 0.2}},
                turnover={1: {"2020-01-02": 0}, 2: {"2020-01-02": 0}},
            )
            run.result("done")
            return run.id

    @pytest.mark.parametrize("suffix", [".json", ".close.json"])
    def test_edited_committed_file_fails(self, project: Project, suffix: str) -> None:
        run_id = project.run()
        path = self.runs(project) / f"{run_id}{suffix}"
        path.write_bytes(path.read_bytes().replace(b"}", b" }", 1))
        assert "hash" in failing(project)

    @pytest.mark.parametrize("suffix", [".json", ".close.json"])
    def test_missing_committed_file_fails(self, project: Project, suffix: str) -> None:
        run_id = project.run()
        (self.runs(project) / f"{run_id}{suffix}").unlink()
        assert "missing" in failing(project)

    @pytest.mark.parametrize(
        "relative", ["returns/AL-R001-CG1.csv", "output/AL-R001-CG1.stdout"]
    )
    def test_edited_ignored_file_fails(self, project: Project, relative: str) -> None:
        self.counted_run_with_series(project)
        path = self.runs(project) / relative
        path.write_bytes(path.read_bytes() + b"x")
        assert "hash" in failing(project)

    def test_missing_ignored_files_are_not_checkable(self, project: Project) -> None:
        self.counted_run_with_series(project)
        shutil.rmtree(self.runs(project) / "returns")
        shutil.rmtree(self.runs(project) / "output")
        report = verify_project(project.root)
        assert report.ok
        assert [f.check for f in report.warnings] == ["not checkable"] * 3

    @pytest.mark.parametrize(
        "name",
        ["AL-R009-CG1.json", "AL-R009-CG1.close.json"],
    )
    def test_orphans_are_warnings(self, project: Project, name: str) -> None:
        project.run()
        (self.runs(project) / name).write_text("{}\n")
        report = verify_project(project.root)
        assert report.ok
        assert [f.check for f in report.warnings] == ["orphan"]


class TestCommits:
    def test_unreachable_commit_fails(self, project: Project) -> None:
        run_git(project.root, "checkout", "-q", "-b", "feature")
        project.write("src/feature.py", "x = 1\n")
        project.commit("feature")
        project.run()
        project.commit("ledger on feature")
        run_git(project.root, "checkout", "-q", "main")
        # Bring the ledger and run files over without the feature commits.
        run_git(project.root, "checkout", "feature", "--", "bgl")
        run_git(project.root, "branch", "-q", "-D", "feature")
        messages = checks(project)
        assert any("not reachable" in m for c, m in messages if c == "commit")

    def test_shallow_clone_fails_with_advice(
        self, project: Project, tmp_path: Path
    ) -> None:
        project.run()
        project.commit("ledger")
        project.commit("one more")
        clone = tmp_path / "clone"
        run_git(
            tmp_path, "clone", "-q", "--depth", "1", project.root.as_uri(), str(clone)
        )
        messages = [m for c, m in checks_at(clone) if c == "commit"]
        assert messages and "unshallow" in messages[0]


def checks_at(root: Path) -> list[tuple[str, str]]:
    return [(f.check, f.message) for f in verify_project(root).failures]


class TestLocks:
    def test_changed_locked_value_fails(self, project: Project) -> None:
        project.run()
        project.edit_toml("budget = 3", "budget = 9")
        project.commit("budget")
        messages = checks(project)
        assert any("budget" in m for c, m in messages if c == "lock")

    def test_changed_spec_file_is_a_warning(self, project: Project) -> None:
        project.run()
        project.write("docs/alpha.md", "revised\n")
        project.commit("revise")
        report = verify_project(project.root)
        assert report.ok
        assert [f.check for f in report.warnings] == ["lock"]

    def test_locked_study_removed_from_config(self, project: Project) -> None:
        project.run(study="beta")
        text = (project.root / "bgl.toml").read_text()
        start = text.index("[studies.beta]")
        (project.root / "bgl.toml").write_text(text[:start])
        project.commit("drop beta")
        messages = checks(project)
        assert any("no longer" in m for c, m in messages if c == "lock")


class TestContains:
    def test_found(self, project: Project) -> None:
        project.run()
        head = project.last()["hash"]
        report = verify_project(project.root, contains=head)
        assert report.ok
        assert report.contains == ("CG1 line 3",)

    def test_not_found_fails(self, project: Project) -> None:
        project.run()
        assert "contains" in [c for c, _ in checks(project, contains="0" * 64)]

    def test_bad_hash(self, project: Project) -> None:
        with pytest.raises(ValueError, match="hex hash"):
            verify_project(project.root, contains="xyz")


@pytest.mark.parametrize(
    ("path", "ok"),
    [
        ("bgl/runs/AL-R001-CG1.json", True),
        ("bgl/./runs/x.json", True),
        ("../x.json", False),
        ("/etc/passwd", False),
        ("C:/x.json", False),
        ("bgl\\runs\\x.json", False),
        ("", False),
        (None, False),
    ],
)
def test_safe_relative(path: object, ok: bool) -> None:
    assert (_safe_relative(path) is not None) == ok

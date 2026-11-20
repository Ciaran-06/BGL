"""The pre-open checks: every refusal, its rule ID and what gets recorded."""

import shutil
import sys
from typing import Any

import pytest
from conftest import Project, run_git

from bgl import RunRefused, budget
from bgl.checks import CHECKS
from bgl.errors import AmendError
from bgl.home import create_machine_id
from bgl.studylock import amend_study

DATA_RUN: dict[str, Any] = {
    "run_type": "data",
    "sample": None,
    "variants": None,
    "prediction": None,
    "sample_start": None,
    "sample_end": None,
}


def refused(project: Project, rule: str, **overrides: Any) -> RunRefused:
    """Start a run that must be refused under rule; return the refusal."""
    with pytest.raises(RunRefused) as info, project.start(**overrides):
        pytest.fail("the body of a refused run must never start")
    assert info.value.rule == rule, str(info.value)
    return info.value


def refused_row(
    project: Project, rule: str, *, chain: str = "CG1", **overrides: Any
) -> dict[str, Any]:
    """Like refused, and check the refusal was recorded as the chain's last row."""
    before = len(project.rows(chain))
    error = refused(project, rule, **overrides)
    rows = project.rows(chain)
    assert len(rows) == before + 1, "a refusal writes exactly one row"
    row = rows[-1]
    assert (row["event"], row["status"]) == ("refused", "refused")
    assert row["reason"] == f"{rule}: {error.reason}"
    return row


class TestRefusalRecord:
    def test_row_and_id(self, project: Project) -> None:
        row = refused_row(project, "R5", prediction=None)
        assert row["id"] == "AL-F001-CG1"
        assert row["study"] == "alpha"
        assert row["type"] == "diagnostic"
        assert row["script"] == "scripts/run.py"
        assert refused_row(project, "R5", prediction="")["id"] == "AL-F002-CG1"

    def test_refusals_use_no_budget_and_write_no_lock(self, project: Project) -> None:
        for _ in range(4):
            refused_row(project, "R5", prediction=None)
        assert budget("alpha", root=project.root).used == 0
        assert project.events() == ["refused"] * 4
        assert not (project.root / "bgl" / "runs").exists()

    def test_message_names_the_rule(self, project: Project) -> None:
        error = refused(project, "R5", prediction=None)
        assert str(error).startswith("Refused (R5): ")

    def test_check_table_is_in_the_specs_rule_index_order(self) -> None:
        assert [rule for rule, _ in CHECKS] == [
            "R22",
            "R3",
            "R3b",
            "R17",
            "R8",
            "R4",
            "R5",
            "R21",
            "R6",
            "R20",
            "R2",
        ]

    def test_r3_is_reported_before_r3b(self, project: Project) -> None:
        project.write("docs/alpha.md", "edited\n")
        project.write("notebooks/try.py", "x = 1\n")
        refused_row(project, "R3")

    def test_checks_run_in_the_specs_order(self, project: Project) -> None:
        # Breaks R3 (dirty tree), R8 (missing input), R5 and R21 at once.
        project.write("scripts/run.py", "# edited\n")
        broken = {"inputs": ["data/missing.csv"], "prediction": None, "variants": 0}
        refused_row(project, "R3", **broken)
        run_git(project.root, "checkout", "--", "scripts/run.py")
        refused_row(project, "R8", **broken)
        broken["inputs"] = ["data/design.csv"]
        refused_row(project, "R5", **broken)
        broken["prediction"] = "p"
        refused_row(project, "R21", **broken)


class TestR19WriterIdentity:
    def test_no_writer_file(self, project: Project) -> None:
        (project.root / ".bgl" / "writer").unlink()
        assert "bgl join" in refused(project, "R19").reason
        assert project.rows() == []

    def test_code_not_in_registry(self, project: Project) -> None:
        project.use_writer("ZZ9")
        assert "not in the registry" in refused(project, "R19").reason

    def test_no_private_key_here(self, project: Project) -> None:
        shutil.rmtree(project.home / "keys")
        assert "bgl join" in refused(project, "R19").reason

    def test_chain_written_on_another_machine(self, project: Project) -> None:
        project.run(run_type="exploratory")
        (project.home / "machine_id").unlink()
        create_machine_id(project.home)
        assert "another machine" in refused(project, "R19").reason
        assert len(project.rows()) == 2


class TestR7ChainIntact:
    def chain(self, project: Project) -> Any:
        return project.root / "bgl" / "ledger" / "CG1.jsonl"

    def test_edited_chain_refuses_and_writes_nothing(self, project: Project) -> None:
        project.run(run_type="exploratory")
        path = self.chain(project)
        before = path.read_bytes().replace(b"Lookback grid", b"Edited words!")
        path.write_bytes(before)
        error = refused(project, "R7")
        assert "line 1" in error.reason and "bgl verify" in error.reason
        assert path.read_bytes() == before

    def test_torn_tail_names_repair(self, project: Project) -> None:
        project.run(run_type="exploratory")
        with open(self.chain(project), "ab") as handle:
            handle.write(b'{"id"')
        assert "bgl repair" in refused(project, "R7").reason

    def test_another_writers_broken_chain_does_not_block(
        self, project: Project
    ) -> None:
        project.add_writer("HC1", "custodian_a")
        project.write_toml()
        project.commit("add writer")
        (project.root / "bgl" / "ledger").mkdir(parents=True)
        (project.root / "bgl" / "ledger" / "HC1.jsonl").write_bytes(b"garbage\n")
        project.run(run_type="exploratory")
        assert project.last()["status"] == "ok"


class TestR1KnownStudy:
    def test_unknown_study(self, project: Project) -> None:
        row = refused_row(project, "R1", study="gamma")
        assert row["id"] is None
        assert row["study"] is None
        assert "gamma" in row["reason"]


class TestR22StudyLock:
    def lock(self, project: Project) -> None:
        project.run()

    def test_lock_row_holds_study_registry_spec_hashes_and_project(
        self, project: Project
    ) -> None:
        self.lock(project)
        lock = project.rows()[0]["lock"]
        assert lock["study"]["budget"] == 3
        assert lock["study"]["claims"] == ["effect_exists", "effect_persists"]
        assert lock["registry"] == {
            "CG1": {"person": "researcher_a", "key": project.keys["CG1"]}
        }
        assert list(lock["spec_files"]) == ["docs/alpha.md"]
        assert lock["project"] == {"mode": "solo", "witness": False}

    def test_changed_budget_refuses_the_next_open(self, project: Project) -> None:
        self.lock(project)
        project.edit_toml("budget = 3", "budget = 9")
        project.commit("more budget")
        row = refused_row(project, "R22", run_type="exploratory")
        assert "budget" in row["reason"] and "bgl amend" in row["reason"]

    def test_increase_by_amend_is_accepted(self, project: Project) -> None:
        self.lock(project)
        project.edit_toml("budget = 3", "budget = 9")
        project.commit("more budget")
        row = amend_study(project.root, "alpha", "budget", "more variants to test")
        assert row["amend"] == {"key": "budget", "old": 3, "new": 9}
        assert row["event"] == "amend" and row["study"] == "alpha"
        project.run()
        assert budget("alpha", root=project.root).budget == 9

    def test_decrease_is_refused(self, project: Project) -> None:
        self.lock(project)
        project.edit_toml("budget = 3", "budget = 2")
        project.commit("less budget")
        with pytest.raises(AmendError, match="only increase"):
            amend_study(project.root, "alpha", "budget", "tidy")
        refused(project, "R22")

    def test_holdout_key_can_never_be_amended(self, project: Project) -> None:
        project.run(study="beta")
        project.edit_toml("holdout_start = 2025-01-01", "holdout_start = 2026-01-01")
        project.commit("move holdout")
        for key in ("holdout_start", "holdout_files", "prefix", "returns"):
            with pytest.raises(AmendError, match="cannot be amended"):
                amend_study(project.root, "beta", key, "please")
        row = refused_row(project, "R22", study="beta")
        assert "holdout_start" in row["reason"]

    def test_unlocked_study_can_change_freely(self, project: Project) -> None:
        project.edit_toml("budget = 3", "budget = 9")
        project.commit("more budget")
        project.run()
        with pytest.raises(AmendError, match="not locked"):
            amend_study(project.root, "beta", "budget", "why")

    def test_other_studys_lock_is_untouched(self, project: Project) -> None:
        self.lock(project)
        project.edit_toml("budget = 5", "budget = 6")  # beta, not locked
        project.commit("beta budget")
        project.run()

    def test_added_claim(self, project: Project) -> None:
        self.lock(project)
        project.edit_toml(
            'claims = ["effect_exists", "effect_persists"]',
            'claims = ["effect_exists", "effect_persists", "effect_scales"]',
        )
        project.commit("claim")
        refused(project, "R22")
        amend_study(project.root, "alpha", "claims", "new question")
        project.run()

    def test_spec_file_change_blocks_only_confirmatory_runs(
        self, project: Project
    ) -> None:
        self.lock(project)
        project.write("docs/alpha.md", "# Alpha design, revised\n")
        project.commit("revise design")
        project.run()
        confirm = {
            "run_type": "confirmatory",
            "claims": ["effect_exists"],
            "variants": 1,
        }
        row = refused_row(project, "R22", **confirm)
        assert "spec files" in row["reason"]
        amend_study(project.root, "alpha", "spec_files", "clarified the design")
        project.run(**confirm)

    def test_registry_may_grow_after_the_lock(self, project: Project) -> None:
        self.lock(project)
        project.add_writer("HC1", "custodian_a")
        project.write_toml()
        project.commit("add writer")
        project.run()

    def test_mode_and_witness_change_is_refused(self, project: Project) -> None:
        project.add_writer("HC1", "custodian_a")
        project.custodian = "custodian_a"  # team mode needs one for beta
        project.write_toml()
        project.commit("custodian")
        self.lock(project)
        project.edit_toml('mode = "solo"', 'mode = "team"')
        project.edit_toml(
            "witness = false\n",
            '\n[project.witness]\nrekor_url = "https://rekor.example"\n'
            'ots_calendars = ["https://a.example"]\n',
        )
        project.commit("team")
        row = refused_row(project, "R22")
        assert "project.mode" in row["reason"]
        assert "project.witness" in row["reason"]

    def test_uncommitted_spec_file_refuses_the_locking_run(
        self, project: Project
    ) -> None:
        project.edit_toml('["docs/alpha.md"]', '["docs/new.md"]')
        project.commit("point at a new spec")
        project.write("docs/new.md", "# not committed\n")
        row = refused_row(project, "R22")
        assert "docs/new.md" in row["reason"]
        project.run(run_type="exploratory")  # an uncounted run needs no lock

    def test_amend_needs_a_reason_and_a_known_study(self, project: Project) -> None:
        self.lock(project)
        with pytest.raises(AmendError, match="reason"):
            amend_study(project.root, "alpha", "budget", "  ")
        with pytest.raises(AmendError, match="unknown study"):
            amend_study(project.root, "gamma", "budget", "why")
        with pytest.raises(AmendError, match="nothing to do"):
            amend_study(project.root, "alpha", "budget", "why")


class TestR3CleanCode:
    def test_modified_tracked_file(self, project: Project) -> None:
        project.write("docs/alpha.md", "edited\n")
        assert "docs/alpha.md" in refused_row(project, "R3")["reason"]

    def test_staged_new_file(self, project: Project) -> None:
        project.write("data/new.csv")
        run_git(project.root, "add", "data/new.csv")
        assert "data/new.csv" in refused_row(project, "R3")["reason"]

    def test_untracked_data_files_are_allowed(self, project: Project) -> None:
        project.write("data/new.csv")
        project.run()

    def test_ledger_and_runs_dir_changes_are_allowed(self, project: Project) -> None:
        project.run()
        project.commit("commit the ledger")
        project.run()  # the first run's close row is now uncommitted
        project.run(run_type="exploratory")

    def test_script_must_be_tracked(self, project: Project) -> None:
        script = project.write("scripts/notes.txt", "not committed\n")
        assert "not tracked" in refused_row(project, "R3", script=script)["reason"]

    def test_no_git_user(self, project: Project) -> None:
        run_git(project.root, "config", "--unset", "user.email")
        assert "git config" in refused_row(project, "R3")["reason"]

    @pytest.mark.parametrize("run_type", ["diagnostic", "exploratory"])
    def test_config_file_must_be_tracked(self, project: Project, run_type: str) -> None:
        """N18: bgl.toml itself must be tracked."""
        run_git(project.root, "rm", "-q", "--cached", "bgl.toml")
        project.write(".gitignore", ".bgl/\nscratch/\nbgl.toml\n")
        project.commit("stop tracking bgl.toml")
        row = refused_row(project, "R3", run_type=run_type)
        assert "bgl.toml is not tracked" in row["reason"]


class TestR22WithoutACommit:
    def test_lock_content_problem_is_a_refusal_not_an_assert(
        self, project: Project
    ) -> None:
        """F17: with a lock and no commit to compare, R22 names the git problem."""
        project.run()
        run_git(project.root, "checkout", "-q", "--orphan", "fresh")
        row = refused_row(project, "R22")
        assert "no commits" in row["reason"]

    def test_first_counted_open_without_a_commit(self, project: Project) -> None:
        run_git(project.root, "checkout", "-q", "--orphan", "fresh")
        assert "no commits" in refused_row(project, "R22")["reason"]
        assert "lock" not in project.events()


class TestR3bUntrackedPython:
    def test_message_names_files_and_both_fixes(self, project: Project) -> None:
        project.write("notebooks/try.py", "x = 1\n")
        row = refused_row(project, "R3b")
        assert row["reason"] == (
            "R3b: untracked Python files in the repo: notebooks/try.py. Commit "
            "them, or move them to scratch/ (ignored by git)."
        )

    def test_scratch_is_ignored(self, project: Project) -> None:
        project.write("scratch/try.py", "x = 1\n")
        project.run()


class TestR17AtOpen:
    def test_module_imported_from_an_ignored_folder(
        self, project: Project, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        project.write("scratch/early_helper.py", "VALUE = 1\n")
        monkeypatch.syspath_prepend(str(project.root / "scratch"))
        import early_helper  # type: ignore[import-not-found]  # noqa: F401

        try:
            row = refused_row(project, "R17")
        finally:
            del sys.modules["early_helper"]
        assert "scratch/early_helper.py" in row["reason"]
        project.run()


class TestR8Inputs:
    @pytest.mark.parametrize(
        ("inputs", "words"),
        [
            (["data/missing.csv"], "data/missing.csv"),
            (["data"], "not files"),
            (["data/design.csv", "data/./design.csv"], "listed twice"),
        ],
    )
    def test_refused(self, project: Project, inputs: list[str], words: str) -> None:
        assert words in refused_row(project, "R8", inputs=inputs)["reason"]

    def test_no_inputs_is_allowed(self, project: Project) -> None:
        project.run(inputs=[])


class TestR4Duplicates:
    def test_same_script_code_config_inputs_is_refused(self, project: Project) -> None:
        first = project.run(config={"a": 1})
        row = refused_row(project, "R4", config={"a": 1}, what_changed="new words")
        assert first in row["reason"]

    @pytest.mark.parametrize(
        "change",
        [
            {"config": {"a": 2}},
            {"inputs": ["data/design.csv", "data/raw.csv"]},
            {"run_type": "exploratory"},
            {"study": "beta"},
        ],
    )
    def test_any_changed_fact_is_not_a_duplicate(
        self, project: Project, change: dict[str, Any]
    ) -> None:
        project.run(config={"a": 1})
        project.run(**{"config": {"a": 1}, **change})

    def test_changed_code_is_not_a_duplicate(self, project: Project) -> None:
        project.run(config={"a": 1})
        project.write("scripts/lib.py", "x = 1\n")
        project.commit("new code")
        project.run(config={"a": 1})

    def test_committing_the_ledger_does_not_defeat_it(self, project: Project) -> None:
        project.run(config={"a": 1})
        project.commit("commit the ledger")
        refused_row(project, "R4", config={"a": 1})

    def test_float_and_int_configs_differ(self, project: Project) -> None:
        project.run(config={"a": 1})
        project.run(config={"a": 1.0})

    @pytest.mark.parametrize("run_type", ["diagnostic", "exploratory"])
    def test_what_changed_must_say_something(
        self, project: Project, run_type: str
    ) -> None:
        refused_row(project, "R4", run_type=run_type, what_changed="  ")


class TestR5Prediction:
    @pytest.mark.parametrize("prediction", [None, "", "   "])
    def test_diagnostic_needs_one(self, project: Project, prediction: Any) -> None:
        refused_row(project, "R5", prediction=prediction)

    def test_confirmatory_needs_one(self, project: Project) -> None:
        refused_row(
            project,
            "R5",
            run_type="confirmatory",
            claims=["effect_exists"],
            variants=1,
            prediction=None,
        )

    def test_exploratory_and_data_do_not(self, project: Project) -> None:
        project.run(run_type="exploratory", prediction=None)
        project.run(**DATA_RUN)


class TestR21Variants:
    @pytest.mark.parametrize(
        ("run_type", "variants", "ok"),
        [
            ("exploratory", 0, True),
            ("exploratory", 500, True),
            ("exploratory", -1, False),
            ("exploratory", None, False),
            ("diagnostic", 1, True),
            ("diagnostic", 0, False),
            ("diagnostic", None, False),
            ("confirmatory", 1, True),
            ("confirmatory", 2, False),
            ("confirmatory", 0, False),
            ("data", None, True),
            ("data", 0, False),
            ("data", 1, False),
        ],
    )
    def test_ranges(
        self, project: Project, run_type: str, variants: Any, ok: bool
    ) -> None:
        arguments: dict[str, Any] = {"run_type": run_type, "variants": variants}
        if run_type == "data":
            arguments = {**DATA_RUN, "variants": variants}
        if run_type == "confirmatory":
            arguments["claims"] = ["effect_exists"]
        if ok:
            project.run(**arguments)
        else:
            refused_row(project, "R21", **arguments)


CONFIRM: dict[str, Any] = {
    "run_type": "confirmatory",
    "claims": ["effect_exists"],
    "variants": 1,
}


class TestR6Confirmatory:
    @pytest.mark.parametrize(
        ("change", "words"),
        [
            ({"claims": None}, "at least one"),
            ({"claims": []}, "at least one"),
            ({"claims": ["effect_exists", "effect_exists"]}, "listed twice"),
            ({"claims": ["other"]}, "not claims of study alpha"),
            ({"selects": {"run": "AL-R001-CG1", "variant": 1}}, "own selected trial"),
        ],
    )
    def test_refused(
        self, project: Project, change: dict[str, Any], words: str
    ) -> None:
        row = refused_row(project, "R6", **{**CONFIRM, **change})
        assert words in row["reason"]

    def test_claims_on_another_run_type(self, project: Project) -> None:
        refused_row(project, "R6", claims=["effect_exists"])
        refused_row(project, "R6", selects={"run": "AL-R001-CG1", "variant": 1})

    def test_without_a_holdout_the_run_selects_itself(self, project: Project) -> None:
        run_id = project.run(**CONFIRM)
        opened = project.rows()[1]
        assert opened["selects"] == {"run": run_id, "variant": 1}
        assert opened["claims"] == ["effect_exists"]

    def test_each_claim_is_decided_once(self, project: Project) -> None:
        first = project.run(**CONFIRM)
        row = refused_row(
            project, "R6", **{**CONFIRM, "claims": ["effect_persists", "effect_exists"]}
        )
        assert first in row["reason"]
        project.run(**{**CONFIRM, "claims": ["effect_persists"]})

    def test_a_crashed_confirmatory_run_still_decided_its_claim(
        self, project: Project
    ) -> None:
        with pytest.raises(RuntimeError), project.start(**CONFIRM):
            raise RuntimeError("boom")
        refused_row(project, "R6", **CONFIRM, config={"again": 1})

    def test_void_frees_exactly_one_rerun(self, project: Project) -> None:
        first = project.run(**CONFIRM)
        project.void(first)
        second = project.run(**CONFIRM)
        refused_row(project, "R6", **CONFIRM, config={"third": 1})
        project.void(second)  # the rerun cannot be voided into a third try
        refused_row(project, "R6", **CONFIRM, config={"third": 2})

    def test_sample_must_match_the_study(self, project: Project) -> None:
        design = project.run(study="beta")
        arguments = project.holdout_arguments(
            design, sample="design", sample_start="2020-01-01", sample_end="2024-12-31"
        )
        assert "sample = holdout" in refused_row(project, "R6", **arguments)["reason"]
        row = refused_row(
            project,
            "R6",
            **CONFIRM,
            sample="holdout",
            sample_start="2025-01-01",
            sample_end="2025-06-30",
        )
        assert "sample = design" in row["reason"]


class TestR6Selects:
    def test_required_with_a_holdout(self, project: Project) -> None:
        design = project.run(study="beta")
        arguments = project.holdout_arguments(design, selects=None)
        assert "selects" in refused_row(project, "R6", **arguments)["reason"]

    def test_must_name_a_design_run_of_this_study(self, project: Project) -> None:
        other_study = project.run()
        data_run = project.run(study="beta", **DATA_RUN)
        for target in ("BE-R999-CG1", other_study, data_run):
            arguments = project.holdout_arguments(target)
            refused_row(project, "R6", **arguments)

    @pytest.mark.parametrize("variant", [0, 3, -1])
    def test_variant_must_exist(self, project: Project, variant: int) -> None:
        design = project.run(study="beta")  # declares 2 variants
        arguments = project.holdout_arguments(
            design, selects={"run": design, "variant": variant}
        )
        assert "variant" in refused_row(project, "R6", **arguments)["reason"]

    def test_valid_selection_opens_the_holdout_run(self, project: Project) -> None:
        design = project.run(study="beta", run_type="exploratory")
        with project.start(
            **project.holdout_arguments(design, selects={"run": design, "variant": 2})
        ) as run:
            run.outcome("effect_exists", "supported")
            run.result("done")
        assert project.last()["status"] == "ok"


class TestR20Holdout:
    def test_sample_for_each_run_type(self, project: Project) -> None:
        cases: list[dict[str, Any]] = [
            {
                "sample": "holdout",
                "sample_start": "2025-01-01",
                "sample_end": "2025-06-30",
                "study": "beta",
            },
            {"sample": "live"},
            {"run_type": "exploratory", "sample": "holdout"},
            {**DATA_RUN, "sample": "design"},
            {"run_type": "replay", "sample": "design", "variants": 1},
            {"sample": None},
        ]
        for case in cases:
            refused_row(project, "R20", **case)

    def test_holdout_is_opened_once(self, project: Project) -> None:
        design = project.run(study="beta")
        arguments = project.holdout_arguments(design)
        first = project.run(**arguments)
        row = refused_row(project, "R6", **arguments, config={"again": 1})
        assert first in row["reason"]

    def test_second_look_for_another_claim_is_refused_by_r20(
        self, project: Project
    ) -> None:
        project.edit_toml(
            'claims = ["effect_exists"]\nprior_trials = 0\nholdout_start',
            'claims = ["effect_exists", "effect_lasts"]\nprior_trials = 0\n'
            "holdout_start",
        )
        project.commit("two claims in beta")
        design = project.run(study="beta")
        first = project.run(**project.holdout_arguments(design))
        row = refused_row(
            project, "R20", **project.holdout_arguments(design, claims=["effect_lasts"])
        )
        assert first in row["reason"] and "once" in row["reason"]

    def test_void_frees_one_holdout_rerun(self, project: Project) -> None:
        design = project.run(study="beta")
        arguments = project.holdout_arguments(design)
        first = project.run(**arguments)
        project.void(first)
        project.run(**arguments)
        refused_row(project, "R6", **arguments, config={"third": 1})

    def test_only_the_custodian_opens_the_holdout(self, project: Project) -> None:
        project.add_writer("HC1", "custodian_a")
        project.custodian = "custodian_a"
        project.write_toml()
        project.commit("custodian")
        design = project.run(study="beta")
        arguments = project.holdout_arguments(design)
        row = refused_row(project, "R20", **arguments)
        assert "custodian_a" in row["reason"] and "researcher_a" in row["reason"]
        project.use_writer("HC1")
        project.run(**arguments)
        assert project.last("HC1")["status"] == "ok"

    @pytest.mark.parametrize("path", ["data/holdout.csv", "data/raw.csv"])
    def test_design_run_cannot_declare_guarded_files(
        self, project: Project, path: str
    ) -> None:
        row = refused_row(project, "R20", study="beta", inputs=[path])
        assert path in row["reason"]

    def test_tidied_path_is_still_recognised(self, project: Project) -> None:
        refused_row(project, "R20", study="beta", inputs=["data/./holdout.csv"])

    def test_split_run_may_declare_them_before_the_lock_only(
        self, project: Project
    ) -> None:
        split = {
            **DATA_RUN,
            "study": "beta",
            "inputs": ["data/raw.csv", "data/holdout.csv"],
        }
        project.run(**split)
        project.run(study="beta")  # locks beta
        row = refused_row(project, "R20", **split, config={"again": 1})
        assert "after the study lock" in row["reason"]

    def test_other_studys_files_are_not_guarded(self, project: Project) -> None:
        project.run(inputs=["data/holdout.csv"])  # alpha has no holdout

    @pytest.mark.parametrize(
        ("start", "end", "ok"),
        [
            ("2020-01-01", "2024-12-31", True),
            ("2020-01-01", "2025-01-01", False),
            ("2020-01-01", "2025-06-30", False),
            ("2024-12-31", "2024-12-31", True),
            ("2024-06-01", "2024-01-01", False),
            (None, "2024-12-31", False),
            ("2020-01-01", None, False),
        ],
    )
    def test_design_dates(
        self, project: Project, start: Any, end: Any, ok: bool
    ) -> None:
        arguments = {"study": "beta", "sample_start": start, "sample_end": end}
        if ok:
            project.run(**arguments)
        else:
            refused_row(project, "R20", **arguments)

    @pytest.mark.parametrize(
        ("start", "ok"), [("2025-01-01", True), ("2024-12-31", False)]
    )
    def test_holdout_dates(self, project: Project, start: str, ok: bool) -> None:
        design = project.run(study="beta")
        arguments = project.holdout_arguments(design, sample_start=start)
        if ok:
            project.run(**arguments)
        else:
            refused_row(project, "R20", **arguments)

    def test_study_without_holdout_has_no_date_limit(self, project: Project) -> None:
        project.run(sample_start="2020-01-01", sample_end="2026-01-01")


class TestR2Budget:
    def test_run_after_the_budget_is_refused(self, project: Project) -> None:
        for _ in range(3):
            project.run()
        row = refused_row(project, "R2")
        assert "3 of 3" in row["reason"]
        assert budget("alpha", root=project.root).left == 0

    def test_uncounted_runs_are_unlimited(self, project: Project) -> None:
        for _ in range(3):
            project.run()
        project.run(run_type="exploratory")
        project.run(**DATA_RUN)

    def test_budget_counts_every_chain_here(self, project: Project) -> None:
        project.add_writer("HC1", "custodian_a")
        project.write_toml()
        project.commit("add writer")
        project.run()
        project.run()
        project.use_writer("HC1")
        project.run()
        row = project.rows("HC1")
        assert [r["event"] for r in row] == ["open", "close"]  # CG1 holds the lock
        refused(project, "R2")
        project.use_writer("CG1")
        refused(project, "R2")

    def test_zero_budget(self, project: Project) -> None:
        project.edit_toml("budget = 3", "budget = 0")
        project.commit("no budget")
        refused_row(project, "R2")
        project.run(run_type="exploratory")


def second_writer(project: Project, *, custodian: bool = False) -> None:
    """Register HC1 (custodian_a) on this machine; CG1 stays the writer."""
    project.add_writer("HC1", "custodian_a")
    if custodian:
        project.custodian = "custodian_a"
    project.write_toml()
    project.commit("add HC1")


class TestAcrossChains:
    """Rules that count every chain on this machine, not only the writer's."""

    def test_r4_duplicate_in_another_chain(self, project: Project) -> None:
        second_writer(project)
        first = project.run(config={"a": 1})
        project.use_writer("HC1")
        row = refused_row(project, "R4", chain="HC1", config={"a": 1})
        assert first in row["reason"]
        assert project.last("HC1")["chain"] == "HC1"

    def test_r6_claim_decided_in_another_chain(self, project: Project) -> None:
        second_writer(project)
        first = project.run(**CONFIRM)
        project.use_writer("HC1")
        row = refused_row(project, "R6", chain="HC1", **CONFIRM, config={"again": 1})
        assert first in row["reason"]

    def test_r20_holdout_opened_once_across_chains(self, project: Project) -> None:
        second_writer(project)  # rewrites bgl.toml, so edit it afterwards
        project.edit_toml(
            'claims = ["effect_exists"]\nprior_trials = 0\nholdout_start',
            'claims = ["effect_exists", "effect_lasts"]\nprior_trials = 0\n'
            "holdout_start",
        )
        project.commit("two claims in beta")
        design = project.run(study="beta")
        first = project.run(**project.holdout_arguments(design))
        project.use_writer("HC1")
        row = refused_row(
            project,
            "R20",
            chain="HC1",
            **project.holdout_arguments(design, claims=["effect_lasts"]),
        )
        assert first in row["reason"] and "once" in row["reason"]


class TestReplayRuns:
    REPLAY: dict[str, Any] = {
        "study": "beta",
        "run_type": "replay",
        "sample": "live",
        "variants": 1,
        "prediction": None,
        "sample_start": None,
        "sample_end": None,
    }

    def test_only_the_custodian_opens_replay_runs(self, project: Project) -> None:
        second_writer(project, custodian=True)
        row = refused_row(project, "R20", **self.REPLAY)
        assert "only the custodian (custodian_a) may open replay runs" in row["reason"]
        project.use_writer("HC1")
        project.run(**self.REPLAY)
        assert project.last("HC1")["status"] == "ok"
        assert project.last("HC1")["id"] == "BE-P001-HC1"

    def test_without_a_custodian_anyone_may(self, project: Project) -> None:
        project.run(**self.REPLAY)
        assert project.last()["status"] == "ok"


class TestSecondMachine:
    """F1: BGL never writes a row verify would reject."""

    def test_amend_from_a_second_machine_is_refused(self, project: Project) -> None:
        from bgl.errors import RowRefusedError

        project.run()
        project.edit_toml("budget = 3", "budget = 9")
        project.commit("more budget")
        chain = project.root / "bgl" / "ledger" / "CG1.jsonl"
        before = chain.read_bytes()
        (project.home / "machine_id").unlink()
        create_machine_id(project.home)
        with pytest.raises(RowRefusedError, match="machine_id differs"):
            amend_study(project.root, "alpha", "budget", "more variants")
        assert chain.read_bytes() == before


class TestDryRunRefusal:
    def test_failed_check_writes_a_dry_row_marked_refused(
        self, project: Project
    ) -> None:
        fixture = project.write("scratch/fixture.csv")
        with pytest.raises(RunRefused) as info:
            with project.start(
                dry=True, dry_inputs={"data/design.csv": fixture}, prediction=None
            ):
                pass
        assert info.value.rule == "R5"
        row = project.last()
        assert (row["event"], row["status"], row["id"]) == (
            "dry",
            "refused",
            "AL-T001-CG1",
        )

    def test_missing_fixture(self, project: Project) -> None:
        with pytest.raises(RunRefused) as info:
            with project.start(
                dry=True, dry_inputs={"data/design.csv": "scratch/none.csv"}
            ):
                pass
        assert info.value.rule == "R25"

from conftest import Project

from bgl.status import project_status


def study(project: Project, name: str) -> object:
    return next(s for s in project_status(project.root).studies if s.name == name)


def test_fresh_project(project: Project) -> None:
    status = project_status(project.root)
    alpha = next(s for s in status.studies if s.name == "alpha")
    assert (alpha.used, alpha.budget, alpha.n, alpha.locked) == (0, 3, 0, False)
    assert alpha.claims_open == ("effect_exists", "effect_persists")
    assert [c.code for c in status.chains] == ["CG1"]
    assert status.chains[0].head is None
    assert status.chains[0].witness.startswith("off")
    assert status.uncommitted_ledger == ()


def test_budget_n_and_claims(project: Project) -> None:
    project.run()  # diagnostic, 2 variants
    project.run(run_type="exploratory", variants=5)
    project.run(
        run_type="data",
        sample=None,
        variants=None,
        prediction=None,
        sample_start=None,
        sample_end=None,
    )
    confirm = project.run(run_type="confirmatory", claims=["effect_exists"], variants=1)
    alpha = study(project, "alpha")
    assert alpha.used == 2  # type: ignore[attr-defined]
    assert alpha.n == 2 + 5 + 1  # type: ignore[attr-defined]
    assert alpha.locked  # type: ignore[attr-defined]
    assert alpha.claims_decided == {"effect_exists": confirm}  # type: ignore[attr-defined]
    assert alpha.claims_open == ("effect_persists",)  # type: ignore[attr-defined]


def test_prior_trials_add_to_n(project: Project) -> None:
    project.edit_toml(
        "prior_trials = 0\nholdout = false", "prior_trials = 7\nholdout = false"
    )
    project.commit("prior")
    project.run()
    assert study(project, "alpha").n == 9  # type: ignore[attr-defined]


def test_holdout_run_is_not_a_trial(project: Project) -> None:
    design = project.run(study="beta")
    project.run(**project.holdout_arguments(design))
    assert study(project, "beta").n == 2  # type: ignore[attr-defined]


def test_voided_claim_is_open_again(project: Project) -> None:
    run_id = project.run(run_type="confirmatory", claims=["effect_exists"], variants=1)
    project.void(run_id)
    alpha = study(project, "alpha")
    assert alpha.voided == (run_id,)  # type: ignore[attr-defined]
    assert "effect_exists" in alpha.claims_open  # type: ignore[attr-defined]
    assert alpha.n == 1  # type: ignore[attr-defined]  # voided runs stay in N


def test_in_progress_and_uncommitted(project: Project) -> None:
    with project.start() as run:
        assert study(project, "alpha").in_progress == (run.id,)  # type: ignore[attr-defined]
        run.result("done")
    assert project_status(project.root).uncommitted_ledger == ("bgl/ledger/CG1.jsonl",)
    project.commit("ledger")
    assert project_status(project.root).uncommitted_ledger == ()

from typing import Any

import pytest

from bgl.errors import AmendError
from bgl.studylock import (
    AMENDABLE,
    Difference,
    check_amendment,
    effective_lock,
    lock_differences,
)


def content(**study: Any) -> dict[str, Any]:
    table: dict[str, Any] = {
        "budget": 3,
        "prefix": "AL",
        "claims": ["a"],
        "prior_trials": 0,
        "spec_files": ["docs/alpha.md"],
        "holdout": False,
        "returns": {"periods_per_year": 252},
    }
    table.update(study)
    return {
        "study": table,
        "registry": {"CG1": {"person": "p", "key": "k1"}},
        "spec_files": {"docs/alpha.md": "1" * 64},
        "project": {"mode": "solo", "witness": False},
    }


def amend(key: str, new: Any) -> dict[str, Any]:
    return {"amend": {"key": key, "old": None, "new": new}}


def keys(found: list[Difference]) -> list[str]:
    return [difference.key for difference in found]


class TestLockDifferences:
    def test_same_content_has_none(self) -> None:
        assert lock_differences(content(), content()) == []

    @pytest.mark.parametrize(
        ("change", "key"),
        [
            ({"budget": 4}, "budget"),
            ({"prefix": "ZZ"}, "prefix"),
            ({"claims": ["a", "b"]}, "claims"),
            ({"holdout": None}, "holdout"),
            ({"gate": {"dsr_threshold": "0.95"}}, "gate"),
            ({"returns": {"periods_per_year": 365}}, "returns"),
        ],
    )
    def test_study_change(self, change: dict[str, Any], key: str) -> None:
        current = content(**change)
        if change.get("holdout", 0) is None:
            del current["study"]["holdout"]
        assert keys(lock_differences(content(), current)) == [key]

    def test_spec_hash_change_is_spec_only(self) -> None:
        current = content()
        current["spec_files"] = {"docs/alpha.md": "2" * 64}
        assert lock_differences(content(), current) == [
            Difference("spec_files", spec_only=True)
        ]

    def test_spec_list_change_is_spec_only(self) -> None:
        current = content(spec_files=[])
        current["spec_files"] = {}
        assert lock_differences(content(), current) == [
            Difference("spec_files", spec_only=True)
        ]

    def test_registry_may_grow(self) -> None:
        current = content()
        current["registry"]["HC1"] = {"person": "q", "key": "k2"}
        assert lock_differences(content(), current) == []

    def test_registry_entry_changed_or_removed(self) -> None:
        changed = content()
        changed["registry"]["CG1"]["key"] = "other"
        removed = content()
        removed["registry"] = {}
        for current in (changed, removed):
            assert keys(lock_differences(content(), current)) == ["project.chains.CG1"]

    @pytest.mark.parametrize(
        ("key", "value"),
        [("mode", "team"), ("witness", {"rekor_url": "https://r.example"})],
    )
    def test_project_change(self, key: str, value: Any) -> None:
        current = content()
        current["project"][key] = value
        assert keys(lock_differences(content(), current)) == [f"project.{key}"]


class TestEffectiveLock:
    def test_no_amendments_is_a_copy(self) -> None:
        locked = content()
        state = effective_lock(locked, [])
        assert state == locked
        state["study"]["budget"] = 99
        assert locked["study"]["budget"] == 3

    def test_amendments_apply_in_order(self) -> None:
        state = effective_lock(content(), [amend("budget", 5), amend("budget", 8)])
        assert state["study"]["budget"] == 8

    def test_each_key(self) -> None:
        gate = {"dsr_threshold": "0.95"}
        spec = {"paths": ["docs/b.md"], "hashes": {"docs/b.md": "3" * 64}}
        state = effective_lock(
            content(),
            [
                amend("prior_trials", 4),
                amend("claims", ["a", "b"]),
                amend("gate", gate),
                amend("spec_files", spec),
            ],
        )
        assert state["study"]["prior_trials"] == 4
        assert state["study"]["claims"] == ["a", "b"]
        assert state["study"]["gate"] == gate
        assert state["study"]["spec_files"] == ["docs/b.md"]
        assert state["spec_files"] == {"docs/b.md": "3" * 64}

    def test_amended_lock_matches_amended_config(self) -> None:
        state = effective_lock(content(), [amend("budget", 5)])
        assert lock_differences(state, content(budget=5)) == []
        assert keys(lock_differences(state, content(budget=6))) == ["budget"]

    def test_unknown_key_changes_nothing(self) -> None:
        assert effective_lock(content(), [amend("prefix", "ZZ")]) == content()


class TestCheckAmendment:
    @pytest.mark.parametrize(
        ("key", "old", "new", "fix"),
        [
            ("budget", 3, 4, None),
            ("prior_trials", 0, 12, None),
            ("claims", ["a"], ["a", "b"], None),
            ("claims", ["a"], ["b", "a"], None),
            ("gate", None, {"x": "1"}, None),
            ("gate", {"x": "1"}, {"x": "2"}, "f" * 40),
            ("spec_files", {"paths": []}, {"paths": ["a.md"]}, None),
        ],
    )
    def test_allowed(self, key: str, old: Any, new: Any, fix: str | None) -> None:
        check_amendment(key, old, new, fix)

    @pytest.mark.parametrize(
        ("key", "old", "new", "fix", "message"),
        [
            ("budget", 3, 2, None, "only increase"),
            ("budget", 3, 3, None, "nothing to do"),
            ("prior_trials", 5, 4, None, "only increase"),
            ("claims", ["a", "b"], ["a"], None, "only be added"),
            ("claims", ["a"], ["b"], None, "only be added"),
            ("claims", ["a", "b"], ["b", "a"], None, "only be added"),
            ("gate", {"x": "1"}, None, None, "cannot be removed"),
            ("gate", {"x": "1"}, {"x": "2"}, None, "--fix COMMIT"),
            ("prefix", "AL", "ZZ", None, "cannot be amended"),
            ("holdout_start", "2025-01-01", "2026-01-01", None, "cannot be amended"),
            ("returns", {}, {"a": 1}, None, "cannot be amended"),
            ("custodian", "a", "b", None, "cannot be amended"),
        ],
    )
    def test_refused(
        self, key: str, old: Any, new: Any, fix: str | None, message: str
    ) -> None:
        with pytest.raises(AmendError, match=message):
            check_amendment(key, old, new, fix)

    def test_amendable_keys_are_the_specs(self) -> None:
        assert set(AMENDABLE) == {
            "budget",
            "prior_trials",
            "claims",
            "gate",
            "spec_files",
        }

from typing import Any

import pytest

from bgl.errors import AmendError
from bgl.studylock import (
    AMENDABLE,
    Difference,
    allowed_change,
    apply_amendments,
    check_amendment,
    effective_lock,
    lock_differences,
    valid_amend,
    valid_lock_content,
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


def amend(
    key: str,
    old: Any,
    new: Any,
    row_hash: str = "",
    *,
    chain: str = "CG1",
    fix: str | None = None,
) -> dict[str, Any]:
    """An amend row as apply_amendments sees it: content, chain, fix and hash."""
    return {
        "amend": {"key": key, "old": old, "new": new},
        "chain": chain,
        "fix_commit": fix,
        "hash": row_hash or f"{key}-{old!r}-{new!r}",
    }


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

    def test_amendments_chain_on_their_recorded_old(self) -> None:
        state = effective_lock(
            content(), [amend("budget", 3, 5), amend("budget", 5, 8)]
        )
        assert state["study"]["budget"] == 8

    def test_each_key(self) -> None:
        gate = {"dsr_threshold": "0.95"}
        old_spec = {"paths": ["docs/alpha.md"], "hashes": {"docs/alpha.md": "1" * 64}}
        spec = {"paths": ["docs/b.md"], "hashes": {"docs/b.md": "3" * 64}}
        state = effective_lock(
            content(),
            [
                amend("prior_trials", 0, 4),
                amend("claims", ["a"], ["a", "b"]),
                amend("gate", None, gate),
                amend("spec_files", old_spec, spec),
            ],
        )
        assert state["study"]["prior_trials"] == 4
        assert state["study"]["claims"] == ["a", "b"]
        assert state["study"]["gate"] == gate
        assert state["study"]["spec_files"] == ["docs/b.md"]
        assert state["spec_files"] == {"docs/b.md": "3" * 64}

    def test_amended_lock_matches_amended_config(self) -> None:
        state = effective_lock(content(), [amend("budget", 3, 5)])
        assert lock_differences(state, content(budget=5)) == []
        assert keys(lock_differences(state, content(budget=6))) == ["budget"]

    def test_unknown_key_changes_nothing(self) -> None:
        assert effective_lock(content(), [amend("prefix", "AL", "ZZ")]) == content()


class TestApplyByOld:
    """N13: an amendment applies where its recorded old is the value in force."""

    def test_either_order_gives_the_same_lock(self) -> None:
        first, second = amend("budget", 3, 5), amend("budget", 5, 8)
        for rows in ([first, second], [second, first]):
            state, never = apply_amendments(content(), rows)
            assert state["study"]["budget"] == 8
            assert never == []

    def test_one_that_never_fits_is_never_applied(self) -> None:
        stale = amend("budget", 4, 9)  # the lock says 3, never 4
        state, never = apply_amendments(content(), [stale])
        assert state["study"]["budget"] == 3
        assert never == [stale]

    def test_two_chains_on_the_same_old_lower_hash_wins_in_any_order(self) -> None:
        low = amend("budget", 3, 9, "a" * 64, chain="CG1")
        high = amend("budget", 3, 5, "b" * 64, chain="HC1")
        for rows in ([low, high], [high, low]):
            state, never = apply_amendments(content(), rows)
            assert state["study"]["budget"] == 9
            assert never == [high]

    def test_one_chain_applies_in_its_own_order(self) -> None:
        """An honest cycle in one chain: S0->S1, S1->S0, S0->S2 all apply.

        The last row has the lowest hash, so a hash tie-break within a chain
        would apply it first and leave the first two never fitting.
        """
        s0 = {"dsr_threshold": "0.9"}
        s1 = {"dsr_threshold": "0.95"}
        s2 = {"dsr_threshold": "0.99"}
        fix = "f" * 40
        rows = [
            amend("gate", s0, s1, "c" * 64, fix=fix),
            amend("gate", s1, s0, "b" * 64, fix=fix),
            amend("gate", s0, s2, "a" * 64, fix=fix),
        ]
        state, never = apply_amendments(content(gate=s0), rows)
        assert state["study"]["gate"] == s2
        assert never == []

    def test_old_compares_as_json_not_python(self) -> None:
        # True == 1 in Python, but a recorded old of {"x": true} is not {"x": 1}.
        row = amend("gate", {"x": True}, {"x": 2}, fix="f" * 40)
        state, never = apply_amendments(content(gate={"x": 1}), [row])
        assert state["study"]["gate"] == {"x": 1}
        assert never == [row]

    @pytest.mark.parametrize(
        ("key", "old", "new", "fix"),
        [
            ("budget", 3, 2, None),  # a cut
            ("prior_trials", 0, 0, None),  # no change
            ("claims", ["a"], [], None),  # a removed claim
            ("gate", {"x": "1"}, {"x": "2"}, None),  # a correction with no fix
        ],
    )
    def test_a_disallowed_change_never_applies(
        self, key: str, old: Any, new: Any, fix: str | None
    ) -> None:
        """N13, N44: the typed rules are checked again, not only when written."""
        locked = content(**{key: old})
        row = amend(key, old, new, fix=fix)
        state, never = apply_amendments(locked, [row])
        assert state == locked
        assert never == [row]
        assert not allowed_change(row)

    def test_absent_gate_has_old_null(self) -> None:
        gate = {"dsr_threshold": "0.95"}
        state, never = apply_amendments(content(), [amend("gate", None, gate)])
        assert state["study"]["gate"] == gate and never == []

    @pytest.mark.parametrize(
        "bad",
        [
            None,
            "budget",
            {"key": "budget", "old": 3},
            {"key": "budget", "old": 3, "new": 5, "extra": 1},
            {"key": "budget", "old": 3, "new": "5"},
            {"key": "budget", "old": 3, "new": True},
            {"key": "budget", "old": 3, "new": -1},
            {"key": "claims", "old": ["a"], "new": "a,b"},
            {"key": "gate", "old": None, "new": [1]},
            {"key": "spec_files", "old": None, "new": {"paths": []}},
            {"key": "prefix", "old": "AL", "new": "ZZ"},
        ],
    )
    def test_malformed_content_never_applies(self, bad: Any) -> None:
        assert not valid_amend(bad)
        row = {"amend": bad, "hash": "c" * 64}
        state, never = apply_amendments(content(), [row])
        assert state == content()
        assert never == [row]

    def test_does_not_change_the_lock_or_rows(self) -> None:
        locked, row = content(), amend("claims", ["a"], ["a", "b"])
        state, _ = apply_amendments(locked, [row])
        state["study"]["claims"].append("c")
        assert locked["study"]["claims"] == ["a"]
        assert row["amend"]["new"] == ["a", "b"]


class TestValidLockContent:
    def test_built_content_is_valid(self) -> None:
        assert valid_lock_content(content())

    @pytest.mark.parametrize(
        "change",
        [
            lambda c: c.pop("registry"),
            lambda c: c.update(extra=1),
            lambda c: c.update(study=[]),
            lambda c: c["study"].pop("spec_files"),
            lambda c: c.update(spec_files=["docs/alpha.md"]),
            lambda c: c["project"].pop("witness"),
            lambda c: c.update(project="solo"),
        ],
    )
    def test_malformed(self, change: Any) -> None:
        bad = content()
        change(bad)
        assert not valid_lock_content(bad)

    @pytest.mark.parametrize("bad", [None, 1, "lock", []])
    def test_not_a_mapping(self, bad: Any) -> None:
        assert not valid_lock_content(bad)


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
            # Wrongly typed values (a hand-made lock or row): never a traceback.
            ("budget", "3", 4, None, "right type"),
            ("budget", True, 4, None, "right type"),
            ("prior_trials", 0, 2.5, None, "right type"),
            ("claims", "a", ["a", "b"], None, "right type"),
            ("claims", [["a"]], ["b"], None, "right type"),
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

import pytest

from bgl.ids import format_run_id, next_run_id, parse_run_id


def test_format_matches_spec_example() -> None:
    assert format_run_id("AL", "R", 7, "CG1") == "AL-R007-CG1"


def test_counter_grows_past_three_digits() -> None:
    assert format_run_id("AL", "R", 1000, "CG1") == "AL-R1000-CG1"
    assert parse_run_id("AL-R1000-CG1") == ("AL", "R", 1000, "CG1")


@pytest.mark.parametrize(
    "bad",
    ["", "AL-R07-CG1", "al-R007-CG1", "AL-Z007-CG1", "AL-R007-cg1", "AL-R007", None, 7],
)
def test_parse_rejects(bad: object) -> None:
    assert parse_run_id(bad) is None


class TestNextRunId:
    def test_first(self) -> None:
        assert next_run_id("AL", "R", "CG1", []) == "AL-R001-CG1"

    def test_after_highest_not_after_count(self) -> None:
        used = ["AL-R001-CG1", "AL-R005-CG1"]
        assert next_run_id("AL", "R", "CG1", used) == "AL-R006-CG1"

    def test_counters_are_separate_per_study_letter_and_chain(self) -> None:
        used = ["BE-R009-CG1", "AL-X009-CG1", "AL-R009-HC1", "AL-R002-CG1"]
        assert next_run_id("AL", "R", "CG1", used) == "AL-R003-CG1"

    def test_ignores_things_that_are_not_ids(self) -> None:
        used = [None, "notes", 12, "AL-R001-CG1.json", "AL-R004-CG1"]
        assert next_run_id("AL", "R", "CG1", used) == "AL-R005-CG1"

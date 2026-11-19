from typing import Any

from bgl.canonical import GENESIS_HASH
from bgl.ledger import ChainProblem, ChainReport, LedgerReport
from bgl.records import LedgerView, budget_of


def row(event: str, **fields: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"event": event, "study": "alpha", "id": None, "type": None}
    base.update(fields)
    return base


def view(**chains: list[dict[str, Any]]) -> LedgerView:
    reports = {
        code: ChainReport(code, tuple(rows), f"head-{code}", None)
        for code, rows in chains.items()
    }
    return LedgerView.from_report(LedgerReport(reports, ()))


def test_rows_are_in_registry_order_then_row_order() -> None:
    made = view(
        CG1=[row("open", id="a"), row("close", id="a")], HC1=[row("open", id="b")]
    )
    assert [r["id"] for r in made.rows] == ["a", "a", "b"]


def test_counted_opens_across_chains_and_statuses() -> None:
    made = view(
        CG1=[
            row("open", type="diagnostic", id="1"),
            row("close", type="diagnostic", id="1", status="crashed"),
            row("open", type="exploratory", id="2"),
            row("open", type="data", id="3"),
            row("refused", type="diagnostic", id="4"),
            row("dry", type="diagnostic", id="5"),
            row("open", type="diagnostic", id="6", study="beta"),
        ],
        HC1=[row("open", type="confirmatory", id="7"), row("void", id="7")],
    )
    assert [r["id"] for r in made.counted_opens("alpha")] == ["1", "7"]
    assert budget_of(made, "alpha", 5).used == 2
    assert budget_of(made, "alpha", 5).left == 3
    assert budget_of(made, "alpha", 1).left == 0
    assert made.voided_ids() == frozenset({"7"})


def test_lock_and_ids() -> None:
    made = view(CG1=[row("lock", id="1"), row("open", id="1")], HC1=[])
    assert made.has_lock("alpha")
    assert not made.has_lock("beta")
    assert made.ids_in_chain("CG1") == ["1", "1"]
    assert made.ids_in_chain("ZZ9") == []


def test_other_heads_is_null_for_a_chain_with_no_rows() -> None:
    made = view(CG1=[row("open")], HC1=[row("open")], XX1=[])
    assert made.other_heads("CG1") == {"HC1": "head-HC1", "XX1": None}


def test_rows_before_a_break_still_count() -> None:
    broken = ChainReport(
        "HC1",
        (row("open", type="diagnostic", id="9"),),
        GENESIS_HASH,
        ChainProblem(2, "not valid JSON"),
    )
    made = LedgerView.from_report(LedgerReport({"HC1": broken}, ()))
    assert len(made.counted_opens("alpha")) == 1

"""Per-model token splits for multi-model requests (pure)."""

from datetime import datetime, timezone

from app.llm.splits import split_rows, summarize_models

T = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)


def test_main_model_is_the_one_with_most_tokens_first_on_a_tie():
    assert summarize_models([("a", 10, 0), ("b", 5, 5), ("a", 0, 1)]) == (
        "a", 15, 6, [("a", 10, 1), ("b", 5, 5)],
    )
    assert summarize_models([("x", 1, 1), ("y", 2, 0)])[0] == "x"


def test_splits_sum_to_zero_and_give_each_model_its_tokens():
    per_model = summarize_models([("big", 1000, 300), ("small", 400, 40), ("tiny", 5, 0)])[3]
    rows = split_rows("e1", T, "svc", "/chat", per_model)
    assert [(r[4], r[5], r[6]) for r in rows] == [("big", -405, -40), ("small", 400, 40), ("tiny", 5, 0)]
    assert sum(r[5] for r in rows) == 0 and sum(r[6] for r in rows) == 0
    # the event row carries the totals on "big": 1405 in; with the splits, big keeps its own 1000
    assert 1405 + rows[0][5] == 1000


def test_one_model_needs_no_split():
    assert split_rows("e1", T, "svc", "/chat", [("only", 10, 1)]) == []
    assert split_rows("e1", T, "svc", "/chat", []) == []

import math

import pytest

from eval.metrics import Metric, norm_title, percentile, recall_at_k, reciprocal_rank


def test_recall_divides_by_min_k():
    assert recall_at_k([1, 2, 3, 4, 5], set(range(1, 104)), 5) == 1.0
    assert recall_at_k([9, 1, 8], {1, 2}, 5) == 0.5
    with pytest.raises(ValueError):
        recall_at_k([1], set(), 5)


def test_mrr_and_percentile():
    assert reciprocal_rank([7, 3, 1], {1}) == pytest.approx(1 / 3)
    assert reciprocal_rank([7], {1}) == 0.0
    assert percentile([1, 2, 3, 4], 50) == 2 and percentile([1, 2, 3, 4], 95) == 4
    assert math.isnan(percentile([], 50))


def test_titles_and_render():
    assert norm_title("Отправить отчёт!") == norm_title("отправить отчет")
    m = Metric("x", "≥ 90%")
    m.add(True), m.add(False)
    assert m.render() == "50%"

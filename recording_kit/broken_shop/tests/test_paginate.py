import pytest

from shop.paginate import page_count, paginate

ITEMS = list(range(25))


def test_first_page():
    assert paginate(ITEMS, 1, 5) == [0, 1, 2, 3, 4]


def test_last_page():
    assert paginate(ITEMS, 5, 5) == [20, 21, 22, 23, 24]


def test_partial_last_page():
    assert paginate(ITEMS, 3, 10) == [20, 21, 22, 23, 24]


def test_past_the_end_is_empty():
    assert paginate(ITEMS, 9, 5) == []


def test_page_count():
    assert page_count(25, 5) == 5
    assert page_count(26, 5) == 6


def test_rejects_page_zero():
    with pytest.raises(ValueError):
        paginate(ITEMS, 0, 5)

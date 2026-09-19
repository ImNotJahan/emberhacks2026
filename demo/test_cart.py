from cart import add_item, total


def test_add_one_item():
    assert add_item(("apple", 1.50)) == [("apple", 1.50)]


def test_each_cart_starts_empty():
    assert add_item(("pear", 2.00)) == [("pear", 2.00)]


def test_total():
    assert total([("fig", 3.00), ("kiwi", 0.50)]) == 3.50

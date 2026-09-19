from shop.cart import Cart


def test_add_and_total():
    c = Cart()
    c.add("apple", 50, 3)
    assert c.total() == 150


def test_discount():
    c = Cart()
    c.add("pear", 1000)
    assert c.apply_discount(10) == 900


def test_new_cart_is_empty():
    assert Cart().total() == 0


def test_carts_are_independent():
    a, b = Cart(), Cart()
    a.add("fig", 200)
    assert b.total() == 0

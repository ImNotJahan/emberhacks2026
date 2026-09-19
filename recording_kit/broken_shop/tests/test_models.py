from shop.models import Product
from shop.pricing import bulk_price


def test_price_formats_as_dollars():
    assert str(Product("p1", "Pen", 250).price) == "$2.50"


def test_bulk_discount():
    assert bulk_price(Product("p1", "Pen", 250), 10).cents == 2250


def test_no_bulk_discount_below_ten():
    assert bulk_price(Product("p1", "Pen", 250), 9).cents == 2250

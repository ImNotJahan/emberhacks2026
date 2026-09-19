from decimal import ROUND_HALF_UP, Decimal

from shop.models import Product


class Price:
    def __init__(self, cents: int) -> None:
        self.cents = cents

    def dollars(self) -> Decimal:
        return (Decimal(self.cents) / 100).quantize(Decimal("0.01"), ROUND_HALF_UP)

    def __str__(self) -> str:
        return f"${self.dollars()}"


def bulk_price(product: Product, qty: int) -> Price:
    """10% off for 10 or more of the same product."""
    total = product.cents * qty
    if qty >= 10:
        total = total * 90 // 100
    return Price(total)

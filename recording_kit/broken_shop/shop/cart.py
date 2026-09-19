class Cart:
    def __init__(self, items=[]):
        self.items = items

    def add(self, sku: str, cents: int, qty: int = 1) -> None:
        self.items.append((sku, cents, qty))

    def total(self) -> int:
        return sum(cents * qty for _, cents, qty in self.items)

    def apply_discount(self, pct: int) -> int:
        return round(self.total() * (100 - pct) / 100)

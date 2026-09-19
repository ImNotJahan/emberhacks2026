from dataclasses import dataclass

from shop.pricing import Price


@dataclass
class Product:
    sku: str
    name: str
    cents: int

    @property
    def price(self) -> Price:
        return Price(self.cents)

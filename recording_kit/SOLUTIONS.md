# Operator only: the planted bugs

Don't let the volunteer see this file. Copy only `broken_shop/` to them.

| Bug | Where | Symptom | Fix | Catalogue pattern |
|---|---|---|---|---|
| Circular import | `shop/pricing.py` imports `Product` from `shop.models` just for a type hint | `ImportError: cannot import name 'Product' from partially initialized module 'shop.models'`. The traceback points at models.py. | `if TYPE_CHECKING:` import + `"Product"` string annotation, or drop the import | S9, S14 |
| Off-by-one | `shop/paginate.py`: `end = start + size - 1  # last index on the page` | first/last page tests are missing one item | `end = start + size` (slice end is exclusive) | S1, S2 |
| Mutable default | `shop/cart.py`: `def __init__(self, items=[])` | tests fail only when the whole file runs; each passes alone. `test_discount` fails with 1035 ≠ 900. | `items=None`, `self.items = list(items or [])` | S18-like: confusing, order-dependent |
| Infinite loop | `shop/fetch.py`: `next_cursor = …` never assigned to `cursor` | `fetch_all.py` prints `cursor=None` forever; Ctrl+C | `cursor = resp["next_cursor"]`, `if cursor is None: break` | S4, S25 |

With all four fixed and `shop/tax.py` written, `pytest` shows 19 passed and `fetch_all.py` prints `done: 140 orders`.

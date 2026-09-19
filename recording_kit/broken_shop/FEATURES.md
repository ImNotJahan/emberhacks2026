# Your task

Add sales tax to the shop. The tests are already written: make `pytest tests/test_tax.py` pass.

Create `shop/tax.py` with:

- `TAX_RATES`: `ON` 13%, `AB` 5%, `QC` 15%.
- `tax_for(cents, region) -> int`: tax in cents, rounded half up (5 cents in ON is 0.65, so it rounds to 1).
  An unknown region raises `KeyError`.
- `total_with_tax(cents, region) -> int`.

When those pass, if you have time: add `BC` (12%) with a test, and a `Cart.total_with_tax(region)` method with a test.
Commit when you are happy (`git init` first if needed). Work the way you normally would, in the VS Code terminal.

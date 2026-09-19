"""Feature task (productive session): implement shop/tax.py so these pass. See FEATURES.md."""
import pytest

from shop.tax import tax_for, total_with_tax


def test_ontario():
    assert tax_for(1000, "ON") == 130


def test_alberta():
    assert tax_for(1000, "AB") == 50


def test_quebec_combined():
    assert tax_for(1000, "QC") == 150


def test_rounds_half_up():
    assert tax_for(5, "ON") == 1


def test_unknown_region():
    with pytest.raises(KeyError):
        tax_for(1000, "XX")


def test_total_with_tax():
    assert total_with_tax(2000, "AB") == 2100

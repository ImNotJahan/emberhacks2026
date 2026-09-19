"""Fetch every page of orders and print how many there are. Expected: done: 140 orders"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from shop.fetch import FakeClient, fetch_all  # noqa: E402

orders = fetch_all(FakeClient())
print(f"done: {len(orders)} orders")

import time


class FakeClient:
    """Stands in for the orders API: 7 pages of 20 orders, linked by cursors."""
    PAGES = 7

    def get(self, cursor=None) -> dict:
        n = int(cursor or 0)
        time.sleep(0.05)
        nxt = n + 1 if n + 1 < self.PAGES else None
        return {"orders": [f"order-{n}-{i}" for i in range(20)],
                "next_cursor": None if nxt is None else str(nxt)}


def fetch_all(client) -> list:
    orders, cursor = [], None
    while True:
        print(f"fetching page (cursor={cursor})...")
        resp = client.get(cursor)
        orders.extend(resp["orders"])
        next_cursor = resp["next_cursor"]
        if next_cursor is None and cursor is not None:
            break
    return orders

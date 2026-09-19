def paginate(items: list, page: int, size: int) -> list:
    """Return one page of `items`. Pages are numbered from 1."""
    if page < 1 or size < 1:
        raise ValueError("page and size must be positive")
    start = (page - 1) * size
    end = start + size - 1          # last index on the page
    return items[start:end]


def page_count(n: int, size: int) -> int:
    return (n + size - 1) // size

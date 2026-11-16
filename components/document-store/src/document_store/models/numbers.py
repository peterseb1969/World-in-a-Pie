"""Numbers a document can store and serve.

MongoDB holds a 64-bit integer at most: a larger whole number fails at
encoding, after everything before the write has run (a document's Registry
id is allocated by then, and every document in the same insert fails with
it). No JSON response can carry NaN or Infinity, so a stored one makes every
later read of it fail. Both are refused on the way in, wherever they sit.
"""

import math
from typing import Any

INT64_MIN, INT64_MAX = -(2**63), 2**63 - 1


def numbers_in(value: Any, path: str = "") -> list[tuple[str, int | float]]:
    """(path, number) for every number inside value; booleans are not numbers."""
    if isinstance(value, bool):
        return []
    if isinstance(value, int | float):
        return [(path, value)]
    if isinstance(value, dict):
        return [
            found for k, v in value.items()
            for found in numbers_in(v, f"{path}.{k}" if path else str(k))
        ]
    if isinstance(value, list):
        return [found for i, v in enumerate(value) for found in numbers_in(v, f"{path}[{i}]")]
    return []


def storable(number: int | float) -> bool:
    if isinstance(number, float):
        return math.isfinite(number)
    return INT64_MIN <= number <= INT64_MAX


def refuse_unstorable_numbers(value: Any, what: str) -> Any:
    """value, or ValueError naming each number in it that cannot be stored
    (a whole number outside 64 bits, NaN, Infinity)."""
    bad = [path for path, number in numbers_in(value) if not storable(number)]
    if bad:
        raise ValueError(
            f"{what} holds numbers that cannot be stored (a whole number must lie "
            f"between -2^63 and 2^63-1; NaN and Infinity are refused): {', '.join(bad)}"
        )
    return value

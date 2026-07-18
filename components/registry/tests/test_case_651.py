"""UUID7 timestamps are true UTC regardless of host timezone.

The generator once built its embedded timestamp via
``datetime.utcnow().timestamp()`` — a naive datetime that ``.timestamp()``
reinterprets as LOCAL time, skewing the time-ordering bits by the host's
UTC offset on any non-UTC machine. These tests pin the fix by running the
generator under a forced non-UTC timezone and decoding the embedded
milliseconds back out.
"""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime

import pytest

from registry.models.id_algorithm import IdGenerator


def _embedded_ms(uuid7_str: str) -> int:
    """Decode the leading 48 timestamp bits of a UUID7 string."""
    return int(uuid7_str.replace("-", "")[:12], 16)


@pytest.fixture
def forced_timezone():
    """Run the test body under a fixed, far-from-UTC timezone.

    tzset() is POSIX-only, which covers the platforms the suite runs on
    (macOS dev hosts, Linux CI). The original TZ is always restored.
    """
    original = os.environ.get("TZ")
    os.environ["TZ"] = "Pacific/Kiritimati"  # UTC+14 — maximal offset
    time.tzset()
    try:
        yield
    finally:
        if original is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = original
        time.tzset()


def test_uuid7_timestamp_is_utc_under_non_utc_host_timezone(forced_timezone):
    """The embedded millis must match true UTC now — under the old naive
    utcnow() code this is off by the host's UTC offset (14h here)."""
    before_ms = int(datetime.now(UTC).timestamp() * 1000)
    generated = IdGenerator.generate_uuid7()
    after_ms = int(datetime.now(UTC).timestamp() * 1000)

    embedded = _embedded_ms(generated)
    assert before_ms <= embedded <= after_ms, (
        f"embedded {embedded} outside true-UTC window "
        f"[{before_ms}, {after_ms}] — off by ~{(embedded - after_ms) / 3.6e6:.1f}h"
    )


def test_uuid7_remains_time_ordered(forced_timezone):
    """Sequential mints stay monotonically non-decreasing in their
    timestamp bits (the property the UTC fix exists to protect)."""
    ids = [IdGenerator.generate_uuid7() for _ in range(50)]
    stamps = [_embedded_ms(i) for i in ids]
    assert stamps == sorted(stamps)

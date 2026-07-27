"""CASE-700 — wip-auth model/provider consistency fixes.

1. is_expired compares against a UTC-aware now (matching the rest of the
   system) and treats a naive expires_at as UTC, instead of deriving the
   comparison tz from the stored value (which made a naive value compare
   against a naive *local* now — off by the host's UTC offset).
2. APIKeyProvider exposes a public keys() accessor so callers (the
   Registry) stop reaching into the private _keys list.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from wip_auth import APIKeyProvider, APIKeyRecord, hash_api_key


def _rec(name: str, *, expires_at=None, enabled: bool = True) -> APIKeyRecord:
    return APIKeyRecord(
        name=name,
        key_hash=hash_api_key(name + "-secret"),
        expires_at=expires_at,
        enabled=enabled,
    )


class TestIsExpired:
    def test_none_never_expires(self) -> None:
        assert _rec("k", expires_at=None).is_expired() is False

    def test_aware_past_expired(self) -> None:
        past = datetime.now(UTC) - timedelta(days=1)
        assert _rec("k", expires_at=past).is_expired() is True

    def test_aware_future_not_expired(self) -> None:
        future = datetime.now(UTC) + timedelta(days=1)
        assert _rec("k", expires_at=future).is_expired() is False

    def test_naive_past_expired_regardless_of_host_tz(self) -> None:
        # A naive expires_at is assumed UTC. Far-past → expired on any host;
        # the old code (naive local now) could disagree near the boundary on
        # a non-UTC host.
        assert _rec("k", expires_at=datetime(2000, 1, 1)).is_expired() is True

    def test_naive_future_not_expired(self) -> None:
        assert _rec("k", expires_at=datetime(2999, 1, 1)).is_expired() is False

    def test_naive_does_not_raise(self) -> None:
        # Regression guard: a UTC-aware now vs a naive expires_at must not
        # TypeError — the fix normalizes the naive value to UTC first.
        _rec("k", expires_at=datetime(2100, 1, 1)).is_expired()


class TestKeysAccessor:
    def test_returns_enabled_keys(self) -> None:
        provider = APIKeyProvider(
            [_rec("a"), _rec("b"), _rec("c", enabled=False)]
        )
        names = {k.name for k in provider.iter_keys()}
        assert names == {"a", "b"}  # disabled key filtered at construction

    def test_is_a_snapshot_tuple(self) -> None:
        provider = APIKeyProvider([_rec("a")])
        snap = provider.iter_keys()
        assert isinstance(snap, tuple)

    def test_reflects_add_key(self) -> None:
        provider = APIKeyProvider([_rec("a")])
        provider.add_key(_rec("b"))
        assert {k.name for k in provider.iter_keys()} == {"a", "b"}

"""A grant with an expiry resolves: live until it expires, nothing after.

MongoDB hands datetimes back without a timezone, and NamespaceGrant.is_expired
compared them with an aware now(UTC), which raises TypeError: resolving the
permission of any caller holding a grant with expires_at failed (a 500 on
every request that checks it) instead of honouring or ignoring the grant.
A stored expiry is UTC; a naive one is read as such.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from registry.api.grants import _resolve_permission
from registry.models.grant import NamespaceGrant
from wip_auth import UserIdentity


def _identity(group: str) -> UserIdentity:
    return UserIdentity(user_id="u-expiry", username="u", groups=[group], auth_method="jwt")


@pytest.mark.asyncio
@pytest.mark.parametrize(("delta", "expected"), [(timedelta(days=1), "write"), (timedelta(days=-1), "none")])
async def test_a_grant_with_an_expiry_resolves(client, delta, expected):
    ns, group = f"exp-{uuid.uuid4().hex[:6]}", f"g-{uuid.uuid4().hex[:6]}"
    await NamespaceGrant(
        namespace=ns, subject=group, subject_type="group", permission="write",
        granted_by="test", expires_at=datetime.now(UTC) + delta,
    ).insert()
    assert await _resolve_permission(_identity(group), ns) == expected


def test_is_expired_reads_a_naive_expiry_as_utc():
    now = datetime.now(UTC).replace(tzinfo=None)
    grant = NamespaceGrant(namespace="n", subject="s", subject_type="group", permission="read", granted_by="t")
    grant.expires_at = now - timedelta(minutes=1)
    assert grant.is_expired() is True
    grant.expires_at = now + timedelta(minutes=1)
    assert grant.is_expired() is False
    grant.expires_at = datetime.now(UTC) + timedelta(minutes=1)
    assert grant.is_expired() is False

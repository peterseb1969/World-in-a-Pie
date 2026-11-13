"""A grant with an expiry resolves: live until it expires, nothing after.

MongoDB hands datetimes back without a timezone, and NamespaceGrant.is_expired
compared them with an aware now(UTC), which raised TypeError: a permission
resolution that reached the caller's grants failed (a 500) when one carried
expires_at, instead of honouring or ignoring it. Resolutions answered before
the grants are read (superadmin, reservation owner, config-key declaration,
scope miss) were unaffected. A stored expiry is UTC; a naive one is read as
such.

CASE-1268: an expiry already past is refused at grant create, per item,
instead of being stored and answered "created" while it gives nothing.
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


@pytest.mark.asyncio
async def test_is_expired_reads_a_naive_expiry_as_utc(client):
    # client initialises Beanie, which a NamespaceGrant needs even unsaved.
    now = datetime.now(UTC).replace(tzinfo=None)
    grant = NamespaceGrant(namespace="n", subject="s", subject_type="group", permission="read", granted_by="t")
    grant.expires_at = now - timedelta(minutes=1)
    assert grant.is_expired() is True
    grant.expires_at = now + timedelta(minutes=1)
    assert grant.is_expired() is False
    grant.expires_at = datetime.now(UTC) + timedelta(minutes=1)
    assert grant.is_expired() is False


@pytest.mark.asyncio
async def test_an_expiry_already_past_is_refused_per_item(client, auth_headers):
    await NamespaceGrant.find({"namespace": "default", "subject": {"$regex": "^exp-"}}).delete()
    past = datetime.now(UTC) - timedelta(minutes=1)
    naive_past = past.replace(tzinfo=None).isoformat()
    future = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    resp = await client.post("/api/registry/namespaces/default/grants", json=[
        {"subject": "exp-past", "subject_type": "user", "expires_at": past.isoformat()},
        {"subject": "exp-naive", "subject_type": "user", "expires_at": naive_past},
        {"subject": "exp-future", "subject_type": "user", "expires_at": future},
    ], headers=auth_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [r["status"] for r in body["results"]] == ["error", "error", "created"]
    for r in body["results"][:2]:
        assert r["error_code"] == "validation_failed"
        assert r["details"] == {"rule": "expired", "field": "expires_at"}
        assert "already passed" in r["error"]
    assert (body["succeeded"], body["failed"]) == (1, 2)
    stored = {g.subject async for g in NamespaceGrant.find({"namespace": "default", "subject": {"$regex": "^exp-"}})}
    assert stored == {"exp-future"}


@pytest.mark.asyncio
async def test_renewing_a_grant_with_a_past_expiry_leaves_it_as_it_was(client, auth_headers):
    await NamespaceGrant.find({"namespace": "default", "subject": "exp-renew"}).delete()
    future = datetime.now(UTC) + timedelta(days=1)
    url = "/api/registry/namespaces/default/grants"
    first = await client.post(url, json=[{"subject": "exp-renew", "expires_at": future.isoformat()}], headers=auth_headers)
    assert first.json()["results"][0]["status"] == "created"
    past = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    again = await client.post(url, json=[{"subject": "exp-renew", "permission": "admin", "expires_at": past}], headers=auth_headers)
    assert again.json()["results"][0]["error_code"] == "validation_failed"
    grant = await NamespaceGrant.find_one({"namespace": "default", "subject": "exp-renew"})
    assert grant is not None and grant.permission == "read" and not grant.is_expired()
    await grant.delete()

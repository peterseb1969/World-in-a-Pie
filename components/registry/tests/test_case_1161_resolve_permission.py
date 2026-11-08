"""Permission resolution for keys and users, pinned before
_resolve_permission's refactor.

Provenance: CASE-1161. The suite never reached one branch: an unscoped key
in a privileged group that is not wip-admins (wip-services) resolves from
its grants, like a scoped key; an unscoped key in no privileged group gets
none whatever it is granted. Also pinned: a key named in the legacy
"apikey:<name>" form matches grants stored under the bare name; an OIDC
user without an email resolves by user id and groups; the highest
unexpired, non-orphaned grant wins.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from registry.api.grants import _resolve_permission
from registry.models.grant import NamespaceGrant
from wip_auth import UserIdentity


def _ns() -> str:
    return f"c1161p-{uuid.uuid4().hex[:6]}"


async def _grant(ns: str, subject: str, subject_type: str, permission: str, **extra) -> None:
    await NamespaceGrant(
        namespace=ns, subject=subject, subject_type=subject_type, permission=permission,
        granted_by="test", **extra,
    ).insert()


def _key(name: str, groups: list[str], namespaces: list[str] | None, username: str | None = None) -> UserIdentity:
    return UserIdentity(
        user_id=f"apikey:{name}", username=username or name, groups=groups,
        auth_method="api_key", raw_claims={"namespaces": namespaces},
    )


@pytest.mark.asyncio
async def test_an_unscoped_service_key_resolves_from_its_grants(client):
    ns = _ns()
    await _grant(ns, "svc-1161", "api_key", "write")
    assert await _resolve_permission(_key("svc-1161", ["wip-services"], None), ns) == "write"
    # Unscoped and not privileged: none, whatever it is granted.
    await _grant(ns, "plain-1161", "api_key", "admin")
    assert await _resolve_permission(_key("plain-1161", ["wip-users"], None), ns) == "none"


@pytest.mark.asyncio
async def test_a_legacy_key_name_matches_the_bare_grant(client):
    ns = _ns()
    await _grant(ns, "legacy-1161", "api_key", "read")
    identity = _key("legacy-1161", [], [ns], username="apikey:legacy-1161")
    assert await _resolve_permission(identity, ns) == "read"


@pytest.mark.asyncio
async def test_a_user_without_an_email_resolves_by_id_and_groups(client):
    ns = _ns()
    await _grant(ns, "user-1161", "user", "read")
    await _grant(ns, "team-1161", "group", "write")
    user = UserIdentity(user_id="user-1161", username="u", groups=["team-1161"], auth_method="jwt")
    assert await _resolve_permission(user, ns) == "write"


@pytest.mark.asyncio
async def test_the_highest_live_grant_wins(client):
    ns = _ns()
    past = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=1)  # stored naive
    await _grant(ns, "best-1161", "api_key", "read")
    await _grant(ns, "team-1161b", "group", "admin", expires_at=past)
    await _grant(ns, "team-1161c", "group", "admin", orphaned=True)
    await _grant(ns, "team-1161d", "group", "write")
    identity = _key("best-1161", ["team-1161b", "team-1161c", "team-1161d"], [ns])
    assert await _resolve_permission(identity, ns) == "write"

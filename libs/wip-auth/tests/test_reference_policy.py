"""Who may reference an entity in another namespace (wip_auth.reference_policy).

Two gates: the source namespace's isolation_mode (outbound) and the target
namespace's inbound_references (inbound, decided by the writer's read). The
identities below are config-file keys, whose permissions resolve locally, so
no Registry is involved.
"""

from unittest.mock import AsyncMock, patch

import pytest

from wip_auth.identity import reset_current_identity, set_current_identity
from wip_auth.models import UserIdentity
from wip_auth.permissions import PERMISSION_UNAVAILABLE
from wip_auth.reference_policy import (
    PermissionUnavailableError,
    caller_can_read,
    check_reference,
    inbound_open,
    outbound_allows,
)

OPEN = {"isolation_mode": "open", "allowed_external_refs": []}


async def CLOSED(_ns: str) -> dict:
    return {"inbound_references": "closed"}


async def OPENED(_ns: str) -> dict:
    return {"inbound_references": "open"}


def _key(namespaces: list[str], grants: dict[str, str] | None = None, groups=None) -> UserIdentity:
    return UserIdentity(
        user_id="apikey:app",
        username="app",
        auth_method="api_key",
        groups=groups or [],
        raw_claims={"namespaces": namespaces, "grants": grants or {}},
    )


class TestOutbound:
    def test_open_references_any_namespace(self):
        assert outbound_allows("app", OPEN, "other")

    def test_strict_references_only_its_allowlist(self):
        strict = {"isolation_mode": "strict", "allowed_external_refs": ["lib"]}
        assert outbound_allows("app", strict, "lib")
        assert not outbound_allows("app", strict, "other")

    def test_strict_gets_no_implicit_wip(self):
        assert not outbound_allows("app", {"isolation_mode": "strict"}, "wip")

    def test_own_namespace_always(self):
        assert outbound_allows("app", {"isolation_mode": "strict"}, "app")


class TestInbound:
    def test_closed_is_the_default(self):
        assert not inbound_open("lib", {})

    def test_open_accepts_any_writer(self):
        assert inbound_open("lib", {"inbound_references": "open"})

    def test_wip_is_always_open(self):
        assert inbound_open("wip", {"inbound_references": "closed"})


class TestCheckReference:
    async def test_writer_who_reads_the_target_may_reference_it(self):
        writer = _key(["app", "lib"], {"app": "write"})
        assert await check_reference("app", OPEN, "lib", CLOSED, writer) == "ok"

    async def test_writer_who_cannot_read_a_closed_target_is_refused(self):
        writer = _key(["app"], {"app": "write"})
        assert await check_reference("app", OPEN, "lib", CLOSED, writer) == "inbound"

    async def test_open_target_needs_no_read(self):
        writer = _key(["app"], {"app": "write"})
        assert await check_reference("app", OPEN, "lib", OPENED, writer) == "ok"

    async def test_outbound_is_checked_before_inbound(self):
        writer = _key(["app", "lib"], {"app": "write"})
        strict = {"isolation_mode": "strict", "allowed_external_refs": []}
        assert await check_reference("app", strict, "lib", CLOSED, writer) == "outbound"

    async def test_privileged_keys_pass_the_inbound_gate(self):
        service = _key([], groups=["wip-services"])
        assert await check_reference("app", OPEN, "lib", CLOSED, service) == "ok"

    async def test_same_namespace_is_never_checked(self):
        assert await check_reference("app", {"isolation_mode": "strict"}, "app", CLOSED, None) == "ok"

    async def test_request_identity_is_used_when_none_is_passed(self):
        token = set_current_identity(_key(["app", "lib"], {"app": "write"}))
        try:
            assert await check_reference("app", OPEN, "lib", CLOSED) == "ok"
        finally:
            reset_current_identity(token)

    async def test_no_identity_is_refused(self):
        assert await check_reference("app", OPEN, "lib", CLOSED) == "inbound"


async def test_unreadable_permission_refuses_rather_than_passes():
    jwt_user = UserIdentity(
        user_id="u@x", username="u", auth_method="jwt", groups=[], raw_claims={},
    )
    with (
        patch(
            "wip_auth.reference_policy.resolve_permission",
            new=AsyncMock(return_value=PERMISSION_UNAVAILABLE),
        ),
        pytest.raises(PermissionUnavailableError),
    ):
        await caller_can_read("lib", jwt_user)

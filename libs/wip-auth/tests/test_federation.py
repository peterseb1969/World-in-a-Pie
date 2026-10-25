"""Groups of logins federated through a Dex connector.

wip-admins and wip-services stay with local users: a login through a
connector never keeps them, whatever its provider sends. Group mappings
grant other groups by verified email or domain. Local Dex users are
untouched, and without a policy every token's groups stay as issued.
"""

from __future__ import annotations

import base64
import json

import pytest

from wip_auth.federation import FederatedGroupPolicy, connector_id
from wip_auth.providers.oidc import OIDCProvider

# The `sub` of a real Dex ID token for the local user admin-001
# (connector "local"), taken from a dev install.
LOCAL_ADMIN_SUB = "CglhZG1pbi0wMDESBWxvY2Fs"


def dex_sub(user_id: str, connector: str) -> str:
    """Dex's subject encoding: protobuf {1: user_id, 2: connector_id}, base64url."""
    raw = b"\n" + bytes([len(user_id)]) + user_id.encode() + b"\x12" + bytes([len(connector)]) + connector.encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


POLICY = FederatedGroupPolicy.from_json(json.dumps({
    "local_connectors": ["local"],
    "reserved_groups": ["wip-admins", "wip-services"],
    "mappings": [
        {"email_domain": "corp.com", "group": "wip-editors"},
        {"email": "Ana@Else.org", "group": "wip-viewers"},
    ],
}))
assert POLICY is not None


class TestConnectorId:
    def test_real_dex_subject(self) -> None:
        assert connector_id({"sub": LOCAL_ADMIN_SUB}) == "local"

    def test_encoder_matches_dex(self) -> None:
        assert dex_sub("admin-001", "local") == LOCAL_ADMIN_SUB

    def test_federated_claims_win(self) -> None:
        claims = {"sub": dex_sub("u", "local"), "federated_claims": {"connector_id": "google"}}
        assert connector_id(claims) == "google"

    @pytest.mark.parametrize("sub", ["", "not base64!", "plain-user-id", base64.urlsafe_b64encode(b"\x08\x01").decode()])
    def test_unreadable_subject_is_unknown(self, sub: str) -> None:
        assert connector_id({"sub": sub}) is None


class TestPolicy:
    def test_local_user_keeps_reserved_groups(self) -> None:
        assert POLICY.apply({"sub": LOCAL_ADMIN_SUB}, ["wip-admins"]) == ["wip-admins"]

    @pytest.mark.parametrize("group", ["wip-admins", "wip-services"])
    def test_federated_user_never_keeps_a_reserved_group(self, group: str) -> None:
        claims = {"sub": dex_sub("1234", "google"), "email": "x@other.org"}
        assert POLICY.apply(claims, [group, "team-a"]) == ["team-a"]

    def test_unknown_connector_is_treated_as_federated(self) -> None:
        assert POLICY.apply({"sub": "opaque"}, ["wip-admins"]) == []

    def test_domain_and_exact_mappings(self) -> None:
        google = dex_sub("1", "google")
        assert POLICY.apply({"sub": google, "email": "bob@Corp.com"}, []) == ["wip-editors"]
        assert POLICY.apply({"sub": google, "email": "ana@else.org"}, []) == ["wip-viewers"]
        assert POLICY.apply({"sub": google, "email": "bob@notcorp.com"}, []) == []
        assert POLICY.apply({"sub": google, "email": "bob@sub.corp.com"}, []) == []

    def test_mapping_needs_a_verified_email(self) -> None:
        claims = {"sub": dex_sub("1", "google"), "email": "bob@corp.com", "email_verified": False}
        assert POLICY.apply(claims, []) == []

    def test_mapped_group_is_not_duplicated(self) -> None:
        claims = {"sub": dex_sub("1", "google"), "email": "bob@corp.com"}
        assert POLICY.apply(claims, ["wip-editors"]) == ["wip-editors"]

    def test_local_user_gets_no_mapping(self) -> None:
        claims = {"sub": LOCAL_ADMIN_SUB, "email": "admin@corp.com"}
        assert POLICY.apply(claims, ["wip-viewers"]) == ["wip-viewers"]

    @pytest.mark.parametrize("text", [None, "", "   "])
    def test_no_policy(self, text: str | None) -> None:
        assert FederatedGroupPolicy.from_json(text) is None


class TestOIDCProvider:
    def _identity(self, claims: dict, policy: FederatedGroupPolicy | None):
        provider = OIDCProvider(issuer_url="https://h/dex", federated_groups=policy)
        return provider._extract_identity(claims)

    def test_federated_token_is_filtered(self) -> None:
        claims = {"sub": dex_sub("9", "github"), "email": "bob@corp.com",
                  "groups": ["wip-admins", "devs"]}
        assert self._identity(claims, POLICY).groups == ["devs", "wip-editors"]

    def test_without_a_policy_groups_stay_as_issued(self) -> None:
        claims = {"sub": dex_sub("9", "github"), "groups": ["wip-admins"]}
        assert self._identity(claims, None).groups == ["wip-admins"]

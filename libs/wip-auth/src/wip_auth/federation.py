"""Groups of logins federated through a Dex connector.

Dex can federate logins from external identity providers (its
connectors). Their users' groups come from the provider, so two rules
apply to them and to nobody else:

- **Reserved groups stay local.** wip-admins and wip-services bypass
  namespace authorization; only the install's own Dex users and API keys
  hold them. A federated login never keeps them, whatever its provider
  sends.
- **Group mappings add groups.** The deployment maps federated users to
  WIP groups by exact email or by email domain, since most providers send
  no groups WIP knows. A mapping trusts the email, so it applies only when
  the token does not mark the email unverified (`email_verified: false`).

The policy is the JSON in WIP_AUTH_FEDERATED_GROUPS, rendered by the
deployer only when the install declares a connector. Without it, every
token's groups stay exactly as issued, so an install that uses another
OIDC provider directly is unaffected.

Which connector authenticated a token: Dex puts it in `federated_claims`
when the client asks for the `federated:id` scope, and always encodes it in
`sub` (base64url of a protobuf message: field 1 the user id, field 2 the
connector id). A token whose connector cannot be read is treated as
federated, so a reserved group is never kept by default.

The auth-gateway, which does not depend on this library, carries a copy
of `connector_id` and `FederatedGroupPolicy.apply`; a parity test there
holds the two to the same behaviour.
"""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any


def connector_id(claims: Mapping[str, Any]) -> str | None:
    """The Dex connector that authenticated these claims, or None if unknown."""
    federated = claims.get("federated_claims")
    if isinstance(federated, Mapping) and isinstance(federated.get("connector_id"), str):
        return str(federated["connector_id"])
    sub = claims.get("sub")
    if not isinstance(sub, str) or not sub:
        return None
    try:
        raw = base64.urlsafe_b64decode(sub + "=" * (-len(sub) % 4))
    except (binascii.Error, ValueError):
        return None
    return _protobuf_string_field(raw, 2)


def _protobuf_string_field(raw: bytes, wanted: int) -> str | None:
    """The first length-delimited field `wanted` of a protobuf message."""
    pos = 0
    while pos < len(raw):
        key, pos = _varint(raw, pos)
        if key is None:
            return None
        field, wire_type = key >> 3, key & 7
        if wire_type != 2:  # Dex's subject holds only string fields
            return None
        length, pos = _varint(raw, pos)
        if length is None or pos + length > len(raw):
            return None
        if field == wanted:
            try:
                return raw[pos : pos + length].decode()
            except UnicodeDecodeError:
                return None
        pos += length
    return None


def _varint(raw: bytes, pos: int) -> tuple[int | None, int]:
    value, shift = 0, 0
    while pos < len(raw) and shift < 64:
        byte = raw[pos]
        pos += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, pos
        shift += 7
    return None, pos


@dataclass(frozen=True)
class GroupMapping:
    group: str
    email: str | None = None
    email_domain: str | None = None

    def matches(self, email: str) -> bool:
        if self.email is not None:
            return email == self.email.casefold()
        return self.email_domain is not None and email.endswith("@" + self.email_domain.casefold())


@dataclass(frozen=True)
class FederatedGroupPolicy:
    local_connectors: frozenset[str]
    reserved_groups: frozenset[str]
    mappings: tuple[GroupMapping, ...]

    @classmethod
    def from_json(cls, text: str | None) -> FederatedGroupPolicy | None:
        """The policy in WIP_AUTH_FEDERATED_GROUPS, or None when unset/empty."""
        if not text or not text.strip():
            return None
        data = json.loads(text)
        return cls(
            local_connectors=frozenset(data.get("local_connectors") or ["local"]),
            reserved_groups=frozenset(data.get("reserved_groups") or []),
            mappings=tuple(
                GroupMapping(
                    group=m["group"], email=m.get("email"), email_domain=m.get("email_domain")
                )
                for m in data.get("mappings") or []
            ),
        )

    def apply(self, claims: Mapping[str, Any], groups: Iterable[str]) -> list[str]:
        """The groups a token's holder gets: unchanged for a local user;
        for a federated one, the provider's groups minus the reserved ones,
        plus every matching mapping."""
        groups = list(groups)
        if connector_id(claims) in self.local_connectors:
            return groups
        out = [g for g in groups if g not in self.reserved_groups]
        email = str(claims.get("email") or "").casefold()
        if email and claims.get("email_verified") is not False:
            for m in self.mappings:
                if m.matches(email) and m.group not in out and m.group not in self.reserved_groups:
                    out.append(m.group)
        return out

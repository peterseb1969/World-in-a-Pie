"""Trusted header authentication provider.

Accepts user identity from gateway/proxy headers (X-WIP-User, X-WIP-Groups).
Requires a privileged X-API-Key alongside the identity headers: the key proves
the request came through a trusted proxy, not a direct client. Only keys in a
privileged group (the gateway's and services' platform key) may assert an
identity. A namespace-scoped key is any app or agent's credential, not a
proxy's, and the groups an asserted identity carries are taken as given — so
accepting it would let its holder claim any group, wip-admins included.
"""

import logging

from fastapi import Request

from ..models import APIKeyRecord, UserIdentity
from .api_key import verify_api_key

logger = logging.getLogger("wip_auth.trusted_header")

# Groups whose keys may assert a user identity on a request.
PROXY_KEY_GROUPS = frozenset({"wip-admins", "wip-services"})


class TrustedHeaderProvider:
    """Accept user identity from trusted gateway/proxy headers.

    This provider checks for X-WIP-User AND validates X-API-Key in one step.
    Both must be present and valid for authentication to succeed. This prevents
    clients from spoofing identity headers without a valid service key.

    Headers:
        X-WIP-User: User email (e.g., "admin@wip.local")
        X-WIP-Groups: Comma-separated group list (e.g., "wip-admins,wip-editors")
        X-API-Key: Valid service API key (proves request came through trusted proxy)
    """

    def __init__(
        self,
        keys: list[APIKeyRecord],
        header_name: str = "X-API-Key",
        hash_salt: str = "wip_auth_salt",
        default_groups: list[str] | None = None,
    ):
        self._keys = [k for k in keys if k.enabled]
        self.header_name = header_name
        self.hash_salt = hash_salt
        self.default_groups = default_groups or ["wip-users"]

    def _proxy_key(self, api_key: str) -> APIKeyRecord | None:
        """The matching, unexpired key record if it may assert identities."""
        for record in self._keys:
            if verify_api_key(
                api_key, record.key_hash, self.hash_salt
            ) and not record.is_expired():
                return record if PROXY_KEY_GROUPS.intersection(record.groups) else None
        return None

    async def authenticate(self, request: Request) -> UserIdentity | None:
        """Authenticate via trusted proxy headers + API key.

        Returns UserIdentity if X-WIP-User and valid X-API-Key are both present.
        Returns None otherwise (falls through to next provider).
        """
        user = request.headers.get("x-wip-user")
        if not user:
            return None

        # X-WIP-User is present — require a privileged key to trust it. Any
        # other key falls through and authenticates as itself.
        api_key = request.headers.get(self.header_name.lower())
        if not api_key or self._proxy_key(api_key) is None:
            logger.warning(
                "X-WIP-User header present without a privileged API key — "
                "ignoring identity headers (possible spoofing attempt)"
            )
            return None

        # Parse groups
        groups_header = request.headers.get("x-wip-groups", "")
        groups = [g.strip() for g in groups_header.split(",") if g.strip()]

        identity = UserIdentity(
            user_id=user,
            username=user.split("@")[0] if "@" in user else user,
            email=user if "@" in user else None,
            groups=groups or self.default_groups,
            auth_method="gateway_oidc",
            provider="trusted_header",
        )

        logger.info(
            "Trusted header auth: user=%s groups=%s",
            user,
            ",".join(identity.groups),
        )

        return identity

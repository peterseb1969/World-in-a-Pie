"""Trusted header authentication provider.

Accepts user identity from gateway/proxy headers (X-WIP-User, X-WIP-Groups).
Requires a valid X-API-Key alongside the identity headers to prevent spoofing —
the API key proves the request came through a trusted proxy, not a direct client.
"""

import logging

from fastapi import Request

from ..models import APIKeyRecord, UserIdentity
from .api_key import verify_api_key

logger = logging.getLogger("wip_auth.trusted_header")


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

    def _validate_api_key(self, api_key: str) -> bool:
        """Check if the API key matches any registered key."""
        for record in self._keys:
            if verify_api_key(api_key, record.key_hash, self.hash_salt):
                if not record.is_expired():
                    return True
        return False

    async def authenticate(self, request: Request) -> UserIdentity | None:
        """Authenticate via trusted proxy headers + API key.

        Returns UserIdentity if X-WIP-User and valid X-API-Key are both present.
        Returns None otherwise (falls through to next provider).
        """
        user = request.headers.get("x-wip-user")
        if not user:
            return None

        # X-WIP-User is present — require a valid API key to trust it
        api_key = request.headers.get(self.header_name.lower())
        if not api_key or not self._validate_api_key(api_key):
            logger.warning(
                "X-WIP-User header present but API key missing/invalid — "
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

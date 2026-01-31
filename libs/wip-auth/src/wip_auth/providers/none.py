"""No-auth provider for development and testing."""

from fastapi import Request

from ..models import UserIdentity


class NoAuthProvider:
    """Authentication provider that always returns an anonymous identity.

    Use this provider when auth_mode is "none" for development/testing.
    All requests are allowed through with an anonymous identity.

    The anonymous identity has:
    - user_id: "anonymous"
    - username: "anonymous"
    - groups: configured default_groups
    - auth_method: "none"

    Example:
        provider = NoAuthProvider(default_groups=["wip-users"])
        identity = await provider.authenticate(request)
        # Returns anonymous identity for any request
    """

    def __init__(self, default_groups: list[str] | None = None):
        """Initialize the no-auth provider.

        Args:
            default_groups: Groups to assign to anonymous users
        """
        self.default_groups = default_groups or ["wip-users"]

    async def authenticate(self, request: Request) -> UserIdentity:
        """Always return an anonymous identity.

        Args:
            request: The FastAPI request (ignored)

        Returns:
            Anonymous UserIdentity
        """
        return UserIdentity(
            user_id="anonymous",
            username="anonymous",
            email=None,
            groups=self.default_groups,
            auth_method="none",
            provider="none",
        )

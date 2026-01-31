"""Base protocol for authentication providers."""

from typing import Protocol, runtime_checkable

from fastapi import Request

from ..models import UserIdentity


@runtime_checkable
class AuthProvider(Protocol):
    """Protocol for authentication providers.

    All auth providers must implement this interface. The authenticate method
    extracts credentials from the request and returns a UserIdentity if valid.

    Providers should:
    - Return UserIdentity on successful authentication
    - Return None if no credentials found (let other providers try)
    - Raise HTTPException for invalid/expired credentials

    Example implementation:
        class MyProvider:
            async def authenticate(self, request: Request) -> UserIdentity | None:
                token = request.headers.get("X-My-Token")
                if not token:
                    return None  # No credentials, try next provider
                if not validate(token):
                    raise HTTPException(401, "Invalid token")
                return UserIdentity(user_id="...", username="...", auth_method="jwt")
    """

    async def authenticate(self, request: Request) -> UserIdentity | None:
        """Attempt to authenticate the request.

        Args:
            request: The FastAPI request object

        Returns:
            UserIdentity if authentication successful, None if no credentials found

        Raises:
            HTTPException: If credentials are present but invalid
        """
        ...

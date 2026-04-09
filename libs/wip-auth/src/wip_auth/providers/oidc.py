"""OIDC/JWT authentication provider."""

import time
from typing import Any

import httpx
import jwt
from fastapi import HTTPException, Request

from ..models import UserIdentity


class JWKSCache:
    """Cache for JWKS (JSON Web Key Set) keys.

    Fetches and caches public keys from an OIDC provider's JWKS endpoint.
    Keys are refreshed when expired or when validation fails with cached keys.
    """

    def __init__(self, jwks_url: str, cache_ttl: int = 3600):
        """Initialize the JWKS cache.

        Args:
            jwks_url: URL of the JWKS endpoint
            cache_ttl: Cache time-to-live in seconds (default 1 hour)
        """
        self.jwks_url = jwks_url
        self.cache_ttl = cache_ttl
        self._keys: dict[str, dict] = {}
        self._last_fetch: float = 0
        self._client: httpx.AsyncClient | None = None

    async def _ensure_client(self) -> httpx.AsyncClient:
        """Get or create the HTTP client."""
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=10.0)
        return self._client

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None

    async def fetch_keys(self, force: bool = False) -> None:
        """Fetch JWKS from the endpoint.

        Args:
            force: Force refresh even if cache is valid
        """
        now = time.time()

        # Check if cache is still valid
        if not force and self._keys and (now - self._last_fetch) < self.cache_ttl:
            return

        client = await self._ensure_client()
        try:
            response = await client.get(self.jwks_url)
            response.raise_for_status()
            jwks = response.json()

            # Index keys by kid (key ID)
            self._keys = {}
            for key in jwks.get("keys", []):
                if kid := key.get("kid"):
                    self._keys[kid] = key

            self._last_fetch = now

        except httpx.HTTPError as e:
            # If we have cached keys, keep using them
            if self._keys:
                return
            raise RuntimeError(f"Failed to fetch JWKS from {self.jwks_url}: {e}") from e

    async def get_key(self, kid: str) -> dict | None:
        """Get a public key by ID.

        Args:
            kid: The key ID from the JWT header

        Returns:
            The JWK dict or None if not found
        """
        await self.fetch_keys()

        if kid in self._keys:
            return self._keys[kid]

        # Key not found - try refreshing in case keys were rotated
        await self.fetch_keys(force=True)
        return self._keys.get(kid)


class OIDCProvider:
    """Authentication provider for OIDC/JWT authentication.

    Validates JWT tokens from the Authorization header against a JWKS endpoint.
    Works with any OIDC-compliant provider (Authelia, Authentik, Zitadel, etc.).

    Features:
    - Automatic JWKS key fetching and caching
    - Standard claim extraction (sub, preferred_username, email, groups)
    - Configurable audience and issuer validation
    - Support for custom claims

    Example:
        provider = OIDCProvider(
            issuer_url="http://authelia:9091",
            audience="wip",
        )
        identity = await provider.authenticate(request)
    """

    def __init__(
        self,
        issuer_url: str | None = None,
        jwks_url: str | None = None,
        audience: str = "wip",
        algorithms: list[str] | None = None,
        groups_claim: str = "groups",
        default_groups: list[str] | None = None,
        verify_exp: bool = True,
        leeway: int = 30,
    ):
        """Initialize the OIDC provider.

        Args:
            issuer_url: OIDC issuer URL (used to derive JWKS URL if not provided)
            jwks_url: Explicit JWKS endpoint URL
            audience: Expected audience claim value
            algorithms: Allowed JWT algorithms (default: RS256, ES256)
            groups_claim: Claim name containing user groups
            default_groups: Groups to assign if not in token
            verify_exp: Whether to verify token expiration
            leeway: Clock skew tolerance in seconds
        """
        # Determine JWKS URL
        if jwks_url:
            self._jwks_url = jwks_url
        elif issuer_url:
            self._jwks_url = f"{issuer_url.rstrip('/')}/.well-known/jwks.json"
        else:
            raise ValueError("Either issuer_url or jwks_url must be provided")

        self.issuer_url = issuer_url
        self.audience = audience
        self.algorithms = algorithms or ["RS256", "ES256"]
        self.groups_claim = groups_claim
        self.default_groups = default_groups or []
        self.verify_exp = verify_exp
        self.leeway = leeway

        self._jwks_cache = JWKSCache(self._jwks_url)

    async def close(self) -> None:
        """Clean up resources."""
        await self._jwks_cache.close()

    def _get_token_from_header(self, request: Request) -> str | None:
        """Extract JWT from Authorization header.

        Args:
            request: The FastAPI request

        Returns:
            The JWT string or None if not present
        """
        auth_header = request.headers.get("Authorization")
        if not auth_header:
            return None

        parts = auth_header.split()
        if len(parts) != 2 or parts[0].lower() != "bearer":
            return None

        return parts[1]

    async def _get_signing_key(self, token: str) -> dict:
        """Get the signing key for a token.

        Args:
            token: The JWT string

        Returns:
            The JWK dict for verifying the token

        Raises:
            HTTPException: If key cannot be found
        """
        try:
            # Decode header without verification to get kid
            header = jwt.get_unverified_header(token)
        except jwt.exceptions.DecodeError as e:
            raise HTTPException(
                status_code=401,
                detail=f"Invalid token format: {e}",
            ) from e

        kid = header.get("kid")
        if not kid:
            raise HTTPException(
                status_code=401,
                detail="Token missing key ID (kid)",
            )

        key = await self._jwks_cache.get_key(kid)
        if not key:
            raise HTTPException(
                status_code=401,
                detail=f"Unknown signing key: {kid}",
            )

        return key

    def _extract_identity(self, claims: dict[str, Any]) -> UserIdentity:
        """Extract UserIdentity from JWT claims.

        Args:
            claims: Decoded JWT claims

        Returns:
            UserIdentity from the claims
        """
        # Standard OIDC claims
        user_id = claims.get("sub", "unknown")
        username = claims.get("preferred_username") or claims.get("name") or user_id
        email = claims.get("email")

        # Groups - check configured claim and common alternatives
        groups = claims.get(self.groups_claim)
        if groups is None:
            groups = claims.get("roles") or claims.get("role")
        if groups is None:
            groups = self.default_groups
        if isinstance(groups, str):
            groups = [groups]


        return UserIdentity(
            user_id=user_id,
            username=username,
            email=email,
            groups=groups,
            auth_method="jwt",
            provider=self.issuer_url or "oidc",
            raw_claims=claims,
        )

    async def authenticate(self, request: Request) -> UserIdentity | None:
        """Authenticate the request using JWT.

        Args:
            request: The FastAPI request

        Returns:
            UserIdentity if authenticated, None if no Authorization header

        Raises:
            HTTPException: If token is present but invalid
        """
        token = self._get_token_from_header(request)

        if token is None:
            # No Authorization header - let other providers try
            return None

        # Get the signing key
        signing_key = await self._get_signing_key(token)

        # Build PyJWT key from JWK
        public_key: Any
        try:
            public_key = jwt.algorithms.RSAAlgorithm.from_jwk(signing_key)
        except Exception:
            try:
                public_key = jwt.algorithms.ECAlgorithm.from_jwk(signing_key)
            except Exception as e:
                raise HTTPException(
                    status_code=401,
                    detail=f"Unsupported key type: {e}",
                ) from e

        # Verify and decode the token
        try:
            claims = jwt.decode(
                token,
                public_key,
                algorithms=self.algorithms,
                audience=self.audience,
                issuer=self.issuer_url,  # Optional issuer validation
                leeway=self.leeway,
                options={
                    "verify_exp": self.verify_exp,
                    "verify_aud": bool(self.audience),
                    "verify_iss": bool(self.issuer_url),
                },
            )
        except jwt.ExpiredSignatureError as e:
            raise HTTPException(
                status_code=401,
                detail="Token has expired",
                headers={"WWW-Authenticate": "Bearer error=\"invalid_token\""},
            ) from e
        except jwt.InvalidAudienceError as e:
            raise HTTPException(
                status_code=401,
                detail="Invalid token audience",
                headers={"WWW-Authenticate": "Bearer error=\"invalid_token\""},
            ) from e
        except jwt.InvalidIssuerError as e:
            raise HTTPException(
                status_code=401,
                detail="Invalid token issuer",
                headers={"WWW-Authenticate": "Bearer error=\"invalid_token\""},
            ) from e
        except jwt.PyJWTError as e:
            raise HTTPException(
                status_code=401,
                detail=f"Token validation failed: {e}",
                headers={"WWW-Authenticate": "Bearer error=\"invalid_token\""},
            ) from e

        return self._extract_identity(claims)

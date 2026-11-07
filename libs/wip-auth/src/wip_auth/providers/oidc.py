"""OIDC/JWT authentication provider."""

import asyncio
import logging
import time
from typing import Any

import httpx
import jwt
from fastapi import HTTPException, Request

from ..federation import FederatedGroupPolicy
from ..models import UserIdentity

logger = logging.getLogger(__name__)


# How often the signing keys are re-read from the identity provider.
_REFRESH_SECONDS = 300.0
# How long a copy may be used without a successful re-read. Past this the copy
# is not trusted (a key the provider retired, perhaps because it leaked, would
# otherwise keep validating tokens for as long as the provider is unreachable),
# and token validation answers 503 until the provider answers again.
_MAX_KEY_AGE_SECONDS = 3600.0
# The least time between two fetch attempts, whatever prompted them: a failed
# attempt, the refresh interval, or a token naming a key id the copy lacks.
# The last is reachable before any signature is checked, so without this any
# caller could make every service fetch on every request.
_MIN_ATTEMPT_INTERVAL_SECONDS = 10.0


class JWKSCache:
    """The identity provider's signing keys (JWKS), re-read on a schedule.

    Keys are re-read every ``refresh_seconds``, and on demand when a token
    names a key id the copy does not have (the provider rotated its keys), but
    never more often than ``min_attempt_interval`` apart. A copy that could not
    be re-read for ``max_key_age`` is no longer used: ``get_key`` raises
    ``RuntimeError``, which the provider answers with 503. A failed or
    malformed response never replaces the last good copy.
    """

    def __init__(
        self,
        jwks_url: str,
        cache_ttl: float = _REFRESH_SECONDS,
        *,
        max_key_age: float = _MAX_KEY_AGE_SECONDS,
        min_attempt_interval: float = _MIN_ATTEMPT_INTERVAL_SECONDS,
    ):
        """Initialize the JWKS cache.

        Args:
            jwks_url: URL of the JWKS endpoint
            cache_ttl: how often the keys are re-read, in seconds
            max_key_age: how long a copy may be used without a successful
                re-read, in seconds (at least ``cache_ttl``)
            min_attempt_interval: least time between two fetch attempts
        """
        self.jwks_url = jwks_url
        self.cache_ttl = cache_ttl
        self.max_key_age = max(max_key_age, cache_ttl)
        self.min_attempt_interval = min_attempt_interval
        self._keys: dict[str, dict] = {}
        self._last_fetch: float | None = None  # last success (monotonic)
        self._last_attempt: float | None = None
        self._last_error: str | None = None
        self._client: httpx.AsyncClient | None = None
        self._lock: asyncio.Lock | None = None

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

    def _age(self, now: float) -> float | None:
        return None if self._last_fetch is None else now - self._last_fetch

    def _may_attempt(self, now: float) -> bool:
        return self._last_attempt is None or now - self._last_attempt >= self.min_attempt_interval

    async def fetch_keys(self, force: bool = False) -> None:
        """Re-read the keys if they are due (or ``force``), within the attempt
        limit, then check the copy may still be used.

        Raises:
            RuntimeError: no usable copy — never fetched, or older than
                ``max_key_age`` and the provider cannot be read.
        """
        now = time.monotonic()
        age = self._age(now)
        due = force or age is None or age >= self.cache_ttl
        if due and self._may_attempt(now):
            if self._lock is None:
                self._lock = asyncio.Lock()
            async with self._lock:
                # Another request may have fetched while this one waited.
                now = time.monotonic()
                if self._may_attempt(now):
                    await self._attempt(now)
        self._check_usable(time.monotonic())

    async def _attempt(self, now: float) -> None:
        self._last_attempt = now
        client = await self._ensure_client()
        try:
            response = await client.get(self.jwks_url)
            response.raise_for_status()
            jwks = response.json()
            if not isinstance(jwks, dict) or not isinstance(jwks.get("keys"), list):
                raise ValueError("the response is not a JWKS document")
            keys = {
                key["kid"]: key
                for key in jwks["keys"]
                if isinstance(key, dict) and isinstance(key.get("kid"), str) and key["kid"]
            }
            if not keys:
                raise ValueError("the JWKS document holds no keys")
        except (httpx.HTTPError, ValueError) as e:
            # The last good copy stays; whether it may still be used is
            # _check_usable's call.
            self._last_error = str(e) or type(e).__name__
            logger.warning("JWKS fetch from %s failed: %s", self.jwks_url, self._last_error)
            return
        self._keys = keys
        self._last_fetch = now
        self._last_error = None

    def _check_usable(self, now: float) -> None:
        age = self._age(now)
        if age is None:
            raise RuntimeError(
                f"Failed to fetch JWKS from {self.jwks_url}: {self._last_error or 'not fetched yet'}"
            )
        if age > self.max_key_age:
            raise RuntimeError(
                f"JWKS from {self.jwks_url} could not be re-read for {age:.0f}s "
                f"(limit {self.max_key_age:.0f}s): {self._last_error or 'no answer'}"
            )

    async def get_key(self, kid: str) -> dict | None:
        """Get a public key by ID.

        A key id the copy lacks prompts one re-read (the provider may have
        rotated its keys), within the attempt limit.

        Returns:
            The JWK dict or None if not found

        Raises:
            RuntimeError: the copy cannot be used (see ``fetch_keys``)
        """
        await self.fetch_keys()
        if kid in self._keys:
            return self._keys[kid]
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
        audience: str | list[str] = "wip",
        algorithms: list[str] | None = None,
        groups_claim: str = "groups",
        default_groups: list[str] | None = None,
        verify_exp: bool = True,
        leeway: int = 30,
        federated_groups: FederatedGroupPolicy | None = None,
    ):
        """Initialize the OIDC provider.

        Args:
            issuer_url: OIDC issuer URL (used to derive JWKS URL if not provided)
            jwks_url: Explicit JWKS endpoint URL
            audience: Accepted audience claim value, or a list of them (a
                token is accepted when its aud matches any entry)
            algorithms: Allowed JWT algorithms (default: RS256, ES256)
            groups_claim: Claim name containing user groups
            default_groups: Groups to assign if not in token
            verify_exp: Whether to verify token expiration
            leeway: Clock skew tolerance in seconds
            federated_groups: Group policy for logins federated through a
                Dex connector (see wip_auth.federation); None leaves every
                token's groups as issued
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
        self.federated_groups = federated_groups
        # Set by create_providers_from_config when the policy file is watched.
        self.policy_watcher: Any = None

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

        try:
            key = await self._jwks_cache.get_key(kid)
        except RuntimeError as e:
            # The signing keys are unreachable, so the token can be neither
            # accepted nor called invalid. Refuse it as a service failure,
            # never fall through to another provider.
            logger.error("JWT rejected: %s", e)
            raise HTTPException(
                status_code=503,
                detail="Identity provider unavailable: cannot verify token",
            ) from e
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
        if self.federated_groups is not None:
            groups = self.federated_groups.apply(claims, groups)


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

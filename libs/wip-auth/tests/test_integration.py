"""Integration tests for wip-auth with FastAPI."""

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from wip_auth import (
    APIKeyProvider,
    APIKeyRecord,
    AuthConfig,
    NoAuthProvider,
    UserIdentity,
    create_auth_middleware,
    hash_api_key,
    optional_identity,
    require_admin,
    require_api_key,
    require_groups,
    require_identity,
    reset_auth_config,
    set_auth_config,
    setup_auth,
)


@pytest.fixture(autouse=True)
def reset_config():
    """Reset config before and after each test."""
    reset_auth_config()
    yield
    reset_auth_config()


class TestMiddlewareIntegration:
    """Tests for auth middleware with FastAPI."""

    def test_middleware_with_no_auth_provider(self):
        """NoAuthProvider should allow all requests."""
        app = FastAPI()
        middleware_class = create_auth_middleware([NoAuthProvider()])
        app.add_middleware(middleware_class)

        @app.get("/test")
        async def test_route(identity: UserIdentity = Depends(require_identity())):
            return {"user": identity.username}

        client = TestClient(app)
        response = client.get("/test")

        assert response.status_code == 200
        assert response.json()["user"] == "anonymous"

    def test_middleware_with_api_key_provider(self):
        """APIKeyProvider should validate API keys."""
        keys = [APIKeyRecord(name="test", key_hash=hash_api_key("secret"))]
        provider = APIKeyProvider(keys)

        app = FastAPI()
        middleware_class = create_auth_middleware([provider])
        app.add_middleware(middleware_class)

        @app.get("/test")
        async def test_route(identity: UserIdentity = Depends(require_identity())):
            return {"user": identity.username}

        client = TestClient(app)

        # Without key - should fail
        response = client.get("/test")
        assert response.status_code == 401

        # With valid key - should succeed
        response = client.get("/test", headers={"X-API-Key": "secret"})
        assert response.status_code == 200
        assert response.json()["user"] == "test"

    def test_middleware_with_multiple_providers(self):
        """Middleware should try providers in order."""
        keys = [APIKeyRecord(name="api", key_hash=hash_api_key("api_secret"))]
        providers = [
            APIKeyProvider(keys),
            NoAuthProvider(),
        ]

        app = FastAPI()
        middleware_class = create_auth_middleware(providers)
        app.add_middleware(middleware_class)

        @app.get("/test")
        async def test_route(identity: UserIdentity = Depends(require_identity())):
            return {"user": identity.username, "method": identity.auth_method}

        client = TestClient(app)

        # With API key - should use APIKeyProvider
        response = client.get("/test", headers={"X-API-Key": "api_secret"})
        assert response.json()["method"] == "api_key"

        # Without key - should fall through to NoAuthProvider
        response = client.get("/test")
        assert response.json()["method"] == "none"


class TestDependencies:
    """Tests for FastAPI dependencies."""

    @pytest.fixture
    def app_with_auth(self):
        """Create app with API key auth."""
        keys = [
            APIKeyRecord(
                name="admin-key",
                key_hash=hash_api_key("admin_secret"),
                groups=["wip-admins"],
            ),
            APIKeyRecord(
                name="user-key",
                key_hash=hash_api_key("user_secret"),
                groups=["wip-users"],
            ),
        ]
        provider = APIKeyProvider(keys)

        app = FastAPI()
        middleware_class = create_auth_middleware([provider])
        app.add_middleware(middleware_class)

        @app.get("/public")
        async def public_route(identity: UserIdentity | None = Depends(optional_identity())):
            if identity:
                return {"user": identity.username}
            return {"user": None}

        @app.get("/protected")
        async def protected_route(identity: UserIdentity = Depends(require_identity())):
            return {"user": identity.username}

        @app.get("/admin")
        async def admin_route(identity: UserIdentity = Depends(require_admin())):
            return {"admin": identity.username}

        @app.get("/groups")
        async def groups_route(
            identity: UserIdentity = Depends(require_groups(["wip-editors", "wip-admins"]))
        ):
            return {"user": identity.username}

        return TestClient(app)

    def test_optional_identity_without_auth(self, app_with_auth):
        """optional_identity should return None without auth."""
        response = app_with_auth.get("/public")
        assert response.status_code == 200
        assert response.json()["user"] is None

    def test_optional_identity_with_auth(self, app_with_auth):
        """optional_identity should return identity with auth."""
        response = app_with_auth.get("/public", headers={"X-API-Key": "admin_secret"})
        assert response.status_code == 200
        assert response.json()["user"] == "admin-key"

    def test_require_identity_without_auth(self, app_with_auth):
        """require_identity should return 401 without auth."""
        response = app_with_auth.get("/protected")
        assert response.status_code == 401

    def test_require_identity_with_auth(self, app_with_auth):
        """require_identity should succeed with auth."""
        response = app_with_auth.get("/protected", headers={"X-API-Key": "user_secret"})
        assert response.status_code == 200
        assert response.json()["user"] == "user-key"

    def test_require_admin_without_admin_group(self, app_with_auth):
        """require_admin should return 403 without admin group."""
        response = app_with_auth.get("/admin", headers={"X-API-Key": "user_secret"})
        assert response.status_code == 403

    def test_require_admin_with_admin_group(self, app_with_auth):
        """require_admin should succeed with admin group."""
        response = app_with_auth.get("/admin", headers={"X-API-Key": "admin_secret"})
        assert response.status_code == 200
        assert response.json()["admin"] == "admin-key"

    def test_require_groups_with_any_group(self, app_with_auth):
        """require_groups should succeed if user has any required group."""
        response = app_with_auth.get("/groups", headers={"X-API-Key": "admin_secret"})
        assert response.status_code == 200

    def test_require_groups_without_required_groups(self, app_with_auth):
        """require_groups should return 403 without required groups."""
        response = app_with_auth.get("/groups", headers={"X-API-Key": "user_secret"})
        assert response.status_code == 403


class TestSetupAuth:
    """Tests for setup_auth helper."""

    def test_setup_auth_api_key_mode(self):
        """setup_auth should work with api_key_only mode."""
        config = AuthConfig(
            mode="api_key_only",
            legacy_api_key="test_key_123",
        )
        set_auth_config(config)

        app = FastAPI()
        setup_auth(app, config)

        @app.get("/test")
        async def test_route(identity: UserIdentity = Depends(require_identity())):
            return {"user": identity.username}

        client = TestClient(app)

        # Should work with the legacy key
        response = client.get("/test", headers={"X-API-Key": "test_key_123"})
        assert response.status_code == 200
        assert response.json()["user"] == "legacy"

    def test_setup_auth_none_mode(self):
        """setup_auth should work with none mode."""
        config = AuthConfig(mode="none")
        set_auth_config(config)

        app = FastAPI()
        setup_auth(app, config)

        @app.get("/test")
        async def test_route(identity: UserIdentity = Depends(require_identity())):
            return {"user": identity.username}

        client = TestClient(app)

        # Should allow without any credentials
        response = client.get("/test")
        assert response.status_code == 200
        assert response.json()["user"] == "anonymous"


class TestLegacyCompatibility:
    """Tests for backward compatibility."""

    def test_require_api_key_alias(self):
        """require_api_key should work as a direct dependency (no factory call)."""
        keys = [APIKeyRecord(name="test", key_hash=hash_api_key("secret"))]
        provider = APIKeyProvider(keys)

        app = FastAPI()
        middleware_class = create_auth_middleware([provider])
        app.add_middleware(middleware_class)

        @app.get("/test")
        async def test_route(identity: UserIdentity = Depends(require_api_key)):
            return {"user": identity.username}

        client = TestClient(app)

        response = client.get("/test", headers={"X-API-Key": "secret"})
        assert response.status_code == 200
        assert response.json()["user"] == "test"

    def test_identity_string_format(self):
        """Identity string should match expected format.

        JWT priority: email > username > "user:{user_id}"
        """
        # API key identity
        api_identity = UserIdentity(
            user_id="apikey:service",
            username="service",
            auth_method="api_key",
        )
        assert api_identity.identity_string == "apikey:service"

        # JWT identity with email — email wins
        jwt_with_email = UserIdentity(
            user_id="user-123",
            username="john",
            email="john@example.com",
            auth_method="jwt",
        )
        assert jwt_with_email.identity_string == "john@example.com"

        # JWT identity without email — username wins
        jwt_no_email = UserIdentity(
            user_id="user-123",
            username="john",
            auth_method="jwt",
        )
        assert jwt_no_email.identity_string == "john"

        # JWT identity with neither — falls back to user:{id}
        jwt_minimal = UserIdentity(
            user_id="user-123",
            username="",
            auth_method="jwt",
        )
        assert jwt_minimal.identity_string == "user:user-123"

        # Anonymous
        anon_identity = UserIdentity(
            user_id="anonymous",
            username="anonymous",
            auth_method="none",
        )
        assert anon_identity.identity_string == "anonymous"

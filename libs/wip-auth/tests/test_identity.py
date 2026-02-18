"""Tests for identity context management functions."""

import pytest

from wip_auth import (
    UserIdentity,
    clear_current_identity,
    get_actor_info,
    get_current_identity,
    get_identity_owner,
    get_identity_string,
    set_current_identity,
)


@pytest.fixture(autouse=True)
def clean_identity():
    """Ensure identity context is clean before and after each test."""
    clear_current_identity()
    yield
    clear_current_identity()


# ===========================================================================
# get_current_identity / set_current_identity round-trip
# ===========================================================================


class TestIdentityRoundTrip:
    """Test get/set current identity."""

    def test_set_and_get_identity(self):
        """Setting an identity should make it retrievable via get_current_identity."""
        identity = UserIdentity(
            user_id="user-123",
            username="alice",
            email="alice@example.com",
            auth_method="jwt",
            groups=["wip-admins"],
        )

        set_current_identity(identity)
        result = get_current_identity()

        assert result is not None
        assert result.user_id == "user-123"
        assert result.username == "alice"
        assert result is identity

    def test_default_identity_is_none(self):
        """Without setting an identity, get_current_identity should return None."""
        result = get_current_identity()
        assert result is None

    def test_set_identity_to_none(self):
        """Setting identity to None should be retrievable as None."""
        identity = UserIdentity(
            user_id="user-123",
            username="alice",
            auth_method="jwt",
        )
        set_current_identity(identity)
        assert get_current_identity() is not None

        set_current_identity(None)
        assert get_current_identity() is None


# ===========================================================================
# clear_current_identity
# ===========================================================================


class TestClearIdentity:
    """Test clear_current_identity."""

    def test_clear_removes_identity(self):
        """clear_current_identity should reset context to None."""
        identity = UserIdentity(
            user_id="user-123",
            username="alice",
            auth_method="jwt",
        )
        set_current_identity(identity)
        assert get_current_identity() is not None

        clear_current_identity()

        assert get_current_identity() is None

    def test_clear_when_already_none(self):
        """Clearing when no identity is set should not raise."""
        clear_current_identity()
        assert get_current_identity() is None


# ===========================================================================
# get_identity_string
# ===========================================================================


class TestGetIdentityString:
    """Test get_identity_string for various identity types."""

    def test_api_key_user(self):
        """API key user should return 'apikey:<username>'."""
        identity = UserIdentity(
            user_id="apikey:service-bot",
            username="service-bot",
            auth_method="api_key",
        )
        set_current_identity(identity)

        result = get_identity_string()

        assert result == "apikey:service-bot"

    def test_jwt_user_with_email(self):
        """JWT user with email should return the email."""
        identity = UserIdentity(
            user_id="user-456",
            username="bob",
            email="bob@example.com",
            auth_method="jwt",
        )
        set_current_identity(identity)

        result = get_identity_string()

        assert result == "bob@example.com"

    def test_jwt_user_without_email(self):
        """JWT user without email should return the username."""
        identity = UserIdentity(
            user_id="user-789",
            username="charlie",
            auth_method="jwt",
        )
        set_current_identity(identity)

        result = get_identity_string()

        assert result == "charlie"

    def test_anonymous_no_identity_set(self):
        """With no identity set, should return 'anonymous'."""
        result = get_identity_string()

        assert result == "anonymous"

    def test_anonymous_auth_method_none(self):
        """Identity with auth_method='none' should return 'anonymous'."""
        identity = UserIdentity(
            user_id="anonymous",
            username="anonymous",
            auth_method="none",
        )
        set_current_identity(identity)

        result = get_identity_string()

        assert result == "anonymous"


# ===========================================================================
# get_identity_owner
# ===========================================================================


class TestGetIdentityOwner:
    """Test get_identity_owner for various identity types."""

    def test_no_identity_returns_none(self):
        """Should return None when no identity is set."""
        result = get_identity_owner()
        assert result is None

    def test_api_key_returns_owner_from_raw_claims(self):
        """API key identity should return owner from raw_claims."""
        identity = UserIdentity(
            user_id="apikey:service",
            username="service",
            auth_method="api_key",
            raw_claims={"owner": "admin@wip.local"},
        )
        set_current_identity(identity)

        result = get_identity_owner()

        assert result == "admin@wip.local"

    def test_api_key_no_owner_in_claims(self):
        """API key without owner in raw_claims should return None."""
        identity = UserIdentity(
            user_id="apikey:service",
            username="service",
            auth_method="api_key",
            raw_claims={},
        )
        set_current_identity(identity)

        result = get_identity_owner()

        assert result is None

    def test_api_key_no_raw_claims(self):
        """API key without raw_claims should return None."""
        identity = UserIdentity(
            user_id="apikey:service",
            username="service",
            auth_method="api_key",
        )
        set_current_identity(identity)

        result = get_identity_owner()

        assert result is None

    def test_jwt_user_returns_email(self):
        """JWT user with email should return the email as owner."""
        identity = UserIdentity(
            user_id="user-123",
            username="alice",
            email="alice@example.com",
            auth_method="jwt",
        )
        set_current_identity(identity)

        result = get_identity_owner()

        assert result == "alice@example.com"

    def test_jwt_user_no_email_returns_user_id(self):
        """JWT user without email should return user_id as owner."""
        identity = UserIdentity(
            user_id="user-123",
            username="alice",
            auth_method="jwt",
        )
        set_current_identity(identity)

        result = get_identity_owner()

        assert result == "user-123"

    def test_none_auth_method_returns_none(self):
        """Identity with auth_method='none' should return None."""
        identity = UserIdentity(
            user_id="anonymous",
            username="anonymous",
            auth_method="none",
        )
        set_current_identity(identity)

        result = get_identity_owner()

        assert result is None


# ===========================================================================
# get_actor_info
# ===========================================================================


class TestGetActorInfo:
    """Test get_actor_info returns complete dict."""

    def test_no_identity_returns_anonymous_dict(self):
        """Should return anonymous info when no identity is set."""
        result = get_actor_info()

        assert result == {
            "actor": "anonymous",
            "actor_owner": None,
            "auth_method": "none",
        }

    def test_api_key_actor_info(self):
        """Should return complete actor info for API key identity."""
        identity = UserIdentity(
            user_id="apikey:deploy-bot",
            username="deploy-bot",
            auth_method="api_key",
            raw_claims={"owner": "ops@wip.local"},
        )
        set_current_identity(identity)

        result = get_actor_info()

        assert result["actor"] == "apikey:deploy-bot"
        assert result["actor_owner"] == "ops@wip.local"
        assert result["auth_method"] == "api_key"

    def test_jwt_actor_info_with_email(self):
        """Should return complete actor info for JWT identity with email."""
        identity = UserIdentity(
            user_id="user-789",
            username="diana",
            email="diana@example.com",
            auth_method="jwt",
            groups=["wip-editors"],
        )
        set_current_identity(identity)

        result = get_actor_info()

        assert result["actor"] == "diana@example.com"
        assert result["actor_owner"] == "diana@example.com"
        assert result["auth_method"] == "jwt"

    def test_jwt_actor_info_without_email(self):
        """Should use user_id as actor_owner when JWT has no email."""
        identity = UserIdentity(
            user_id="user-999",
            username="eve",
            auth_method="jwt",
        )
        set_current_identity(identity)

        result = get_actor_info()

        assert result["actor"] == "eve"
        assert result["actor_owner"] == "user-999"
        assert result["auth_method"] == "jwt"

    def test_actor_info_has_correct_keys(self):
        """Result dict should always have exactly three keys."""
        # No identity
        result = get_actor_info()
        assert set(result.keys()) == {"actor", "actor_owner", "auth_method"}

        # With identity
        identity = UserIdentity(
            user_id="user-1",
            username="test",
            auth_method="jwt",
        )
        set_current_identity(identity)
        result = get_actor_info()
        assert set(result.keys()) == {"actor", "actor_owner", "auth_method"}

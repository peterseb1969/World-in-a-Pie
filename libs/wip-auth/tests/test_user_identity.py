"""Tests for UserIdentity model methods."""

import pytest

from wip_auth import UserIdentity


# ===========================================================================
# identity_string property
# ===========================================================================


class TestIdentityString:
    """Test UserIdentity.identity_string property."""

    def test_api_key_identity_string(self):
        """API key auth should return 'apikey:<username>'."""
        identity = UserIdentity(
            user_id="apikey:service-bot",
            username="service-bot",
            auth_method="api_key",
        )

        assert identity.identity_string == "apikey:service-bot"

    def test_jwt_identity_string_with_email(self):
        """JWT auth with email should return the email."""
        identity = UserIdentity(
            user_id="user-123",
            username="alice",
            email="alice@example.com",
            auth_method="jwt",
        )

        assert identity.identity_string == "alice@example.com"

    def test_jwt_identity_string_with_username_no_email(self):
        """JWT auth with username but no email should return username."""
        identity = UserIdentity(
            user_id="user-456",
            username="bob",
            auth_method="jwt",
        )

        assert identity.identity_string == "bob"

    def test_jwt_identity_string_no_email_no_username(self):
        """JWT auth with neither email nor username should return 'user:<user_id>'."""
        identity = UserIdentity(
            user_id="user-789",
            username="",
            auth_method="jwt",
        )

        assert identity.identity_string == "user:user-789"

    def test_none_auth_identity_string(self):
        """No-auth method should return 'anonymous'."""
        identity = UserIdentity(
            user_id="anonymous",
            username="anonymous",
            auth_method="none",
        )

        assert identity.identity_string == "anonymous"

    def test_jwt_email_takes_priority_over_username(self):
        """When both email and username are present, email should win for JWT."""
        identity = UserIdentity(
            user_id="user-abc",
            username="alice",
            email="alice@corp.com",
            auth_method="jwt",
        )

        assert identity.identity_string == "alice@corp.com"

    def test_api_key_always_uses_username(self):
        """API key should use username even if email is set."""
        identity = UserIdentity(
            user_id="apikey:svc",
            username="svc",
            email="svc@example.com",
            auth_method="api_key",
        )

        assert identity.identity_string == "apikey:svc"


# ===========================================================================
# has_group
# ===========================================================================


class TestHasGroup:
    """Test UserIdentity.has_group method."""

    def test_has_group_positive(self):
        """Should return True if the group is present."""
        identity = UserIdentity(
            user_id="user-1",
            username="alice",
            auth_method="jwt",
            groups=["wip-admins", "wip-editors"],
        )

        assert identity.has_group("wip-admins") is True
        assert identity.has_group("wip-editors") is True

    def test_has_group_negative(self):
        """Should return False if the group is absent."""
        identity = UserIdentity(
            user_id="user-1",
            username="alice",
            auth_method="jwt",
            groups=["wip-viewers"],
        )

        assert identity.has_group("wip-admins") is False

    def test_has_group_empty_groups(self):
        """Should return False when groups list is empty."""
        identity = UserIdentity(
            user_id="user-1",
            username="alice",
            auth_method="jwt",
            groups=[],
        )

        assert identity.has_group("wip-admins") is False

    def test_has_group_default_groups(self):
        """Default groups (empty list) should not contain any group."""
        identity = UserIdentity(
            user_id="user-1",
            username="alice",
            auth_method="jwt",
        )

        assert identity.has_group("wip-admins") is False


# ===========================================================================
# has_any_group
# ===========================================================================


class TestHasAnyGroup:
    """Test UserIdentity.has_any_group method."""

    def test_has_any_group_matches_one(self):
        """Should return True if at least one group matches."""
        identity = UserIdentity(
            user_id="user-1",
            username="alice",
            auth_method="jwt",
            groups=["wip-editors"],
        )

        assert identity.has_any_group(["wip-admins", "wip-editors"]) is True

    def test_has_any_group_matches_all(self):
        """Should return True when all requested groups are present."""
        identity = UserIdentity(
            user_id="user-1",
            username="alice",
            auth_method="jwt",
            groups=["wip-admins", "wip-editors", "wip-viewers"],
        )

        assert identity.has_any_group(["wip-admins", "wip-editors"]) is True

    def test_has_any_group_no_match(self):
        """Should return False when none of the groups match."""
        identity = UserIdentity(
            user_id="user-1",
            username="alice",
            auth_method="jwt",
            groups=["wip-viewers"],
        )

        assert identity.has_any_group(["wip-admins", "wip-editors"]) is False

    def test_has_any_group_empty_requested(self):
        """Should return False when requesting with empty list."""
        identity = UserIdentity(
            user_id="user-1",
            username="alice",
            auth_method="jwt",
            groups=["wip-admins"],
        )

        assert identity.has_any_group([]) is False

    def test_has_any_group_empty_user_groups(self):
        """Should return False when user has no groups."""
        identity = UserIdentity(
            user_id="user-1",
            username="alice",
            auth_method="jwt",
            groups=[],
        )

        assert identity.has_any_group(["wip-admins"]) is False


# ===========================================================================
# has_all_groups
# ===========================================================================


class TestHasAllGroups:
    """Test UserIdentity.has_all_groups method."""

    def test_has_all_groups_all_present(self):
        """Should return True when all groups are present."""
        identity = UserIdentity(
            user_id="user-1",
            username="alice",
            auth_method="jwt",
            groups=["wip-admins", "wip-editors", "wip-viewers"],
        )

        assert identity.has_all_groups(["wip-admins", "wip-editors"]) is True

    def test_has_all_groups_some_missing(self):
        """Should return False when some groups are missing."""
        identity = UserIdentity(
            user_id="user-1",
            username="alice",
            auth_method="jwt",
            groups=["wip-editors"],
        )

        assert identity.has_all_groups(["wip-admins", "wip-editors"]) is False

    def test_has_all_groups_none_present(self):
        """Should return False when none of the groups are present."""
        identity = UserIdentity(
            user_id="user-1",
            username="alice",
            auth_method="jwt",
            groups=["wip-viewers"],
        )

        assert identity.has_all_groups(["wip-admins", "wip-editors"]) is False

    def test_has_all_groups_exact_match(self):
        """Should return True for exact group match."""
        identity = UserIdentity(
            user_id="user-1",
            username="alice",
            auth_method="jwt",
            groups=["wip-admins", "wip-editors"],
        )

        assert identity.has_all_groups(["wip-admins", "wip-editors"]) is True

    def test_has_all_groups_empty_requested(self):
        """Should return True when requesting with empty list (vacuous truth)."""
        identity = UserIdentity(
            user_id="user-1",
            username="alice",
            auth_method="jwt",
            groups=["wip-admins"],
        )

        assert identity.has_all_groups([]) is True

    def test_has_all_groups_empty_user_groups(self):
        """Should return False when user has no groups and some are requested."""
        identity = UserIdentity(
            user_id="user-1",
            username="alice",
            auth_method="jwt",
            groups=[],
        )

        assert identity.has_all_groups(["wip-admins"]) is False

    def test_has_all_groups_empty_both(self):
        """Should return True when both lists are empty (vacuous truth)."""
        identity = UserIdentity(
            user_id="user-1",
            username="alice",
            auth_method="jwt",
            groups=[],
        )

        assert identity.has_all_groups([]) is True


# ===========================================================================
# Edge cases
# ===========================================================================


class TestEdgeCases:
    """Test edge cases for UserIdentity."""

    def test_groups_default_to_empty_list(self):
        """Groups should default to empty list if not provided."""
        identity = UserIdentity(
            user_id="user-1",
            username="alice",
            auth_method="jwt",
        )

        assert identity.groups == []

    def test_email_defaults_to_none(self):
        """Email should default to None if not provided."""
        identity = UserIdentity(
            user_id="user-1",
            username="alice",
            auth_method="jwt",
        )

        assert identity.email is None

    def test_identity_string_jwt_none_email_empty_username(self):
        """JWT with None email and empty username should fall back to user_id."""
        identity = UserIdentity(
            user_id="sub-claim-value",
            username="",
            email=None,
            auth_method="jwt",
        )

        assert identity.identity_string == "user:sub-claim-value"

    def test_single_group_membership(self):
        """Operations should work correctly with a single group."""
        identity = UserIdentity(
            user_id="user-1",
            username="alice",
            auth_method="jwt",
            groups=["only-group"],
        )

        assert identity.has_group("only-group") is True
        assert identity.has_group("other") is False
        assert identity.has_any_group(["only-group", "other"]) is True
        assert identity.has_all_groups(["only-group"]) is True
        assert identity.has_all_groups(["only-group", "other"]) is False

    def test_many_groups(self):
        """Operations should work correctly with many groups."""
        groups = [f"group-{i}" for i in range(100)]
        identity = UserIdentity(
            user_id="user-1",
            username="alice",
            auth_method="jwt",
            groups=groups,
        )

        assert identity.has_group("group-0") is True
        assert identity.has_group("group-99") is True
        assert identity.has_group("group-100") is False
        assert identity.has_any_group(["group-50", "nonexistent"]) is True
        assert identity.has_all_groups(["group-0", "group-50", "group-99"]) is True

    def test_raw_claims_defaults_to_none(self):
        """raw_claims should default to None."""
        identity = UserIdentity(
            user_id="user-1",
            username="alice",
            auth_method="jwt",
        )

        assert identity.raw_claims is None

    def test_provider_defaults_to_none(self):
        """provider should default to None."""
        identity = UserIdentity(
            user_id="user-1",
            username="alice",
            auth_method="jwt",
        )

        assert identity.provider is None

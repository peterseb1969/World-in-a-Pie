"""The platform group names have one definition (CASE-1221).

wip_auth.groups defines them; the rest of wip-auth, and every service
(each suite runs the same gate over its own source), uses the names.
These tests spell the groups literally on purpose: a retune of the
constants must fail here, not follow along.
"""

from pathlib import Path

import pytest

import wip_auth
from wip_auth.groups import ADMIN_GROUP, PRIVILEGED_GROUPS, SERVICES_GROUP
from wip_auth.models import UserIdentity
from wip_auth.providers.trusted_header import PROXY_KEY_GROUPS
from wip_auth.testing import (
    assert_no_privileged_group_literals,
    collect_privileged_group_literals,
)

SOURCE = Path(wip_auth.__file__).parent


def test_the_names():
    assert ADMIN_GROUP == "wip-admins"
    assert SERVICES_GROUP == "wip-services"
    assert frozenset({"wip-admins", "wip-services"}) == PRIVILEGED_GROUPS
    assert wip_auth.get_auth_config().admin_groups == ["wip-admins"]


def test_exported():
    for name in ("ADMIN_GROUP", "SERVICES_GROUP", "PRIVILEGED_GROUPS", "is_superadmin"):
        assert name in wip_auth.__all__
        assert hasattr(wip_auth, name)


def test_proxy_key_groups_follow_the_privileged_set():
    assert frozenset({"wip-admins", "wip-services"}) == PROXY_KEY_GROUPS


def test_is_superadmin():
    def identity(groups):
        return UserIdentity(user_id="u", username="u", groups=groups, auth_method="api_key")

    assert wip_auth.is_superadmin(identity(["wip-admins"]))
    assert not wip_auth.is_superadmin(identity(["wip-services"]))
    assert not wip_auth.is_superadmin(identity(["wip-editors"]))


def test_only_groups_py_spells_them():
    offenders, examined = collect_privileged_group_literals(SOURCE)
    assert examined > 0
    assert offenders and all(o.startswith("groups.py:") for o in offenders), offenders


def test_gate_catches_a_literal_and_ignores_prose(tmp_path):
    (tmp_path / "ok.py").write_text(
        '"""Mentions "wip-admins" in a docstring."""\n'
        "def f():\n"
        '    """Also "wip-services"."""\n'
        '    raise ValueError("requires wip-admins")\n'
    )
    assert_no_privileged_group_literals(tmp_path)
    (tmp_path / "bad.py").write_text('GROUPS = ["wip-admins"]\n')
    offenders, _ = collect_privileged_group_literals(tmp_path)
    assert offenders == ["bad.py:1"]
    with pytest.raises(AssertionError, match="spelled inline"):
        assert_no_privileged_group_literals(tmp_path)


def test_gate_fails_on_an_empty_walk(tmp_path):
    with pytest.raises(AssertionError, match="ZERO"):
        assert_no_privileged_group_literals(tmp_path / "missing")

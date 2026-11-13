"""Namespace deletion says what it refuses, with the right status
(CASE-1279, 77e5498f R1, R2, R5)."""

import re
import uuid
from types import SimpleNamespace

import pytest

from registry.models.namespace import Namespace
from registry.services import namespace_deletion as nd

NS = "/api/registry/namespaces"


def test_the_counter_filter_does_not_reach_a_name_extending_this_one():
    pattern = re.compile(nd.id_counter_filter("a")["counter_key"]["$regex"])
    assert pattern.match("a:terms:TERM-")
    assert not pattern.match("a:b:terms:TERM-"), "namespace 'a:b' is another namespace"
    assert not pattern.match("@prefix:TERM-")


def test_wip_is_not_told_to_switch_to_full():
    wip = SimpleNamespace(prefix="wip", deletion_mode="retain", status="active")
    messages = [m for _rule, m, _s in nd.deletion_refusals(wip)]
    assert not any("PATCH" in m for m in messages), messages
    other = SimpleNamespace(prefix="x", deletion_mode="retain", status="active")
    assert any("PATCH" in m for _r, m, _s in nd.deletion_refusals(other))


@pytest.mark.asyncio
async def test_a_namespace_locked_meanwhile_is_a_409(client, auth_headers, monkeypatch):
    prefix = f"del1279-{uuid.uuid4().hex[:8]}"
    r = await client.put(f"{NS}/{prefix}", json={"deletion_mode": "full"}, headers=auth_headers)
    assert r.status_code == 200, r.text

    async def locked_by_another(*args, **kwargs):
        return False

    monkeypatch.setattr(nd, "set_unless_locked", locked_by_another)
    r = await client.delete(f"{NS}/{prefix}", headers=auth_headers)
    assert r.status_code == 409, r.text
    assert "already locked" in r.text
    await Namespace.get_motor_collection().delete_one({"prefix": prefix})

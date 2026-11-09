"""A template delete does not go ahead on an answer nobody gave.

- When the last version is hard-deleted, the Registry entry goes first: a
  Registry that refuses (or cannot be reached) leaves nothing deleted. The
  rows went first, and a failed entry delete was only logged while the item
  said "deleted", leaving the entry, its key and its synonym behind.
- The all-versions hard delete's event named one version, so reporting kept
  every other version's row; it names none now.
- Hard-deleting the only active version, other versions retired, tells the
  Registry the template is retired, as a soft delete does.
- "N template(s) extend this template" counted version rows and retired
  children, and so blocked a soft delete of a parent whose children are all
  retired. It counts child templates still in use; a hard delete is still
  blocked by any child, since a retired child resolves through its parent.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from template_store.models.template import Template
from template_store.services.registry_client import RegistryError

from .test_hard_delete import _create_template, _create_version, _delete_template, _full_mode_registry


@pytest.mark.asyncio
async def test_a_registry_that_refuses_leaves_the_template(client: AsyncClient, auth_headers):
    tid = await _create_template(client, auth_headers, "C1168_REG_NO", "Registry refuses")
    registry = _full_mode_registry()
    registry.hard_delete_entry = AsyncMock(side_effect=RegistryError("registry says no"))
    with patch("template_store.services.template_service.get_registry_client", return_value=registry):
        data = await _delete_template(client, auth_headers, tid, hard_delete=True)
    assert data["failed"] == 1, data
    assert data["results"][0]["error_code"] == "registry_error", data
    assert await Template.find({"template_id": tid}).count() == 1


@pytest.mark.asyncio
async def test_an_all_versions_delete_event_names_no_version(client: AsyncClient, auth_headers):
    tid = await _create_template(client, auth_headers, "C1168_EVENT", "Event")
    await _create_version(client, auth_headers, tid)
    with (
        patch("template_store.services.template_service.get_registry_client",
              return_value=_full_mode_registry()),
        patch("template_store.services.template_service.publish_template_event",
              new=AsyncMock()) as publish,
    ):
        data = await _delete_template(client, auth_headers, tid, hard_delete=True)
    assert data["succeeded"] == 1, data
    payload = publish.await_args.args[1]
    assert payload["hard_delete"] is True
    assert "version" not in payload


@pytest.mark.asyncio
async def test_hard_deleting_the_only_active_version_retires_the_template(
    client: AsyncClient, auth_headers,
):
    tid = await _create_template(client, auth_headers, "C1168_LAST_ACTIVE", "Last active")
    await _create_version(client, auth_headers, tid)  # v2 active
    await (await Template.find_one({"template_id": tid, "version": 1})).set({"status": "inactive"})
    registry = _full_mode_registry()
    registry.deactivate_entries = AsyncMock(
        return_value=[{"registry_id": tid, "status": "deactivated"}],
    )
    with patch("template_store.services.template_service.get_registry_client",
               return_value=registry):
        data = await _delete_template(client, auth_headers, tid, version=2, hard_delete=True)
    assert data["succeeded"] == 1, data
    registry.deactivate_entries.assert_awaited_once()
    assert registry.deactivate_entries.await_args.args[0] == [tid]


@pytest.mark.asyncio
async def test_children_are_counted_as_templates_still_in_use(client: AsyncClient, auth_headers):
    parent = await _create_template(client, auth_headers, "C1168_PARENT", "Parent")
    child = await _create_template(
        client, auth_headers, "C1168_CHILD", "Child",
        fields=[{"name": "note", "label": "Note", "type": "string"}], extends=parent,
    )
    await _create_version(client, auth_headers, child,
                          fields=[{"name": "note", "label": "Note!", "type": "string"}])
    deps = (await client.get(f"/api/template-store/templates/{parent}/dependencies",
                             headers=auth_headers)).json()
    assert deps["child_template_count"] == 1, deps

    await Template.find({"template_id": child}).update({"$set": {"status": "inactive"}})
    data = await _delete_template(client, auth_headers, parent, version=1)
    assert data["succeeded"] == 1, data

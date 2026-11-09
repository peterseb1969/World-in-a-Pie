"""A template retirement or revival the Registry cannot record is not written.

Deactivating a template's last active version retires the template;
reactivating a version, a new active version or an activated draft of a
template with no active version revives it. Each asks the Registry first:
when it does not answer (RegistryUnavailableError — the route's 503) or
refuses (registry_error), template-store writes nothing. When the write
fails after the Registry recorded the change, the change is undone. A write
that changes neither (another version stays active) asks the Registry nothing.
"""

import pytest
from httpx import AsyncClient

from template_store.models.template import Template
from template_store.services import template_service
from template_store.services.errors import TemplateError
from template_store.services.template_service import TemplateService
from wip_auth.registry_client import RegistryUnavailableError


def _declaration(value: str, label: str | None = None) -> dict:
    return {
        "namespace": "wip", "value": value, "label": label or value,
        "identity_fields": ["code"],
        "fields": [{"name": "code", "label": "Code", "type": "string", "mandatory": True}],
    }


async def _post(client: AsyncClient, auth_headers: dict, declaration: dict) -> dict:
    resp = await client.post("/api/template-store/templates", headers=auth_headers, json=[declaration])
    assert resp.status_code == 200, resp.text
    result = resp.json()["results"][0]
    assert result["status"] in ("created", "updated"), result
    return result


async def _statuses(template_id: str) -> dict[int, str]:
    return {t.version: t.status for t in await Template.find({"template_id": template_id}).to_list()}


@pytest.fixture
def registry(monkeypatch):
    """The in-process Registry client, with spies on the lifecycle calls."""
    client = template_service.get_registry_client()
    calls: dict[str, list[list[str]]] = {"deactivate": [], "reactivate": []}
    real = {"deactivate": client.deactivate_entries, "reactivate": client.reactivate_entries}

    def spy(verb):
        async def call(entry_ids, updated_by=None, **kw):
            calls[verb].append(list(entry_ids))
            return await real[verb](entry_ids, updated_by=updated_by, **kw)
        return call

    monkeypatch.setattr(client, "deactivate_entries", spy("deactivate"))
    monkeypatch.setattr(client, "reactivate_entries", spy("reactivate"))
    client.calls = calls
    client.real = real
    return client


def _unreachable(monkeypatch, registry, verb):
    async def down(entry_ids, updated_by=None, **kw):
        registry.calls[verb].append(list(entry_ids))
        raise RegistryUnavailableError("Registry unavailable (entries): HTTP 503")
    monkeypatch.setattr(registry, f"{verb}_entries", down)


def _refusing(monkeypatch, registry, verb):
    async def refuse(entry_ids, updated_by=None, **kw):
        registry.calls[verb].append(list(entry_ids))
        return [{"registry_id": e, "status": "error", "error": "simulated refusal"} for e in entry_ids]
    monkeypatch.setattr(registry, f"{verb}_entries", refuse)


@pytest.mark.asyncio
async def test_retiring_the_last_version_is_not_written_when_the_registry_does_not_answer(
    client: AsyncClient, auth_headers: dict, registry, monkeypatch,
):
    tid = (await _post(client, auth_headers, _declaration("LIFE_TPL_DOWN")))["id"]
    _unreachable(monkeypatch, registry, "deactivate")
    with pytest.raises(RegistryUnavailableError):
        await TemplateService.delete_template(tid, version=1)
    assert await _statuses(tid) == {1: "active"}


@pytest.mark.asyncio
async def test_retiring_the_last_version_is_not_written_when_the_registry_refuses(
    client: AsyncClient, auth_headers: dict, registry, monkeypatch,
):
    tid = (await _post(client, auth_headers, _declaration("LIFE_TPL_NO")))["id"]
    _refusing(monkeypatch, registry, "deactivate")
    with pytest.raises(TemplateError) as caught:
        await TemplateService.delete_template(tid, version=1)
    assert caught.value.code == "registry_error"
    assert await _statuses(tid) == {1: "active"}


@pytest.mark.asyncio
async def test_a_version_whose_sibling_stays_active_asks_the_registry_nothing(
    client: AsyncClient, auth_headers: dict, registry, monkeypatch,
):
    tid = (await _post(client, auth_headers, _declaration("LIFE_TPL_TWO")))["id"]
    await _post(client, auth_headers, _declaration("LIFE_TPL_TWO", "Two, v2"))
    _unreachable(monkeypatch, registry, "deactivate")
    _unreachable(monkeypatch, registry, "reactivate")
    assert await TemplateService.delete_template(tid, version=1) is True
    assert await _statuses(tid) == {1: "inactive", 2: "active"}
    assert registry.calls == {"deactivate": [], "reactivate": []}


@pytest.mark.asyncio
async def test_reactivating_a_retired_template_is_not_written_when_the_registry_does_not_answer(
    client: AsyncClient, auth_headers: dict, registry, monkeypatch,
):
    tid = (await _post(client, auth_headers, _declaration("LIFE_TPL_REVIVE")))["id"]
    assert await TemplateService.delete_template(tid, version=1) is True
    _unreachable(monkeypatch, registry, "reactivate")
    resp = await client.post(
        f"/api/template-store/templates/{tid}/reactivate?namespace=wip&version=1", headers=auth_headers,
    )
    assert resp.status_code == 503, resp.text
    assert await _statuses(tid) == {1: "inactive"}


@pytest.mark.asyncio
async def test_a_new_version_of_a_retired_template_is_not_written_when_the_registry_refuses(
    client: AsyncClient, auth_headers: dict, registry, monkeypatch,
):
    tid = (await _post(client, auth_headers, _declaration("LIFE_TPL_REPOST")))["id"]
    assert await TemplateService.delete_template(tid, version=1) is True
    _refusing(monkeypatch, registry, "reactivate")
    resp = await client.post(
        "/api/template-store/templates", headers=auth_headers,
        json=[_declaration("LIFE_TPL_REPOST", "Reposted")],
    )
    row = resp.json()["results"][0]
    assert row["status"] == "error" and row["error_code"] == "registry_error", row
    assert await _statuses(tid) == {1: "inactive"}


@pytest.mark.asyncio
async def test_a_failed_template_write_undoes_the_registry_change(
    client: AsyncClient, auth_headers: dict, registry, monkeypatch,
):
    tid = (await _post(client, auth_headers, _declaration("LIFE_TPL_UNDO")))["id"]

    async def failing_save(self, *args, **kwargs):
        raise RuntimeError("mongo write failed")

    monkeypatch.setattr(Template, "save", failing_save)
    with pytest.raises(RuntimeError, match="mongo write failed"):
        await TemplateService.delete_template(tid, version=1)
    assert registry.calls == {"deactivate": [[tid]], "reactivate": [[tid]]}

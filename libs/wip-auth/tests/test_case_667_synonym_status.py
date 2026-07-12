"""Auto-synonym registration must surface per-item refusals (CASE-667).

/synonyms/add is bulk-first: HTTP 200 even when an item was refused (the
Registry's never-auto-steal guard returns per-item
`error: "Synonym already registered under different entry"`). The client
used to raise only on HTTP failure, so a refused synonym left the entity
created but resolving its value to a DIFFERENT entry — and the services'
rollback-on-synonym-failure paths could only ever fire on transport errors.

`already_exists` stays non-raising: restore-mode creates re-register the
value per version and archive synonym replays re-add the same key; both
lean on the Registry's idempotent fast path.
"""

import pytest

from wip_auth.registry_client import RegistryClientBase, RegistryError

BASE = "http://registry.test:8001"
ADD_URL = f"{BASE}/api/registry/synonyms/add"


def _client() -> RegistryClientBase:
    return RegistryClientBase(base_url=BASE, api_key="k")


async def _register_one(client: RegistryClientBase) -> None:
    await client._register_auto_synonym(
        target_id="entry-1",
        namespace="ns",
        entity_type="templates",
        composite_key={"ns": "ns", "type": "template", "value": "X"},
    )


class TestRegisterAutoSynonym:
    @pytest.mark.asyncio
    async def test_added_succeeds(self, httpx_mock):
        httpx_mock.add_response(
            url=ADD_URL, json={"results": [{"status": "added", "registry_id": "entry-1"}]}
        )
        await _register_one(_client())  # no raise

    @pytest.mark.asyncio
    async def test_already_exists_succeeds(self, httpx_mock):
        """The idempotent re-registration path (CASE-665 depends on it)."""
        httpx_mock.add_response(
            url=ADD_URL,
            json={"results": [{"status": "already_exists", "registry_id": "entry-1"}]},
        )
        await _register_one(_client())  # no raise

    @pytest.mark.asyncio
    async def test_per_item_error_raises(self, httpx_mock):
        """HTTP 200 + per-item error — the previously swallowed shape."""
        httpx_mock.add_response(
            url=ADD_URL,
            json={"results": [{
                "status": "error",
                "error": "Synonym already registered under different entry: entry-9",
            }]},
        )
        with pytest.raises(RegistryError, match=r"status=error.*entry-9"):
            await _register_one(_client())

    @pytest.mark.asyncio
    async def test_target_not_found_raises(self, httpx_mock):
        httpx_mock.add_response(
            url=ADD_URL, json={"results": [{"status": "target_not_found"}]}
        )
        with pytest.raises(RegistryError, match="status=target_not_found"):
            await _register_one(_client())


class TestRegisterAutoSynonymsBulk:
    @pytest.mark.asyncio
    async def test_mixed_success_statuses_return_results(self, httpx_mock):
        httpx_mock.add_response(
            url=ADD_URL,
            json={"results": [{"status": "added"}, {"status": "already_exists"}]},
        )
        results = await _client()._register_auto_synonyms_bulk([
            {"target_id": "a", "namespace": "ns", "entity_type": "terms",
             "composite_key": {"value": "a"}},
            {"target_id": "b", "namespace": "ns", "entity_type": "terms",
             "composite_key": {"value": "b"}},
        ])
        assert [r.status for r in results] == ["added", "already_exists"]

    @pytest.mark.asyncio
    async def test_per_item_failures_raise_naming_items(self, httpx_mock):
        httpx_mock.add_response(
            url=ADD_URL,
            json={"results": [
                {"status": "added"},
                {"status": "error", "error": "owned by entry-9"},
                {"status": "target_not_found"},
            ]},
        )
        with pytest.raises(RegistryError) as exc:
            await _client()._register_auto_synonyms_bulk([
                {"target_id": t, "namespace": "ns", "entity_type": "terms",
                 "composite_key": {"value": t}}
                for t in ("a", "b", "c")
            ])
        message = str(exc.value)
        assert "2 auto-synonym registration(s) failed" in message
        assert "item 1: status=error (owned by entry-9)" in message
        assert "item 2: status=target_not_found" in message

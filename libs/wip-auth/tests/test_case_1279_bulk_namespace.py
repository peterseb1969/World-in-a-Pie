"""resolve_bulk_ids resolves each item in the namespace it belongs to.

- A namespace derived from the caller's key fills in only for an item that
  names none; an item's own namespace outranks it (the function's own
  contract). A namespace the caller passes explicitly still outranks both.
- Only an API key's scope derives a namespace: an OIDC token carrying a
  one-element ``namespaces`` claim is not a scope (permissions._key_scope).
"""

import pytest

import wip_auth.fastapi_helpers as helpers
from wip_auth.fastapi_helpers import _derive_namespace_from_identity, resolve_bulk_ids
from wip_auth.identity import set_current_identity
from wip_auth.models import UserIdentity


def _identity(namespaces, auth_method="api_key"):
    return UserIdentity(
        user_id="apikey:k", username="k", email=None, groups=["wip-writers"],
        auth_method=auth_method, provider=auth_method, raw_claims={"namespaces": namespaces},
    )


@pytest.fixture
def asked(monkeypatch):
    seen: dict[str, list[str]] = {}

    async def fake(ids, entity_type, namespace, **kwargs):
        seen.setdefault(namespace, []).extend(ids)
        return {i: f"{namespace}/{i}" for i in ids}

    monkeypatch.setattr(helpers, "resolve_entity_ids", fake)
    yield seen
    set_current_identity(None)


@pytest.mark.asyncio
async def test_an_items_own_namespace_outranks_the_derived_one(asked):
    set_current_identity(_identity(["key-ns"]))
    items = [{"template_id": "A", "namespace": "item-ns"}, {"template_id": "B"}]
    await resolve_bulk_ids(items, "template_id", "template", namespace=None)
    assert asked == {"item-ns": ["A"], "key-ns": ["B"]}
    assert [i["template_id"] for i in items] == ["item-ns/A", "key-ns/B"]


@pytest.mark.asyncio
async def test_an_explicit_namespace_still_outranks_the_items(asked):
    set_current_identity(_identity(["key-ns"]))
    items = [{"template_id": "A", "namespace": "item-ns"}]
    await resolve_bulk_ids(items, "template_id", "template", namespace="given")
    assert asked == {"given": ["A"]}


def test_an_oidc_namespaces_claim_derives_nothing():
    set_current_identity(_identity(["claimed"], auth_method="jwt"))
    try:
        assert _derive_namespace_from_identity() is None
    finally:
        set_current_identity(None)

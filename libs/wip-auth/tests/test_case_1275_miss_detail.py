"""A miss names a namespace only when it searched one.

Provenance: CASE-1275. `unresolved_detail` is the one text a door gives an
identifier that names nothing, and a hidden entity is answered with it so its
existence is not confirmed. It printed the namespace it derived even when
nothing could be derived — a key scoped to several namespaces that named none got
"... in namespace 'None'", a third answer where the promise is two alike ones.
"""

import pytest

from wip_auth.fastapi_helpers import miss_detail, unresolved_detail
from wip_auth.identity import set_current_identity
from wip_auth.models import UserIdentity


def _identity(namespaces):
    return UserIdentity(
        user_id="apikey:test", username="test", email=None, groups=["wip-writers"],
        auth_method="api_key", provider="api_key", raw_claims={"namespaces": namespaces},
    )


@pytest.mark.asyncio
async def test_a_multi_namespace_key_that_named_no_namespace_gets_no_namespace_phrase():
    set_current_identity(_identity(["ns-a", "ns-b"]))
    assert await unresolved_detail("abc123", "template", None) == (
        "Could not resolve template 'abc123'"
    )


@pytest.mark.asyncio
async def test_a_namespace_that_was_named_still_appears():
    set_current_identity(_identity(["ns-a", "ns-b"]))
    assert await unresolved_detail("abc123", "template", "aa", "template_id") == (
        "Could not resolve template_id 'abc123' in namespace 'aa'"
    )
    set_current_identity(_identity(["my-app"]))
    assert await unresolved_detail("abc123", "template", None) == (
        "Could not resolve template 'abc123' in namespace 'my-app'"
    )


@pytest.mark.asyncio
async def test_a_door_without_context_falls_back_to_its_own_miss_text():
    set_current_identity(_identity(["ns-a", "ns-b"]))
    assert await miss_detail("abc123", "template", None, "Template not found") == (
        "Template not found"
    )
    assert await miss_detail(
        "abc123", "template", "aa", "Template not found", "template_id",
    ) == "Could not resolve template_id 'abc123' in namespace 'aa'"

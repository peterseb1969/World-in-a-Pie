"""A spec key that declares no grants leaves them to the API.

The Registry reads a key-file entry with `grants` (`{}` included) as the
file owning that key's grants: the API can neither change them nor add
others, and an import removes what the API set. The spec used to default
`grants` to `{}` and render it for every key, so no spec key could ever
take a grant through the API. Now `grants` is None unless declared, and a
key without it is rendered without `grants`. Keys the deployer derives
(app keys, the canary, the app edge) keep a declared dict: the file owns
their grants.
"""

from __future__ import annotations

import json
from pathlib import Path

from wip_deploy.build import BuildInputs, build_deployment
from wip_deploy.config_gen.api_keys import (
    APP_EDGE_KEY,
    CANARY_KEY,
    generate_api_keys_json,
)
from wip_deploy.spec.deployment import CANARY_KEY_NAME, Deployment, SpecAPIKey


class _AnySecret:
    def get(self, name: str) -> str:
        return "x"


def _keys(api_keys: list[dict]) -> dict[str, dict]:
    d = build_deployment(BuildInputs(
        name="t", preset="standard", target="compose", hostname="wip.example.org",
        compose_data_dir=Path("/tmp/wip-1263"), api_keys=api_keys,
    ))
    rendered = json.loads(generate_api_keys_json(d, _AnySecret(), []))["keys"]
    return {k["name"]: k for k in rendered}


def test_a_key_without_grants_is_rendered_without_them() -> None:
    keys = _keys([{"name": "agent", "namespaces": ["kb"]}])
    assert "grants" not in keys["agent"]
    assert "stay under the API" in keys["agent"]["description"]


def test_declared_grants_are_rendered_and_empty_ones_too() -> None:
    keys = _keys([
        {"name": "writer", "namespaces": ["kb"], "grants": {"kb": "write"}},
        {"name": "reader", "namespaces": ["kb"], "grants": {}},
    ])
    assert keys["writer"]["grants"] == {"kb": "write"}
    assert keys["reader"]["grants"] == {}
    assert "imports its scope and grants" in keys["reader"]["description"]


def test_the_canary_and_the_edge_key_keep_file_owned_grants() -> None:
    assert CANARY_KEY.grants == {}
    assert APP_EDGE_KEY.grants == {}
    assert _keys([])[CANARY_KEY_NAME]["grants"] == {}


def test_a_saved_spec_keeps_what_it_says() -> None:
    """A spec saved before the change carries `grants: {}` for its keys; it
    still says the file owns them. One without the key loads as None."""
    d = build_deployment(BuildInputs(
        name="t", preset="standard", target="compose", hostname="wip.example.org",
        compose_data_dir=Path("/tmp/wip-1263"),
        api_keys=[{"name": "old", "namespaces": ["kb"], "grants": {}},
                  {"name": "new", "namespaces": ["kb"]}],
    ))
    saved = Deployment.model_validate(json.loads(json.dumps(d.model_dump(mode="json"))))
    by_name = {k.name: k for k in saved.spec.auth.api_keys}
    assert by_name["old"].grants == {}
    assert by_name["new"].grants is None


def test_the_scope_rule_still_holds_for_declared_grants() -> None:
    import pytest
    from pydantic import ValidationError

    SpecAPIKey(name="k", namespaces=["a"])  # nothing declared: nothing to check
    with pytest.raises(ValidationError, match="outside its read scope"):
        SpecAPIKey(name="k", namespaces=["a"], grants={"b": "write"})

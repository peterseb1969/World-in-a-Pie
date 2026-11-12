"""`wip` is not grantable to an app key — at any permission.

Three of the four models that hand an app a namespace already refused it: an
owned namespace from the manifest (`AppNamespace.not_wip`) and one added on the
install (`AddedNamespace.not_wip`). The *use* route (`NamespaceAccess`) did not,
so `spec.namespace_access: [{namespace: wip, permission: write}]` and
`wip-deploy app-namespace add <app> wip --access write` both reached the derived
key's grants verbatim. `wip` is the exclusive home of the platform's
`_`-prefixed vocabularies, so that grant lets an app rewrite the terminologies
every other namespace validates against.

`read` on `wip` is not something a declaration can ask for either: it arrives
implicitly with every derived app key.

The guard being a field validator means it also runs on every *load*, so a spec an
older deployer persisted with the grant would stop deserialising — and the verb that
would undo it, `app-namespace remove`, loads that same spec and exits first. The
load-path sweep (review #3 on CASE-1230, finding 1) and its tests are here too,
because they pin the two halves of one rule: new input is refused, saved input is
dropped and announced.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError as PydanticValidationError

from wip_deploy.build import BuildInputs, build_deployment
from wip_deploy.cli import _DEPLOYMENT_JSON_VERSION, _load_deployment, _upgrade_state_payload
from wip_deploy.config_gen.api_keys import API_KEYS_RENDER_PATH, app_api_key
from wip_deploy.discovery import Discovery, discover
from wip_deploy.renderers import render_compose
from wip_deploy.secrets import collect_required_secrets
from wip_deploy.secrets_backend import ResolvedSecrets
from wip_deploy.spec import App, AppRef, Deployment
from wip_deploy.spec.component import NamespaceAccess

REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def discovery() -> Discovery:
    return discover(REPO_ROOT)


def _app(disc: Discovery, name: str, **spec: Any) -> App:
    base = next(a for a in disc.apps if a.metadata.name == "wip-cooking")
    raw = base.model_dump()
    raw["metadata"]["name"] = name
    raw["spec"]["namespaces"] = []
    raw["spec"].update(spec)
    return App.model_validate(raw)


def _deployment(target: str, apps: list[App]) -> Deployment:
    d = build_deployment(BuildInputs(
        target=target,  # type: ignore[arg-type]
        variant="prod", hostname="wip.example.org", tls="internal",
        registry="ghcr.io/test", compose_data_dir=Path("/tmp/d"), k8s_namespace="wip-t",
    ))
    d.spec.apps = [AppRef(name=a.metadata.name) for a in apps]
    return d


@pytest.mark.parametrize("permission", ["write", "read"])
def test_the_use_route_refuses_wip(permission: str) -> None:
    """The guard is on the declaration, not the privilege level.

    Refusing `write` alone would leave `read` declarable, and an explicit read
    entry is the doorway the write variant walks through.
    """
    with pytest.raises(PydanticValidationError, match="belongs to the platform"):
        NamespaceAccess.model_validate({"namespace": "wip", "permission": permission})


@pytest.mark.parametrize("namespace", ["wip-kb", "wip-t", "wi", "wipp", "my-wip"])
def test_a_near_miss_namespace_is_still_grantable(namespace: str) -> None:
    """The guard matches the platform namespace exactly, never by prefix.

    `wip-kb` is an ordinary app namespace on every install; a `startswith`
    guard would take it down with the real target.
    """
    assert NamespaceAccess(namespace=namespace).namespace == namespace


def test_the_manifest_route_refuses_wip(discovery: Discovery) -> None:
    with pytest.raises(PydanticValidationError, match="belongs to the platform"):
        _app(discovery, "greedy-app", namespace_access=[{"namespace": "wip", "permission": "write"}])


def test_wip_reaches_the_key_as_implicit_read_only(discovery: Discovery) -> None:
    """What the guard must not break: `wip` still scopes the key, never grants.

    An app with no access declaration still reads the platform vocabularies, and
    `wip` stays out of `grants` — where `read` is dropped precisely because the
    scope already carries it.
    """
    key = app_api_key(_app(discovery, "reader"))
    assert key is not None
    assert key.namespaces == ["wip"]
    assert key.grants == {}


def test_a_declared_access_lands_alongside_wip_read(discovery: Discovery) -> None:
    key = app_api_key(
        _app(discovery, "cooking-app", namespace_access=[{"namespace": "library", "permission": "write"}])
    )
    assert key is not None
    assert key.namespaces == ["library", "wip"]
    assert key.grants == {"library": "write"}


def test_a_persisted_spec_carrying_the_grant_is_refused_not_ignored() -> None:
    """The model itself still refuses the entry, however it arrives.

    This is the declaration-level half: nothing can build an `AppRef` holding a
    `wip` access, whether from a manifest, a flag, or a hand-written dict. What
    happens to a spec an older deployer already *saved* is a different question —
    refusing on load would strand the install, so `_upgrade_state_payload` drops
    the entry instead; see the sweep tests below.
    """
    with pytest.raises(PydanticValidationError, match="belongs to the platform"):
        AppRef.model_validate({"name": "wip-kb", "namespace_access": [{"namespace": "wip"}]})


def test_no_shipped_manifest_declares_wip_access(discovery: Discovery) -> None:
    """The guard lands on nothing real: every shipped app names another namespace."""
    offenders = [
        (a.metadata.name, access.namespace)
        for a in discovery.apps
        for access in a.spec.namespace_access
        if access.namespace == "wip"
    ]
    assert offenders == []


def test_rendered_key_file_carries_no_wip_grant(discovery: Discovery) -> None:
    apps = [_app(discovery, "cooking-app", namespace_access=[{"namespace": "library", "permission": "write"}])]
    d = _deployment("compose", apps)
    names = collect_required_secrets(d, discovery.components, apps)
    secrets = ResolvedSecrets(values={n: f"secret-of-{n}" for n in names})
    tree = render_compose(d, discovery.components, apps, secrets)
    keys = {k["name"]: k for k in json.loads(tree.files[Path(API_KEYS_RENDER_PATH)].content)["keys"]}
    entry = keys["app-cooking-app"]
    assert "wip" not in entry["grants"]
    assert entry["namespaces"] == ["library", "wip"]


# ── the saved-spec sweep: an install that took the grant before the guard
#    must load, lose the entry, and say so (CASE-1230 review #3, finding 1) ──


def _state_with_grant(discovery: Discovery, access: list[dict[str, str]]) -> dict[str, Any]:
    """A current-version envelope whose saved app carries `namespace_access` as
    the pre-guard deployer would have written it."""
    d = _deployment("compose", [_app(discovery, "greedy-app")])
    payload: dict[str, Any] = {
        "wip_deploy_format_version": _DEPLOYMENT_JSON_VERSION,
        "deployment": d.model_dump(mode="json"),
    }
    payload["deployment"]["spec"]["apps"][0]["namespace_access"] = access
    return payload


def test_a_saved_grant_is_dropped_at_load_and_named_in_the_warning(
    discovery: Discovery, capsys: pytest.CaptureFixture[str]
) -> None:
    payload = _state_with_grant(discovery, [{"namespace": "wip", "permission": "write"}])
    out = _upgrade_state_payload(payload)

    assert out["deployment"]["spec"]["apps"][0]["namespace_access"] == []
    assert out["wip_deploy_format_version"] == _DEPLOYMENT_JSON_VERSION, "the sweep is a value fix, not a format change"
    err = capsys.readouterr().err
    assert "greedy-app" in err and "wip" in err and "write" in err
    assert "next apply" in err, "the warning must say when the grant actually leaves the key"
    # Only the one entry moved: everything else in the spec round-trips untouched.
    assert _spec_without_apps(out) == _spec_without_apps(payload)


def _spec_without_apps(payload: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in payload["deployment"]["spec"].items() if k != "apps"}


def test_the_sweep_does_not_edit_the_callers_payload_in_place(
    discovery: Discovery,
) -> None:
    """`_upgrade_state_payload` hands back a new envelope; the dict it was given
    stays as it was read, so a caller comparing before/after sees the truth."""
    payload = _state_with_grant(discovery, [{"namespace": "wip", "permission": "write"}])
    _upgrade_state_payload(payload)
    assert payload["deployment"]["spec"]["apps"][0]["namespace_access"] == [
        {"namespace": "wip", "permission": "write"}
    ]


def test_an_install_holding_the_grant_in_its_state_file_still_loads(
    tmp_path: Path, discovery: Discovery, capsys: pytest.CaptureFixture[str]
) -> None:
    """The whole point of the sweep: the install is usable again.

    Pre-fix this exited 2 (`failed to deserialize`) on every verb that reads the
    state — including `app-namespace remove`, the verb that would have undone the
    grant — leaving a full re-specify as the only recovery.
    """
    install = tmp_path / "install"
    install.mkdir()
    (install / "deployment.deployer-state").write_text(
        json.dumps(_state_with_grant(discovery, [{"namespace": "wip", "permission": "write"}]), indent=2)
    )

    d = _load_deployment(install)

    assert d.spec.apps[0].namespace_access == []
    assert "wip" in capsys.readouterr().err


def test_the_swept_spec_is_what_gets_written_back(
    tmp_path: Path, discovery: Discovery, capsys: pytest.CaptureFixture[str]
) -> None:
    """One load is enough to heal the install on disk.

    Every verb that mutates persists the deployment it loaded, so the entry does
    not linger for the next reader — the sweep is not just a per-run pass over a
    file that stays broken.
    """
    from wip_deploy.cli import _persist_deployment

    install = tmp_path / "install"
    install.mkdir()
    state = install / "deployment.deployer-state"
    state.write_text(
        json.dumps(_state_with_grant(discovery, [{"namespace": "wip", "permission": "write"}]), indent=2)
    )

    _persist_deployment(_load_deployment(install), install)

    assert json.loads(state.read_text())["deployment"]["spec"]["apps"][0]["namespace_access"] == []
    # The warning is per read, and one verb reads the envelope more than once
    # (`_persist_deployment` inspects the predecessor it is about to overwrite),
    # so this asserts it is there and never silent — not a line count.
    assert capsys.readouterr().err.count("namespace access on 'wip'") >= 1


def test_an_older_envelope_is_swept_on_the_way_up(
    discovery: Discovery, capsys: pytest.CaptureFixture[str]
) -> None:
    """A version-1 state runs the whole chain and still loses the grant."""
    payload = _state_with_grant(discovery, [{"namespace": "wip", "permission": "write"}])
    payload["wip_deploy_format_version"] = 1
    out = _upgrade_state_payload(payload)

    assert out["wip_deploy_format_version"] == _DEPLOYMENT_JSON_VERSION
    assert out["deployment"]["spec"]["apps"][0]["namespace_access"] == []
    assert "wip" in capsys.readouterr().err


def test_a_state_without_the_entry_is_returned_untouched_and_silent(
    discovery: Discovery, capsys: pytest.CaptureFixture[str]
) -> None:
    payload = _state_with_grant(discovery, [{"namespace": "library", "permission": "write"}])
    assert _upgrade_state_payload(payload) == payload
    assert capsys.readouterr().err == ""


def test_the_sweep_matches_wip_exactly_and_keeps_the_others_in_order(
    discovery: Discovery, capsys: pytest.CaptureFixture[str]
) -> None:
    """`wip-kb` and `wipp` are ordinary namespaces; the sweep takes one entry,
    not the family, and does not reshuffle the rest."""
    payload = _state_with_grant(discovery, [
        {"namespace": "wip-kb", "permission": "write"},
        {"namespace": "wip", "permission": "read"},
        {"namespace": "wipp", "permission": "write"},
    ])
    out = _upgrade_state_payload(payload)
    kept = out["deployment"]["spec"]["apps"][0]["namespace_access"]
    assert [a["namespace"] for a in kept] == ["wip-kb", "wipp"]


@pytest.mark.parametrize(
    "payload",
    [
        {"wip_deploy_format_version": _DEPLOYMENT_JSON_VERSION},
        {"wip_deploy_format_version": _DEPLOYMENT_JSON_VERSION, "deployment": {}},
        {"wip_deploy_format_version": _DEPLOYMENT_JSON_VERSION, "deployment": {"spec": {}}},
        {"wip_deploy_format_version": _DEPLOYMENT_JSON_VERSION, "deployment": {"spec": {"apps": "not-a-list"}}},
        {"wip_deploy_format_version": _DEPLOYMENT_JSON_VERSION, "deployment": {"spec": {"apps": ["not-a-dict"]}}},
    ],
)
def test_a_malformed_envelope_is_left_for_model_validation_to_report(
    payload: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    """The sweep is a security fix, not a repair shop: it never swallows a
    malformed spec, and never warns about an entry it could not read."""
    assert _upgrade_state_payload(payload) == payload
    assert capsys.readouterr().err == ""


def test_unreadable_metadata_still_loses_the_grant(
    discovery: Discovery, capsys: pytest.CaptureFixture[str]
) -> None:
    """Naming the install is decoration; the drop itself does not depend on it."""
    payload = _state_with_grant(discovery, [{"namespace": "wip", "permission": "write"}])
    payload["deployment"]["metadata"] = "not-a-mapping"
    out = _upgrade_state_payload(payload)

    assert out["deployment"]["spec"]["apps"][0]["namespace_access"] == []
    err = capsys.readouterr().err
    assert "wip" in err and "greedy-app" in err

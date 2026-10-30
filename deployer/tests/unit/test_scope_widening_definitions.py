"""A scoped apply widens to every service whose rendered definition changed.

The previous render on disk is compared with the new one per workload: the
compose / dev service block, or the k8s pod template. That catches literal
env, image, ports and mounts on every target; the config-hash label inside
the definition carries the secret values a service reads, which the
definition only references. Dev renders stamp that label on every service,
as compose does.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from wip_deploy.apply import changed_outside_scope
from wip_deploy.build import BuildInputs, build_deployment
from wip_deploy.config_gen.config_hash import CONFIG_HASH_KEY
from wip_deploy.discovery import Discovery, discover
from wip_deploy.renderers import render_compose, render_dev_simple, render_k8s
from wip_deploy.secrets import collect_required_secrets
from wip_deploy.secrets_backend import ResolvedSecrets
from wip_deploy.spec import App, AppRef, Deployment

REPO_ROOT = Path(__file__).resolve().parents[3]
TARGETS = ["compose", "dev", "k8s"]


@pytest.fixture(scope="module")
def discovery() -> Discovery:
    return discover(REPO_ROOT)


def _deployment(target: str) -> Deployment:
    d = build_deployment(BuildInputs(
        target=target,  # type: ignore[arg-type]
        variant="prod" if target != "dev" else "dev",
        hostname="wip.example.org", tls="internal", registry="ghcr.io/test",
        compose_data_dir=Path("/tmp/d"), k8s_namespace="wip-t",
    ))
    d.spec.apps = [AppRef(name="wip-kb")]
    if target == "dev":
        assert d.spec.platform.dev is not None
        d.spec.platform.dev.apps_from_registry = ["wip-kb"]
    return d


def _render(d: Deployment, disc: Discovery, apps: list[App], overrides: dict[str, str] | None = None) -> Any:
    names = collect_required_secrets(d, disc.components, apps)
    values = {n: f"v-{n}" for n in names}
    values.update(overrides or {})
    secrets = ResolvedSecrets(values=values)
    if d.spec.target == "k8s":
        return render_k8s(d, disc.components, apps, secrets)
    if d.spec.target == "dev":
        return render_dev_simple(d, disc.components, apps, secrets, repo_root=REPO_ROOT)
    return render_compose(d, disc.components, apps, secrets)


def _on_disk(tree: Any, root: Path) -> None:
    for path, entry in tree.files.items():
        out = root / path
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(entry.content)


def _with_literal(disc: Discovery, name: str, var: str, value: str) -> list[App]:
    """The discovered apps, with one app's literal env var changed."""
    apps = []
    for a in disc.apps:
        if a.metadata.name == name:
            raw = a.model_dump()
            for ev in raw["spec"]["env"]["required"]:
                if ev["name"] == var:
                    ev["source"] = {"literal": value}
            a = App.model_validate(raw)
        apps.append(a)
    return apps


@pytest.mark.parametrize("target", TARGETS)
def test_an_unchanged_render_widens_nothing(target: str, tmp_path: Path, discovery: Discovery) -> None:
    d = _deployment(target)
    _on_disk(_render(d, discovery, discovery.apps), tmp_path)
    again = _render(d, discovery, discovery.apps)
    assert changed_outside_scope(again, tmp_path, target, []) == []


@pytest.mark.parametrize("target", TARGETS)
def test_a_literal_env_change_widens_that_service(target: str, tmp_path: Path, discovery: Discovery) -> None:
    """Not a secret: the config hash alone never saw this (compose and k8s
    missed it too before definitions were compared)."""
    d = _deployment(target)
    _on_disk(_render(d, discovery, discovery.apps), tmp_path)
    changed = _render(d, discovery, _with_literal(discovery, "wip-kb", "NODE_ENV", "staging"))
    assert changed_outside_scope(changed, tmp_path, target, []) == ["wip-kb"]
    assert changed_outside_scope(changed, tmp_path, target, ["wip-kb"]) == []


@pytest.mark.parametrize("target", TARGETS)
def test_a_rotated_secret_widens_its_readers(target: str, tmp_path: Path, discovery: Discovery) -> None:
    d = _deployment(target)
    _on_disk(_render(d, discovery, discovery.apps), tmp_path)
    rotated = _render(d, discovery, discovery.apps, {"app-wip-kb-api-key": "rotated"})
    assert "wip-kb" in changed_outside_scope(rotated, tmp_path, target, [])


def test_dev_stamps_a_config_hash_on_apps_and_services(discovery: Discovery) -> None:
    d = _deployment("dev")
    tree = _render(d, discovery, discovery.apps)
    services = yaml.safe_load(tree.files[Path("docker-compose.yaml")].content)["services"]
    for name in ("wip-kb", "registry", "document-store"):
        assert CONFIG_HASH_KEY in (services[name].get("labels") or {}), name


def test_a_service_new_to_the_render_is_not_named(tmp_path: Path, discovery: Discovery) -> None:
    d = _deployment("compose")
    d.spec.apps = []
    _on_disk(_render(d, discovery, discovery.apps), tmp_path)
    d.spec.apps = [AppRef(name="wip-kb")]
    assert "wip-kb" not in changed_outside_scope(
        _render(d, discovery, discovery.apps), tmp_path, "compose", [],
    )

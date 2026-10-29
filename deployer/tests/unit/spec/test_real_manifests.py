"""Smoke test: every real manifest in the repo parses against the spec.

This is the single most important test in step 2 of the v2 deployer
rollout — it proves the spec shape matches the actual components we need
to deploy. Any failure here means either:

1. A design gap in the spec (fix the spec), OR
2. A bad manifest (fix the manifest).

Tests run against committed manifests — no test doubles.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from wip_deploy.spec.app import App
from wip_deploy.spec.component import Component

REPO_ROOT = Path(__file__).parent.parent.parent.parent.parent


def _load_yaml(path: Path) -> dict[str, object]:
    with path.open() as f:
        return yaml.safe_load(f)


# ────────────────────────────────────────────────────────────────────
# Component manifests — discovered from the repo
# ────────────────────────────────────────────────────────────────────


COMPONENT_MANIFESTS = sorted(
    [
        *REPO_ROOT.glob("components/*/wip-component.yaml"),
        *REPO_ROOT.glob("ui/*/wip-component.yaml"),
    ]
)

APP_MANIFESTS = sorted(REPO_ROOT.glob("apps/*/wip-app.yaml"))


@pytest.mark.parametrize(
    "manifest_path",
    COMPONENT_MANIFESTS,
    ids=[p.parent.name for p in COMPONENT_MANIFESTS],
)
def test_component_manifest_parses(manifest_path: Path) -> None:
    data = _load_yaml(manifest_path)
    component = Component.model_validate(data)
    # Sanity: manifest name should be non-empty.
    assert component.metadata.name, f"empty name in {manifest_path}"


@pytest.mark.parametrize(
    "manifest_path",
    APP_MANIFESTS,
    ids=[p.parent.name for p in APP_MANIFESTS],
)
def test_app_manifest_parses(manifest_path: Path) -> None:
    data = _load_yaml(manifest_path)
    app = App.model_validate(data)
    assert app.metadata.name, f"empty name in {manifest_path}"
    assert app.app_metadata.route_prefix.startswith("/")


# ────────────────────────────────────────────────────────────────────
# Aggregate sanity: combined manifests form a coherent deployment
# ────────────────────────────────────────────────────────────────────


def test_all_components_discoverable() -> None:
    """Every core-category component we expect exists."""
    components = [Component.model_validate(_load_yaml(p)) for p in COMPONENT_MANIFESTS]
    names = {c.metadata.name for c in components}

    core_required = {"registry", "def-store", "template-store", "document-store"}
    missing = core_required - names
    assert not missing, f"missing core components: {missing}"

    infra_required = {"mongodb"}
    assert infra_required <= names


def test_def_store_wires_the_template_store_dependency_check() -> None:
    """def-store's terminology-delete guard calls Template Store; the
    manifest must hand it the URL and a key under the names the service
    reads, or the guard cannot run on any install (it then fails closed and
    refuses every non-forced delete)."""
    manifest_path = next(
        p for p in COMPONENT_MANIFESTS if p.parent.name == "def-store"
    )
    component = Component.model_validate(_load_yaml(manifest_path))
    required = {e.name: e.source for e in component.spec.env.required}

    assert required["TEMPLATE_STORE_URL"].from_component == "template-store"
    assert required["TEMPLATE_STORE_API_KEY"].from_secret == "api-key"


def test_no_duplicate_component_names() -> None:
    components = [Component.model_validate(_load_yaml(p)) for p in COMPONENT_MANIFESTS]
    names = [c.metadata.name for c in components]
    assert len(names) == len(set(names)), f"duplicate names: {names}"


def test_no_duplicate_app_names() -> None:
    apps = [App.model_validate(_load_yaml(p)) for p in APP_MANIFESTS]
    names = [a.metadata.name for a in apps]
    assert len(names) == len(set(names)), f"duplicate app names: {names}"


def test_depends_on_references_exist_across_all_manifests() -> None:
    """Every depends_on target is either another component or an app."""
    components = [Component.model_validate(_load_yaml(p)) for p in COMPONENT_MANIFESTS]
    apps = [App.model_validate(_load_yaml(p)) for p in APP_MANIFESTS]
    all_names = {c.metadata.name for c in components} | {
        a.metadata.name for a in apps
    }

    errs: list[str] = []
    for c in components:
        for dep in c.spec.depends_on:
            if dep not in all_names:
                errs.append(f"{c.metadata.name} → {dep} (missing)")
    for a in apps:
        for dep in a.spec.depends_on:
            if dep not in all_names:
                errs.append(f"{a.metadata.name} → {dep} (missing)")

    assert not errs, "unresolved depends_on: " + ", ".join(errs)


def test_maximal_deployment_passes_full_validation() -> None:
    """End-to-end: construct a deployment enabling every optional module +
    every app, run validate_all against the full set of real manifests.

    This is the integration test for the contract layer: spec + component
    manifests + app manifests + cross-cutting validators all agree.
    """
    from wip_deploy.spec import (
        AppRef,
        AuthSpec,
        ComposePlatform,
        Deployment,
        DeploymentMetadata,
        DeploymentSpec,
        ImagesSpec,
        NetworkSpec,
        PlatformSpec,
        SecretsSpec,
    )
    from wip_deploy.spec.validators import validate_all

    components = [Component.model_validate(_load_yaml(p)) for p in COMPONENT_MANIFESTS]
    apps = [App.model_validate(_load_yaml(p)) for p in APP_MANIFESTS]

    optional_names = sorted(
        c.metadata.name for c in components if c.metadata.category == "optional"
    )
    app_names = sorted(a.metadata.name for a in apps)

    deployment = Deployment(
        metadata=DeploymentMetadata(name="maximal-test"),
        spec=DeploymentSpec(
            target="compose",
            modules={"optional": optional_names},  # type: ignore[arg-type]
            apps=[AppRef(name=n) for n in app_names],
            auth=AuthSpec(mode="oidc", gateway=True),
            network=NetworkSpec(hostname="wip.local"),
            images=ImagesSpec(registry="ghcr.io/test", tag="test"),
            platform=PlatformSpec(compose=ComposePlatform(data_dir="/tmp/d")),
            secrets=SecretsSpec(backend="file", location="/tmp/s"),
        ),
    )

    report = validate_all(deployment, components, apps)
    assert report.ok, "maximal deployment validation failed:\n" + "\n".join(
        f"  - {e}" for e in report.errors
    )


# ────────────────────────────────────────────────────────────────────
# Healthcheck commands bound their own HTTP probes
# ────────────────────────────────────────────────────────────────────

# A compose healthcheck's `timeout` only marks a run unhealthy; it does not
# stop the probe process. A manifest-declared `command` that runs curl or
# wget without its own time limit therefore hangs against a server that
# accepts and never answers, and a new probe starts every interval until
# hundreds pile up inside the container. The deployer bounds the probes it
# generates itself; this guards the commands manifests declare.

_CURL = re.compile(r"(?<![\w-])curl(?![\w-])")
_WGET = re.compile(r"(?<![\w-])wget(?![\w-])")
_CURL_BOUND = re.compile(r"(?:--max-time[ =]|-m )(\d+)")
_WGET_BOUND = re.compile(r"(?:-T ?|--timeout=)(\d+)")


def _probe_bound_problems(command: list[str], timeout_seconds: int) -> list[str]:
    line = " ".join(command)
    problems = []
    for tool, uses, bound, flag in (
        ("curl", _CURL, _CURL_BOUND, "--max-time N"),
        ("wget", _WGET, _WGET_BOUND, "-T N"),
    ):
        if not uses.search(line):
            continue
        limits = [int(n) for n in bound.findall(line)]
        if not limits:
            problems.append(f"{tool} without {flag}")
        elif max(limits) > timeout_seconds:
            problems.append(f"{tool} limit {max(limits)}s exceeds timeout_seconds={timeout_seconds}")
    return problems


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        (["wget", "-q", "-O", "/dev/null", "http://h/healthz"], ["wget without -T N"]),
        (["sh", "-c", "curl -fsS http://h/ | grep -q ok"], ["curl without --max-time N"]),
        (["wget", "-q", "-T", "5", "http://h/"], []),
        (["sh", "-c", "curl -fsS --max-time 5 http://h/"], []),
        (["sh", "-c", "curl -m 9 http://h/"], ["curl limit 9s exceeds timeout_seconds=5"]),
        (["pg_isready", "-U", "wip"], []),
    ],
)
def test_probe_bound_detector(command: list[str], expected: list[str]) -> None:
    assert _probe_bound_problems(command, 5) == expected


@pytest.mark.parametrize(
    "manifest_path",
    COMPONENT_MANIFESTS + APP_MANIFESTS,
    ids=[p.parent.name for p in COMPONENT_MANIFESTS + APP_MANIFESTS],
)
def test_healthcheck_command_bounds_its_http_probe(manifest_path: Path) -> None:
    data = _load_yaml(manifest_path)
    model = App if manifest_path.name == "wip-app.yaml" else Component
    hc = model.model_validate(data).spec.healthcheck
    if hc is None or hc.command is None:
        return
    problems = _probe_bound_problems(hc.command, hc.timeout_seconds)
    assert not problems, f"{manifest_path}: healthcheck command {hc.command}: {problems}"

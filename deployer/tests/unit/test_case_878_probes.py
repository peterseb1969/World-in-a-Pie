"""CASE-878 — readiness may fail on a dependency; liveness never does.

k8s used to render readinessProbe and livenessProbe from one endpoint with
no startupProbe. A liveness failure restarts the pod, and a restart cannot
bring a dependency back, so no health endpoint could ever report a down
dependency; and a store still retrying MongoDB at startup (up to 120 s,
before uvicorn serves anything) was killed by liveness after ~80 to 95 s.

Now:
- readinessProbe checks `endpoint`; livenessProbe and startupProbe check
  `liveness_endpoint`, falling back to `endpoint`;
- the startupProbe budget (start_period + 150 s) outlasts the startup retry,
  and k8s runs neither other probe until it first succeeds;
- `retries` is every probe's failureThreshold;
- the stores' readiness is /ready (pings MongoDB) and reporting-sync keeps
  /health (503 on unusable service roles); all five live on /livez.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from wip_deploy.discovery import Discovery, discover
from wip_deploy.renderers.compose import _healthcheck_block
from wip_deploy.renderers.k8s import _probes
from wip_deploy.spec.component import HealthcheckSpec

REPO_ROOT = Path(__file__).resolve().parents[3]
STORES = ("registry", "def-store", "template-store", "document-store")
STARTUP_RETRY_SECONDS = 120  # wip_auth.startup._DEFAULT_MAX_WAIT_SECONDS


@pytest.fixture(scope="module")
def discovery() -> Discovery:
    return discover(REPO_ROOT)


def _component(discovery: Discovery, name: str):
    return next(c for c in discovery.components if c.metadata.name == name)


def _with_healthcheck(component, **fields):
    hc = component.spec.healthcheck.model_copy(update=fields)
    spec = component.spec.model_copy(update={"healthcheck": hc})
    return component.model_copy(update={"spec": spec})


def _path(probe: dict) -> str:
    return probe["httpGet"]["path"]


@pytest.mark.parametrize("name", STORES)
def test_store_readiness_checks_mongodb_and_liveness_does_no_io(discovery, name):
    probes = _probes(_component(discovery, name))
    assert _path(probes["readinessProbe"]) == "/ready"
    assert _path(probes["livenessProbe"]) == "/livez"
    assert _path(probes["startupProbe"]) == "/livez"


def test_reporting_sync_keeps_health_for_readiness(discovery):
    probes = _probes(_component(discovery, "reporting-sync"))
    assert _path(probes["readinessProbe"]) == "/health"
    assert _path(probes["livenessProbe"]) == "/livez"


def test_without_liveness_endpoint_all_probes_share_endpoint(discovery):
    probes = _probes(_component(discovery, "ingest-gateway"))
    paths = {_path(probes[k]) for k in ("readinessProbe", "livenessProbe", "startupProbe")}
    assert paths == {"/api/ingest-gateway/health"}


@pytest.mark.parametrize("name", [*STORES, "reporting-sync"])
def test_startup_budget_outlasts_the_startup_retry(discovery, name):
    component = _component(discovery, name)
    startup = _probes(component)["startupProbe"]
    budget = startup["failureThreshold"] * startup["periodSeconds"]
    assert budget >= component.spec.healthcheck.start_period_seconds + 150
    assert budget > STARTUP_RETRY_SECONDS + component.spec.healthcheck.start_period_seconds


def test_retries_is_every_probes_failure_threshold(discovery):
    component = _with_healthcheck(_component(discovery, "registry"), retries=7)
    probes = _probes(component)
    assert probes["readinessProbe"]["failureThreshold"] == 7
    assert probes["livenessProbe"]["failureThreshold"] == 7


def test_command_checks_get_a_startup_probe_too(discovery):
    probes = _probes(_component(discovery, "mongodb"))
    assert "exec" in probes["startupProbe"]
    assert probes["startupProbe"]["exec"] == probes["livenessProbe"]["exec"]
    # Fresh dicts: a shared one would render as a YAML anchor/alias.
    assert probes["startupProbe"]["exec"] is not probes["livenessProbe"]["exec"]


def test_http_probes_share_no_dicts(discovery):
    probes = _probes(_component(discovery, "ingest-gateway"))  # one path for all three
    gets = [probes[k]["httpGet"] for k in ("readinessProbe", "livenessProbe", "startupProbe")]
    assert len({id(g) for g in gets}) == 3


def test_compose_health_uses_the_readiness_endpoint(discovery):
    block = _healthcheck_block(_component(discovery, "registry"))
    assert "/ready" in block["test"][1]
    assert "/livez" not in block["test"][1]


def test_liveness_endpoint_requires_an_endpoint_check():
    with pytest.raises(ValidationError, match="liveness_endpoint"):
        HealthcheckSpec(command=["true"], liveness_endpoint="/livez")
    with pytest.raises(ValidationError):
        HealthcheckSpec(endpoint="/ready", liveness_endpoint="livez")  # not a path

"""CASE-615 — the deployer injects WIP_VARIANT; the guard family can't
silently re-dormant.

Every backend service's production-safety guard keys on WIP_VARIANT=prod,
and before this case nothing in any deployment path set it — six guards,
all dormant, in every rendered install. These tests pin the whole chain:
spec default, BuildInputs plumbing, state round-trip (redeploy loads the
persisted spec), and the rendered env for the guarded components against
the REAL manifests.
"""

from __future__ import annotations

from pathlib import Path

from wip_deploy.build import BuildInputs, build_deployment
from wip_deploy.config_gen import Literal, make_spec_context, resolve_all_env
from wip_deploy.spec import Deployment

# The components whose startup guards consume WIP_VARIANT: five via
# wip_auth.security.check_production_security + auth-gateway's local
# guard. Keep in lockstep with the guard call sites.
GUARDED = [
    "registry",
    "def-store",
    "template-store",
    "document-store",
    "reporting-sync",
    "auth-gateway",
]


def _with_variant(deployment: Deployment, variant: str) -> Deployment:
    spec = deployment.spec.model_copy(update={"variant": variant})
    return deployment.model_copy(update={"spec": spec})


def test_spec_variant_defaults_to_dev(compose_deployment: Deployment) -> None:
    assert compose_deployment.spec.variant == "dev"


def test_build_inputs_plumb_variant_to_spec() -> None:
    prod = build_deployment(
        BuildInputs(variant="prod", compose_data_dir=Path("/tmp/d"))
    )
    assert prod.spec.variant == "prod"
    default = build_deployment(BuildInputs(compose_data_dir=Path("/tmp/d")))
    assert default.spec.variant == "dev"


def test_variant_survives_state_round_trip(compose_deployment: Deployment) -> None:
    """Redeploy/rebuild re-load the persisted deployer-state spec — the
    variant must survive serialization, or a redeploy would silently
    disarm the guards."""
    prod = _with_variant(compose_deployment, "prod")
    reloaded = Deployment.model_validate(prod.model_dump(mode="json"))
    assert reloaded.spec.variant == "prod"


def test_prod_deployment_arms_every_guarded_component(
    maximal_compose_deployment: Deployment, real_discovery
) -> None:
    prod = _with_variant(maximal_compose_deployment, "prod")
    ctx = make_spec_context(prod, real_discovery.components)
    env = resolve_all_env(prod, real_discovery.components, real_discovery.apps, ctx)
    for name in GUARDED:
        assert name in env, f"{name} not active in maximal deployment"
        assert env[name].required.get("WIP_VARIANT") == Literal("prod"), (
            f"{name} rendered without WIP_VARIANT=prod — its production "
            f"guard would be dormant"
        )


def test_dev_deployment_renders_dev_variant(
    maximal_compose_deployment: Deployment, real_discovery
) -> None:
    ctx = make_spec_context(maximal_compose_deployment, real_discovery.components)
    env = resolve_all_env(
        maximal_compose_deployment,
        real_discovery.components,
        real_discovery.apps,
        ctx,
    )
    for name in GUARDED:
        assert env[name].required.get("WIP_VARIANT") == Literal("dev")


def test_k8s_target_carries_variant_too(
    maximal_k8s_deployment: Deployment, real_discovery
) -> None:
    """Both renderers consume the same resolve_all_env output — pin the
    k8s-shaped deployment as well so per-target drift can't creep in."""
    prod = _with_variant(maximal_k8s_deployment, "prod")
    ctx = make_spec_context(prod, real_discovery.components)
    env = resolve_all_env(prod, real_discovery.components, real_discovery.apps, ctx)
    for name in GUARDED:
        assert env[name].required.get("WIP_VARIANT") == Literal("prod")

"""Activation predicate evaluation.

A single pure function that every layer consults to decide whether a
component (or app) is part of the current deployment. Putting the logic
here — and nowhere else — prevents renderers and config generators from
re-implementing it with subtle differences.

Rules:
  - category=core:           always active
  - category=optional:       active iff name is in deployment.spec.modules.optional
  - category=infrastructure: active iff
        component.spec.activation is None, OR
        every predicate in component.spec.activation holds on the Deployment
"""

from __future__ import annotations

from wip_deploy.spec.component import ActivationSpec, Component
from wip_deploy.spec.deployment import Deployment


def is_component_active(component: Component, deployment: Deployment) -> bool:
    """Return True iff the component is active in this deployment."""
    cat = component.metadata.category

    if cat == "core":
        return True

    if cat == "optional":
        return component.metadata.name in deployment.spec.modules.optional

    # infrastructure
    if component.spec.activation is None:
        return True
    return _activation_predicate_holds(component.spec.activation, deployment)


def _activation_predicate_holds(
    predicate: ActivationSpec, deployment: Deployment
) -> bool:
    """AND of every set field. Empty lists mean 'no requirement'."""
    spec = deployment.spec
    return (
        (
            not predicate.requires_any_module
            or bool(set(spec.modules.optional) & set(predicate.requires_any_module))
        )
        and (
            not predicate.requires_auth_mode
            or spec.auth.mode in predicate.requires_auth_mode
        )
        and (
            predicate.requires_auth_gateway is None
            or spec.auth.gateway == predicate.requires_auth_gateway
        )
    )

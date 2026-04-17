"""Cross-cutting validators that span Deployment + Components + Apps.

Pydantic model validators on `Deployment` and `Component` handle
constraints visible from a single model. The validators here need to see
the Deployment together with the discovered Component / App manifests —
they run after discovery, not on model construction.

Each validator raises `ValidationError` on failure. The `validate_all`
helper collects errors for a single report pass.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from wip_deploy.spec.activation import is_component_active
from wip_deploy.spec.app import App
from wip_deploy.spec.component import Component
from wip_deploy.spec.deployment import Deployment

# ────────────────────────────────────────────────────────────────────


class ValidationError(Exception):
    """One cross-cutting validation failure."""


@dataclass
class ValidationReport:
    errors: list[ValidationError] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def raise_if_errors(self) -> None:
        if self.errors:
            joined = "\n  - ".join(str(e) for e in self.errors)
            raise ValidationError(f"validation failed:\n  - {joined}")


# ────────────────────────────────────────────────────────────────────
# Individual validators (each returns list[ValidationError])
# ────────────────────────────────────────────────────────────────────


def _validate_modules_exist(
    deployment: Deployment, components: list[Component]
) -> list[ValidationError]:
    """Every name in modules.optional must correspond to an optional
    Component manifest on disk."""
    known_optional = {
        c.metadata.name for c in components if c.metadata.category == "optional"
    }
    errs: list[ValidationError] = []
    for mod in deployment.spec.modules.optional:
        if mod not in known_optional:
            errs.append(
                ValidationError(
                    f"modules.optional references unknown component {mod!r} "
                    f"(known optional components: {sorted(known_optional)})"
                )
            )
    return errs


def _validate_apps_exist(
    deployment: Deployment, apps: list[App]
) -> list[ValidationError]:
    """Every entry in deployment.spec.apps must have a manifest on disk."""
    known_apps = {a.metadata.name for a in apps}
    errs: list[ValidationError] = []
    for ref in deployment.spec.apps:
        if ref.name not in known_apps:
            errs.append(
                ValidationError(
                    f"spec.apps references unknown app {ref.name!r} "
                    f"(known apps: {sorted(known_apps)})"
                )
            )
    return errs


def _validate_oidc_clients_require_oidc(
    deployment: Deployment,
    components: list[Component],
    apps: list[App],
) -> list[ValidationError]:
    """Any ACTIVE component or enabled app with an oidc_client requires
    OIDC auth. Inactive components are ignored (they don't contribute to
    this deployment)."""
    if deployment.spec.auth.mode in ("oidc", "hybrid"):
        return []

    errs: list[ValidationError] = []
    for comp in components:
        if not is_component_active(comp, deployment):
            continue
        if comp.spec.oidc_client is not None:
            errs.append(
                ValidationError(
                    f"component {comp.metadata.name!r} declares an oidc_client "
                    f"but auth.mode={deployment.spec.auth.mode!r}"
                )
            )

    enabled_apps = {ref.name for ref in deployment.spec.apps if ref.enabled}
    for app in apps:
        if app.metadata.name in enabled_apps and app.spec.oidc_client is not None:
            errs.append(
                ValidationError(
                    f"app {app.metadata.name!r} declares an oidc_client "
                    f"but auth.mode={deployment.spec.auth.mode!r}"
                )
            )
    return errs


def _validate_depends_on_resolvable(
    components: list[Component], apps: list[App]
) -> list[ValidationError]:
    """Every name in a component/app's depends_on must name a real component
    or app."""
    all_names = {c.metadata.name for c in components} | {a.metadata.name for a in apps}
    errs: list[ValidationError] = []
    for comp in components:
        for dep in comp.spec.depends_on:
            if dep not in all_names:
                errs.append(
                    ValidationError(
                        f"component {comp.metadata.name!r} depends_on unknown "
                        f"component {dep!r}"
                    )
                )
    for app in apps:
        for dep in app.spec.depends_on:
            if dep not in all_names:
                errs.append(
                    ValidationError(
                        f"app {app.metadata.name!r} depends_on unknown "
                        f"component {dep!r}"
                    )
                )
    return errs


def _validate_env_from_component_resolvable(
    components: list[Component], apps: list[App]
) -> list[ValidationError]:
    """Every from_component* reference must name a discovered component."""
    all_names = {c.metadata.name for c in components} | {a.metadata.name for a in apps}
    errs: list[ValidationError] = []
    owners: list[Component | App] = [*components, *apps]
    for owner in owners:
        for env_var in [*owner.spec.env.required, *owner.spec.env.optional]:
            for ref in (
                env_var.source.from_component,
                env_var.source.from_component_host,
                env_var.source.from_component_port,
            ):
                if ref is not None and ref not in all_names:
                    errs.append(
                        ValidationError(
                            f"{owner.metadata.name!r} env {env_var.name!r} "
                            f"references unknown component {ref!r}"
                        )
                    )
    return errs


def _validate_oidc_client_ids_unique(
    components: list[Component], apps: list[App], deployment: Deployment
) -> list[ValidationError]:
    """Dex static client IDs must be unique across all active components/apps."""
    enabled_apps = {ref.name for ref in deployment.spec.apps if ref.enabled}

    client_ids: dict[str, str] = {}  # client_id -> owner_name
    errs: list[ValidationError] = []

    for comp in components:
        if not is_component_active(comp, deployment):
            continue
        if comp.spec.oidc_client is None:
            continue
        cid = comp.spec.oidc_client.client_id
        if cid in client_ids:
            errs.append(
                ValidationError(
                    f"duplicate OIDC client_id {cid!r} "
                    f"(claimed by {client_ids[cid]!r} and {comp.metadata.name!r})"
                )
            )
        else:
            client_ids[cid] = comp.metadata.name

    for app in apps:
        if app.metadata.name not in enabled_apps:
            continue
        if app.spec.oidc_client is None:
            continue
        cid = app.spec.oidc_client.client_id
        if cid in client_ids:
            errs.append(
                ValidationError(
                    f"duplicate OIDC client_id {cid!r} "
                    f"(claimed by {client_ids[cid]!r} and app {app.metadata.name!r})"
                )
            )
        else:
            client_ids[cid] = app.metadata.name

    return errs


# ────────────────────────────────────────────────────────────────────
# Orchestrator
# ────────────────────────────────────────────────────────────────────


def _validate_images_resolvable(
    deployment: Deployment,
    components: list[Component],
    apps: list[App],
) -> list[ValidationError]:
    """For pull-based targets (compose, k8s), every active component with a
    short-name image MUST have a registry set.

    Short-name images (no `/` in `image.name`) are rewritten to
    `{registry}/{name}:{tag}` at render time. Without a registry, the
    compose file would contain bare tags like `registry:v2.0.0` which
    fail to pull from Docker Hub. Catching this up front beats waiting
    for a confusing `manifest unknown` from podman-compose.

    The dev target builds from source via `build_context`, so this
    guard doesn't apply there.
    """
    errors: list[ValidationError] = []
    target = deployment.spec.target
    if target not in ("compose", "k8s"):
        return errors
    if deployment.spec.images.registry is not None:
        return errors

    enabled_app_names = {a.name for a in deployment.spec.apps if a.enabled}
    owners: list[Component | App] = []
    for c in components:
        if is_component_active(c, deployment):
            owners.append(c)
    for a in apps:
        if a.metadata.name in enabled_app_names:
            owners.append(a)

    unresolved: list[str] = []
    for owner in owners:
        if "/" not in owner.spec.image.name:
            unresolved.append(owner.metadata.name)

    if unresolved:
        names = ", ".join(sorted(unresolved))
        errors.append(ValidationError(
            f"target={target!r} needs pre-built images, but no --registry "
            f"was specified and these components use short-name images: "
            f"{names}. Fix: pass --registry <host> (e.g. "
            f"--registry ghcr.io/peterseb1969) to resolve them against a "
            f"published registry, OR use --target dev --dev-mode simple "
            f"to build from local source."
        ))
    return errors


def validate_all(
    deployment: Deployment,
    components: list[Component],
    apps: list[App],
) -> ValidationReport:
    """Run every cross-cutting validator. Collect all errors, don't stop at
    the first."""
    report = ValidationReport()
    report.errors.extend(_validate_modules_exist(deployment, components))
    report.errors.extend(_validate_apps_exist(deployment, apps))
    report.errors.extend(
        _validate_oidc_clients_require_oidc(deployment, components, apps)
    )
    report.errors.extend(_validate_depends_on_resolvable(components, apps))
    report.errors.extend(_validate_env_from_component_resolvable(components, apps))
    report.errors.extend(
        _validate_oidc_client_ids_unique(components, apps, deployment)
    )
    report.errors.extend(_validate_images_resolvable(deployment, components, apps))
    return report

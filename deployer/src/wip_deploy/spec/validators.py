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
from wip_deploy.spec.deployment import APP_EDGE_MODULE, Deployment

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


def _validate_app_edge_prerequisites(deployment: Deployment) -> list[ValidationError]:
    """The app edge needs the auth gateway (it runs forward_auth per
    attachment). As an optional module its activation is its name in modules.optional alone, so the
    requirement is enforced here, loudly, rather than by silently not
    rendering a module the operator asked for."""
    spec = deployment.spec
    if APP_EDGE_MODULE not in spec.modules.optional:
        return []
    if spec.auth.gateway:
        return []
    return [ValidationError(
        f"module {APP_EDGE_MODULE!r} needs the auth gateway (auth.gateway is off); "
        f"remove it (`wip-deploy remove-module {APP_EDGE_MODULE}`, or "
        f"`--remove {APP_EDGE_MODULE}` on install) or turn the gateway on"
    )]


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


def _spec_context_paths() -> set[str]:
    """Every dotted path `from_spec` can name: the string leaves of SpecContext."""
    import dataclasses
    import typing

    from wip_deploy.config_gen import spec_context

    paths: set[str] = set()

    def walk(cls: type, prefix: str) -> None:
        hints = typing.get_type_hints(cls, vars(spec_context))
        for f in dataclasses.fields(cls):
            hint = hints[f.name]
            path = f"{prefix}{f.name}"
            if dataclasses.is_dataclass(hint):
                walk(hint, f"{path}.")
            else:
                paths.add(path)

    walk(spec_context.SpecContext, "")
    return paths


def _validate_from_spec_paths(
    components: list[Component], apps: list[App]
) -> list[ValidationError]:
    """Every `from_spec` names a value the spec context provides. Left to the
    render, an unknown path raised KeyError there, a traceback."""
    known = _spec_context_paths()
    return [
        ValidationError(
            f"{owner.metadata.name!r} env {env_var.name!r} reads from_spec "
            f"{env_var.source.from_spec!r}, which the spec context does not provide"
        )
        for owner in (*components, *apps)
        for env_var in (*owner.spec.env.required, *owner.spec.env.optional)
        if env_var.source.from_spec is not None and env_var.source.from_spec not in known
    ]


def _validate_required_env_targets_active(
    deployment: Deployment, components: list[Component], apps: list[App]
) -> list[ValidationError]:
    """A REQUIRED env var of an active owner must reference an active
    component or enabled app: the design assigns this to validation, and left
    to env resolution it surfaced as a traceback (an optional var whose
    target is inactive is simply left unset)."""
    enabled = {r.name for r in deployment.spec.apps if r.enabled}
    active = {c.metadata.name for c in components if is_component_active(c, deployment)} | enabled
    owners: list[Component | App] = [
        *(c for c in components if is_component_active(c, deployment)),
        *(a for a in apps if a.metadata.name in enabled),
    ]
    errs: list[ValidationError] = []
    for owner in owners:
        for env_var in owner.spec.env.required:
            src = env_var.source
            for ref in (src.from_component, src.from_component_host, src.from_component_port):
                if ref is not None and ref not in active:
                    errs.append(ValidationError(
                        f"{owner.metadata.name!r} requires env {env_var.name!r} from "
                        f"{ref!r}, which this install does not run; enable it, or make "
                        "the var optional"
                    ))
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


def _validate_app_labels_unique(deployment: Deployment, apps: list[App]) -> list[ValidationError]:
    """Each enabled app's label names its origin, <label>.<hostname>: two
    apps cannot share one."""
    enabled = {ref.name for ref in deployment.spec.apps if ref.enabled}
    owners: dict[str, str] = {}
    errs: list[ValidationError] = []
    for app in apps:
        if app.metadata.name not in enabled:
            continue
        label = app.app_metadata.label
        if label in owners:
            errs.append(ValidationError(
                f"apps {owners[label]!r} and {app.metadata.name!r} both have label {label!r}; "
                f"each app is served on its own origin, {label}.<hostname>"
            ))
        else:
            owners[label] = app.metadata.name
    return errs


# Names the renderers give a service of their own, outside the component
# list: the public Caddy (compose service `caddy`, k8s `services/caddy.yaml`).
RESERVED_SERVICE_NAMES = frozenset({"caddy"})


def _validate_app_names_free(
    deployment: Deployment, components: list[Component], apps: list[App],
) -> list[ValidationError]:
    """An enabled app's name is its service's name on every target (the
    compose service key, the k8s Service and its file), so it cannot be a
    component's or the front door's: the renderer would silently replace
    one with the other."""
    enabled = {ref.name for ref in deployment.spec.apps if ref.enabled}
    taken = {c.metadata.name for c in components} | RESERVED_SERVICE_NAMES
    return [
        ValidationError(
            f"app {app.metadata.name!r} has the name of "
            f"{'the front door' if app.metadata.name in RESERVED_SERVICE_NAMES else 'a component'}; "
            "rename the app — its service would replace that one"
        )
        for app in apps
        if app.metadata.name in enabled and app.metadata.name in taken
    ]


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


def _validate_namespace_reservations(
    deployment: Deployment, apps: list[App]
) -> list[ValidationError]:
    """Enabled apps' namespace reservations agree: one owner per namespace,
    one reservation per ID prefix (config_gen/namespaces.py)."""
    from wip_deploy.config_gen.namespaces import reservation_conflicts

    return [ValidationError(e) for e in reservation_conflicts(deployment, apps)]


def _validate_app_key_names(
    deployment: Deployment, apps: list[App]
) -> list[ValidationError]:
    """A spec-declared key must not take the name of an app's derived key:
    one name would stand for two keys."""
    from wip_deploy.config_gen.api_keys import app_api_keys

    derived = {k.name: k.owner for k in app_api_keys(deployment, apps)}
    return [
        ValidationError(
            f"auth.api_keys declares {k.name!r}, the name of the key derived for "
            f"{derived[k.name].removeprefix('app:')}; rename the declared key"
        )
        for k in deployment.spec.auth.api_keys
        if k.name in derived
    ]


def _validate_app_operator_secrets(
    deployment: Deployment, components: list[Component], apps: list[App]
) -> list[ValidationError]:
    """An app's operator-supplied secret must not be one another owner uses.

    The manifest rule (App.reads_only_its_own_secrets) lets an app name any
    secret the operator supplies, and nothing about the name says whose it
    is: `supplied_by: operator` on `app-dnd-api-key` or `mongo-root-password`
    would hand the app that secret's value from the install's store. So the
    names the platform uses are refused here: the master key, every secret a
    component manifest names, every app key and declared key, another app's
    deployer-supplied secrets, and the families the deployer generates
    (NATS and MongoDB user passwords, Dex passwords and client secrets,
    Garage's), bcrypt companions included. Operator secrets shared between
    apps, such as an `anthropic-api-key`, stay allowed.
    """
    from wip_deploy.config_gen.api_keys import (
        APP_EDGE_KEY,
        CANARY_KEY,
        MASTER_KEY_SECRET,
        app_api_key,
    )
    from wip_deploy.config_gen.dex import (
        CLIENT_SECRET_PREFIX as DEX_CLIENT_PREFIX,
    )
    from wip_deploy.config_gen.dex import (
        PASSWORD_SECRET_PREFIX as DEX_PASSWORD_PREFIX,
    )
    from wip_deploy.config_gen.garage import GARAGE_SECRET_NAMES
    from wip_deploy.config_gen.mongodb import PASSWORD_SECRET_PREFIX as MONGO_PREFIX
    from wip_deploy.config_gen.mongodb import ROOT_PASSWORD_SECRET
    from wip_deploy.config_gen.nats import PASSWORD_SECRET_PREFIX as NATS_PREFIX
    from wip_deploy.secrets import BCRYPT_SUFFIX

    owners: dict[str, str] = {MASTER_KEY_SECRET: "the install's master key"}
    for name in (*GARAGE_SECRET_NAMES, ROOT_PASSWORD_SECRET):
        owners[name] = "a secret the deployer generates"
    # The keys' names one by one, not through effective_api_keys: that refuses
    # a name used twice, which _validate_app_key_names reports by name.
    for key in (*deployment.spec.auth.api_keys, APP_EDGE_KEY, CANARY_KEY):
        owners[key.secret_name] = "an API key of this install"
    for c in deployment.spec.auth.connectors:
        if c.secret_key:
            owners[c.secret_name] = f"Dex connector {c.id!r}"
    for c in components:
        for ev in (*c.spec.env.required, *c.spec.env.optional):
            if ev.source.from_secret is not None:
                owners.setdefault(ev.source.from_secret, f"component {c.metadata.name!r}")
    for a in apps:
        key = app_api_key(a)
        if key is not None:
            owners[key.secret_name] = f"app {a.metadata.name!r}'s own key"
        for ev in (*a.spec.env.required, *a.spec.env.optional):
            if ev.source.from_secret is not None and ev.source.supplied_by != "operator":
                owners.setdefault(ev.source.from_secret, f"app {a.metadata.name!r}")
    generated = (NATS_PREFIX, MONGO_PREFIX, DEX_PASSWORD_PREFIX, DEX_CLIENT_PREFIX)

    def owner_of(secret: str) -> str | None:
        base = secret.removesuffix(BCRYPT_SUFFIX)
        if base in owners:
            return owners[base]
        if base.startswith(generated):
            return "a secret the deployer generates"
        return None

    enabled = {r.name for r in deployment.spec.apps if r.enabled}
    errs: list[ValidationError] = []
    for a in apps:
        if a.metadata.name not in enabled:
            continue
        for ev in a.spec.env.optional:
            secret = ev.source.from_secret
            if secret is None or ev.source.supplied_by != "operator":
                continue
            owner = owner_of(secret)
            if owner is not None:
                errs.append(ValidationError(
                    f"app {a.metadata.name!r}: {ev.name} names {secret!r} as an "
                    f"operator-supplied secret, but that is {owner}. An app may only "
                    "be handed a secret no other owner uses; give the operator's "
                    "secret a name of its own"
                ))
    return errs


def validate_all(
    deployment: Deployment,
    components: list[Component],
    apps: list[App],
) -> ValidationReport:
    """Run every cross-cutting validator. Collect all errors, don't stop at
    the first."""
    report = ValidationReport()
    report.errors.extend(_validate_modules_exist(deployment, components))
    report.errors.extend(_validate_app_edge_prerequisites(deployment))
    report.errors.extend(_validate_apps_exist(deployment, apps))
    report.errors.extend(
        _validate_oidc_clients_require_oidc(deployment, components, apps)
    )
    report.errors.extend(_validate_depends_on_resolvable(components, apps))
    report.errors.extend(_validate_env_from_component_resolvable(components, apps))
    report.errors.extend(_validate_required_env_targets_active(deployment, components, apps))
    report.errors.extend(_validate_from_spec_paths(components, apps))
    report.errors.extend(
        _validate_oidc_client_ids_unique(components, apps, deployment)
    )
    report.errors.extend(_validate_images_resolvable(deployment, components, apps))
    report.errors.extend(_validate_namespace_reservations(deployment, apps))
    report.errors.extend(_validate_app_key_names(deployment, apps))
    report.errors.extend(_validate_app_labels_unique(deployment, apps))
    report.errors.extend(_validate_app_names_free(deployment, components, apps))
    report.errors.extend(_validate_app_operator_secrets(deployment, components, apps))
    return report

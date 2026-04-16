"""Per-component env variable resolution.

Each component declares its env (required + optional) with an EnvSource.
This module resolves those sources into concrete values or secret
references, producing an `EnvMap` per component that renderers consume.

Resolution strategy per EnvSource type:
  - literal                → Literal(value)
  - from_spec              → Literal(resolved SpecContext value)
  - from_secret            → SecretRef(name) — backend resolves later
  - from_component         → Literal(URL of that component for this target)
  - from_component_host    → Literal(DNS name of that component)
  - from_component_port    → Literal(default port of that component)

Target-awareness lives here. Moving between compose/k8s/dev changes
only URLs/hostnames — the set of env vars and their named references do
not change.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal as TypingLiteral

from wip_deploy.config_gen.spec_context import SpecContext, resolve_from_spec
from wip_deploy.spec import Deployment
from wip_deploy.spec.activation import is_component_active
from wip_deploy.spec.app import App
from wip_deploy.spec.component import Component, EnvSource, Port

# ────────────────────────────────────────────────────────────────────
# Resolved env value types
# ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Literal:
    """A concrete env value. Renderer writes verbatim."""

    value: str


@dataclass(frozen=True)
class SecretRef:
    """A reference to a named secret. Renderer substitutes at render time:
    file-backend renders as a literal, k8s-backend renders as a
    SecretKeyRef."""

    name: str


EnvValue = Literal | SecretRef
EnvMap = dict[str, EnvValue]


class InactiveComponentRef(LookupError):
    """Raised when `from_component*` references a component that exists
    in the repo but isn't active in this deployment.

    Callers distinguish this from plain `KeyError` (unknown component):
    optional env vars can silently skip on InactiveComponentRef, but
    required env vars must propagate it as a hard error.
    """


class UncollectedSecretRef(LookupError):
    """Raised when `from_secret` references a secret that wasn't
    collected for this deployment.

    Required env vars only collect secrets they actually need. Optional
    env vars may point at secrets whose natural source (e.g. minio for
    minio-root-password) is inactive — in that case the secret simply
    isn't generated, and the optional env var should be omitted rather
    than interpolating to empty string.
    """


@dataclass(frozen=True)
class ResolvedEnv:
    """A component's fully-resolved env, split by required vs optional.

    Required vars must have a value at render time (an unresolved SecretRef
    without a backend binding is a render-time error). Optional vars may
    be omitted if their source is absent (e.g., an optional secret that
    doesn't exist).
    """

    required: EnvMap
    optional: EnvMap

    def merged(self) -> EnvMap:
        """Flat dict for simple downstream consumers. Required wins on
        collision (shouldn't happen — spec uniqueness is separate)."""
        return {**self.optional, **self.required}


# ────────────────────────────────────────────────────────────────────
# Component URL helpers
# ────────────────────────────────────────────────────────────────────


# Non-HTTP URL schemes by component name. Everything else defaults to http.
_COMPONENT_SCHEMES: dict[str, str] = {
    "mongodb": "mongodb",
    "postgres": "postgresql",
    "nats": "nats",
}


def _default_port(component: Component) -> Port:
    """Pick the port a reference resolves to.

    Preference order: port named "http", then the first declared port.
    Components with multiple ports put the primary (data-path) port
    first; secondary ports (monitoring, console, etc.) come after. This
    matches how NATS (`[nats:4222, monitor:8222]`) and MinIO
    (`[s3:9000, console:9001]`) declare.
    """
    by_name = {p.name: p for p in component.spec.ports}
    if "http" in by_name:
        return by_name["http"]
    if component.spec.ports:
        return component.spec.ports[0]
    raise ValueError(f"component {component.metadata.name!r} has no ports")


def _component_host(
    name: str,
    target: TypingLiteral["compose", "k8s", "dev"],
    *,
    namespace: str = "wip",
) -> str:
    """Render the DNS name at which a component is reachable."""
    if target == "k8s":
        return f"wip-{name}.{namespace}.svc.cluster.local"
    # compose + dev both use the container-network shortname.
    return f"wip-{name}"


def _component_url(
    component: Component,
    target: TypingLiteral["compose", "k8s", "dev"],
    *,
    namespace: str = "wip",
) -> str:
    host = _component_host(component.metadata.name, target, namespace=namespace)
    port = _default_port(component)
    scheme = _COMPONENT_SCHEMES.get(component.metadata.name, "http")
    # Trailing slash matters for URI-style schemes (mongodb, postgresql);
    # hostname-style (http, nats) conventionally omit it.
    if scheme in ("mongodb", "postgresql"):
        return f"{scheme}://{host}:{port.container_port}/"
    return f"{scheme}://{host}:{port.container_port}"


# ────────────────────────────────────────────────────────────────────
# Resolution
# ────────────────────────────────────────────────────────────────────


def resolve_env_source(
    source: EnvSource,
    *,
    deployment: Deployment,
    ctx: SpecContext,
    components_by_name: dict[str, Component],
    active_names: set[str],
    namespace: str,
    collected_secrets: set[str] | None = None,
) -> EnvValue:
    """Resolve a single EnvSource to an EnvValue.

    Raises:
      - KeyError if a referenced name doesn't exist in the repo or
        `from_spec` path doesn't resolve
      - InactiveComponentRef if a `from_component*` target exists but
        is not active in this deployment (optional envs should skip;
        required envs should fail)
      - UncollectedSecretRef if `from_secret` references a secret that
        wasn't collected (same skip/fail semantics as component refs)
      - ValueError if a referenced component has no ports

    When `collected_secrets` is None, `from_secret` always succeeds
    (back-compat for callers that don't yet thread secrets through).
    """
    if source.literal is not None:
        return Literal(source.literal)

    if source.from_spec is not None:
        return Literal(resolve_from_spec(source.from_spec, ctx))

    if source.from_secret is not None:
        if collected_secrets is not None and source.from_secret not in collected_secrets:
            raise UncollectedSecretRef(
                f"from_secret references uncollected secret "
                f"{source.from_secret!r}"
            )
        return SecretRef(source.from_secret)

    if source.from_component is not None:
        target_c = _lookup_active_component(
            source.from_component, components_by_name, active_names, "from_component"
        )
        return Literal(
            _component_url(target_c, deployment.spec.target, namespace=namespace)
        )

    if source.from_component_host is not None:
        _lookup_active_component(
            source.from_component_host,
            components_by_name,
            active_names,
            "from_component_host",
        )
        return Literal(
            _component_host(
                source.from_component_host,
                deployment.spec.target,
                namespace=namespace,
            )
        )

    if source.from_component_port is not None:
        target_c = _lookup_active_component(
            source.from_component_port,
            components_by_name,
            active_names,
            "from_component_port",
        )
        return Literal(str(_default_port(target_c).container_port))

    # spec.exactly_one_source makes this unreachable
    raise AssertionError(f"EnvSource has no source set: {source!r}")


def _lookup_active_component(
    name: str,
    components_by_name: dict[str, Component],
    active_names: set[str],
    field: str,
) -> Component:
    """Look up a component by name, requiring it to be active. Raises
    KeyError for unknown names; InactiveComponentRef for known-but-
    inactive names."""
    target = components_by_name.get(name)
    if target is None:
        raise KeyError(f"{field} references unknown {name!r}")
    if name not in active_names:
        raise InactiveComponentRef(
            f"{field} references inactive component {name!r}"
        )
    return target


# ────────────────────────────────────────────────────────────────────


def resolve_component_env(
    component: Component | App,
    deployment: Deployment,
    ctx: SpecContext,
    components: list[Component],
    apps: list[App],
    collected_secrets: set[str] | None = None,
) -> ResolvedEnv:
    """Resolve a single component's (or app's) entire env declaration.

    Optional env vars whose `from_component*` target exists but is
    inactive are silently omitted — that's how WIP services toggle
    behavior (e.g. document-store publishes to NATS only when NATS
    is active). Required env vars with inactive targets raise.

    `collected_secrets` names the secrets that will be present in the
    deployment's secret backend. Optional env vars whose `from_secret`
    name isn't collected are skipped (symmetric with from_component*);
    required env vars with uncollected secrets raise. When None,
    `from_secret` always succeeds (back-compat).
    """
    components_by_name: dict[str, Component] = {c.metadata.name: c for c in components}
    for a in apps:
        components_by_name.setdefault(a.metadata.name, a)  # type: ignore[arg-type]

    # An optional env var pointing at an inactive from_component* is a
    # deliberate "turn this integration off" signal — not an error.
    active_names: set[str] = {
        c.metadata.name for c in components if is_component_active(c, deployment)
    }
    enabled_app_names = {a.name for a in deployment.spec.apps if a.enabled}
    active_names.update(
        a.metadata.name for a in apps if a.metadata.name in enabled_app_names
    )

    namespace = (
        deployment.spec.platform.k8s.namespace
        if deployment.spec.target == "k8s" and deployment.spec.platform.k8s
        else "wip"
    )

    def _resolve(evars: list, *, skip_inactive: bool) -> EnvMap:  # type: ignore[type-arg]
        out: EnvMap = {}
        for ev in evars:
            try:
                out[ev.name] = resolve_env_source(
                    ev.source,
                    deployment=deployment,
                    ctx=ctx,
                    components_by_name=components_by_name,
                    active_names=active_names,
                    namespace=namespace,
                    collected_secrets=collected_secrets,
                )
            except (InactiveComponentRef, UncollectedSecretRef):
                if skip_inactive:
                    continue
                raise
        return out

    return ResolvedEnv(
        required=_resolve(component.spec.env.required, skip_inactive=False),
        optional=_resolve(component.spec.env.optional, skip_inactive=True),
    )


def resolve_all_env(
    deployment: Deployment,
    components: list[Component],
    apps: list[App],
    ctx: SpecContext,
    collected_secrets: set[str] | None = None,
) -> dict[str, ResolvedEnv]:
    """Resolve env for every ACTIVE component + enabled app in the
    deployment. Inactive components are skipped entirely — they would
    fail resolution (required env vars referencing their own inactive
    dependencies) and wouldn't be emitted to the compose file anyway.

    `collected_secrets` gates optional `from_secret` references — see
    `resolve_component_env` for details.
    """
    enabled_app_names = {a.name for a in deployment.spec.apps if a.enabled}

    out: dict[str, ResolvedEnv] = {}
    for c in components:
        if not is_component_active(c, deployment):
            continue
        out[c.metadata.name] = resolve_component_env(
            c, deployment, ctx, components, apps, collected_secrets
        )
    for a in apps:
        if a.metadata.name not in enabled_app_names:
            continue
        out[a.metadata.name] = resolve_component_env(
            a, deployment, ctx, components, apps, collected_secrets
        )
    return out

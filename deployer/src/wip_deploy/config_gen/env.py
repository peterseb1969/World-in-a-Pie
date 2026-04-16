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
    namespace: str,
) -> EnvValue:
    """Resolve a single EnvSource to an EnvValue.

    Raises KeyError if `from_spec` path doesn't resolve, or if a
    `from_component*` references an unknown name. Raises ValueError if
    a referenced component has no ports.
    """
    if source.literal is not None:
        return Literal(source.literal)

    if source.from_spec is not None:
        return Literal(resolve_from_spec(source.from_spec, ctx))

    if source.from_secret is not None:
        return SecretRef(source.from_secret)

    if source.from_component is not None:
        target_c = components_by_name.get(source.from_component)
        if target_c is None:
            raise KeyError(
                f"from_component references unknown {source.from_component!r}"
            )
        return Literal(
            _component_url(target_c, deployment.spec.target, namespace=namespace)
        )

    if source.from_component_host is not None:
        if source.from_component_host not in components_by_name:
            raise KeyError(
                f"from_component_host references unknown "
                f"{source.from_component_host!r}"
            )
        return Literal(
            _component_host(
                source.from_component_host,
                deployment.spec.target,
                namespace=namespace,
            )
        )

    if source.from_component_port is not None:
        target_c = components_by_name.get(source.from_component_port)
        if target_c is None:
            raise KeyError(
                f"from_component_port references unknown "
                f"{source.from_component_port!r}"
            )
        return Literal(str(_default_port(target_c).container_port))

    # spec.exactly_one_source makes this unreachable
    raise AssertionError(f"EnvSource has no source set: {source!r}")


# ────────────────────────────────────────────────────────────────────


def resolve_component_env(
    component: Component | App,
    deployment: Deployment,
    ctx: SpecContext,
    components: list[Component],
    apps: list[App],
) -> ResolvedEnv:
    """Resolve a single component's (or app's) entire env declaration."""
    components_by_name: dict[str, Component] = {c.metadata.name: c for c in components}
    # Apps can also be referenced from_component (e.g., inter-app URLs).
    # Treat app components uniformly for URL resolution purposes — an App
    # has the same ComponentSpec shape.
    for a in apps:
        # Intentionally shadow the App as a Component-like for URL lookup.
        components_by_name.setdefault(a.metadata.name, a)  # type: ignore[arg-type]

    namespace = (
        deployment.spec.platform.k8s.namespace
        if deployment.spec.target == "k8s" and deployment.spec.platform.k8s
        else "wip"
    )

    def _resolve(evars: list) -> EnvMap:  # type: ignore[type-arg]
        out: EnvMap = {}
        for ev in evars:
            out[ev.name] = resolve_env_source(
                ev.source,
                deployment=deployment,
                ctx=ctx,
                components_by_name=components_by_name,
                namespace=namespace,
            )
        return out

    return ResolvedEnv(
        required=_resolve(component.spec.env.required),
        optional=_resolve(component.spec.env.optional),
    )


def resolve_all_env(
    deployment: Deployment,
    components: list[Component],
    apps: list[App],
    ctx: SpecContext,
) -> dict[str, ResolvedEnv]:
    """Resolve env for every component + app in the deployment. Returned
    dict is keyed by component/app name."""
    out: dict[str, ResolvedEnv] = {}
    for c in components:
        out[c.metadata.name] = resolve_component_env(c, deployment, ctx, components, apps)
    for a in apps:
        out[a.metadata.name] = resolve_component_env(a, deployment, ctx, components, apps)
    return out

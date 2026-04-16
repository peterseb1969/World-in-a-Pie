"""Component manifest model.

A Component declares everything a renderer needs to know about a single
service. Manifests live next to the service they describe:
`components/<name>/wip-component.yaml`.

Infrastructure services (mongodb, postgres, nats, minio, dex) are components
too — they differ only in having `image.build_context=None` (pre-built
images only). This keeps the pipeline uniform: no "service vs infrastructure"
code paths.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator

from wip_deploy.spec._base import WIPModel

# ────────────────────────────────────────────────────────────────────
# Env variable resolution
# ────────────────────────────────────────────────────────────────────


class EnvSource(WIPModel):
    """Declarative source for an env var value.

    Exactly one of the six fields must be set. Resolution happens in
    `config_gen/env.py`, which is target-aware:

      - `literal`:             constant string, used verbatim
      - `from_spec`:            dotted path into a SpecContext (computed
                                values derived from the Deployment)
      - `from_secret`:          reference to a named secret; resolved
                                later by the secret backend
      - `from_component`:       full URL of another component (e.g.,
                                `mongodb://wip-mongodb:27017/`)
      - `from_component_host`:  DNS name of another component only
                                (e.g., `wip-mongodb`)
      - `from_component_port`:  port number of another component as string
    """

    literal: str | None = None
    from_spec: str | None = None
    from_secret: str | None = None
    from_component: str | None = None
    from_component_host: str | None = None
    from_component_port: str | None = None

    @model_validator(mode="after")
    def exactly_one_source(self) -> EnvSource:
        sources = (
            "literal",
            "from_spec",
            "from_secret",
            "from_component",
            "from_component_host",
            "from_component_port",
        )
        set_fields = [name for name in sources if getattr(self, name) is not None]
        if len(set_fields) != 1:
            raise ValueError(
                f"EnvSource must set exactly one of {sources}; "
                f"got: {set_fields or 'none'}"
            )
        return self


class EnvVar(WIPModel):
    name: str = Field(min_length=1)
    source: EnvSource


class EnvSpec(WIPModel):
    required: list[EnvVar] = Field(default_factory=list)
    optional: list[EnvVar] = Field(default_factory=list)


# ────────────────────────────────────────────────────────────────────
# Network / routing
# ────────────────────────────────────────────────────────────────────


class Port(WIPModel):
    name: str = Field(min_length=1)
    container_port: int = Field(ge=1, le=65535)
    protocol: Literal["TCP", "UDP"] = "TCP"


class Route(WIPModel):
    """An HTTP route this component exposes.

    `auth_required=True` combined with `spec.auth.gateway=True` at the
    deployment level places the request through the auth-gateway. How
    that is expressed is target-specific:
      - compose/dev: Caddy `forward_auth` block
      - k8s:        NGINX Ingress `auth-url` annotation
    """

    path: str = Field(pattern=r"^/.*")
    auth_required: bool = True
    streaming: bool = False


# ────────────────────────────────────────────────────────────────────
# Storage / resources / health
# ────────────────────────────────────────────────────────────────────


class StorageSpec(WIPModel):
    name: str = Field(min_length=1)
    mount_path: str = Field(pattern=r"^/.*")
    size: str = "10Gi"
    access_mode: Literal["ReadWriteOnce", "ReadWriteMany"] = "ReadWriteOnce"


class HealthcheckSpec(WIPModel):
    """Healthcheck for a component.

    Exactly one of `endpoint` (HTTP GET must return 2xx) or `command`
    (executed inside the container; exit 0 = healthy) must be set.
    Infrastructure components like mongodb/postgres use `command`;
    WIP services use `endpoint`.

    `port` names which Port to probe for HTTP checks. Default resolution
    (in the renderer): the port named "http" if present, else the port
    named "monitor", else the first declared port. Explicit `port` is
    required only when both ambiguity and multiple HTTP-ish ports exist
    (e.g., NATS with data on :4222 and monitoring on :8222).
    """

    endpoint: str | None = Field(default=None, pattern=r"^/.*")
    port: str | None = None
    command: list[str] | None = None
    # Probe tool for HTTP endpoint checks. `auto` emits a shell-chained
    # `curl || wget` probe that works regardless of which is installed;
    # required for images with wget-only (most Alpine-based apps).
    # `curl` or `wget` forces one specifically (slightly smaller command).
    # Irrelevant for `command`-style checks.
    probe: Literal["curl", "wget", "auto"] = "auto"
    interval_seconds: int = Field(default=10, ge=1)
    timeout_seconds: int = Field(default=5, ge=1)
    retries: int = Field(default=3, ge=1)
    start_period_seconds: int = Field(default=30, ge=0)

    @model_validator(mode="after")
    def exactly_one_check_type(self) -> HealthcheckSpec:
        has_endpoint = self.endpoint is not None
        has_command = self.command is not None
        if has_endpoint == has_command:
            raise ValueError(
                "HealthcheckSpec must set exactly one of (endpoint, command); "
                f"got endpoint={self.endpoint!r}, command={self.command!r}"
            )
        if self.command is not None and not self.command:
            raise ValueError("HealthcheckSpec.command must not be empty")
        if self.port is not None and self.endpoint is None:
            raise ValueError("HealthcheckSpec.port only valid with endpoint check")
        return self


class ResourceSpec(WIPModel):
    cpu_request: str | None = None
    memory_request: str | None = None
    cpu_limit: str | None = None
    memory_limit: str | None = None


# ────────────────────────────────────────────────────────────────────
# OIDC client / post-install / observability
# ────────────────────────────────────────────────────────────────────


class OidcClientSpec(WIPModel):
    """Declares this component as an OIDC client in Dex.

    The client_secret is never stored here; it's resolved via the secret
    backend at install time and injected into Dex config + component env.
    """

    client_id: str = Field(min_length=1)
    redirect_paths: list[str] = Field(default_factory=lambda: ["/auth/callback"])


class PostInstallHook(WIPModel):
    """Escape hatch for actions the platform cannot express declaratively
    (e.g., Registry's /initialize-wip API call).

    Runs after the component reports healthy. If the list of hooks grows
    beyond a handful across all components, investigate the gap.
    """

    name: str = Field(min_length=1)
    run: str = Field(min_length=1)
    after: Literal["healthy", "ready"] = "healthy"


class ObservabilitySpec(WIPModel):
    """Reserved for v2-future. Unused by any renderer currently."""

    logs: dict[str, str] = Field(default_factory=dict)
    metrics: dict[str, str] = Field(default_factory=dict)
    traces: dict[str, str] = Field(default_factory=dict)


# ────────────────────────────────────────────────────────────────────
# Activation
# ────────────────────────────────────────────────────────────────────


class ActivationSpec(WIPModel):
    """When an infrastructure component is active.

    Components with `category=core` are always active. Components with
    `category=optional` are active iff their name appears in
    `deployment.spec.modules.optional`.

    Components with `category=infrastructure` are active when:
      - no `activation` block is present (always active), OR
      - all set predicates evaluate to True.

    Empty lists mean "no requirement on that dimension" (not "require
    empty").
    """

    requires_any_module: list[str] = Field(default_factory=list)
    """At least one of these optional modules must be active."""

    requires_auth_mode: list[Literal["oidc", "api-key-only", "hybrid"]] = Field(
        default_factory=list
    )
    """auth.mode must be one of these values."""

    requires_auth_gateway: bool | None = None
    """auth.gateway must equal this value. None = no requirement."""


# ────────────────────────────────────────────────────────────────────
# Image
# ────────────────────────────────────────────────────────────────────


class ImageRef(WIPModel):
    """Where the component's image comes from.

    `name` forms:
      - Short name (e.g. `document-store`): combined with
        `spec.images.registry` → `{registry}/{name}:{tag_or_spec_tag}`.
      - Fully-qualified (e.g. `docker.io/library/mongo`): used verbatim
        plus `:{tag}`. The `/` character discriminates; renderers MUST
        detect it rather than duplicating the check.

    `tag=None` means "inherit `spec.images.tag`" — right for WIP services
    which track the deployment-wide tag. Infrastructure components
    (mongo, postgres, dex) pin their own tag to a specific upstream
    version and must set this explicitly.

    `build_context=None` means pre-built image only. `build_context=Path(...)`
    enables local builds on compose/dev when `spec.images.registry` is None.
    """

    name: str = Field(min_length=1, pattern=r"^[a-z0-9][a-z0-9._/-]*$")
    tag: str | None = None
    build_context: Path | None = None
    build_args: dict[str, str] = Field(default_factory=dict)


# ────────────────────────────────────────────────────────────────────
# Top-level Component
# ────────────────────────────────────────────────────────────────────


class ComponentMetadata(WIPModel):
    name: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9-]*$")
    category: Literal["core", "optional", "infrastructure"]
    description: str


class ComponentSpec(WIPModel):
    image: ImageRef
    ports: list[Port] = Field(default_factory=list)
    env: EnvSpec = Field(default_factory=EnvSpec)
    routes: list[Route] = Field(default_factory=list)
    storage: list[StorageSpec] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    healthcheck: HealthcheckSpec | None = None
    resources: ResourceSpec | None = None
    oidc_client: OidcClientSpec | None = None
    post_install: list[PostInstallHook] = Field(default_factory=list)
    observability: ObservabilitySpec | None = None
    activation: ActivationSpec | None = None
    # Explicit entrypoint command. When set, overrides both the image's
    # default CMD and the renderer's uvicorn heuristic. Use when an
    # image's default CMD is wrong (e.g., mismatched port) or when the
    # service uses a non-uvicorn entry point (e.g., `python -m wip_mcp`).
    command: list[str] | None = None

    @model_validator(mode="after")
    def unique_port_names(self) -> ComponentSpec:
        names = [p.name for p in self.ports]
        if len(names) != len(set(names)):
            raise ValueError(f"duplicate port names: {names}")
        return self

    @model_validator(mode="after")
    def unique_storage_names(self) -> ComponentSpec:
        names = [s.name for s in self.storage]
        if len(names) != len(set(names)):
            raise ValueError(f"duplicate storage names: {names}")
        return self

    @model_validator(mode="after")
    def unique_route_paths(self) -> ComponentSpec:
        paths = [r.path for r in self.routes]
        if len(paths) != len(set(paths)):
            raise ValueError(f"duplicate route paths: {paths}")
        return self


class Component(WIPModel):
    """Top-level component manifest.

    Loaded from `components/<name>/wip-component.yaml`. Referenced by
    `modules.optional` in the Deployment spec (for optional components)
    or implicitly active (for category=core and category=infrastructure).
    """

    api_version: Literal["wip.dev/v1"] = "wip.dev/v1"
    kind: Literal["Component"] = "Component"
    metadata: ComponentMetadata
    spec: ComponentSpec

    @field_validator("kind", mode="before")
    @classmethod
    def _normalize_kind(cls, v: object) -> object:
        return v

"""Component manifest model.

A Component declares everything a renderer needs to know about a single
service. Manifests live next to the service they describe:
`components/<name>/wip-component.yaml`.

Infrastructure services (mongodb, postgres, nats, garage, dex) are components
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
    # Strip the route's path prefix before forwarding to the backend.
    # Caddy: `handle_path` (vs `handle`). NGINX Ingress: `rewrite-target: /`.
    # Needed for backends that serve at the root and don't know about the
    # public prefix — MinIO's S3 API is the canonical case.
    strip_prefix: bool = False
    # Subpaths within `path` that should NOT be stripped — useful when
    # the same backend exposes both root-level and prefixed APIs under
    # the public prefix. MinIO is the canonical case: its S3 API at
    # /<bucket>/<key> requires strip_prefix=True (presigned URLs encode
    # the bucket+key path), but its admin/health endpoints live at
    # an object store's own health/admin endpoints under its URL space.
    # Renderers emit these as preserve-prefix routes that take
    # precedence over the regex/strip rule. Only meaningful when
    # `strip_prefix=True`. CASE-248.
    preserve_prefix_subpaths: list[str] = Field(default_factory=list)
    # Rewrite the Host header to the upstream's own host:port when
    # proxying. Needed when the backend validates the Host it receives
    # against a cryptographic signature: SigV4 presigned URLs sign the
    # Host of the INTERNAL endpoint (what services sign against, e.g.
    # wip-garage:3900), while the browser presents the public host —
    # without this rewrite the two differ and the store rejects the
    # signature. Object storage (Garage; MinIO under SigV4) is the
    # canonical case. Caddy: `header_up Host {upstream_hostport}`.
    # NGINX Ingress: the `upstream-vhost` annotation (forces a
    # per-rule Ingress, same as strip_prefix).
    upstream_host_header: bool = False
    # Emit a 301 from the bare path to the trailing-slash variant
    # (Caddy only — nginx Ingress matches both via pathType=Prefix).
    # Defaults True for SPA-friendly behavior (relative-URL resolution
    # and cookie-path consistency depend on it). Backends with their
    # own slash-canonicalization logic must opt out — MCP is the
    # canonical case: its StreamableHTTP transport mounts at the bare
    # path and redirects /mcp/ → /mcp, which loops with Caddy's
    # redirect in the other direction.
    redirect_bare_path: bool = True
    # Groups allowed through this route when it is auth-protected behind
    # the gateway. Empty = any logged-in user. A logged-in user outside
    # these groups gets 403 from the gateway's verify — the app-level gate
    # for gateway-mode apps, which run no OIDC client of their own. The
    # list travels in the route's verify URL (Caddy forward_auth `uri`,
    # NGINX `auth-url`), so names are restricted to URL- and
    # Caddyfile-safe characters.
    allowed_groups: list[str] = Field(default_factory=list)

    @field_validator("allowed_groups")
    @classmethod
    def _group_names_are_safe(cls, groups: list[str]) -> list[str]:
        for g in groups:
            if not g or not all(ch.isalnum() or ch in "._-" for ch in g):
                raise ValueError(
                    f"allowed_groups entry {g!r}: use letters, digits, '.', '_' or '-'"
                )
        return groups

    @model_validator(mode="after")
    def _groups_need_auth(self) -> Route:
        # A group list on an unauthenticated route would look like
        # protection and be none.
        if self.allowed_groups and not self.auth_required:
            raise ValueError(
                f"route {self.path}: allowed_groups requires auth_required: true"
            )
        return self


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

    `endpoint` answers "can this serve traffic?" — compose's healthcheck and
    the k8s readinessProbe use it, so it may fail while a dependency is down.
    `liveness_endpoint` answers "is the process alive?" — the k8s
    livenessProbe and startupProbe use it, falling back to `endpoint`. Set it
    whenever `endpoint` checks a dependency: a liveness failure restarts the
    pod, and a restart cannot bring a dependency back.
    """

    endpoint: str | None = Field(default=None, pattern=r"^/.*")
    liveness_endpoint: str | None = Field(default=None, pattern=r"^/.*")
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
        if self.liveness_endpoint is not None and self.endpoint is None:
            raise ValueError("HealthcheckSpec.liveness_endpoint only valid with endpoint check")
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


NatsRole = Literal["ingest-producer", "event-subscriber", "replay-subscriber", "nats-monitor"]


class NatsClientSpec(WIPModel):
    """Declares this component as a NATS client with its own NATS user.

    NATS requires authentication; each client connects as a user named
    after the component, restricted to the subjects it is granted. The
    deployer generates the user's password secret, injects ``NATS_URL``
    (unless declared), ``NATS_USER`` / ``NATS_PASSWORD`` into the component
    and renders the user into NATS's config. A subscription to the user's
    own reply inbox (``_INBOX_<name>.>``) is always granted; JetStream
    replies arrive there.

    Exactly one of two forms:

    - ``roles`` — fixed, safe grants the deployer expands
      (config_gen/nats.py): ``ingest-producer`` publishes ingest work items,
      ``event-subscriber`` consumes the platform event stream through a
      durable named after the owner, ``replay-subscriber`` receives replayed
      events, ``nats-monitor`` reads stream and consumer listings (no
      message contents). **Apps must use roles.**
    - ``publish`` / ``publish_deny`` / ``subscribe`` — raw NATS permission
      subjects, data subjects and the ``$JS.API.*`` / ``$JS.ACK.*``
      JetStream API, where ``*`` and ``>`` only match whole tokens;
      ``publish_deny`` overrides ``publish``. Platform components only.
    """

    roles: list[NatsRole] = Field(default_factory=list)
    publish: list[str] = Field(default_factory=list)
    publish_deny: list[str] = Field(default_factory=list)
    subscribe: list[str] = Field(default_factory=list)

    @property
    def raw(self) -> bool:
        return bool(self.publish or self.publish_deny or self.subscribe)

    @model_validator(mode="after")
    def one_form(self) -> NatsClientSpec:
        if bool(self.roles) == self.raw:
            raise ValueError(
                "nats_client declares exactly one of `roles` or raw "
                "`publish`/`publish_deny`/`subscribe` subjects"
            )
        if self.raw and not self.publish:
            raise ValueError("a raw nats_client needs at least one `publish` subject")
        if len(set(self.roles)) != len(self.roles):
            raise ValueError(f"duplicate nats_client roles: {self.roles}")
        for subject in [*self.publish, *self.publish_deny, *self.subscribe]:
            if not subject or any(c.isspace() or c == '"' for c in subject):
                raise ValueError(f"invalid NATS subject {subject!r}")
        return self


MongoRole = Literal["read-all"]
MongoAction = Literal["find", "insert", "update", "remove"]


class MongoCollectionPrivilege(WIPModel):
    """Actions on one collection of another component's database."""

    db: str = Field(min_length=1)
    collection: str = Field(min_length=1)
    actions: list[MongoAction] = Field(min_length=1)


class MongoClientSpec(WIPModel):
    """Declares this component as a MongoDB client with its own user.

    MongoDB requires authentication; each client connects as a user named
    after the component (``MONGO_USER`` / ``MONGO_PASSWORD``, injected by
    the deployer, read by ``wip_auth.mongo``). The MongoDB start step
    (config_gen/mongodb.py) creates or updates every declared user on
    each start. Exactly one of two forms:

    - ``databases`` (``readWrite`` on each) plus optional
      ``collection_privileges`` (narrow actions on single collections of
      other databases) — platform components only.
    - ``roles`` — ``read-all`` grants ``read`` on every database a
      component declares. **Apps must use roles**: an app can never write
      to MongoDB, so WIP data only changes through WIP's API.
    """

    roles: list[MongoRole] = Field(default_factory=list)
    databases: list[str] = Field(default_factory=list)
    collection_privileges: list[MongoCollectionPrivilege] = Field(default_factory=list)

    @property
    def raw(self) -> bool:
        return bool(self.databases or self.collection_privileges)

    @model_validator(mode="after")
    def one_form(self) -> MongoClientSpec:
        if bool(self.roles) == self.raw:
            raise ValueError(
                "mongo_client declares exactly one of `roles` or "
                "`databases`/`collection_privileges`"
            )
        return self


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

    requires_core: bool | None = None
    """When True, requires at least one core component to be active.
    Used by infrastructure that exists to support core (mongodb stores
    core data; router proxies to core API endpoints). In apps-only
    installs (CASE-359, `modules.suppress_core=True`), core is
    deactivated → these dependencies auto-deactivate too. None = no
    requirement (default)."""


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

    `tag` is a FALLBACK default, not an override (CASE-438): it applies
    only when the deployment specifies no tag for this service. An
    explicit `spec.images.tag` (CLI `--tag`) or a
    `spec.images.tag_overrides` entry (CLI `--image-tag NAME=TAG`) wins.
    `tag=None` means "no fallback" — resolution ends at `latest`.
    Infrastructure components (mongo, postgres, dex) use fully-qualified
    names, which exempts them from the deployment-wide tag; they pin
    their own upstream version here. Full precedence:
    `config_gen/images.py`.

    `build_context=None` means pre-built image only. `build_context=Path(...)`
    enables a local source build on the **dev** target only — the dev renderer
    (`renderers/dev_simple.py`) materializes the context and builds incrementally.
    **compose** never local-builds (`renderers/compose.py:_build_block` returns
    None by design — pre-built / registry-pull only); **k8s** pulls too
    (`build_context`-set images are pushed to a LAN registry, then pulled —
    `renderers/k8s.py`). `spec.images.registry` selects the pull source; it does
    not gate a build.
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
    nats_client: NatsClientSpec | None = None
    mongo_client: MongoClientSpec | None = None
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

# `wip-deploy` v2 — Design

**Status:** Design approved; implementation pending
**Date:** 2026-04-16
**Author:** Peter + BE-YAC
**See also:** `install-path-drift.md` (the problem this solves)

## Context

v1.x ships three divergent installation paths:

- `scripts/setup.sh` — local/dev (modular layered compose, per-component builds)
- `scripts/quick-install.sh` + `scripts/setup-wip.sh` — turnkey production (monolithic compose, auth-gateway via `forward_auth`)
- `k8s/` — hand-maintained Kubernetes manifests (NGINX Ingress, direct proxy, no auth-gateway)

Each re-implements overlapping logic independently. The drift between them has reached the point where Theme 7's auth-gateway exists only on the compose path, and k8s is broken. See `install-path-drift.md` for the full analysis.

v2 replaces all three paths with a single declarative deployment system:

- A typed internal spec describes deployment intent
- Component manifests describe each service's deployment needs
- Shared config generators produce structured configs from (spec + components)
- Per-target renderers translate to native idiom (compose file, kustomize tree, Tiltfile)

Solo-user project. v1.x is stable. v2 is a clean break — no back-compat concerns, no migration tooling, no stable schema promises.

## Goal

Replace all deployment surfaces with one declarative, type-checked system whose output is correct by construction across three targets (compose, k8s, dev) and four concerns (components, apps, auth, networking).

## Non-goals

- Backward compatibility with v1.x deployments
- User-facing spec schema stability (it's internal)
- Distribution via PyPI
- Multi-tenancy, multi-namespace, GitOps, operator-pattern CRDs
- HA replicas, PodDisruptionBudgets, HorizontalPodAutoscalers (single-replica only)
- Observability wiring (out of scope for v2 initial — reserved field only)
- Backup/restore integration (backup is an existing WIP feature; migration between installs uses it)

---

## Architecture

```
User input (flags + preset)           Repo manifests
        │                                   │
        ▼                                   ▼
┌─────────────────┐          ┌──────────────────────────┐
│  Deployment     │          │  list[Component]          │
│  (Pydantic)     │          │  list[App]                │
└────────┬────────┘          └─────────────┬────────────┘
         │                                 │
         └────────────┬────────────────────┘
                      ▼
         ┌────────────────────────┐
         │  Shared config gen     │
         │  (pure functions)      │
         │  + Secret backend      │
         └────────────┬───────────┘
                      ▼
         ┌────────────────────────┐
         │  Structured configs    │
         │  (DexConfig,           │
         │   CaddyConfig, …)      │
         └────────────┬───────────┘
                      ▼
    ┌─────────────────┼─────────────────┐
    ▼                 ▼                 ▼
┌────────┐      ┌─────────┐       ┌──────────┐
│Compose │      │   K8s   │       │   Dev    │
│Renderer│      │Renderer │       │Renderer  │
└───┬────┘      └────┬────┘       └────┬─────┘
    ▼                ▼                 ▼
compose.yaml    kustomize/       Tiltfile +
+ Caddyfile     base + ov.       support files
+ .env          …                …
```

**Contract:** Renderers read from the upper layers. They never write to them and never make architectural decisions. All decisions live in the spec + component manifests + shared config generators.

---

## Layer 1a — The Deployment spec

The spec describes **what** the user wants. It is generated internally from CLI flags + preset + environment, Pydantic-validated, passed through the system, and optionally dumped to disk for debugging or re-install.

**Internal artifact, not user-facing.** Users do not write `wip-deployment.yaml` by hand. Flags + presets are the user surface. The spec can be dumped (`--save-spec`, `--show-spec`) and re-loaded (`--from-spec`) for reproducibility, but its schema is not stable and has no migration tooling.

### Shape

```python
class Deployment(BaseModel):
    api_version: Literal["wip.dev/v1"]
    kind: Literal["Deployment"]
    metadata: DeploymentMetadata
    spec: DeploymentSpec

class DeploymentMetadata(BaseModel):
    name: str                              # e.g., "prod-pi-cluster"
    created_at: datetime

class DeploymentSpec(BaseModel):
    target: Literal["compose", "k8s", "dev"]
    modules: ModulesSpec
    apps: list[AppRef]
    auth: AuthSpec
    network: NetworkSpec
    images: ImagesSpec
    platform: PlatformSpec
    secrets: SecretsSpec
    apply: ApplySpec                       # wait behavior

class ModulesSpec(BaseModel):
    optional: list[str]                    # e.g., ["console", "oidc", "reporting", "files"]
    # Core (mongodb, registry, def-store, template-store, document-store) is
    # always active and not listed here.

class AppRef(BaseModel):
    name: str                              # matches apps/<name>/
    enabled: bool = True

class AuthSpec(BaseModel):
    mode: Literal["oidc", "api-key-only", "hybrid"]
    gateway: bool                          # Theme 7: auth-gateway in request path
    users: list[DexUser] = Field(default_factory=default_dex_users)
    session_ttl: str = "15m"

class DexUser(BaseModel):
    email: str
    username: str
    group: str                             # e.g., "wip-admins"

class NetworkSpec(BaseModel):
    hostname: str                          # e.g., "wip.local", "wip-kubi.local"
    tls: Literal["internal", "letsencrypt", "external"]
    https_port: int = 8443                 # compose only; k8s uses 443 via Ingress
    http_port: int = 8080

class ImagesSpec(BaseModel):
    registry: str | None = None            # None = build from source (compose/dev)
    tag: str = "latest"
    pull_policy: Literal["always", "if-not-present"] = "if-not-present"

class PlatformSpec(BaseModel):
    """Discriminated union; only the block matching `target` is consulted."""
    compose: ComposePlatform | None = None
    k8s: K8sPlatform | None = None
    dev: DevPlatform | None = None

class ComposePlatform(BaseModel):
    data_dir: Path
    platform_variant: Literal["default", "pi4", "windows"] = "default"

class K8sPlatform(BaseModel):
    namespace: str = "wip"
    storage_class: str = "rook-ceph-block"
    ingress_class: str = "nginx"
    tls_secret_name: str = "wip-tls"

class DevPlatform(BaseModel):
    mode: Literal["tilt", "simple"] = "tilt"
    source_mount: bool = True

class SecretsSpec(BaseModel):
    backend: Literal["file", "k8s-secret", "sops"]
    location: str | None = None            # path (file/sops) or ignored (k8s-secret)

class ApplySpec(BaseModel):
    wait: bool = True
    timeout_seconds: int = 300
    on_timeout: Literal["fail", "warn", "continue"] = "fail"
```

### What is in the spec

- Deployment *intent*: which modules, which apps, which auth model, which hostname
- Target selector and target-specific parameters
- Resolution hints for images and secrets (not the values)
- Apply-time behavior (wait/timeout)

### What is NOT in the spec

- Which ports each service listens on (from component manifest)
- Which image each service uses (from component manifest + spec.images)
- Any service's env requirements (from component manifest)
- Caddy/Dex/Ingress config (generated)
- Secret values (resolved via backend; never in spec)
- Build details (orthogonal to deployment)
- Runtime state (backup schedules, replay cursors)

### Cross-cutting validation

Enforced by Pydantic root validators:

- `auth.gateway=True` requires `auth.mode != "api-key-only"`
- `target=X` requires `platform.X` to be set
- Every `apps[].name` resolves to an app manifest on disk
- Every component with `oidc_client` requires `auth.mode != "api-key-only"`
- `network.tls=letsencrypt` requires a non-localhost hostname

---

## Layer 1b — The Component manifest

Each service carries its own manifest, checked in at `components/<name>/wip-component.yaml`. This is the **single source of truth for that component's deployment shape**, consumed by every renderer identically.

### Locality

Manifests live with the components they describe, not with the deployer:

```
components/document-store/
├── src/
├── wip-component.yaml      ← manifest
├── Dockerfile
└── pyproject.toml
```

The deployer discovers manifests by walking `components/*/wip-component.yaml` and `apps/*/wip-app.yaml` from the repo root. The deployer package does not bundle manifests; they are always read live.

### Infrastructure components are first-class

MongoDB, Postgres, NATS, MinIO, Dex each get their own `components/<name>/wip-component.yaml`. They are components with `image.build_context: null` (pre-built images only). This unifies the model — no "service vs infrastructure" code path.

### Shape

```python
class Component(BaseModel):
    api_version: Literal["wip.dev/v1"]
    kind: Literal["Component"]
    metadata: ComponentMetadata
    spec: ComponentSpec

class ComponentMetadata(BaseModel):
    name: str                              # e.g., "document-store"
    category: Literal["core", "optional", "infrastructure"]
    description: str

class ComponentSpec(BaseModel):
    image: ImageRef
    ports: list[Port]
    env: EnvSpec
    routes: list[Route] = []
    storage: list[StorageSpec] = []
    depends_on: list[str] = []
    healthcheck: HealthcheckSpec | None = None
    resources: ResourceSpec | None = None
    oidc_client: OidcClientSpec | None = None
    post_install: list[PostInstallHook] = []
    observability: ObservabilitySpec | None = None    # reserved; unused in v2 initial

class ImageRef(BaseModel):
    name: str                              # e.g., "wip-document-store"
    build_context: Path | None = None      # None = pre-built image only
    build_args: dict[str, str] = {}

class Port(BaseModel):
    name: str                              # "http", "monitor"
    container_port: int
    protocol: Literal["TCP", "UDP"] = "TCP"

class EnvSpec(BaseModel):
    required: list[EnvVar]
    optional: list[EnvVar] = []

class EnvVar(BaseModel):
    name: str
    source: EnvSource

class EnvSource(BaseModel):
    """Declarative value source. Target-aware resolution in config_gen/env.py."""
    literal: str | None = None             # constant
    from_spec: str | None = None           # dotted path into Deployment spec
    from_secret: str | None = None         # named secret reference
    from_component: str | None = None      # component name → target-specific URL

class Route(BaseModel):
    path: str                              # e.g., "/api/document-store"
    auth_required: bool = True
    streaming: bool = False                # disables proxy buffering

class StorageSpec(BaseModel):
    name: str                              # PVC name / volume name
    mount_path: str
    size: str = "10Gi"
    access_mode: Literal["ReadWriteOnce", "ReadWriteMany"] = "ReadWriteOnce"

class HealthcheckSpec(BaseModel):
    endpoint: str                          # e.g., "/health"
    interval_seconds: int = 10
    timeout_seconds: int = 5
    retries: int = 3
    start_period_seconds: int = 30

class ResourceSpec(BaseModel):
    cpu_request: str | None = None         # e.g., "100m"
    memory_request: str | None = None      # e.g., "256Mi"
    cpu_limit: str | None = None
    memory_limit: str | None = None

class OidcClientSpec(BaseModel):
    client_id: str
    redirect_paths: list[str] = ["/auth/callback"]

class PostInstallHook(BaseModel):
    name: str
    run: str                               # shell invocation
    after: str = "healthy"                 # when to run
```

### Key design decisions

**Routes are target-agnostic declarations.** A component declares `path`, `auth_required`, `streaming`. The compose renderer emits `reverse_proxy` (with `forward_auth` when `auth_required=true` and `spec.auth.gateway=true`). The k8s renderer emits an Ingress path (with `nginx.ingress.kubernetes.io/auth-url` annotation when `auth_required=true` and `spec.auth.gateway=true`). Same declaration, two idioms. Routing decisions never live in renderers.

**EnvSource is declarative.** A component can source an env var from a literal, from a spec field by dotted path, from a named secret, or from another component's URL. It cannot run shell or arbitrary logic. This forbids a class of ad-hoc growth.

**Target-aware URL resolution.** `from_component: mongodb` resolves to:
- compose: `mongodb://wip-mongodb:27017/`
- k8s: `mongodb://wip-mongodb.wip.svc.cluster.local:27017/`
- dev: `mongodb://localhost:27017/`

Resolution logic lives in `config_gen/env.py`. Component manifests stay target-agnostic.

**Post-install hooks are a blessed escape hatch.** Example: Registry's `initialize-wip` namespace bootstrap. Runs after the platform reports healthy. Intended for a handful of cases; if the list grows past ~5 across all components, that's a sign the model has a gap.

---

## Layer 1c — Apps

Apps are components with additional metadata. An app's manifest (`apps/<name>/wip-app.yaml`) is a superset of the component manifest:

```python
class App(Component):
    # App inherits everything from Component, then adds:
    app_metadata: AppMetadata

class AppMetadata(BaseModel):
    display_name: str
    route_prefix: str                      # e.g., "/apps/dnd"
    ui_only: bool = True                   # has a frontend; not a backend service
```

An app's `routes` should all carry `auth_required=true` by default, and the app typically has an `oidc_client` declaring its Dex client.

This replaces the `wip.app.*` label-scraping mechanism in `setup-wip.sh`. Label parsing is gone; structured data flows through Pydantic from manifest to renderer.

---

## Layer 1d — Shared config generators

Pure functions. Input: `(Deployment, list[Component], list[App], ResolvedSecrets)`. Output: structured Python objects representing the desired config. Renderers consume these objects, never parse strings.

### Files in `config_gen/`

| File | Responsibility | Output type |
|---|---|---|
| `dex.py` | Dex YAML: issuer, users (bcrypt-hashed), static clients | `DexConfig` |
| `caddy.py` | Caddyfile: routes with `forward_auth` where applicable | `CaddyConfig` |
| `nginx_ingress.py` | K8s Ingress rules with `auth-url` annotations | `list[IngressRule]` |
| `env.py` | Per-component env map, with target-aware URL resolution | `dict[component_name, dict[key, val]]` |
| `routing.py` | Shared: compute the auth/route map once; feed Caddy + NGINX | `list[ResolvedRoute]` |
| `secrets.py` | Orchestrate the secret backend to ensure all required secrets exist | `ResolvedSecrets` |

### No templates

Config generators return structured dataclasses. Renderers serialize with `yaml.safe_dump` (for YAML outputs) or direct string-building (for Caddyfile, Tiltfile — neither is YAML).

Rationale:

- Templates invite string interpolation bugs. Structured objects don't.
- Templates resist type-checking. Pydantic models don't.
- Templates have a mini-language. We already have Python.
- Testing a rendered template requires re-parsing. Testing a structured object is direct.

The one permitted exception: if a single exotic file needs templating (e.g., a hand-rolled nginx.conf for a peculiar app), Jinja may be used locally for that file. It must not become the primary mechanism.

---

## Layer 1e — Secret backend

Secrets are resolved via a pluggable backend. The spec declares which backend to use; the deployer reads/generates/persists through it.

```python
class SecretBackend(Protocol):
    def get_or_generate(self, name: str, generator: Callable[[], str]) -> str: ...
    def persist(self) -> None: ...
    def list_names(self) -> list[str]: ...

class FileSecretBackend:
    """~/.wip-deploy/<deployment-name>/secrets/*"""

class K8sSecretBackend:
    """k8s Secret objects in spec.platform.k8s.namespace"""

class SopsSecretBackend:
    """SOPS-encrypted YAML file"""
```

**Lifecycle:**

- First install: `get_or_generate` produces a value, persists it. Database volumes initialize with the generated password.
- Re-install: `get_or_generate` reads the existing value. This is critical — a fresh password would diverge from the stored database initialization.
- Rotate: explicit `wip-deploy rotate-secrets` command. Regenerates specified secrets, coordinates database-side password update (for Mongo/Postgres admin creds), re-renders, re-applies.
- Nuke: `wip-deploy nuke` optionally wipes secrets alongside data.

The current `quick-install.sh`'s "delete volumes on repeat install" is a workaround for the absence of this lifecycle. v2 solves it properly.

---

## Layer 2 — Renderers

Each renderer is a class implementing a standard interface:

```python
class Renderer(Protocol):
    target_name: str

    def render(
        self,
        deployment: Deployment,
        components: list[Component],
        apps: list[App],
        configs: GeneratedConfigs,
        secrets: ResolvedSecrets,
    ) -> FileTree: ...

    def apply(self, tree: FileTree, wait: bool, timeout: int) -> ApplyResult: ...

    def status(self) -> DeploymentStatus: ...

    def teardown(self, nuke_data: bool) -> None: ...

    def logs(self, component: str | None, follow: bool) -> Iterator[str]: ...
```

`FileTree` is a typed representation of the output directory: `{path: file_contents}` plus permission/mode metadata.

### Per-target notes

**Compose renderer** (`renderers/compose.py`):

- Emits `docker-compose.yaml` + `Caddyfile` + `.env` + optional config mount directories
- Uses compose **profiles** for modules (`--profile reporting` enables the reporting module)
- YAML anchors/aliases for repeated patterns
- Caddyfile hand-built (Caddy syntax isn't YAML)
- `apply` runs `podman-compose --env-file .env -f docker-compose.yaml up -d`
- Wait logic: polls `docker compose ps --format json` for `health: healthy` on every service with a healthcheck

**K8s renderer** (`renderers/k8s.py`):

- Emits a kustomize tree: `base/` + `overlays/<preset>/`
- `base/` contains: Namespace, ConfigMaps, Secrets (template — values filled from backend), StatefulSets (mongodb/postgres/nats/minio/dex), Deployments (everything else), Services, a single Ingress with annotations
- `overlays/<preset>/` toggles optional components via strategic merge patches
- NOT Helm. Kustomize.
- `apply` runs `kustomize build | kubectl apply -f -`
- Wait logic: polls `kubectl rollout status` per Deployment/StatefulSet plus `kubectl get pods -l app` for Ready condition

**Dev renderer** (`renderers/dev_tilt.py` + `renderers/dev_simple.py`):

- Tilt mode: emits a `Tiltfile` that watches source paths, rebuilds images incrementally, uses `live_update` to sync changes into running containers
- Simple mode: emits compose.yaml with source mounts + restart-on-change via `podman-compose up`
- `apply` runs `tilt up` or `podman-compose up -d` respectively
- Tilt mode is the default; simple is fallback for when Tilt is not installed

### What the k8s renderer does NOT install

Cluster prerequisites (assumed to exist, documented in a prereqs file):

- ingress-nginx controller
- MetalLB (or another LoadBalancer provider)
- cert-manager (if `tls=letsencrypt` or internal-CA)
- Rook-Ceph (or another StorageClass provider matching `storage_class`)

The renderer consumes these via Ingress class, LoadBalancer Service, cert-manager annotations, StorageClass names. It does not manage their lifecycle.

---

## The CLI

Framework: **typer** (Click-based; Python type hints drive the CLI).

Entry point: `pyproject.toml` declares `[project.scripts] wip-deploy = "wip_deploy.cli:main"`. After `pip install -e ./deployer`, `wip-deploy` is on PATH.

### Subcommands

```
wip-deploy install [OPTIONS]
    --preset NAME               # headless|core|standard|analytics|full
    --target compose|k8s|dev    # deployment target
    --hostname HOST             # external hostname
    --add MODULE[,MODULE]       # add modules to preset
    --remove MODULE[,MODULE]    # remove modules from preset
    --registry URL              # pull pre-built images
    --tag TAG
    --no-wait                   # skip healthy-wait
    --wait-timeout N            # override spec timeout
    --on-timeout fail|warn|continue
    --save-spec PATH            # dump resolved spec
    --from-spec PATH            # use dumped spec as input
    -y, --yes                   # skip confirmation

wip-deploy upgrade [--target T] [--tag TAG] [wait options]
    # Re-render, re-apply. Secrets preserved. Useful for image-tag bumps
    # or picking up manifest changes.

wip-deploy start [COMPONENT...]
wip-deploy stop [COMPONENT...]
wip-deploy restart [COMPONENT...]
    # Operator shortcuts. Empty list = all components.

wip-deploy status
    # Reads deployed state: running containers/pods, health, image tags.

wip-deploy logs [COMPONENT] [--follow] [--tail N]
    # Shells out to podman logs / kubectl logs / tilt logs.

wip-deploy render --target T --output-dir DIR
    # Dry-run: emit the file tree without applying.

wip-deploy show-spec [--format yaml|json]
    # Dump the resolved Deployment spec (internal-artifact affordance).

wip-deploy validate
    # Check spec + manifest discovery + cross-cutting validators without rendering.

wip-deploy rotate-secrets [--secret NAME]
    # Regenerate specified secret(s), re-render, re-apply, coordinate
    # database-side password updates.

wip-deploy nuke [--keep-data] [--keep-secrets] [-y]
    # Teardown. Data wipe is opt-in via NOT passing --keep-data.

wip-deploy dev [--mode tilt|simple]
    # Alias: install --target dev --preset standard, then tilt up or podman-compose up.
```

---

## Apply-wait behavior

**Default:** wait, up to `spec.apply.timeout_seconds`, fail loudly on timeout.

Configuration precedence (highest wins):

1. CLI flag (`--no-wait`, `--wait-timeout N`, `--on-timeout X`)
2. Deployment spec (`spec.apply.wait`, `spec.apply.timeout_seconds`, `spec.apply.on_timeout`)
3. Built-in defaults (`wait=true`, `timeout=300s`, `on_timeout="fail"`)

**What counts as healthy:**

- The component's declared healthcheck endpoint returns 200, **AND**
- The platform reports ready (compose `service_healthy`, k8s `Ready` condition)

Both must be true. Post-install hooks run *after* the component is healthy; each hook's success is part of the wait.

**On timeout:**

- `fail` — exit non-zero, surface which components didn't reach healthy
- `warn` — log warning, continue (install "succeeds")
- `continue` — quieter form of warn, intended for CI/scripted flows

**Rationale:** first-install ergonomics need wait-to-healthy — otherwise `wip-deploy install && wip-deploy status` reports breakage because the platform is still starting. CI/scripted flows benefit from `--no-wait`. Upgrade operations benefit from `--on-timeout warn` because one stuck pod shouldn't fail the whole upgrade. Neither default is right for the other context; configurability at both levels is necessary.

---

## Dependency ordering

Each component declares `depends_on: [mongodb, registry]`.

**Compose renderer** emits `depends_on: {<dep>: {condition: service_healthy}}`.

**K8s renderer** does not emit explicit ordering beyond startup probes; k8s scheduling + readiness gates handle it. Dependencies inform *wait* logic (a component is considered "ready for wait" only once its deps are ready), but they do not drive application order.

**Dev renderer (Tilt)** uses Tilt's native resource graph: `resource(name, deps=[...])`.

**Post-install hooks** handle cases the platform can't (e.g., Registry's `initialize-wip` API call). They run after the target component reports healthy, in declaration order within a component, across components in `depends_on` topological order.

---

## Repo layout

### Added

```
deployer/                               # new Python sub-package
├── pyproject.toml
├── src/wip_deploy/
│   ├── __init__.py
│   ├── cli.py
│   ├── spec/
│   │   ├── __init__.py
│   │   ├── deployment.py
│   │   ├── component.py
│   │   ├── app.py
│   │   └── validators.py
│   ├── presets/
│   │   ├── __init__.py
│   │   ├── headless.py
│   │   ├── core.py
│   │   ├── standard.py
│   │   ├── analytics.py
│   │   └── full.py
│   ├── config_gen/
│   │   ├── __init__.py
│   │   ├── dex.py
│   │   ├── caddy.py
│   │   ├── nginx_ingress.py
│   │   ├── env.py
│   │   ├── routing.py
│   │   └── secrets.py
│   ├── secrets_backend/
│   │   ├── __init__.py
│   │   ├── base.py
│   │   ├── file.py
│   │   ├── k8s.py
│   │   └── sops.py
│   ├── renderers/
│   │   ├── __init__.py
│   │   ├── base.py
│   │   ├── compose.py
│   │   ├── k8s.py
│   │   ├── dev_tilt.py
│   │   └── dev_simple.py
│   ├── discovery.py
│   ├── apply.py
│   ├── status.py
│   └── errors.py
├── tests/
│   ├── unit/
│   ├── golden/
│   │   ├── outputs/                    # checked-in golden renderings
│   │   └── test_golden.py
│   ├── invariants/                     # cross-target invariant tests
│   └── integration/                    # CI-only slow tests
└── README.md

components/
├── registry/
│   └── wip-component.yaml              # new
├── def-store/wip-component.yaml        # new
├── template-store/wip-component.yaml   # new
├── document-store/wip-component.yaml   # new
├── reporting-sync/wip-component.yaml   # new
├── ingest-gateway/wip-component.yaml   # new
├── mcp-server/wip-component.yaml       # new
├── wip-auth-gateway/                   # new first-class component dir
│   └── wip-component.yaml
├── mongodb/                            # new infra-as-component
│   └── wip-component.yaml
├── postgres/wip-component.yaml         # new
├── nats/wip-component.yaml             # new
├── minio/wip-component.yaml            # new
└── dex/wip-component.yaml              # new

apps/                                   # new top-level
├── react-console/
│   └── wip-app.yaml
├── dnd/wip-app.yaml
└── clintrial/wip-app.yaml

tools/                                  # renamed from scripts/
├── build-release.sh                    # kept (orthogonal: image builds)
├── wip-test.sh                         # kept (orthogonal: test runner)
├── quality-audit.sh                    # kept (orthogonal)
└── seed-data/                          # kept (orthogonal)
```

### Removed

```
scripts/setup.sh                        # → wip-deploy install --target compose
scripts/setup-wip.sh                    # → internal to compose renderer
scripts/quick-install.sh                # → 20-line bootstrap that installs deployer
docker-compose/                         # → rendered by deployer
docker-compose.production.yml           # → rendered by deployer
docker-compose.app.*.yml                # → app manifests in apps/*/wip-app.yaml
k8s/                                    # → rendered by deployer
components/*/docker-compose.yml         # → rendered by deployer
components/*/docker-compose.override.yml
components/*/docker-compose.registry.yml
config/presets/                         # → deployer/src/wip_deploy/presets/
config/production/Caddyfile.template    # → deployer/src/wip_deploy/config_gen/caddy.py
config/production/dex-config.template   # → deployer/src/wip_deploy/config_gen/dex.py
ui/wip-console/docker-compose*.yml      # → rendered by deployer
```

### Bootstrap script

`quick-install.sh` survives in a minimal form — its only job is to get the deployer installed on a fresh machine:

```bash
#!/usr/bin/env bash
# Install wip-deploy on a fresh machine and run first install.
set -euo pipefail
git clone https://github.com/peterseb1969/World-in-a-Pie.git
cd World-in-a-Pie
pip install ./deployer
wip-deploy install --preset standard --target compose --hostname "$1"
```

~20 lines total including hostname handling, dependency pre-checks, and help text.

---

## Package internal structure — dependency direction

Strictly downward. Each layer depends only on layers below.

```
cli.py
  ↓
presets/ + discovery.py
  ↓
spec/              ← Pydantic models, depend on nothing
  ↑
config_gen/        ← consume spec + components
  ↑
renderers/         ← consume spec + components + configs
  ↑
apply.py + status.py   ← shell out to podman/kubectl/tilt
```

`spec/` has no internal dependencies and can be used standalone. `renderers/` do not import from each other. `config_gen/` modules can depend on `routing.py` for shared routing computation but not on each other.

---

## Test strategy

Four tiers. First three run on every commit; fourth runs on CI only.

### Tier 1 — Unit

Pure-function tests for:

- Pydantic model validation (valid cases, invalid cases, cross-cutting validators)
- Config generators (given known inputs, assert exact output objects)
- Secret backend implementations (mock file I/O, mock k8s API)
- Individual renderer helpers (route resolution, env resolution)

Fast. Hundreds to thousands of tests. Drive coverage here.

### Tier 2 — Golden-file

For each (preset × target) pair, render to a temp directory and diff against a checked-in golden output tree.

```
tests/golden/outputs/
├── standard-compose/
│   ├── docker-compose.yaml
│   ├── Caddyfile
│   └── .env
├── standard-k8s/
│   ├── base/...
│   └── overlays/...
├── analytics-compose/...
└── ...
```

Any change to renderer logic or spec defaults appears as a diff. If intentional, regenerate the golden. If regression, fix the code.

**Caution:** golden tests are brittle against noise (timestamps, random IDs). The renderer must be deterministic given identical input (no timestamps, sorted keys, stable ordering) for this tier to be useful.

### Tier 3 — Syntactic validation

Render output, then run the target's own validator:

- `docker compose -f <rendered> config` must exit 0
- `kustomize build <rendered>` must exit 0 and produce valid YAML
- `tilt alpha tiltfile-result` or `tilt dump engine` on the rendered Tiltfile

Fast (seconds), catches syntax issues that unit tests miss.

### Tier 4 — Cross-target invariants

**The drift-prevention layer.** These tests exist on day one and grow with every new cross-cutting concern.

For each invariant property, assert it holds in every target's rendered output:

```python
def test_gateway_protects_app_routes_on_all_targets():
    dep = make_deployment(auth_gateway=True, apps=["dnd"])
    comps = discover_test_components()
    for target in ["compose", "k8s", "dev"]:
        output = render(dep, comps, target=target)
        assert_auth_gateway_protects(output, route="/apps/dnd", target=target)
```

Each `assert_auth_gateway_protects_<target>` helper knows its target's idiom (Caddy `forward_auth`, NGINX `auth-url`, Tilt pass-through) but asserts the same *semantic* property.

Examples of invariants to encode:

- Gateway protects app routes when `auth.gateway=true`
- Gateway does NOT protect routes when `auth.gateway=false`
- `streaming=true` disables proxy buffering (Caddy `flush_interval -1`, NGINX `proxy-buffering: off`)
- Every component listed in `modules.optional` appears in the rendered output exactly when listed
- Every OIDC client declared in any manifest appears in Dex config
- Every env var declared as `from_component: X` resolves to a URL referencing X

### Tier 5 (CI only) — Integration

On Gitea Actions, render → `podman-compose up -d` → hit `/health` on every component → teardown. Slow (minutes). Runs on PRs to `main`, not on every push.

Same can be done against a disposable k3d cluster for the k8s renderer.

---

## Developer workflows

### Adding a new component

1. `mkdir components/new-service/`
2. Write code in `components/new-service/src/`
3. Write `components/new-service/wip-component.yaml`:
   ```yaml
   apiVersion: wip.dev/v1
   kind: Component
   metadata:
     name: new-service
     category: optional
     description: Does the new thing.
   spec:
     image:
       name: wip-new-service
       build_context: .
     ports:
       - {name: http, container_port: 8009}
     env:
       required:
         - name: MONGO_URI
           source: {from_component: mongodb}
         - name: WIP_API_KEY
           source: {from_secret: api-key}
     routes:
       - {path: /api/new-service, auth_required: true}
     healthcheck:
       endpoint: /health
   ```
4. If it belongs to a preset: `deployer/src/wip_deploy/presets/full.py` gets `"new-service"` appended to `optional`.
5. `wip-deploy validate` — confirms schema + discovery.
6. `wip-deploy install --preset full` — deploys everywhere.

**Zero** scripts touched. **Zero** per-target YAML edited. **Zero** template changes.

### Adding a new app

Same pattern, with `apps/new-app/wip-app.yaml`:

```yaml
apiVersion: wip.dev/v1
kind: App
metadata:
  name: new-app
spec:
  image: {name: wip-new-app, build_context: .}
  ports: [{name: http, container_port: 80}]
  routes:
    - {path: /apps/new-app, auth_required: true}
  oidc_client:
    client_id: new-app
    redirect_paths: [/auth/callback]
app_metadata:
  display_name: New App
  route_prefix: /apps/new-app
```

Then: `wip-deploy install --preset standard --app new-app`.

### Installing (compose)

```bash
wip-deploy install --preset standard --target compose --hostname wip.local
```

Outputs:
- `~/wip-deploy/<name>/docker-compose.yaml`
- `~/wip-deploy/<name>/Caddyfile`
- `~/wip-deploy/<name>/.env`
- `~/wip-deploy/<name>/config/dex/config.yaml`
- `~/.wip-deploy/<name>/secrets/` (persistent; not removed on nuke unless `--keep-secrets=false`)

### Installing (k8s)

```bash
wip-deploy install --preset standard --target k8s --hostname wip-kubi.local
```

Outputs kustomize tree, applies via `kubectl apply -k`. Secrets via K8sSecretBackend write `kind: Secret` objects.

### Dev loop

```bash
wip-deploy dev --mode tilt
```

Emits Tiltfile + runs `tilt up`. Source-mount + live-reload. Ctrl-C tears down.

---

## Migration plan

**Shape:** in-place on a branch.

1. New branch `v2-deployer` off `develop`.
2. Build `deployer/` from scratch. Validate by running both paths locally during development (the old `setup.sh` still works until we delete it).
3. Write component manifests for every service.
4. Get golden-file and invariant tests passing for all three targets.
5. Run `wip-deploy install --preset standard --target compose` on a fresh machine. Validate end-to-end. Same for `--target k8s` against the Pi cluster.
6. Once both targets install cleanly:
   - Delete `scripts/setup.sh`, `scripts/setup-wip.sh`
   - Delete `docker-compose/`, `docker-compose.production.yml`, `docker-compose.app.*.yml`
   - Delete `k8s/`
   - Delete per-component `docker-compose*.yml` files
   - Delete `config/presets/`, `config/production/`
   - Rename `scripts/` → `tools/`
   - Shrink `quick-install.sh` to the bootstrap form
7. Merge `v2-deployer` → `develop` as a single commit. v1.1 remains on the `v1.1` tag.

No coexistence period. v1.1 is tagged; v2 replaces. No "both scripts exist for a while" — that's the current state and it is the problem.

---

## Design calls & rationale

### Component manifests live with components

Locality, ownership, plugin parity, single-PR surface. Deployer walks `components/*/wip-component.yaml` at runtime.

### Core is implicit

`modules.optional` lists only optional components. MongoDB, Registry, Def-Store, Template-Store, Document-Store are always present. Avoids the footgun of "forgot to list registry → nothing works."

### Mongo/Postgres/NATS/MinIO/Dex are first-class components

Unified model, no special "infrastructure" code path. Costs ~5 one-file manifests; gains symmetry across the pipeline.

### No Jinja templates

Structured objects → renderers serialize. Bypasses template interpolation bugs, enables type checking, avoids a second mini-language.

### Apps are components with extra metadata

No separate app subsystem. Same discovery, same pipeline, same rendering — plus a route prefix and a first-class OIDC client declaration.

### Post-install hooks, not a scripting layer

A small escape hatch. If it grows past ~5 entries total, investigate the gap in the model.

### Secrets through a backend abstraction

Solves the "repeat install breaks volume passwords" problem that v1's `quick-install.sh` papers over with `podman volume rm`. First-install generates + persists; re-install reads existing; rotate is deliberate.

### No stable schema, no migration tooling

Single-user project. Breaking changes to the spec are free. Users (Peter) don't hand-write the YAML; it's internal.

### Cross-target invariant tests are load-bearing

Without them, the new architecture drifts the same way the old one did. They exist on day one, grow with every cross-cutting concern, and block merge if they fail.

### In-place replacement, no coexistence

Coexistence is what produced the drift. v1.1 on the tag, v2 on the branch, delete + merge.

---

## Out of scope for v2 initial

- Observability wiring (logs/metrics/traces integration into deployment)
- HA replicas, PodDisruptionBudgets, anti-affinity
- Multi-namespace / multi-tenant deployments
- GitOps reconciliation (spec is internal; not designed for checked-in-by-user flow)
- Kubernetes CustomResourceDefinitions for WIP concepts
- ACME cert-manager integration (out initially; `tls: internal` only for k8s; `tls: letsencrypt` supported for compose via Caddy)
- Backup/restore integration (existing WIP feature; used for cross-install migration manually)
- PyPI publication

## Open questions

None. Implementation can begin.

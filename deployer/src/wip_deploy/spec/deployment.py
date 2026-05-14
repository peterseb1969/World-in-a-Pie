"""Deployment spec model — the top-level user-intent object.

The Deployment is constructed internally from CLI flags + preset +
environment. It is validated by Pydantic and passed through config
generators and renderers. It is not a stable user-facing file format.

Dumpable for debugging (`wip-deploy show-spec --save /tmp/spec.yaml`) and
loadable for reproducibility (`wip-deploy install --from-spec ...`), but
the schema (`api_version: wip.dev/v1`) has no stability promise.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator

from wip_deploy.spec._base import WIPModel

# ────────────────────────────────────────────────────────────────────
# Top-level ids
# ────────────────────────────────────────────────────────────────────


Target = Literal["compose", "k8s", "dev"]


class DeploymentMetadata(WIPModel):
    name: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9-]*$")
    created_at: datetime = Field(default_factory=datetime.now)


# ────────────────────────────────────────────────────────────────────
# Modules / apps
# ────────────────────────────────────────────────────────────────────


class ModulesSpec(WIPModel):
    """Optional components to include.

    Core components (registry, def-store, template-store, document-store)
    and required infrastructure (mongodb) are normally always active.
    Valid `optional` values are names of components with
    `category=optional` in their manifest.

    `suppress_core` (CASE-359): when True, core components are
    deactivated AND infrastructure components with
    `activation.requires_core=True` (mongodb, router) auto-deactivate.
    Used for apps-only installs that talk to a remote WIP via
    `--remote-wip` (CASE-358). The typical apps-only command is
    `wip-deploy install --apps-only --remote-wip <URL> --app <NAME>`.
    """

    optional: list[str] = Field(default_factory=list)
    suppress_core: bool = False

    @field_validator("optional")
    @classmethod
    def _normalize_and_dedupe(cls, v: list[str]) -> list[str]:
        # Preserve order but remove duplicates.
        seen: set[str] = set()
        out: list[str] = []
        for mod in v:
            if mod not in seen:
                seen.add(mod)
                out.append(mod)
        return out


class AppRef(WIPModel):
    """Reference to an app by name. The app's manifest is resolved from
    `apps/<name>/wip-app.yaml` at discovery time."""

    name: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9-]*$")
    enabled: bool = True


# ────────────────────────────────────────────────────────────────────
# Auth
# ────────────────────────────────────────────────────────────────────


class DexUser(WIPModel):
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    username: str = Field(min_length=1)
    group: str = Field(min_length=1)


def _default_dex_users() -> list[DexUser]:
    return [
        DexUser(email="admin@wip.local", username="admin", group="wip-admins"),
        DexUser(email="editor@wip.local", username="editor", group="wip-editors"),
        DexUser(email="viewer@wip.local", username="viewer", group="wip-viewers"),
    ]


class AuthSpec(WIPModel):
    mode: Literal["oidc", "api-key-only", "hybrid"]
    gateway: bool
    """If True, the auth-gateway component sits in the request path for
    auth-required routes. Implementation differs per target but the
    guarantee is the same (Theme 7)."""
    users: list[DexUser] = Field(default_factory=_default_dex_users)
    session_ttl: str = "15m"

    @model_validator(mode="after")
    def gateway_requires_oidc(self) -> AuthSpec:
        if self.gateway and self.mode == "api-key-only":
            raise ValueError(
                "auth.gateway=True requires auth.mode ∈ {oidc, hybrid}; "
                "got api-key-only"
            )
        return self

    @model_validator(mode="after")
    def users_required_for_oidc(self) -> AuthSpec:
        if self.mode in ("oidc", "hybrid") and not self.users:
            raise ValueError(
                f"auth.mode={self.mode} requires at least one Dex user"
            )
        return self


# ────────────────────────────────────────────────────────────────────
# Network
# ────────────────────────────────────────────────────────────────────


TLSMode = Literal["internal", "letsencrypt", "external", "self-signed"]


class NetworkSpec(WIPModel):
    hostname: str = Field(min_length=1)
    tls: TLSMode = "internal"
    https_port: int = Field(default=8443, ge=1, le=65535)
    http_port: int = Field(default=8080, ge=1, le=65535)
    # CASE-358: URL of a remote WIP install this deployment talks to.
    # When set, `SpecContext.network.external_base_url` resolves here
    # instead of this install's own _public_base. Used by apps that
    # need to reach a different WIP (e.g., Console-on-Mac → WIP-on-Pi).
    # None on standard same-host installs.
    #
    # Does NOT override the local install's own hostname/ports — that
    # would break Caddy binding and OIDC issuer URLs on the local
    # install. Cross-host requires CASE-359 (apps-only) to suppress
    # the local backend stack; this field is the URL-plumbing half.
    remote_wip_url: str | None = Field(default=None)
    # CASE-373: True when the install dir has a
    # `secrets/external-ca.crt` file present, set by the install verb
    # at render time. App containers get the file bind-mounted at
    # `/etc/ssl/certs/external-ca.crt` plus an `NODE_EXTRA_CA_CERTS`
    # env var. Backend containers (when present in the same install)
    # do not need cross-host trust — the slot is app-only.
    #
    # This field is recordable spec — visible in `deployment.deployer-state`
    # — so `wip-deploy status --diff` and post-hoc inspection both see it.
    # The toggle is driven by the install verb, not user-facing CLI flags:
    # if `wip-deploy import-bundle` wrote a CA, install picks it up.
    external_ca_mount: bool = Field(default=False)

    @model_validator(mode="after")
    def letsencrypt_requires_public_hostname(self) -> NetworkSpec:
        if self.tls == "letsencrypt":
            h = self.hostname.lower()
            if h in ("localhost", "127.0.0.1", "::1") or h.endswith(".local"):
                raise ValueError(
                    f"tls=letsencrypt requires a public hostname; "
                    f"got {self.hostname!r}"
                )
        return self

    @model_validator(mode="after")
    def remote_wip_url_is_well_formed(self) -> NetworkSpec:
        """remote_wip_url must be http(s)://<host>[:<port>] with no path/query.

        We refuse trailing slashes, paths, and query strings because the
        URL is concatenated with `/api/<svc>/...` paths downstream;
        a trailing slash would yield `//api/...` which some servers
        treat as a different resource than `/api/...`.
        """
        if self.remote_wip_url is None:
            return self

        from urllib.parse import urlparse

        url = self.remote_wip_url
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            raise ValueError(
                f"remote_wip_url must use http:// or https:// scheme; "
                f"got {url!r}"
            )
        if not parsed.hostname:
            raise ValueError(
                f"remote_wip_url must include a hostname; got {url!r}"
            )
        if parsed.path and parsed.path != "":
            raise ValueError(
                f"remote_wip_url must not include a path; got {url!r} "
                f"(strip the path — apps append their own /api/* paths)"
            )
        if parsed.query or parsed.fragment:
            raise ValueError(
                f"remote_wip_url must not include query or fragment; "
                f"got {url!r}"
            )
        return self


# ────────────────────────────────────────────────────────────────────
# Images
# ────────────────────────────────────────────────────────────────────


class ImagesSpec(WIPModel):
    """How component images are sourced.

    `registry=None` means build-from-source where a `build_context` is
    declared in the component manifest, and pull-by-name otherwise. Set
    a registry to pull all images by `{registry}/{name}:{tag}`.
    """

    registry: str | None = None
    tag: str = "latest"
    pull_policy: Literal["always", "if-not-present"] = "if-not-present"


# ────────────────────────────────────────────────────────────────────
# Platform (per-target)
# ────────────────────────────────────────────────────────────────────


class ComposePlatform(WIPModel):
    data_dir: Path
    platform_variant: Literal["default", "pi4", "windows"] = "default"


class K8sPlatform(WIPModel):
    namespace: str = Field(default="wip", pattern=r"^[a-z][a-z0-9-]*$")
    storage_class: str = "rook-ceph-block"
    ingress_class: str = "nginx"
    tls_secret_name: str = "wip-tls"


class DevPlatform(WIPModel):
    mode: Literal["tilt", "simple"] = "tilt"
    source_mount: bool = True
    # CLI-provided map of app_name → local build-context path for
    # hot-reload dev. Apps not in this dict, AND not in
    # apps_from_registry, trip the CASE-355 loud-fail at render
    # time. Populated from `--app-source NAME=PATH` (repeatable).
    # CASE-55: lets app developers iterate against a full WIP stack
    # without a push → rebuild → redeploy round-trip.
    app_sources: dict[str, Path] = Field(default_factory=dict)
    # CLI-provided list of app names that should use the registry image
    # in dev mode despite no local source. The explicit opt-in for the
    # mixed-mode use case CASE-355 P2 named: most apps from local source,
    # one or two from the registry image (e.g., a stable app you don't
    # iterate on this session). Populated from `--app-from-registry NAME`
    # (repeatable). Without this, dev installs fail loud on missing
    # source rather than silently shipping a cached/registry copy.
    apps_from_registry: list[str] = Field(default_factory=list)


class PlatformSpec(WIPModel):
    """Discriminated by DeploymentSpec.target; only the matching block is
    consulted. DeploymentSpec validates that the required block is set."""

    compose: ComposePlatform | None = None
    k8s: K8sPlatform | None = None
    dev: DevPlatform | None = None


# ────────────────────────────────────────────────────────────────────
# Secrets
# ────────────────────────────────────────────────────────────────────


class SecretsSpec(WIPModel):
    """How secrets are resolved. The spec never carries secret values.

    - `file`: generate on first install, persist to a directory
    - `k8s-secret`: read from / write to k8s Secret objects
    - `sops`: SOPS-encrypted YAML file
    """

    backend: Literal["file", "k8s-secret", "sops"]
    location: str | None = None


# ────────────────────────────────────────────────────────────────────
# Apply
# ────────────────────────────────────────────────────────────────────


class ApplySpec(WIPModel):
    """Controls what happens after the platform has been told to bring
    the deployment up.

    `wait=True` (default) polls for every component's healthcheck to
    succeed (and for platform-level readiness) before returning. The CLI
    can override via `--no-wait`, `--wait-timeout N`, `--on-timeout X`."""

    wait: bool = True
    timeout_seconds: int = Field(default=300, ge=1)
    on_timeout: Literal["fail", "warn", "continue"] = "fail"


# ────────────────────────────────────────────────────────────────────
# Deployment (top)
# ────────────────────────────────────────────────────────────────────


class DeploymentSpec(WIPModel):
    target: Target
    modules: ModulesSpec = Field(default_factory=ModulesSpec)
    apps: list[AppRef] = Field(default_factory=list)
    auth: AuthSpec
    network: NetworkSpec
    images: ImagesSpec = Field(default_factory=ImagesSpec)
    platform: PlatformSpec
    secrets: SecretsSpec
    apply: ApplySpec = Field(default_factory=ApplySpec)

    @model_validator(mode="after")
    def platform_block_matches_target(self) -> DeploymentSpec:
        block = getattr(self.platform, self.target)
        if block is None:
            raise ValueError(
                f"target={self.target!r} requires platform.{self.target} "
                f"to be set"
            )
        return self

    @model_validator(mode="after")
    def unique_app_names(self) -> DeploymentSpec:
        names = [a.name for a in self.apps]
        if len(names) != len(set(names)):
            raise ValueError(f"duplicate app names in deployment: {names}")
        return self

    @model_validator(mode="after")
    def secrets_backend_matches_target(self) -> DeploymentSpec:
        # k8s-secret backend only makes sense with k8s target.
        if self.secrets.backend == "k8s-secret" and self.target != "k8s":
            raise ValueError(
                f"secrets.backend='k8s-secret' requires target='k8s'; "
                f"got target={self.target!r}"
            )
        return self

    @model_validator(mode="after")
    def self_signed_requires_k8s(self) -> DeploymentSpec:
        # Auto-generated self-signed certs are k8s-only. Compose's
        # `internal` mode already gives Caddy-managed self-signed; dev
        # uses `internal` for the same reason. Only k8s needs an
        # external cert source, hence the new mode.
        if self.network.tls == "self-signed" and self.target != "k8s":
            raise ValueError(
                f"tls='self-signed' requires target='k8s' (compose/dev "
                f"use tls='internal' for Caddy-managed self-signed certs); "
                f"got target={self.target!r}"
            )
        return self


class Deployment(WIPModel):
    api_version: Literal["wip.dev/v1"] = "wip.dev/v1"
    kind: Literal["Deployment"] = "Deployment"
    metadata: DeploymentMetadata
    spec: DeploymentSpec

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
    and required infrastructure (mongodb) are always active and not listed
    here. Valid values are the names of components with `category=optional`
    in their manifest.
    """

    optional: list[str] = Field(default_factory=list)

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


TLSMode = Literal["internal", "letsencrypt", "external"]


class NetworkSpec(WIPModel):
    hostname: str = Field(min_length=1)
    tls: TLSMode = "internal"
    https_port: int = Field(default=8443, ge=1, le=65535)
    http_port: int = Field(default=8080, ge=1, le=65535)

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


class Deployment(WIPModel):
    api_version: Literal["wip.dev/v1"] = "wip.dev/v1"
    kind: Literal["Deployment"] = "Deployment"
    metadata: DeploymentMetadata
    spec: DeploymentSpec

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
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from wip_deploy.spec._base import WIPModel
from wip_deploy.spec.component import NamespaceAccess, NamespaceSettings

# ────────────────────────────────────────────────────────────────────
# Top-level ids
# ────────────────────────────────────────────────────────────────────


Target = Literal["compose", "k8s", "dev"]

# Security posture, NOT topology: arms the services' production-safety
# guards (WIP_VARIANT=prod refuses to start on known-default secrets).
# Deliberately never derived from target — compose/k8s are routinely
# local-dev shapes; only explicit operator intent may assert prod.
Variant = Literal["dev", "prod"]


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


class AddedNamespace(WIPModel):
    """A namespace the operator gives an app on this install only, beyond
    what its manifest declares (`wip-deploy app-namespace add`). Reserved for
    the app with these settings; the app's key holds `admin` on it."""

    name: str = Field(pattern=r"^[a-z][a-z0-9-]*$")
    settings: NamespaceSettings = Field(default_factory=NamespaceSettings)

    @field_validator("name")
    @classmethod
    def not_wip(cls, v: str) -> str:
        if v == "wip":
            raise ValueError("the 'wip' namespace belongs to the platform")
        return v


class AppRef(WIPModel):
    """Reference to an app by name. The app's manifest is resolved from
    `apps/<name>/wip-app.yaml` at discovery time.

    `namespaces` and `namespace_access` are this install's additions to what
    the manifest declares: an operator decision for one install, made with
    `wip-deploy app-namespace`, never written into the shared manifest."""

    name: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9-]*$")
    enabled: bool = True
    namespaces: list[AddedNamespace] = Field(default_factory=list)
    namespace_access: list[NamespaceAccess] = Field(default_factory=list)

    @model_validator(mode="after")
    def each_namespace_once(self) -> AppRef:
        names = [n.name for n in self.namespaces] + [a.namespace for a in self.namespace_access]
        if len(names) != len(set(names)):
            raise ValueError(f"app {self.name!r}: a namespace is added twice: {names}")
        return self


# ────────────────────────────────────────────────────────────────────
# Auth
# ────────────────────────────────────────────────────────────────────


class DexUser(WIPModel):
    """A Dex static user. `groups` becomes the token's groups claim, so a
    user can hold several authorization groups at once.

    `password` says where the user's password comes from. `generated` (the
    default): the deployer generates it on the first apply and keeps it.
    `supplied`: the operator supplies its bcrypt hash (an existing user
    brought over from a hand-written Dex config keeps their password), the
    deployer never generates one, and no plaintext exists."""

    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    username: str = Field(min_length=1)
    groups: list[str] = Field(min_length=1)
    password: Literal["generated", "supplied"] = "generated"

    @model_validator(mode="before")
    @classmethod
    def _single_group_spelling(cls, data: Any) -> Any:
        # Specs saved before users could hold several groups carry a single
        # `group` string; they load as a one-element `groups` list.
        if isinstance(data, dict) and "group" in data:
            if "groups" in data:
                raise ValueError("declare either 'group' or 'groups', not both")
            data = dict(data)
            data["groups"] = [data.pop("group")]
        return data

    @field_validator("groups")
    @classmethod
    def _groups(cls, groups: list[str]) -> list[str]:
        out: list[str] = []
        for group in groups:
            if not group.strip():
                raise ValueError("group names must be non-empty")
            if group not in out:
                out.append(group)
        return out


def _default_dex_users() -> list[DexUser]:
    return [
        DexUser(email="admin@wip.local", username="admin", groups=["wip-admins"]),
        DexUser(email="editor@wip.local", username="editor", groups=["wip-editors"]),
        DexUser(email="viewer@wip.local", username="viewer", groups=["wip-viewers"]),
    ]


# The deployer adds a key of this name to every install (the canary, see
# config_gen/api_keys.py), so a spec may not declare one itself.
CANARY_KEY_NAME = "wip-canary"


class SpecAPIKey(WIPModel):
    """A declaratively provisioned API key (agents, external tools).

    Rendered into wip-auth's config-file key mechanism
    (WIP_AUTH_API_KEYS_FILE): the plaintext lives in the install's
    secret backend under `<name>-api-key` (generated on first apply,
    stable thereafter), and the key's read scope (`namespaces`) plus
    write grants resolve locally in every service — no MongoDB row,
    no Registry round-trip. Spec-declared keys therefore survive a
    Mongo wipe/restore intact, unlike runtime keys created via
    POST /api-keys (whose plaintext is server-generated and
    unrecoverable once the api_keys collection is rebuilt).
    """

    name: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9-]*$")
    namespaces: list[str] = Field(min_length=1)
    """Read scope — the key sees exactly these namespaces."""
    grants: dict[str, Literal["read", "write", "admin"]] = Field(
        default_factory=dict
    )
    """Write grants per namespace; keys must be within `namespaces`."""
    owner: str = "system"
    groups: list[str] = Field(default_factory=list)

    @property
    def secret_name(self) -> str:
        return f"{self.name}-api-key"

    @model_validator(mode="after")
    def grants_within_namespaces(self) -> SpecAPIKey:
        outside = sorted(set(self.grants) - set(self.namespaces))
        if outside:
            raise ValueError(
                f"api key {self.name!r}: grants on namespaces outside its "
                f"read scope: {outside} (add them to `namespaces` too)"
            )
        return self


# Groups that bypass namespace authorization. Only local Dex users and API
# keys ever hold them: a login federated through a connector can neither
# bring them from its identity provider nor be mapped to them.
RESERVED_GROUPS = ("wip-admins", "wip-services")

# Dex's static-password connector. Its users are the install's local users.
LOCAL_CONNECTOR_ID = "local"

# Config keys the deployer owns on every connector: the redirect URI is
# derived from the issuer, and the secret comes from the secret backend.
_DERIVED_CONNECTOR_KEYS = ("redirectURI",)


def _check_group_name(group: str) -> str:
    if not group or not all(ch.isalnum() or ch in "._-" for ch in group):
        raise ValueError(f"group {group!r}: use letters, digits, '.', '_' or '-'")
    return group


class DexConnector(WIPModel):
    """An external identity provider Dex federates logins from.

    `type` and `config` are Dex's own connector schema
    (https://dexidp.io/docs/connectors/), passed through as declared,
    except for what the deployer owns: `redirectURI` is derived from the
    issuer (`<issuer>/callback`), and the secret lives in the install's
    secret backend under `secret_name`, supplied by the operator and
    written into `config[secret_key]` at render time. It is never
    generated: a made-up client secret would only fail at the provider.
    """

    id: str = Field(pattern=r"^[a-z][a-z0-9-]*$")
    # Dex type names: oidc, github, atlassian-crowd, mockCallback, …
    type: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9-]*$")
    name: str = Field(min_length=1)
    config: dict[str, Any] = Field(default_factory=dict)
    secret_key: str | None = "clientSecret"
    """The config key Dex reads the secret from (`clientSecret` for OAuth
    and OIDC connectors, `bindPW` for LDAP); None for a connector that
    takes no secret."""

    @property
    def secret_name(self) -> str:
        return f"dex-connector-{self.id}"

    @model_validator(mode="after")
    def deployer_owned_keys(self) -> DexConnector:
        if self.id == LOCAL_CONNECTOR_ID:
            raise ValueError(
                f"connector id {LOCAL_CONNECTOR_ID!r} is Dex's local-user "
                "connector; choose another id"
            )
        owned = [k for k in (*_DERIVED_CONNECTOR_KEYS, self.secret_key) if k and k in self.config]
        if owned:
            raise ValueError(
                f"connector {self.id!r}: config may not set {owned} — the "
                "redirect URI is derived from the issuer and the secret "
                f"comes from the secret backend ({self.secret_name})"
            )
        return self


class GroupMapping(WIPModel):
    """Grants a WIP group to users who log in through a connector.

    Matches the token's email exactly or by domain. Local users get their
    group from their own declaration, so mappings never apply to them.
    """

    email: str | None = Field(default=None, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    email_domain: str | None = Field(default=None, pattern=r"^[^@\s]+\.[^@\s]+$")
    group: str

    @field_validator("group")
    @classmethod
    def _group(cls, group: str) -> str:
        _check_group_name(group)
        if group in RESERVED_GROUPS:
            raise ValueError(
                f"group {group!r} is reserved for local users: a federated "
                "login is never mapped to it"
            )
        return group

    @model_validator(mode="after")
    def one_matcher(self) -> GroupMapping:
        if (self.email is None) == (self.email_domain is None):
            raise ValueError("a group mapping matches exactly one of email or email_domain")
        return self


class AuthSpec(WIPModel):
    mode: Literal["oidc", "api-key-only", "hybrid"]
    gateway: bool
    """If True, the auth-gateway component sits in the request path for
    auth-required routes. Implementation differs per target but the
    guarantee is the same (Theme 7)."""
    users: list[DexUser] = Field(default_factory=_default_dex_users)
    session_ttl: str = "15m"
    api_keys: list[SpecAPIKey] = Field(default_factory=list)
    """Declaratively provisioned config-file API keys — see SpecAPIKey."""
    connectors: list[DexConnector] = Field(default_factory=list)
    """External identity providers Dex federates logins from."""
    group_mappings: list[GroupMapping] = Field(default_factory=list)
    """WIP groups granted to federated users, by email or email domain."""

    @model_validator(mode="after")
    def federation_shape(self) -> AuthSpec:
        ids = [c.id for c in self.connectors]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            raise ValueError(f"duplicate connector ids: {dupes}")
        if self.connectors and self.mode == "api-key-only":
            raise ValueError(
                "auth.connectors requires auth.mode ∈ {oidc, hybrid}: Dex is "
                "not running in api-key-only"
            )
        if self.group_mappings and not self.connectors:
            raise ValueError(
                "auth.group_mappings apply to federated logins only; declare a "
                "connector first"
            )
        return self

    @model_validator(mode="after")
    def api_key_names_unique(self) -> AuthSpec:
        names = [k.name for k in self.api_keys]
        dupes = sorted({n for n in names if names.count(n) > 1})
        if dupes:
            raise ValueError(f"duplicate api key names: {dupes}")
        if CANARY_KEY_NAME in names:
            raise ValueError(
                f"api key name {CANARY_KEY_NAME!r} is reserved: the deployer "
                "provisions that key on every install"
            )
        return self

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

    Tag resolution (CASE-438) is centralized in
    `config_gen.images.image_ref`. Precedence for WIP-built (short-name)
    images: `tag_overrides[name]` > `tag` (when set, it is authoritative
    over manifest pins) > manifest pin (`spec.image.tag`) > `latest`.
    `tag=None` means "no deployment-wide tag specified" — manifest pins
    apply. Fully-qualified images (mongo, postgres, dex…) ignore `tag`
    (their pins are upstream version numbers, not WIP release tags) but
    honor an explicit `tag_overrides` entry.
    """

    registry: str | None = None
    tag: str | None = None
    tag_overrides: dict[str, str] = Field(default_factory=dict)
    pull_policy: Literal["always", "if-not-present"] = "if-not-present"

    @field_validator("registry")
    @classmethod
    def _registry_is_an_image_prefix(cls, value: str | None) -> str | None:
        # Images are `{registry}/{name}:{tag}`. A URL scheme or a trailing
        # slash makes that an invalid image reference, which would only
        # surface as every pod failing to pull. A port is not a scheme:
        # `localhost:5000/x` is a valid prefix.
        if value is None:
            return None
        cleaned = value.strip()
        if "://" in cleaned:
            cleaned = cleaned.split("://", 1)[1]
        cleaned = cleaned.rstrip("/")
        if cleaned != value:
            raise ValueError(
                f"registry {value!r} is not an image prefix — images are "
                f"<registry>/<name>:<tag>, so no URL scheme, trailing slash or "
                f"spaces: use {cleaned!r}"
            )
        if not cleaned:
            raise ValueError("registry must not be empty; omit it to build or pull by name")
        return value


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
    # ndots written into every pod's /etc/resolv.conf. kubelet's default is 5,
    # which means any name with fewer than 5 dots is tried against the three
    # search domains BEFORE being tried as written. Inter-service URLs are
    # rendered as `wip-<name>.<ns>.svc.cluster.local` — four dots — so the
    # default costs three NXDOMAIN round trips (x2, since glibc queries A and
    # AAAA in parallel) before the real lookup, i.e. 8 queries where 2 suffice.
    # At 1, a name that already carries a dot is tried as written first, and
    # bare shortnames still resolve through the search list. Set to None to
    # emit no dnsConfig at all and inherit whatever kubelet defaults to.
    pod_dns_ndots: int | None = 1


class DevPlatform(WIPModel):
    # Only "simple" exists. A Tilt-based dev loop was planned and never built;
    # the option is removed rather than left as a default that no renderer honours.
    mode: Literal["simple"] = "simple"
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
    variant: Variant = "dev"
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

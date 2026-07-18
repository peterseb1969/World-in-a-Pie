"""Pre-exposure security verification for a wip-deploy install (CASE-445).

Successor to the retired v1 `scripts/security/production-check.sh`
(CASE-383): that script validated the v1 deployment shape (repo-root
`.env`, hand-maintained Caddyfile) which no longer exists. This module
reads what a v2 install actually is — the persisted deployer-state, the
rendered artifacts, and the secret backend under
`~/.wip-deploy/<name>/` — and answers one question: is this install
safe to expose beyond the local machine?

Pattern from check_app.py: pure-function checks over concrete inputs,
structured CheckResult rows, the CLI renders them as a ✓/✗ list. No
check mutates anything; the whole module is read-only.

### What's checked

  1. **Secret backend permissions** — the secrets dir itself 0700, every
     file in it 0600, plus the install's `.env` if present. Group/other
     bits on any of these leak credentials to local users.

  2. **API key strength** — the `api-key` secret must not be the
     publicly documented dev default and must not be trivially short.
     This is the deploy-side counterpart of the in-service
     WIP_VARIANT=prod startup guard: the guard refuses to *start* on the
     default key, this check catches it *before* exposure.

  3. **TLS mode vs hostname sanity** — a Caddy-internal (self-signed) CA
     on a public hostname means every visitor gets certificate warnings
     and no transport trust. The reverse direction (letsencrypt on a
     non-public hostname) is already rejected at spec validation.

  4. **Variant vs exposure** — `variant` arms the services' prod-mode
     startup guards and is explicit operator intent, never derived from
     the target. A public-shaped install (letsencrypt TLS or a public
     hostname) still at `variant: dev` runs with every startup guard
     disarmed — exactly the mismatch a pre-exposure check must catch.

  5. **Published host ports** — datastore and admin ports (MongoDB,
     PostgreSQL, NATS client/monitor, MinIO console) published to the
     host bypass Caddy and its auth entirely. The rendered compose file
     is the ground truth for what is actually published.

  6. **Security headers on public installs** — a letsencrypt install
     should send Strict-Transport-Security. The renderer currently
     emits no security-header block, so this check failing on a public
     install is a real finding, not noise; LAN-shaped installs
     (tls=internal) pass with a note.

### What's NOT checked

  - Live service health — that's `wip-deploy status`.
  - The k8s secret backend and k8s port exposure — file-backend and
    compose-shaped checks report "not applicable" on k8s installs
    rather than guessing at cluster state.
  - DB/NATS *authentication* config — infra credentials are rendered
    per-install; auditing their strength is future scope.
"""

from __future__ import annotations

import ipaddress
import re
import stat
from dataclasses import dataclass
from pathlib import Path

import yaml

from wip_deploy.check_app import CheckResult
from wip_deploy.spec import Deployment
from wip_deploy.spec.deployment import NetworkSpec

# The publicly documented dev key. Deliberately duplicated from
# libs/wip-auth (wip_auth.security.DEFAULT_API_KEY) rather than imported:
# the deployer does not depend on wip-auth, and the value is frozen by
# being documented — if it ever changes, the docs change with it.
_DEFAULT_DEV_API_KEY = "dev_master_key_for_testing"

# Below this length a random key has too little entropy to matter.
# `wip-deploy install` generates token_urlsafe(16) → 22 chars.
_MIN_API_KEY_LENGTH = 16

# Container ports that must never be published to the host on an
# exposed install: reaching them bypasses Caddy and every auth layer.
_SENSITIVE_CONTAINER_PORTS = {
    27017: "MongoDB",
    5432: "PostgreSQL",
    4222: "NATS client",
    8222: "NATS monitor",
    9001: "MinIO console",
}

# Hostname shapes that mean "not reachable from the internet": mDNS,
# RFC 6762-adjacent private TLDs, dotless single-label names, loopback,
# and RFC 1918 / link-local addresses.
_PRIVATE_HOST_SUFFIXES = (".local", ".internal", ".lan", ".home.arpa", ".test")


@dataclass(frozen=True)
class SecurityReport:
    """All security checks for one install, plus a roll-up."""

    install_dir: Path
    results: list[CheckResult]

    @property
    def ok(self) -> bool:
        return all(r.passed for r in self.results)

    @property
    def failures(self) -> list[CheckResult]:
        return [r for r in self.results if not r.passed]


# ────────────────────────────────────────────────────────────────────
# Hostname classification
# ────────────────────────────────────────────────────────────────────


def is_public_hostname(hostname: str) -> bool:
    """True when the hostname plausibly resolves on the public internet.

    Mirrors (inverted) the spec's letsencrypt_requires_public_hostname
    heuristic and extends it: loopback, private/link-local IPs, private
    TLD suffixes, and dotless single-label names all count as private.
    Anything else — a dotted DNS name or a global IP — counts as public.
    """
    h = hostname.strip().lower()
    if not h or h == "localhost":
        return False
    try:
        addr = ipaddress.ip_address(h)
    except ValueError:
        pass
    else:
        return addr.is_global
    if h.endswith(_PRIVATE_HOST_SUFFIXES):
        return False
    return "." in h


# ────────────────────────────────────────────────────────────────────
# Individual checks — pure functions over concrete inputs
# ────────────────────────────────────────────────────────────────────


def _mode_of(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def _secrets_dir(install_dir: Path, deployment: Deployment) -> Path:
    """The file backend's directory; `location` may be unset in the spec."""
    location = deployment.spec.secrets.location
    if location:
        return Path(location).expanduser()
    return install_dir / "secrets"


def check_secret_permissions(install_dir: Path, deployment: Deployment) -> CheckResult:
    """Secrets dir 0700, every secret file 0600, `.env` 0600 if present."""
    name = "secret file permissions"
    backend = deployment.spec.secrets.backend
    if backend != "file":
        return CheckResult(name, True, f"not applicable (secret backend: {backend})")

    secrets_dir = _secrets_dir(install_dir, deployment)
    if not secrets_dir.is_dir():
        return CheckResult(
            name,
            False,
            f"secret backend directory missing: {secrets_dir}",
            fix_hint="re-run `wip-deploy install` to materialize the secret backend",
        )

    offenders: list[str] = []
    dir_mode = _mode_of(secrets_dir)
    if dir_mode & 0o077:
        offenders.append(f"{secrets_dir}/ is {dir_mode:04o} (want 0700)")
    for f in sorted(secrets_dir.iterdir()):
        if not f.is_file():
            continue
        f_mode = _mode_of(f)
        if f_mode & 0o077:
            offenders.append(f"{f.name} is {f_mode:04o} (want 0600)")

    env_file = install_dir / ".env"
    if env_file.is_file():
        env_mode = _mode_of(env_file)
        if env_mode & 0o077:
            offenders.append(f".env is {env_mode:04o} (want 0600)")

    if offenders:
        return CheckResult(
            name,
            False,
            "; ".join(offenders),
            fix_hint=(
                f"chmod 700 {secrets_dir} && chmod 600 {secrets_dir}/*\n"
                "Group/other-readable secrets are readable by every local user."
            ),
        )
    return CheckResult(name, True, f"dir 0700, files 0600 under {secrets_dir}")


def check_api_key_strength(install_dir: Path, deployment: Deployment) -> CheckResult:
    """The `api-key` secret is not the documented dev default, not trivial."""
    name = "API key strength"
    backend = deployment.spec.secrets.backend
    if backend != "file":
        return CheckResult(name, True, f"not applicable (secret backend: {backend})")

    key_file = _secrets_dir(install_dir, deployment) / "api-key"
    if not key_file.is_file():
        return CheckResult(
            name,
            False,
            f"api-key secret missing: {key_file}",
            fix_hint="re-run `wip-deploy install` to generate the api-key secret",
        )

    key = key_file.read_text().strip()
    if key == _DEFAULT_DEV_API_KEY:
        return CheckResult(
            name,
            False,
            "api-key is the publicly documented dev default",
            fix_hint=(
                "This key appears in the repo's docs — anyone can use it.\n"
                "Replace the api-key secret with a random value and redeploy\n"
                "(`wip-deploy redeploy` — restart alone does not re-read secrets)."
            ),
        )
    if len(key) < _MIN_API_KEY_LENGTH:
        return CheckResult(
            name,
            False,
            f"api-key is only {len(key)} chars (want >= {_MIN_API_KEY_LENGTH})",
            fix_hint="replace with a generated key, e.g. python3 -c "
            '"import secrets; print(secrets.token_urlsafe(16))"',
        )
    return CheckResult(name, True, f"random key present ({len(key)} chars)")


def check_tls_hostname_sanity(network: NetworkSpec) -> CheckResult:
    """A self-signed/internal CA on a public hostname is a broken promise."""
    name = "TLS mode vs hostname"
    public = is_public_hostname(network.hostname)
    if network.tls in ("internal", "self-signed") and public:
        return CheckResult(
            name,
            False,
            f"tls={network.tls} with public hostname {network.hostname!r}",
            fix_hint=(
                "A public hostname behind a self-signed CA gives every visitor\n"
                "certificate warnings and no transport trust. Reinstall with\n"
                "--tls letsencrypt, or use a private hostname if this is LAN-only."
            ),
        )
    return CheckResult(
        name,
        True,
        f"tls={network.tls}, hostname {network.hostname!r} "
        f"({'public' if public else 'private'})",
    )


def check_variant_exposure(deployment: Deployment) -> CheckResult:
    """A public-shaped install must run variant=prod (armed startup guards)."""
    name = "variant vs exposure"
    network = deployment.spec.network
    variant = deployment.spec.variant
    public_shaped = network.tls == "letsencrypt" or is_public_hostname(network.hostname)
    if public_shaped and variant != "prod":
        return CheckResult(
            name,
            False,
            f"public-shaped install (tls={network.tls}, "
            f"hostname {network.hostname!r}) still at variant={variant!r}",
            fix_hint=(
                "variant=dev leaves every in-service prod-mode startup guard\n"
                "disarmed. Variant is explicit operator intent — re-run\n"
                "`wip-deploy install --variant prod` (persists in the install's\n"
                "state; redeploy/rebuild keep it)."
            ),
        )
    if variant == "prod":
        return CheckResult(name, True, "variant=prod — startup guards armed")
    return CheckResult(
        name, True, f"variant=dev on a private-shaped install ({network.hostname!r})"
    )


def check_published_ports(install_dir: Path, deployment: Deployment) -> CheckResult:
    """No datastore/admin container port published to the host."""
    name = "published host ports"
    compose_yaml = install_dir / "docker-compose.yaml"
    if deployment.spec.target == "k8s" or not compose_yaml.is_file():
        return CheckResult(
            name, True, "not applicable (no rendered compose file — k8s install)"
        )

    try:
        compose = yaml.safe_load(compose_yaml.read_text())
    except yaml.YAMLError as exc:
        return CheckResult(name, False, f"could not parse {compose_yaml}: {exc}")

    sensitive: list[str] = []
    published: list[str] = []
    for svc_name, svc in ((compose or {}).get("services") or {}).items():
        for entry in svc.get("ports") or []:
            # Compose short syntax: [HOST_IP:]HOST_PORT:CONTAINER_PORT[/proto],
            # or a bare CONTAINER_PORT (published to an ephemeral host port —
            # still host-reachable). Long syntax: {published: N, target: M}.
            if isinstance(entry, dict):
                host_port = entry.get("published") or "auto"
                container_port = entry.get("target")
            else:
                parts = str(entry).split("/")[0].rsplit(":", 2)
                host_port = parts[-2] if len(parts) >= 2 else "auto"
                container_port = parts[-1]
            try:
                container_num = int(container_port)
            except (TypeError, ValueError):
                continue
            desc = f"{svc_name}: host {host_port} → container {container_num}"
            if container_num in _SENSITIVE_CONTAINER_PORTS:
                sensitive.append(f"{desc} ({_SENSITIVE_CONTAINER_PORTS[container_num]})")
            else:
                published.append(desc)

    if sensitive:
        return CheckResult(
            name,
            False,
            "; ".join(sensitive),
            fix_hint=(
                "These ports bypass Caddy and every auth layer when reached\n"
                "directly. Remove the host mapping from the deployment spec\n"
                "and reinstall; reach the service through Caddy instead."
            ),
        )
    summary = "; ".join(published) if published else "none"
    return CheckResult(name, True, f"no datastore/admin ports on the host (published: {summary})")


def check_caddy_security_headers(install_dir: Path, network: NetworkSpec) -> CheckResult:
    """Public (letsencrypt) installs should send Strict-Transport-Security."""
    name = "security headers (Caddyfile)"
    if network.tls != "letsencrypt":
        return CheckResult(
            name,
            True,
            f"LAN-shaped install (tls={network.tls}) — HSTS applies to public installs",
        )

    caddyfile = install_dir / "config" / "caddy" / "Caddyfile"
    if not caddyfile.is_file():
        return CheckResult(
            name,
            False,
            f"rendered Caddyfile missing: {caddyfile}",
            fix_hint="re-run `wip-deploy install` to render the Caddy config",
        )

    text = caddyfile.read_text()
    if re.search(r"Strict-Transport-Security", text, re.IGNORECASE):
        return CheckResult(name, True, "Strict-Transport-Security present")
    return CheckResult(
        name,
        False,
        "public install without Strict-Transport-Security in the rendered Caddyfile",
        fix_hint=(
            "The renderer does not currently emit a security-header block —\n"
            "add `header Strict-Transport-Security \"max-age=31536000\"` to the\n"
            "site block manually until header rendering lands as a platform\n"
            "feature, and re-run this check."
        ),
    )


# ────────────────────────────────────────────────────────────────────
# Top-level entry
# ────────────────────────────────────────────────────────────────────


def verify_security(install_dir: Path, deployment: Deployment) -> SecurityReport:
    """Run every pre-exposure check against one install. Read-only."""
    network = deployment.spec.network
    results = [
        check_secret_permissions(install_dir, deployment),
        check_api_key_strength(install_dir, deployment),
        check_tls_hostname_sanity(network),
        check_variant_exposure(deployment),
        check_published_ports(install_dir, deployment),
        check_caddy_security_headers(install_dir, network),
    ]
    return SecurityReport(install_dir=install_dir, results=results)

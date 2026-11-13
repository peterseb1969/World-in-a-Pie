"""What the deployer trusts when it sends the install's master key over HTTPS.

The pre-apply checks (runtime reservations, app attachments) and
`verify --security --live` send the install's master key to the install's
public URL. They used to verify the certificate only on a `letsencrypt`
install, so on every other one — `internal`, the default — the master key
went out with verification off.

`trust_anchor` names what to verify against instead:

- `letsencrypt` and `external`: the system's trust store. An external
  terminator with a private CA then fails verification, which the callers
  treat as "could not ask", never as a reason to retry unverified.
- `internal` (compose and dev): the root CA the install's own Caddy issued,
  read from its `wip-caddy` container (what `wip-deploy export-ca` reads).
- `self-signed` (k8s): the certificate the deployer itself put in the
  install's TLS Secret.

When no anchor can be had — the first install, before Caddy has made its CA;
a cluster that cannot be asked — it returns `Untrusted` with the reason, and
the caller skips the request that would carry the key and says so. The key
is never sent unverified.
"""

from __future__ import annotations

import base64
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from wip_deploy.spec.deployment import Deployment

_KUBECTL_TIMEOUT_SECONDS = 30


@dataclass(frozen=True)
class Untrusted:
    """No trust anchor for the install's certificate, and why."""

    why: str


def trust_anchor(deployment: Deployment) -> bool | str | Untrusted:
    """httpx's `verify` for a request to the install's public URL: True for
    the system trust store, a CA file path, or Untrusted."""
    tls = deployment.spec.network.tls
    if tls in ("letsencrypt", "external"):
        return True
    if tls == "internal":
        return _caddy_root_ca(deployment)
    return _tls_secret_cert(deployment)


def _write_anchor(pem: bytes) -> str:
    handle = tempfile.NamedTemporaryFile(prefix="wip-trust-", suffix=".crt", delete=False)  # noqa: SIM115
    with handle:
        handle.write(pem)
    return handle.name


def _caddy_root_ca(deployment: Deployment) -> str | Untrusted:
    from wip_deploy.export_ca import ExportCAError, export_caddy_internal_ca
    from wip_deploy.naming import container_name

    try:
        pem = export_caddy_internal_ca(container_name(deployment.metadata.name, "caddy"))
    except ExportCAError as e:
        return Untrusted(f"the install's Caddy CA could not be read ({str(e).splitlines()[0]})")
    return _write_anchor(pem)


def _tls_secret_cert(deployment: Deployment) -> str | Untrusted:
    from wip_deploy.kube import kubectl

    k8s = deployment.spec.platform.k8s
    if k8s is None:
        return Untrusted(f"tls {deployment.spec.network.tls!r} names no certificate this deployer holds")
    try:
        proc = subprocess.run(
            [*kubectl(), "get", "secret", k8s.tls_secret_name, "-n", k8s.namespace,
             "-o", r"jsonpath={.data.tls\.crt}"],
            capture_output=True, text=True, timeout=_KUBECTL_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return Untrusted(f"the install's TLS Secret could not be read ({type(e).__name__})")
    if proc.returncode != 0 or not proc.stdout.strip():
        return Untrusted(
            f"the install's TLS Secret {k8s.tls_secret_name!r} could not be read "
            f"(kubectl exit {proc.returncode})"
        )
    try:
        return _write_anchor(base64.b64decode(proc.stdout.strip(), validate=True))
    except ValueError:
        return Untrusted(f"the install's TLS Secret {k8s.tls_secret_name!r} holds no readable certificate")


def anchor_path(verify: bool | str | Untrusted) -> Path | None:
    """The temporary CA file `trust_anchor` wrote, if it wrote one."""
    return Path(verify) if isinstance(verify, str) else None

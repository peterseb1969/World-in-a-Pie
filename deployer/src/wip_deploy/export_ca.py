"""Export the Caddy-managed internal CA root cert (CASE-360).

When `wip-deploy install --tls internal` brings up Caddy, Caddy generates
a per-install self-signed root CA on first HTTPS request. The PEM cert
lives inside the `wip-caddy` container at:

    /data/caddy/pki/authorities/local/root.crt

Same-host browsers and `localhost` clients prompt-and-accept fine. But
off-host clients (`curl` from a Mac, Node.js processes via `@wip/proxy`,
embedded devices) reject the self-signed chain without an explicit
trust step. CASE-360 surfaced this as friction in the cross-host
deployment story (CASE-357 §A).

This module is the pure-function half of the `wip-deploy export-ca`
verb. CLI wiring lives in `cli.py`. Single supported source today is
the compose/dev Caddy container; k8s `tls=self-signed` could be added
later via cert-manager's CA Secret.
"""

from __future__ import annotations

import shutil
import subprocess

# Caddy's PKI directory layout is stable across 2.x. The default CA
# (when tls.issuer=internal) is named 'local' and its root is here.
_CADDY_INTERNAL_CA_PATH = "/data/caddy/pki/authorities/local/root.crt"


class ExportCAError(Exception):
    """Raised when the CA cannot be located or extracted.

    The message is operator-facing — callers should print it verbatim
    rather than wrapping with extra context.
    """


def export_caddy_internal_ca(container_name: str = "wip-caddy") -> bytes:
    """Read the Caddy internal-CA root cert from a running container.

    Uses `podman exec <container> cat <path>` rather than `podman cp`
    so the output is the raw PEM bytes (cp would produce a tar stream).

    Returns:
        PEM-encoded certificate bytes.

    Raises:
        ExportCAError: podman missing, container not running, or the
            CA file doesn't exist yet (Caddy lazy-generates on first
            HTTPS request — a freshly-installed but never-probed
            install has no CA on disk).
    """
    if not shutil.which("podman"):
        raise ExportCAError(
            "podman is not available on PATH. wip-deploy export-ca "
            "needs podman to read from the running wip-caddy container."
        )

    # Container-running check first — gives a much clearer error than
    # falling through to `podman exec` against a non-existent container.
    ps = subprocess.run(
        [
            "podman", "ps",
            "--filter", f"name=^{container_name}$",
            "--filter", "status=running",
            "--format", "{{.Names}}",
        ],
        check=False, capture_output=True, text=True,
    )
    if ps.returncode != 0 or container_name not in ps.stdout:
        raise ExportCAError(
            f"container {container_name!r} is not running. "
            f"Bring the install up first: `wip-deploy install --target compose ...`"
        )

    # `podman exec ... cat <file>` streams the raw bytes to stdout.
    # Use bytes mode (no text=True) because the cert is technically PEM
    # text but we don't want any Python-side codec re-encoding.
    result = subprocess.run(
        ["podman", "exec", container_name, "cat", _CADDY_INTERNAL_CA_PATH],
        check=False, capture_output=True,
    )
    if result.returncode != 0:
        # Most common cause: Caddy hasn't generated the CA yet because
        # no HTTPS request has triggered cert provisioning. Trigger it
        # with a probe and try again — the operator instruction in the
        # error gives them the one-line workaround.
        stderr = result.stderr.decode(errors="replace").strip()
        hint = (
            "Caddy generates the internal CA lazily on first HTTPS request. "
            "Try `curl -kI https://localhost:8443/` to trigger generation, "
            "then re-run export-ca."
        )
        raise ExportCAError(
            f"could not read CA from {container_name}:{_CADDY_INTERNAL_CA_PATH}\n"
            f"{stderr}\n{hint}" if stderr else
            f"could not read CA from {container_name}:{_CADDY_INTERNAL_CA_PATH}. "
            f"{hint}"
        )

    if not result.stdout.strip():
        raise ExportCAError(
            f"{container_name}:{_CADDY_INTERNAL_CA_PATH} is empty. "
            f"This is unusual — check the container's logs."
        )

    return result.stdout


# Trust-instruction copy printed after a successful --out write. Kept
# here (not inlined in the CLI) so it can be reused by docs generation
# or other surfaces without duplicating the OS list.
TRUST_INSTRUCTIONS = """\
To trust this CA in:
  Node.js:  export NODE_EXTRA_CA_CERTS=$(pwd)/{out}
  macOS:    security add-trusted-cert -d -r trustRoot -k login.keychain {out}
  Linux:    sudo cp {out} /usr/local/share/ca-certificates/ \\
              && sudo update-ca-certificates
  curl:     curl --cacert {out} https://<wip-host>:<port>/...
"""

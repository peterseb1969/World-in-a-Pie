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
from pathlib import Path
from typing import Any

# Each container-runtime call answers within this, or export-ca stops with an error.
_PODMAN_TIMEOUT_SECONDS = 30

# Caddy's PKI directory layout is stable across 2.x. The default CA
# (when tls.issuer=internal) is named 'local' and its root is here.
_CADDY_INTERNAL_CA_PATH = "/data/caddy/pki/authorities/local/root.crt"


class ExportCAError(Exception):
    """Raised when the CA cannot be located or extracted.

    The message is operator-facing — callers should print it verbatim
    rather than wrapping with extra context.
    """


def _runtime() -> str | None:
    """The container runtime CLI on PATH, as the apply path picks it: podman,
    else docker (the documented fallback), else None. Both take the ps,
    inspect and exec calls below unchanged."""
    if shutil.which("podman"):
        return "podman"
    return "docker" if shutil.which("docker") else None


def _podman(args: list[str], *, text: bool) -> subprocess.CompletedProcess[Any]:
    """One container-runtime call (podman, else docker), bounded: a hung
    socket would hang export-ca."""
    runtime = _runtime() or "podman"
    try:
        return subprocess.run(
            [runtime, *args], check=False, capture_output=True, text=text,
            timeout=_PODMAN_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as e:
        raise ExportCAError(
            f"`{runtime} {' '.join(args[:2])}` did not answer within {_PODMAN_TIMEOUT_SECONDS}s"
        ) from e


def _check_belongs_to(container_name: str, install_dir: Path) -> None:
    """The container serves this install: it mounts the install's own
    Caddyfile. The name is the install's (`<install>_wip-caddy`), but a
    container left over from an earlier install of the same name, or created
    by hand, can carry it too."""
    expected = (install_dir / "config" / "caddy" / "Caddyfile").resolve()
    inspect = _podman(
        ["inspect", "--format", "{{range .Mounts}}{{.Source}}\n{{end}}", container_name], text=True,
    )
    sources = {Path(line.strip()).resolve() for line in (inspect.stdout or "").splitlines() if line.strip()}
    if inspect.returncode != 0 or expected not in sources:
        raise ExportCAError(
            f"container {container_name!r} does not serve this install: it does not "
            f"mount {expected}. Is another install running on this host?"
        )


def export_caddy_internal_ca(
    container_name: str, install_dir: Path | None = None,
) -> bytes:
    """Read the Caddy internal-CA root cert from a running container.

    Uses `<runtime> exec <container> cat <path>` (podman, else docker)
    rather than `cp`, so the output is the raw PEM bytes (cp would produce a
    tar stream).

    Returns:
        PEM-encoded certificate bytes.

    Raises:
        ExportCAError: no container runtime, container not running, or the
            CA file doesn't exist yet (Caddy lazy-generates on first
            HTTPS request — a freshly-installed but never-probed
            install has no CA on disk).
    """
    if _runtime() is None:
        raise ExportCAError(
            "neither podman nor docker is on PATH. wip-deploy export-ca "
            "needs one to read from the running wip-caddy container."
        )

    # Container-running check first — gives a much clearer error than
    # falling through to `exec` against a non-existent container.
    # The name filter matches a part of the name on docker and podman alike
    # (their anchored forms differ), so the exact name is compared here.
    ps = _podman(
        [
            "ps",
            "--filter", f"name={container_name}",
            "--filter", "status=running",
            "--format", "{{.Names}}",
        ],
        text=True,
    )
    running = {line.strip().lstrip("/") for line in (ps.stdout or "").splitlines()}
    if ps.returncode != 0 or container_name not in running:
        raise ExportCAError(
            f"container {container_name!r} is not running. "
            f"Bring the install up first: `wip-deploy install --target compose ...`"
        )

    # `exec ... cat <file>` streams the raw bytes to stdout.
    # Use bytes mode (no text=True) because the cert is technically PEM
    # text but we don't want any Python-side codec re-encoding.
    if install_dir is not None:
        _check_belongs_to(container_name, install_dir)
    result = _podman(["exec", container_name, "cat", _CADDY_INTERNAL_CA_PATH], text=False)
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

    return bytes(result.stdout)


# Trust-instruction copy printed after a successful --out write. Kept
# here (not inlined in the CLI) so it can be reused by docs generation
# or other surfaces without duplicating the OS list.
TRUST_INSTRUCTIONS = """\
To trust this CA in:
  Node.js:  export NODE_EXTRA_CA_CERTS={out}
  macOS:    security add-trusted-cert -r trustRoot -k ~/Library/Keychains/login.keychain-db {out}
  Linux:    sudo cp {out} /usr/local/share/ca-certificates/wip-internal-ca.crt \\
              && sudo update-ca-certificates
  curl:     curl --cacert {out} https://<wip-host>:<port>/...
"""

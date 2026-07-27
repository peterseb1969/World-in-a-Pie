"""Deployment-pointable HTTP client for the backup/restore test matrix.

Shared by the fixture provisioner (Phase 1) and the layer-L runner. A target is
ALWAYS stated explicitly in the invocation — either a named wip-deploy install
(`--install <name>`, read from ``~/.wip-deploy/<name>/``) or a raw
``--base-url`` + ``--key-file`` pair for anything else. There is no implicit
"current install"; the resolved target is echoed so every run's report names
what it hit.

All WIP services sit behind one Caddy/ingress origin at ``/api/<service>/...``;
auth is the ``X-API-Key`` header. See ``wip://conventions`` for the bulk-first
200-OK contract these helpers surface.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

WIP_DEPLOY_ROOT = Path.home() / ".wip-deploy"


class TargetError(RuntimeError):
    """Raised when a deployment target cannot be resolved."""


@dataclass(frozen=True)
class Target:
    """A resolved WIP deployment: where to reach it and how it was named."""

    base_url: str
    api_key: str
    source: str  # human-readable provenance, echoed in report headers
    verify_tls: bool


def resolve_target(
    *,
    install: str | None,
    base_url: str | None,
    key_file: str | None,
    verify_tls: bool,
) -> Target:
    """Resolve a Target from CLI options.

    Exactly one of ``install`` or (``base_url`` + ``key_file``) must be given.
    A named install reads its public hostname/port from the persisted
    ``deployment.deployer-state`` (``spec.network.hostname`` / ``https_port``)
    and its key from ``secrets/api-key`` — the same on-disk layout the design
    doc §7 names as the source of a target's URL + key.
    """
    if install and (base_url or key_file):
        raise TargetError(
            "Pass either --install OR (--base-url + --key-file), not both."
        )

    if install:
        return _resolve_install(install, verify_tls)

    if base_url and key_file:
        kf = Path(key_file).expanduser()
        if not kf.is_file():
            raise TargetError(f"--key-file: not a file: {kf}")
        key = kf.read_text().strip()
        if not key:
            raise TargetError(f"--key-file: file is empty: {kf}")
        return Target(
            base_url=base_url.rstrip("/"),
            api_key=key,
            source=f"--base-url {base_url} (key {kf})",
            verify_tls=verify_tls,
        )

    raise TargetError(
        "No target given. Pass --install <name>, or --base-url <url> "
        "--key-file <path>."
    )


def _resolve_install(name: str, verify_tls: bool) -> Target:
    root = WIP_DEPLOY_ROOT / name
    if not root.is_dir():
        available = (
            sorted(d.name for d in WIP_DEPLOY_ROOT.iterdir() if d.is_dir())
            if WIP_DEPLOY_ROOT.is_dir()
            else []
        )
        raise TargetError(
            f"Install '{name}' not found under {WIP_DEPLOY_ROOT}.\n"
            f"Available: {', '.join(available) if available else '(none)'}"
        )

    state_file = root / "deployment.deployer-state"
    if not state_file.is_file():
        raise TargetError(f"No deployer-state for install '{name}': {state_file}")
    try:
        state = json.loads(state_file.read_text())
        net = state["deployment"]["spec"]["network"]
        hostname = net["hostname"]
        https_port = int(net.get("https_port", 8443))
    except (KeyError, ValueError, TypeError) as exc:
        raise TargetError(
            f"Could not read network config from {state_file}: {exc}"
        ) from exc

    key_file = root / "secrets" / "api-key"
    if not key_file.is_file():
        raise TargetError(f"No api-key for install '{name}': {key_file}")
    key = key_file.read_text().strip()
    if not key:
        raise TargetError(f"api-key file is empty: {key_file}")

    port_suffix = "" if https_port == 443 else f":{https_port}"
    base_url = f"https://{hostname}{port_suffix}"
    return Target(
        base_url=base_url,
        api_key=key,
        source=f"--install {name} ({base_url})",
        verify_tls=verify_tls,
    )


class ApiError(RuntimeError):
    """A non-2xx HTTP response from a WIP service."""

    def __init__(self, method: str, path: str, status: int, body: str):
        self.method = method
        self.path = path
        self.status = status
        self.body = body
        super().__init__(f"{method} {path} -> {status}: {body[:600]}")


class WipClient:
    """Thin httpx wrapper: one origin, X-API-Key auth, JSON in/out.

    Deliberately not a full SDK — just enough to drive the public REST surface
    the fixture provisioner and runner call. Callers own bulk-response
    inspection (200 OK can carry per-item errors); ``check_bulk`` helps.
    """

    def __init__(self, target: Target, timeout: float = 60.0):
        self.target = target
        self._client = httpx.Client(
            base_url=target.base_url,
            headers={"X-API-Key": target.api_key},
            verify=target.verify_tls,
            timeout=timeout,
        )

    def __enter__(self) -> WipClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any | None = None,
        files: Any | None = None,
        data: dict[str, Any] | None = None,
    ) -> Any:
        resp = self._client.request(
            method,
            path,
            params=params,
            json=json_body,
            files=files,
            data=data,
        )
        if resp.status_code >= 300:
            raise ApiError(method, path, resp.status_code, resp.text)
        if not resp.content:
            return None
        ctype = resp.headers.get("content-type", "")
        if "application/json" in ctype:
            return resp.json()
        return resp.text

    def get(self, path: str, **kw: Any) -> Any:
        return self.request("GET", path, **kw)

    def get_bytes(self, path: str, *, params: dict[str, Any] | None = None) -> bytes:
        """GET returning the raw response body (for archive downloads)."""
        resp = self._client.request("GET", path, params=params)
        if resp.status_code >= 300:
            raise ApiError("GET", path, resp.status_code, resp.text)
        return resp.content

    def get_prefix(
        self, path: str, *, max_bytes: int, params: dict[str, Any] | None = None
    ) -> bytes:
        """GET only the first ``max_bytes`` of a response, then hang up.

        For reading a small header out of a large body. HTTP Range is not an
        option against these endpoints — the archive download is a bare
        ``StreamingResponse``, which implements no Range handling and would
        send the whole body regardless — so the transfer is stopped
        client-side instead, by leaving the stream context early.
        """
        with self._client.stream("GET", path, params=params) as resp:
            if resp.status_code >= 300:
                resp.read()
                raise ApiError("GET", path, resp.status_code, resp.text)
            buf = bytearray()
            for chunk in resp.iter_bytes(chunk_size=64 * 1024):
                buf.extend(chunk)
                if len(buf) >= max_bytes:
                    break
        return bytes(buf)

    def post(self, path: str, **kw: Any) -> Any:
        return self.request("POST", path, **kw)

    def put(self, path: str, **kw: Any) -> Any:
        return self.request("PUT", path, **kw)

    def patch(self, path: str, **kw: Any) -> Any:
        return self.request("PATCH", path, **kw)

    def delete(self, path: str, **kw: Any) -> Any:
        return self.request("DELETE", path, **kw)


# Statuses the bulk-first API counts as success (see wip://conventions). Spans
# the write verbs the fixture drives: create/upsert (created/updated/unchanged),
# bulk skips (skipped/already_exists), synonym add (added), and the lifecycle
# transitions (deleted/deactivated/archived/deprecated).
_OK_STATUSES = {
    "created",
    "updated",
    "unchanged",
    "skipped",
    "added",
    "already_exists",
    "deleted",
    "deactivated",
    "archived",
    "deprecated",
}


def check_bulk(response: Any, *, context: str) -> list[dict[str, Any]]:
    """Assert every per-item result in a bulk response succeeded.

    A 200 OK can still carry per-item validation failures inside the body
    (PoNIF #4). Returns the list of result items on success; raises with the
    offending items on any error status.
    """
    if isinstance(response, list):
        items = response
    elif isinstance(response, dict):
        items = response.get("results", response.get("items", []))
    else:
        raise RuntimeError(f"{context}: unexpected response type {type(response)}")

    failed = [
        it
        for it in items
        if isinstance(it, dict) and it.get("status", "created") not in _OK_STATUSES
    ]
    if failed:
        raise RuntimeError(
            f"{context}: {len(failed)} of {len(items)} items failed: "
            f"{json.dumps(failed[:5], default=str)}"
        )
    return items

"""An apply must not reserve a namespace a runtime key already reserves.

A platform admin can reserve namespaces through the Registry's API, for an
app wip-deploy does not manage (a runtime API key with `reserve`). The
install's own reservations come from its apps' manifests. One prefix has one
owner: if an apply rendered a file reservation of a prefix a runtime key
holds, neither could be trusted to create it, and the Registry refuses the
prefix until one of the two is removed.

So an apply asks the running Registry for its runtime reservations and
refuses a collision before rendering, naming both owners. A collision on an
ID prefix is refused the same way, since one prefix names one ID sequence.
When the Registry cannot be reached (a fresh install, a stopped stack) or
predates runtime reservations, the apply proceeds with a note: the Registry
itself refuses a colliding prefix at the namespace upsert, so nothing is
created under two owners.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from wip_deploy.config_gen.namespaces import _prefixed_ids, namespace_reservations_json
from wip_deploy.spec import App, Deployment


@dataclass
class RuntimeReservationCheck:
    errors: list[str] = field(default_factory=list)
    note: str | None = None


def collisions(
    file_reservations: list[dict[str, Any]], runtime_reservations: list[dict[str, Any]]
) -> list[str]:
    """What the install's reservations contradict in the runtime ones."""
    errors: list[str] = []
    runtime = {r["prefix"]: r for r in runtime_reservations}
    runtime_ids: dict[str, str] = {}
    for r in runtime_reservations:
        for entity_type, prefix in _prefixed_ids(r.get("settings") or {}).items():
            runtime_ids[prefix] = f"{r['prefix']}.{entity_type} (runtime key {r['app']!r})"
    for f in file_reservations:
        held = runtime.get(f["prefix"])
        if held is not None:
            errors.append(
                f"namespace {f['prefix']!r} is reserved for app {f['app']!r}, but runtime "
                f"API key {held['app']!r} already reserves it. Release it in the Registry "
                f"(DELETE /api/registry/reservations/{f['prefix']}, or narrow or revoke "
                "the key) or reserve another namespace for the app"
            )
        for entity_type, prefix in sorted(_prefixed_ids(f.get("settings") or {}).items()):
            if prefix in runtime_ids:
                errors.append(
                    f"ID prefix {prefix!r} of {f['prefix']}.{entity_type} (app {f['app']!r}) "
                    f"is already reserved for {runtime_ids[prefix]}; each prefix names one "
                    "ID sequence on the install"
                )
    return errors


def check_runtime_reservations(
    deployment: Deployment,
    apps: list[App],
    admin_key: str | None,
    *,
    transport: Any = None,
    timeout: float = 5.0,
) -> RuntimeReservationCheck:
    rendered = namespace_reservations_json(deployment, apps)
    file_reservations = json.loads(rendered)["reservations"] if rendered else []
    if not file_reservations:
        return RuntimeReservationCheck()
    if not admin_key:
        return RuntimeReservationCheck(
            note="no admin key to ask the Registry for runtime reservations; not checked"
        )

    import httpx

    from wip_deploy.config_gen.spec_context import _public_base

    url = f"{_public_base(deployment)}/api/registry/reservations?source=runtime"
    # Same trust rule as the live verify checks: only a letsencrypt install
    # reliably presents a certificate this machine trusts.
    verify_tls = deployment.spec.network.tls == "letsencrypt"
    try:
        with httpx.Client(
            transport=transport, verify=verify_tls, timeout=timeout, follow_redirects=False
        ) as client:
            resp = client.get(url, headers={"X-API-Key": admin_key})
    except httpx.HTTPError as e:
        return RuntimeReservationCheck(
            note=f"the Registry is not reachable ({type(e).__name__}); runtime "
            "reservations not checked — the Registry refuses a colliding prefix itself"
        )
    if resp.status_code == 404:
        return RuntimeReservationCheck(
            note="the running Registry predates runtime reservations; not checked"
        )
    if resp.status_code != 200 or not str(resp.headers.get("content-type", "")).startswith(
        "application/json"
    ):
        return RuntimeReservationCheck(
            note=f"the Registry answered {resp.status_code} for its runtime reservations; "
            "not checked — the Registry refuses a colliding prefix itself"
        )
    return RuntimeReservationCheck(errors=collisions(file_reservations, resp.json()))

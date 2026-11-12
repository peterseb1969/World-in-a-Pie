"""What an apply's reservations would do to the Registry's runtime ones.

A platform admin can reserve namespaces through the Registry's API, for an
app wip-deploy does not manage (a runtime API key with `reserve`). The
install's own reservations come from its apps' manifests, and the Registry
imports them. One record holds each prefix, and the install's wins: a file
reservation of a prefix a runtime key holds replaces it in the Registry,
logged CRITICAL there, and the key keeps its grants. An apply that would do
that says so, naming the key, before rendering. It does not stop.

An ID prefix is different: one prefix names one ID sequence, and two
reservations of different namespaces claiming it cannot both be honoured,
whichever wins. An apply that would claim an ID prefix a runtime
reservation claims is refused, naming both.

When the Registry cannot be reached (a fresh install, a stopped stack) or
predates runtime reservations, the apply proceeds with a note.
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
    warnings: list[str] = field(default_factory=list)
    note: str | None = None


def replacements(
    file_reservations: list[dict[str, Any]], runtime_reservations: list[dict[str, Any]]
) -> list[str]:
    """The runtime reservations the install's would replace in the Registry."""
    runtime = {r["prefix"]: r for r in runtime_reservations}
    return [
        f"namespace {f['prefix']!r} is reserved for app {f['app']!r} and by runtime API "
        f"key {runtime[f['prefix']]['app']!r}; the Registry replaces the key's reservation "
        "with the app's (the key keeps its grants)"
        for f in file_reservations
        if f["prefix"] in runtime
    ]


def collisions(
    file_reservations: list[dict[str, Any]], runtime_reservations: list[dict[str, Any]]
) -> list[str]:
    """The ID prefixes the install's reservations would claim twice."""
    errors: list[str] = []
    runtime_ids: dict[str, str] = {}
    for r in runtime_reservations:
        for entity_type, prefix in _prefixed_ids(r.get("settings") or {}).items():
            runtime_ids[prefix] = f"{r['prefix']}.{entity_type} (runtime key {r['app']!r})"
    for f in file_reservations:
        for entity_type, prefix in sorted(_prefixed_ids(f.get("settings") or {}).items()):
            if prefix in runtime_ids:
                errors.append(
                    f"ID prefix {prefix!r} of {f['prefix']}.{entity_type} (app {f['app']!r}) "
                    f"is already reserved for {runtime_ids[prefix]}; each prefix names one "
                    "ID sequence on the install"
                )
    return errors


def registry_refusal(status: int, what: str) -> str | None:
    """The error for a Registry that is running and refuses or fails to answer
    a pre-apply check, or None.

    Not reachable, or a 404 from a Registry that predates the endpoint, leaves
    the check to the Registry itself at its next start (a first install has no
    Registry yet). A 401 or 403 means the install's admin key was refused, and
    a 5xx that the Registry failed: going ahead would skip the very check that
    keeps the apply from writing colliding entries, so the apply stops.
    """
    if status in (401, 403):
        return (
            f"the Registry refused the install's admin key ({status}) when asked for "
            f"{what}; the apply stops rather than skip the check. Check the key in the "
            "install's secret store."
        )
    if status >= 500:
        return (
            f"the Registry failed ({status}) when asked for {what}; the apply stops "
            "rather than skip the check. Retry once the Registry answers."
        )
    return None


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
            "reservations not checked"
        )
    if resp.status_code == 404:
        return RuntimeReservationCheck(
            note="the running Registry predates runtime reservations; not checked"
        )
    refused = registry_refusal(resp.status_code, "its runtime reservations")
    if refused:
        return RuntimeReservationCheck(errors=[refused])
    if resp.status_code != 200 or not str(resp.headers.get("content-type", "")).startswith(
        "application/json"
    ):
        return RuntimeReservationCheck(
            note=f"the Registry answered {resp.status_code} for its runtime reservations; "
            "not checked"
        )
    runtime = resp.json()
    return RuntimeReservationCheck(
        errors=collisions(file_reservations, runtime),
        warnings=replacements(file_reservations, runtime),
    )

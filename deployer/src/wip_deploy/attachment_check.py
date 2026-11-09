"""An apply may not give an app a route an app attachment already owns.

An attachment (an app put under the umbrella through the Registry's API)
owns its origin, `<name>.<hostname>`. The Registry refuses an attachment
named like the label of one of the install's own apps, from the attach
policy file the deployer renders. The other direction is checked here: an
apply that would add or enable an app whose label is an existing
attachment's name, active or not, is refused, naming the attachment.
Deactivate does not free a name: a revive would take the origin back.

When the Registry cannot be reached (a fresh install, a stopped stack) or
predates attachments, the apply proceeds with a note, as the runtime
reservation check does.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from wip_deploy.config_gen.attach_policy import app_route_names
from wip_deploy.spec import App, Deployment


@dataclass
class AttachmentCheck:
    errors: list[str] = field(default_factory=list)
    note: str | None = None


def collisions(route_names: list[str], attachments: list[dict[str, Any]]) -> list[str]:
    by_name = {a.get("name"): a for a in attachments}
    return [
        f"the origin {name}.<hostname> belongs to the app attachment {name!r} "
        f"({by_name[name].get('status', 'active')}, upstream {by_name[name].get('upstream')!r}); "
        "change the app's label or retire the attachment first"
        for name in route_names
        if name in by_name
    ]


def check_attachment_collisions(
    deployment: Deployment,
    apps: list[App],
    admin_key: str | None,
    *,
    transport: Any = None,
    timeout: float = 5.0,
) -> AttachmentCheck:
    names = app_route_names(deployment, apps)
    if not names:
        return AttachmentCheck()
    if not admin_key:
        return AttachmentCheck(note="no admin key to ask the Registry for app attachments; not checked")

    import httpx

    from wip_deploy.config_gen.spec_context import _public_base

    url = f"{_public_base(deployment)}/api/registry/attachments"
    verify_tls = deployment.spec.network.tls == "letsencrypt"
    attachments: list[dict[str, Any]] = []
    page = 1
    try:
        with httpx.Client(
            transport=transport, verify=verify_tls, timeout=timeout, follow_redirects=False
        ) as client:
            while True:
                resp = client.get(
                    url, params={"page": page, "page_size": 1000}, headers={"X-API-Key": admin_key}
                )
                if resp.status_code == 404:
                    return AttachmentCheck(
                        note="the running Registry predates app attachments; not checked"
                    )
                if resp.status_code != 200 or not str(
                    resp.headers.get("content-type", "")
                ).startswith("application/json"):
                    return AttachmentCheck(
                        note=f"the Registry answered {resp.status_code} for its app "
                        "attachments; not checked"
                    )
                body = resp.json()
                attachments.extend(body.get("items", []))
                if page >= int(body.get("pages") or 0):
                    break
                page += 1
    except httpx.HTTPError as e:
        return AttachmentCheck(
            note=f"the Registry is not reachable ({type(e).__name__}); app attachments not checked"
        )
    return AttachmentCheck(errors=collisions(names, attachments))

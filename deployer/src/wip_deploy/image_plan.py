"""Which image each service of an install runs, and why — and what a tag
change would move.

`wip-deploy images` prints this; a tag roll (`redeploy --tag`) uses it to
see which apps the new base tag would carry along. Read-only: it computes
from a Deployment and never touches the install.

A service's tag comes from the first precedence step that has one
(`config_gen/images.py`): a per-service pin, the deployment-wide tag, the
manifest pin, then `latest`. Fully-qualified (infra) images skip the
deployment-wide tag. On the dev target, components with a build context
and apps with a local source are built from source as `<name>:dev`; tags
do not reach them.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from wip_deploy.config_gen.images import TagSource, image_ref, resolve_tag
from wip_deploy.spec import Deployment
from wip_deploy.spec.activation import is_component_active
from wip_deploy.spec.app import App
from wip_deploy.spec.component import Component


@dataclass(frozen=True)
class ImageRow:
    name: str
    kind: str  # "component" | "app"
    image: str
    source: TagSource | str  # or "source" for a dev source build
    # Filled only when a change is previewed.
    new_image: str | None = None
    new_source: TagSource | str | None = None

    @property
    def moves(self) -> bool:
        return self.new_image is not None and self.new_image != self.image


def active_owners(
    deployment: Deployment, components: list[Component], apps: list[App]
) -> list[Component | App]:
    """The components and apps this install renders, in that order."""
    enabled_apps = {ref.name for ref in deployment.spec.apps if ref.enabled}
    owners: list[Component | App] = [
        c for c in components if is_component_active(c, deployment)
    ]
    owners += [a for a in apps if a.metadata.name in enabled_apps]
    return owners


def _built_from_source(owner: Component | App, deployment: Deployment) -> bool:
    """Mirrors renderers/dev_simple.py: on dev, components with a build
    context and apps with a registered local source are built locally."""
    if deployment.spec.target != "dev":
        return False
    if isinstance(owner, App):
        dev = deployment.spec.platform.dev
        return dev is not None and owner.metadata.name in dev.app_sources
    return owner.spec.image.build_context is not None


def _resolve(owner: Component | App, deployment: Deployment) -> tuple[str, str]:
    if _built_from_source(owner, deployment):
        return f"{owner.spec.image.name}:dev", "source"
    _, source = resolve_tag(owner, deployment)
    return image_ref(owner, deployment), source


def plan_images(
    deployment: Deployment,
    components: list[Component],
    apps: list[App],
    *,
    after: Deployment | None = None,
) -> list[ImageRow]:
    """One row per active owner. With `after` (the same install with a tag
    change applied), each row also carries what it would render at."""
    rows = []
    for owner in active_owners(deployment, components, apps):
        image, source = _resolve(owner, deployment)
        new_image = new_source = None
        if after is not None:
            new_image, new_source = _resolve(owner, after)
        rows.append(
            ImageRow(
                name=owner.metadata.name,
                kind="app" if isinstance(owner, App) else "component",
                image=image,
                source=source,
                new_image=new_image,
                new_source=new_source,
            )
        )
    return rows


def apps_carried_by_base_tag(rows: list[ImageRow]) -> list[ImageRow]:
    """Apps whose image would move only because the deployment-wide tag
    moved. Apps are built and tagged in their own repos, so the new base
    tag usually names an image that does not exist for them."""
    return [r for r in rows if r.kind == "app" and r.moves and r.new_source == "base"]


def pin_apps_carried_by_base_tag(
    before: Deployment,
    after: Deployment,
    components: list[Component],
    apps: list[App],
) -> list[tuple[str, str]]:
    """Pin, in `after`, every app its new base tag alone would move, to the
    tag it runs in `before`. Returns the (app, tag) pins it added.

    `redeploy --tag` applies this; `images --tag` previews it, so the
    preview shows exactly what the roll will do."""
    if after.spec.images.tag == before.spec.images.tag:
        return []
    rows = plan_images(before, components, apps, after=after)
    pinned = []
    for row in apps_carried_by_base_tag(rows):
        kept = row.image.rsplit(":", 1)[1]
        after.spec.images.tag_overrides[row.name] = kept
        pinned.append((row.name, kept))
    return pinned


_STAY_REASONS = {
    "pin": "stays (pinned)",
    "upstream": "stays (upstream pin)",
    "source": "stays (built from source)",
    "manifest": "stays (manifest pin)",
    "latest": "stays (latest)",
    "base": "stays",
}


def format_image_table(rows: list[ImageRow], *, preview: bool) -> str:
    headers = ["SERVICE", "KIND", "IMAGE", "SOURCE"]
    if preview:
        headers.append("AFTER CHANGE")
    table = [headers]
    for r in rows:
        line = [r.name, r.kind, r.image, r.source]
        if preview:
            line.append(
                f"→ {r.new_image}" if r.moves
                else _STAY_REASONS.get(str(r.new_source), "stays")
            )
        table.append(line)
    widths = [max(len(row[i]) for row in table) for i in range(len(headers))]
    lines = [
        "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)).rstrip()
        for row in table
    ]
    return "\n".join(lines)


def format_image_json(rows: list[ImageRow]) -> str:
    return json.dumps(
        [{**asdict(r), "moves": r.moves} for r in rows], indent=2
    )

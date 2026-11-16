"""Shared image-reference resolution (CASE-438).

Every renderer (compose, k8s, dev) emits container image references.
Putting the tag-precedence computation here — not inside each renderer —
guarantees the targets never disagree about which tag a service runs.

Precedence for WIP-built images (short name, no `/`):

  1. `spec.images.tag_overrides[name]` — per-service CLI override
     (`--image-tag NAME=TAG`), the hotfix path.
  2. For an app, its manifest pin (`spec.image.tag` in the app manifest).
     Apps are built and tagged in their own repos, so the platform tag
     rarely names an image of them; their pin is what they publish.
  3. `spec.images.tag` — the deployment-wide tag. When set it is
     authoritative over component manifest pins: `--tag X` means "every
     WIP-built platform image at X", and every app without a pin.
     `None` means no deployment-wide tag was specified.
  4. A component's manifest pin — a fallback default, not an override.
  5. `latest`.

Fully-qualified images (contain `/`: mongo, postgres, dex, nats, garage,
router) skip step 2 — their manifest pins are upstream version numbers
(`mongo:7`), and a WIP release tag like `--tag 20260610a` would never
resolve. An explicit `tag_overrides` entry still wins: the operator
named that service deliberately.
"""

from __future__ import annotations

from typing import Literal

from wip_deploy.spec import Deployment
from wip_deploy.spec.app import App
from wip_deploy.spec.component import Component

# Which precedence step decided a tag: a per-service pin, the
# deployment-wide tag, the manifest pin, the `latest` fallback, or an
# upstream version pin on a fully-qualified (infra) image.
TagSource = Literal["pin", "base", "manifest", "latest", "upstream"]


def resolve_tag(owner: Component | App, deployment: Deployment) -> tuple[str, TagSource]:
    """The tag an owner renders at, and the precedence step that chose it."""
    ref = owner.spec.image
    spec_images = deployment.spec.images
    override = spec_images.tag_overrides.get(owner.metadata.name)
    if override:
        return override, "pin"
    if "/" in ref.name:
        return (ref.tag, "upstream") if ref.tag else ("latest", "latest")
    if isinstance(owner, App) and ref.tag:
        return ref.tag, "manifest"
    if spec_images.tag:
        return spec_images.tag, "base"
    if ref.tag:
        return ref.tag, "manifest"
    return "latest", "latest"


def image_ref(owner: Component | App, deployment: Deployment) -> str:
    """Resolve the full image reference for a component or app.

    - Fully qualified (contains `/`): `{name}:{tag}` — override or
      manifest pin (required for infra pinning).
    - Short name + deployment registry set: `{registry}/{name}:{tag}`.
    - Short name + no registry: bare `{name}:{tag}` (assumed local
      build will produce this image).
    """
    ref = owner.spec.image
    tag, _ = resolve_tag(owner, deployment)
    if "/" in ref.name:
        return f"{ref.name}:{tag}"
    registry = deployment.spec.images.registry
    if registry:
        return f"{registry}/{ref.name}:{tag}"
    return f"{ref.name}:{tag}"

"""Shared image-reference resolution (CASE-438).

Every renderer (compose, k8s, dev) emits container image references.
Putting the tag-precedence computation here — not inside each renderer —
guarantees the targets never disagree about which tag a service runs.

Precedence for WIP-built images (short name, no `/`):

  1. `spec.images.tag_overrides[name]` — per-service CLI override
     (`--image-tag NAME=TAG`), the hotfix path.
  2. `spec.images.tag` — the deployment-wide tag. When set it is
     AUTHORITATIVE over manifest pins: `--tag X` means "every WIP-built
     image at X". `None` means no deployment-wide tag was specified.
  3. The manifest pin (`spec.image.tag` in the component/app manifest) —
     a fallback default, not an override.
  4. `latest`.

Fully-qualified images (contain `/`: mongo, postgres, dex, nats, minio,
router) skip step 2 — their manifest pins are upstream version numbers
(`mongo:7`), and a WIP release tag like `--tag 20260610a` would never
resolve. An explicit `tag_overrides` entry still wins: the operator
named that service deliberately.
"""

from __future__ import annotations

from wip_deploy.spec import Deployment
from wip_deploy.spec.app import App
from wip_deploy.spec.component import Component


def image_ref(owner: Component | App, deployment: Deployment) -> str:
    """Resolve the full image reference for a component or app.

    - Fully qualified (contains `/`): `{name}:{tag}` — override or
      manifest pin (required for infra pinning).
    - Short name + deployment registry set: `{registry}/{name}:{tag}`.
    - Short name + no registry: bare `{name}:{tag}` (assumed local
      build will produce this image).
    """
    ref = owner.spec.image
    spec_images = deployment.spec.images
    override = spec_images.tag_overrides.get(owner.metadata.name)

    if "/" in ref.name:
        tag = override or ref.tag or "latest"
        return f"{ref.name}:{tag}"

    tag = override or spec_images.tag or ref.tag or "latest"
    if spec_images.registry:
        return f"{spec_images.registry}/{ref.name}:{tag}"
    return f"{ref.name}:{tag}"

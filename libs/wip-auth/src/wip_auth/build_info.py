"""Build provenance for running services (CASE-526).

Every WIP image bakes its git SHA / build timestamp / image tag as env vars at
build time (``WIP_BUILD_SHA`` / ``WIP_BUILD_STAMP`` / ``WIP_IMAGE_TAG``). This
helper reads them into a uniform ``build`` block that every backend service
exposes on its status endpoint, so "which build is this container actually
running?" is answerable in one call (and RC Console can surface it).

In dev (bind-mounted source, no stamped build) the vars default to ``"dev"`` —
itself a useful signal that the container runs unbaked source, not a release
image.
"""

from __future__ import annotations

import os

DEV_SENTINEL = "dev"


def build_metadata(version: str) -> dict[str, str]:
    """Return the uniform build-provenance block for a service status payload.

    ``version`` is the service's package ``__version__`` (hand-bumped semver);
    ``sha`` / ``built_at`` / ``image_tag`` come from the build-time env, or the
    ``"dev"`` sentinel when the image was not stamped (local/dev builds).
    """
    return {
        "version": version,
        "sha": os.getenv("WIP_BUILD_SHA", DEV_SENTINEL),
        "built_at": os.getenv("WIP_BUILD_STAMP", DEV_SENTINEL),
        "image_tag": os.getenv("WIP_IMAGE_TAG", DEV_SENTINEL),
    }

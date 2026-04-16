"""Shared Pydantic base for all spec models.

`extra="forbid"` is deliberate: unknown keys in a manifest YAML should fail
fast rather than be silently dropped. Catches typos in `container_port`
vs `containerPort`, `healthcheck` vs `health_check`, etc.
"""

from pydantic import BaseModel, ConfigDict


class WIPModel(BaseModel):
    """Base class for all spec/manifest Pydantic models."""

    model_config = ConfigDict(extra="forbid", frozen=False, validate_assignment=True)

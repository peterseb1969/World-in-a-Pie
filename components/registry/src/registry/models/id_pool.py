"""ID Pool model for the Registry service.

ID Pools are internal constructs for ID generation. Users interact with
Namespaces (e.g., "wip", "dev"); ID Pools are auto-created for each entity
type within a namespace (e.g., "wip-terminologies", "wip-terms").
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from beanie import Document
from pydantic import BaseModel, Field
from pymongo import IndexModel


class IdGeneratorType(str, Enum):
    """Supported ID generation strategies."""
    UUID4 = "uuid4"
    UUID7 = "uuid7"
    NANOID = "nanoid"
    PREFIXED = "prefixed"
    EXTERNAL = "external"
    CUSTOM = "custom"


class IdGeneratorConfig(BaseModel):
    """Configuration for ID generation within an ID pool."""

    model_config = {"use_enum_values": True}

    type: IdGeneratorType = Field(
        default=IdGeneratorType.UUID4,
        description="ID generation strategy"
    )
    prefix: Optional[str] = Field(
        None,
        description="Prefix for prefixed generator (e.g., 'TERM-', 'TPL-')"
    )
    length: int = Field(
        default=21,
        description="Length for nanoid generator"
    )
    pattern: Optional[str] = Field(
        None,
        description="Pattern for custom generator"
    )


class IdPool(Document):
    """
    An ID generation pool in the Registry.

    ID Pools are internal - users don't interact with them directly.
    They work with Namespaces, and ID Pools are auto-created when
    a Namespace is created.

    Each Namespace creates 5 ID Pools:
    - {namespace}-terminologies
    - {namespace}-terms
    - {namespace}-templates
    - {namespace}-documents
    - {namespace}-files
    """

    pool_id: str = Field(
        ...,
        description="Unique ID pool identifier (e.g., 'wip-terminologies')"
    )
    name: str = Field(
        ...,
        description="Human-readable name"
    )
    description: Optional[str] = Field(
        None,
        description="Purpose of this ID pool"
    )
    id_generator: IdGeneratorConfig = Field(
        default_factory=IdGeneratorConfig,
        description="ID generation configuration for this pool"
    )
    source_endpoint: Optional[str] = Field(
        None,
        description="API endpoint for external ID pools"
    )
    api_key_hash: Optional[str] = Field(
        None,
        description="Hashed API key for this ID pool"
    )
    status: str = Field(
        default="active",
        description="Status: active or inactive"
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional metadata"
    )

    class Settings:
        name = "id_pools"
        indexes = [
            IndexModel([("pool_id", 1)], unique=True, name="pool_id_unique_idx"),
            IndexModel([("status", 1)], name="pool_status_idx"),
        ]


# Pre-configured WIP ID pools (created when "wip" namespace is initialized)
WIP_ID_POOLS = {
    "default": {
        "name": "Default Pool",
        "description": "Default ID pool for general use",
        "id_generator": IdGeneratorConfig(type=IdGeneratorType.UUID4),
    },
    "wip-terminologies": {
        "name": "WIP Terminologies",
        "description": "ID pool for terminology IDs",
        "id_generator": IdGeneratorConfig(type=IdGeneratorType.PREFIXED, prefix="TERM-"),
    },
    "wip-terms": {
        "name": "WIP Terms",
        "description": "ID pool for individual term IDs",
        "id_generator": IdGeneratorConfig(type=IdGeneratorType.PREFIXED, prefix="T-"),
    },
    "wip-templates": {
        "name": "WIP Templates",
        "description": "ID pool for template IDs",
        "id_generator": IdGeneratorConfig(type=IdGeneratorType.PREFIXED, prefix="TPL-"),
    },
    "wip-documents": {
        "name": "WIP Documents",
        "description": "ID pool for document IDs",
        "id_generator": IdGeneratorConfig(type=IdGeneratorType.UUID7),
    },
    "wip-files": {
        "name": "WIP Files",
        "description": "ID pool for file attachment IDs",
        "id_generator": IdGeneratorConfig(type=IdGeneratorType.PREFIXED, prefix="FILE-"),
    },
}

"""Namespace model for the Registry service."""

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
    """Configuration for ID generation within a namespace."""

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


class Namespace(Document):
    """A logical partition in the Registry for ID isolation."""

    namespace_id: str = Field(
        ...,
        description="Unique namespace identifier"
    )
    name: str = Field(
        ...,
        description="Human-readable name"
    )
    description: Optional[str] = Field(
        None,
        description="Purpose of this namespace"
    )
    id_generator: IdGeneratorConfig = Field(
        default_factory=IdGeneratorConfig,
        description="ID generation configuration for this namespace"
    )
    source_endpoint: Optional[str] = Field(
        None,
        description="API endpoint for external namespaces"
    )
    api_key_hash: Optional[str] = Field(
        None,
        description="Hashed API key for this namespace"
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
        name = "namespaces"
        indexes = [
            IndexModel([("namespace_id", 1)], unique=True, name="namespace_id_unique_idx"),
            IndexModel([("status", 1)], name="namespace_status_idx"),
        ]


# Pre-configured WIP internal namespaces
WIP_INTERNAL_NAMESPACES = {
    "default": {
        "name": "Default Namespace",
        "description": "Default namespace for general use",
        "id_generator": IdGeneratorConfig(type=IdGeneratorType.UUID4),
    },
    "wip-terminologies": {
        "name": "WIP Terminologies",
        "description": "Namespace for terminology IDs",
        "id_generator": IdGeneratorConfig(type=IdGeneratorType.PREFIXED, prefix="TERM-"),
    },
    "wip-terms": {
        "name": "WIP Terms",
        "description": "Namespace for individual term IDs",
        "id_generator": IdGeneratorConfig(type=IdGeneratorType.PREFIXED, prefix="T-"),
    },
    "wip-templates": {
        "name": "WIP Templates",
        "description": "Namespace for template IDs",
        "id_generator": IdGeneratorConfig(type=IdGeneratorType.PREFIXED, prefix="TPL-"),
    },
    "wip-documents": {
        "name": "WIP Documents",
        "description": "Namespace for document IDs",
        "id_generator": IdGeneratorConfig(type=IdGeneratorType.UUID7),
    },
    "wip-files": {
        "name": "WIP Files",
        "description": "Namespace for file attachment IDs",
        "id_generator": IdGeneratorConfig(type=IdGeneratorType.PREFIXED, prefix="FILE-"),
    },
}

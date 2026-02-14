"""Namespace model for user-facing namespace management.

A Namespace (e.g., "wip", "dev", "prod") is a user-facing container for
organizing data. Each namespace has an ID algorithm configuration that
defines how IDs are generated for each entity type.
"""

from datetime import datetime, timezone
from typing import Any, Literal, Optional

from beanie import Document
from pydantic import Field
from pymongo import IndexModel

from .id_algorithm import IdAlgorithmConfig, DEFAULT_ID_CONFIG


class Namespace(Document):
    """
    A user-facing namespace for organizing data.

    Users work with Namespaces (e.g., "wip", "dev", "prod"). Each Namespace
    has configurable ID generation per entity type (terminologies, terms,
    templates, documents, files).
    """

    prefix: str = Field(
        ...,
        description="Unique prefix for this namespace (e.g., 'wip', 'dev', 'customer-abc')"
    )
    description: str = Field(
        default="",
        description="Human-readable description of this namespace"
    )
    isolation_mode: Literal["open", "strict"] = Field(
        default="open",
        description="'open' allows cross-namespace refs; 'strict' requires same-namespace only"
    )
    allowed_external_refs: list[str] = Field(
        default_factory=list,
        description="For open mode, optional allowlist of external namespace prefixes"
    )
    id_config: dict[str, Any] = Field(
        default_factory=dict,
        description="Per-entity-type ID algorithm config. Keys: terminologies, terms, templates, documents, files"
    )
    status: Literal["active", "archived", "deleted"] = Field(
        default="active",
        description="Namespace status"
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    created_by: Optional[str] = Field(
        None,
        description="User who created this namespace"
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    updated_by: Optional[str] = Field(
        None,
        description="User who last updated this namespace"
    )

    def get_id_algorithm(self, entity_type: str) -> IdAlgorithmConfig:
        """Get the ID algorithm config for a given entity type."""
        if entity_type in self.id_config:
            cfg = self.id_config[entity_type]
            if isinstance(cfg, dict):
                return IdAlgorithmConfig(**cfg)
            return cfg
        return DEFAULT_ID_CONFIG.get(entity_type, IdAlgorithmConfig(algorithm="uuid7"))

    class Settings:
        name = "namespaces"
        indexes = [
            IndexModel([("prefix", 1)], unique=True, name="prefix_unique_idx"),
            IndexModel([("status", 1)], name="status_idx"),
        ]

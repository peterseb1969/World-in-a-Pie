"""Namespace model for user-facing namespace management.

A Namespace (e.g., "wip", "dev", "prod") is a user-facing container for
organizing data. Each namespace has an ID algorithm configuration that
defines how IDs are generated for each entity type.
"""

import logging
from datetime import UTC, datetime
from typing import Any, ClassVar, Literal

from beanie import Document
from pydantic import Field
from pymongo import IndexModel

from .id_algorithm import DEFAULT_ID_CONFIG, VALID_ENTITY_TYPES, IdAlgorithmConfig

# The unique index that makes a `prefixed` prefix name one ID sequence
# install-wide (see services/id_prefixes.py).
ID_PREFIXES_INDEX = "id_prefixes_unique_idx"


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
    id_prefixes: list[str] = Field(
        default_factory=list,
        description=(
            "Derived from id_config: the prefixes this namespace's 'prefixed' "
            "entity types mint with. Unique across namespaces, so two "
            "namespaces cannot mint the same prefixed IDs. Written by the "
            "namespace write paths, never by callers."
        ),
    )
    deletion_mode: Literal["retain", "full"] = Field(
        default="retain",
        description="'retain' = soft-delete only; 'full' = allows hard-delete and namespace deletion"
    )
    # 'deleted' is a legacy soft-delete marker with no remaining write path:
    # journal-based namespace deletion (2026-03-27) physically removes the
    # document instead of setting a status. The value stays in the Literal so
    # documents soft-deleted before that cutover — and backups containing
    # them — still hydrate; narrowing the enum would 500 every read that
    # materializes such a record. Lifecycle today: active <-> archived
    # (archive/restore), -> locked (deletion in progress) -> physical removal.
    status: Literal["active", "archived", "deleted", "locked"] = Field(
        default="active",
        description=(
            "Namespace status. 'locked' means deletion is in progress. "
            "'deleted' is a legacy soft-delete marker — no current write "
            "path sets it; retained so pre-cutover records still load."
        )
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC)
    )
    created_by: str | None = Field(
        default=None,
        description="User who created this namespace"
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC)
    )
    updated_by: str | None = Field(
        default=None,
        description="User who last updated this namespace"
    )

    def get_id_algorithm(self, entity_type: str) -> IdAlgorithmConfig:
        """Get the ID algorithm config for a given entity type."""
        if entity_type in self.id_config:
            cfg = self.id_config[entity_type]
            if isinstance(cfg, dict):
                return IdAlgorithmConfig(**cfg)
            if isinstance(cfg, IdAlgorithmConfig):
                return cfg
            # Stored before writes were validated: name it instead of
            # failing later on an attribute of a str.
            raise ValueError(
                f"namespace '{self.prefix}' id_config['{entity_type}'] is a "
                f"{type(cfg).__name__}, not an object"
            )
        return DEFAULT_ID_CONFIG.get(entity_type, IdAlgorithmConfig(algorithm="uuid7"))

    class Settings:
        name = "namespaces"
        indexes: ClassVar[list[IndexModel]] = [
            IndexModel([("prefix", 1)], unique=True, name="prefix_unique_idx"),
            IndexModel([("status", 1)], name="status_idx"),
            # Multikey: unique across namespaces, not within one (a namespace
            # whose entity types share a prefix is refused before the write).
            # Only string elements are indexed, so the empty default and
            # pre-existing documents without the field never conflict.
            IndexModel(
                [("id_prefixes", 1)],
                unique=True,
                name=ID_PREFIXES_INDEX,
                partialFilterExpression={"id_prefixes": {"$type": "string"}},
            ),
        ]


_view_logger = logging.getLogger("registry.namespaces")


def id_config_view(ns: Namespace) -> dict[str, Any]:
    """The namespace's id_config per entity type, defaults merged in.

    A stored entry that cannot be read (written before namespace writes
    were validated) is served as {"invalid": <reason>} rather than raised:
    one bad namespace must not fail every namespace listing on the install.
    Minting for that entity type still fails, with the same reason.
    """
    id_config: dict[str, Any] = {}
    for entity_type in sorted(VALID_ENTITY_TYPES):
        try:
            id_config[entity_type] = ns.get_id_algorithm(entity_type).model_dump()
        except ValueError as e:
            _view_logger.warning("namespace %s: unreadable id_config[%s]: %s", ns.prefix, entity_type, e)
            id_config[entity_type] = {"invalid": str(e)}
    return id_config

"""Template model for the Template Store service."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, ClassVar

from beanie import Document
from pydantic import BaseModel, Field, field_validator
from pymongo import IndexModel

from .field import FieldDefinition
from .rule import ValidationRule


class TemplateUsage(StrEnum):
    """How a template's documents are intended to be used.

    - entity (default): full document lifecycle, the v1.x behaviour.
    - reference: lightweight controlled-vocabulary documents (LOV).
      Reserved for a future phase; currently behaves like entity.
    - relationship: typed, property-carrying edge between two
      documents. Requires source_templates / target_templates to be set
      and a source_ref / target_ref reference field on the template.
      See docs/design/document-relationships.md.
    """
    ENTITY = "entity"
    REFERENCE = "reference"
    RELATIONSHIP = "relationship"


class EndpointRef(BaseModel):
    """One declared endpoint of an edge type — a reference with both halves.

    A reference names an identity two ways at once, and both halves are
    load-bearing across a fresh restore:

    - ``lookup_value`` — the caller's anchor, kept verbatim (a template value,
      ``ns:VALUE``, or a canonical id). This is the synonym half: it names the
      identity rather than a specific minting of it, so it survives
      re-anchoring and is the only resolvable handle for endpoints that are
      not inside an archive being restored.
    - ``resolved`` — the canonical template_id this lookup resolved to, filled
      by the platform at write time (never by the caller) and rewritten
      through the id mapping table on a fresh restore. ``None`` only for
      drafts awaiting activation, rows written before this field existed, and
      restored declarations whose endpoint does not exist on the target
      (recorded with a job warning; a null half fails closed wherever an
      exact id is required).

    Documents' ``references[]`` store the same two halves for the same
    reasons; this makes the edge-type declaration follow the platform's one
    reference pattern instead of a single string whose form every consumer
    had to guess.
    """

    lookup_value: str = Field(
        description="The endpoint reference as submitted — value, ns:VALUE, or id; kept verbatim"
    )
    resolved: str | None = Field(
        default=None,
        description="Canonical template_id (server-owned; null when not yet / no longer resolvable)"
    )

    @classmethod
    def coerce(cls, item: "EndpointRef | dict | str") -> "EndpointRef":
        """Accept the legacy single-string form as a lookup-only entry.

        Rows and archives written before the two-half shape hold bare
        strings; they hydrate as ``{lookup_value: s, resolved: None}`` and
        heal at the next write, activation, or restore-door repair.
        """
        if isinstance(item, EndpointRef):
            return item
        if isinstance(item, str):
            return cls(lookup_value=item)
        if isinstance(item, BaseModel):
            return cls.model_validate(item.model_dump())
        return cls.model_validate(item)


class ReportingConfig(BaseModel):
    """Configuration for reporting/analytics sync to PostgreSQL."""

    sync_enabled: bool = Field(
        default=True,
        description="Whether to sync documents of this template to PostgreSQL"
    )
    sync_strategy: str = Field(
        default="latest_only",
        description="Sync strategy: 'latest_only' (upsert) or 'all_versions' (insert all)"
    )
    table_name: str | None = Field(
        default=None,
        description="Custom PostgreSQL table name (auto-generated from value if not set)"
    )
    include_metadata: bool = Field(
        default=True,
        description="Include created_at, created_by, etc. columns"
    )
    flatten_arrays: bool = Field(
        default=True,
        description="Flatten arrays into multiple rows (cross-product)"
    )
    max_array_elements: int = Field(
        default=10,
        ge=1,
        le=100,
        description="Maximum array elements to include when flattening"
    )
    cross_version_view: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Opt-in cross-version entity view over the per-version reporting "
            "tables: {'versions': 'all' | [ints], 'columns': {target: "
            "{'from': source} | {}}}. The identity core is always included; "
            "declared column mappings extend it. Consumed by reporting-sync "
            "(which validates the shape); stored pass-through here."
        )
    )


class TemplateMetadata(BaseModel):
    """Additional metadata for a template."""

    domain: str | None = Field(
        default=None,
        description="Business domain (e.g., 'hr', 'finance', 'healthcare')"
    )
    category: str | None = Field(
        default=None,
        description="Template category (e.g., 'master_data', 'transaction')"
    )
    tags: list[str] = Field(
        default_factory=list,
        description="Tags for categorization and search"
    )
    custom: dict[str, Any] = Field(
        default_factory=dict,
        description="Custom metadata fields"
    )


class Template(Document):
    """
    A schema definition for documents.

    Templates define the structure, validation rules, and constraints
    that documents must conform to. They support inheritance, allowing
    child templates to extend parent templates.

    Examples:
    - Person template: name, birth_date, national_id fields
    - Employee template (extends Person): adds employee_id, department
    - Address template: street, city, postal_code, country
    """

    # Namespace for multi-tenant isolation
    namespace: str = Field(
        ...,
        description="Namespace for data isolation (e.g., wip, dev, seed)"
    )

    # Identity (from Registry)
    template_id: str = Field(
        ...,
        description="Unique ID from Registry (UUID by default)"
    )

    # Human-friendly identifier (mutable)
    value: str = Field(
        ...,
        description="Human-readable value (e.g., 'PERSON'). Must be unique within namespace."
    )

    # Display information
    label: str = Field(
        ...,
        description="Display label (e.g., 'Person Template')"
    )
    description: str | None = Field(
        default=None,
        description="Detailed description of the template's purpose"
    )

    # Versioning
    version: int = Field(
        default=1,
        description="Version number, incremented on updates"
    )

    # Inheritance
    extends: str | None = Field(
        default=None,
        description="Parent template ID for inheritance"
    )
    extends_version: int | None = Field(
        default=None,
        description="Pinned parent version. Required (non-null) whenever the template declares 'extends' — a null there is rejected; the parent is validated against this exact pinned version, never 'latest'. Null only when there is no parent."
    )

    # Identity fields for document upsert
    identity_fields: list[str] = Field(
        default_factory=list,
        description="Fields that form the composite identity key for documents"
    )

    # Peer-projection fields (CASE-343). Names of fields the platform
    # should include when projecting this template's documents in compact
    # "header" contexts: relationships endpoint's `?include=peers`,
    # registry-list summaries, etc. Bare names target `data.<name>`;
    # `metadata.custom.<name>` paths are allowed for app-defined audit
    # fields. Empty list / None → projection falls back to identity_fields
    # at read time (template authors who want richer projection declare
    # explicit header_fields; identity-only templates work zero-config).
    header_fields: list[str] = Field(
        default_factory=list,
        description=(
            "Fields to include in peer/header projections. "
            "Bare names → data.<name>; metadata.custom.<name> paths "
            "allowed. Empty → projection falls back to identity_fields."
        )
    )

    # Declared renames for THIS version relative to the previous one:
    # {new_field: old_field}. A rename declaration means "mechanically the
    # same data under a new key" — it makes the rename losslessly
    # auto-migratable (migrate re-keys before target validation) and
    # matview-mappable, where an undeclared rename is indistinguishable
    # from drop+add. Identity fields can never be renamed (identity is
    # immutable across versions). None/empty on version 1 by definition.
    renames: dict[str, str] | None = Field(
        default=None,
        description=(
            "Field renames relative to the previous version, as "
            "{new_field: old_field}. Enables lossless migration of renamed "
            "fields; identity fields cannot appear."
        )
    )

    # Usage annotation — controls validation, query APIs, and reporting
    # shape. Default 'entity' = v1.x behaviour. 'relationship' enables
    # the document-relationship feature (requires source_templates,
    # target_templates, and source_ref/target_ref reference fields).
    # Immutable after creation.
    usage: TemplateUsage = Field(
        default=TemplateUsage.ENTITY,
        description="Usage class: entity (default), reference, or relationship"
    )

    # Relationship templates only — the single declaration of which
    # templates may sit at each end of an edge, stored as two-half
    # references (see EndpointRef): the submitted anchor verbatim plus the
    # server-resolved canonical id. Rows written before the two-half shape
    # hold bare strings and are coerced to lookup-only entries on read.
    source_templates: list[EndpointRef] = Field(
        default_factory=list,
        description="Declared edge-source endpoints (relationship only)"
    )

    # Relationship templates only — declared edge-target endpoints.
    # See source_templates above.
    target_templates: list[EndpointRef] = Field(
        default_factory=list,
        description="Declared edge-target endpoints (relationship only)"
    )

    @field_validator("source_templates", "target_templates", mode="before")
    @classmethod
    def _coerce_endpoint_entries(cls, v: Any) -> Any:
        # Coercion for stored history, not a write-time guard (write-time
        # rules live at the service's write seam): legacy rows hold bare
        # strings, which hydrate as lookup-only entries.
        if isinstance(v, list):
            return [EndpointRef.coerce(item) for item in v]
        return v

    # Whether updates create new versions (true) or overwrite in place
    # (false). Default true matches v1.x behaviour. Immutable after
    # creation — flipping mid-life would silently reshape an existing
    # template's lifecycle.
    versioned: bool = Field(
        default=True,
        description="True = updates create new versions; False = overwrite in place. Immutable after creation."
    )

    # Schema definition
    fields: list[FieldDefinition] = Field(
        default_factory=list,
        description="Field definitions"
    )

    # Cross-field validation rules
    rules: list[ValidationRule] = Field(
        default_factory=list,
        description="Cross-field validation rules"
    )

    # Metadata
    metadata: TemplateMetadata = Field(
        default_factory=TemplateMetadata,
        description="Additional metadata"
    )

    # Reporting configuration
    reporting: ReportingConfig | None = Field(
        default=None,
        description="Configuration for PostgreSQL reporting sync"
    )

    # Lifecycle
    status: str = Field(
        default="active",
        description="Status: draft, active, inactive"
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC)
    )
    created_by: str | None = Field(
        default=None,
        description="User or system that created this template"
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC)
    )
    updated_by: str | None = Field(
        default=None,
        description="User or system that last updated this template"
    )

    class Settings:
        name = "templates"
        indexes: ClassVar[list[IndexModel]] = [
            # Unique (template_id, version) within namespace — stable ID across versions
            IndexModel([("namespace", 1), ("template_id", 1), ("version", 1)], unique=True, name="ns_template_id_version_unique_idx"),
            # Unique value+version within namespace
            IndexModel([("namespace", 1), ("value", 1), ("version", 1)], unique=True, name="ns_value_version_unique_idx"),
            # Value lookup within namespace
            IndexModel([("namespace", 1), ("value", 1)], name="ns_value_idx"),
            # Status filter within namespace
            IndexModel([("namespace", 1), ("status", 1)], name="ns_status_idx"),
            # Extends lookup within namespace
            IndexModel([("namespace", 1), ("extends", 1)], name="ns_extends_idx"),
            # template_id lookup (non-unique, for finding all versions)
            IndexModel([("template_id", 1)], name="template_id_idx"),
            # Text search (global)
            IndexModel([("label", "text"), ("description", "text")], name="text_search_idx"),
        ]

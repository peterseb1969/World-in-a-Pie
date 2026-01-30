"""Template model for the Template Store service."""

from datetime import datetime, timezone
from typing import Any, Optional

from beanie import Document
from pydantic import BaseModel, Field
from pymongo import IndexModel

from .field import FieldDefinition
from .rule import ValidationRule


class TemplateMetadata(BaseModel):
    """Additional metadata for a template."""

    domain: Optional[str] = Field(
        None,
        description="Business domain (e.g., 'hr', 'finance', 'healthcare')"
    )
    category: Optional[str] = Field(
        None,
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

    # Identity (from Registry)
    template_id: str = Field(
        ...,
        description="Unique ID from Registry (e.g., TPL-000001)"
    )

    # Human-friendly identifier (mutable)
    code: str = Field(
        ...,
        description="Human-readable code (e.g., 'PERSON'). Must be unique."
    )

    # Display information
    name: str = Field(
        ...,
        description="Display name (e.g., 'Person Template')"
    )
    description: Optional[str] = Field(
        None,
        description="Detailed description of the template's purpose"
    )

    # Versioning
    version: int = Field(
        default=1,
        description="Version number, incremented on updates"
    )

    # Inheritance
    extends: Optional[str] = Field(
        None,
        description="Parent template ID for inheritance"
    )

    # Identity fields for document upsert
    identity_fields: list[str] = Field(
        default_factory=list,
        description="Fields that form the composite identity key for documents"
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

    # Lifecycle
    status: str = Field(
        default="active",
        description="Status: active, deprecated, inactive"
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    created_by: Optional[str] = Field(
        None,
        description="User or system that created this template"
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    updated_by: Optional[str] = Field(
        None,
        description="User or system that last updated this template"
    )

    class Settings:
        name = "templates"
        indexes = [
            IndexModel([("template_id", 1)], unique=True, name="template_id_unique_idx"),
            # Changed from unique on code alone to (code, version) to support multi-version
            IndexModel([("code", 1), ("version", 1)], unique=True, name="code_version_unique_idx"),
            IndexModel([("code", 1)], name="code_idx"),  # For listing versions by code
            IndexModel([("status", 1)], name="status_idx"),
            IndexModel([("extends", 1)], name="extends_idx"),
            IndexModel([("name", "text"), ("description", "text")], name="text_search_idx"),
        ]

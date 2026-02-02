"""
Schema Manager - Creates and manages PostgreSQL tables from template definitions.

Responsibilities:
- Generate CREATE TABLE DDL from template field definitions
- Handle schema evolution (ALTER TABLE for new fields)
- Track migrations in _wip_schema_migrations table
"""

import logging
from datetime import datetime
from typing import Any

import asyncpg

from .models import FieldType, FileFieldConfig, ReportingConfig, TemplateField

logger = logging.getLogger(__name__)


# Map WIP field types to PostgreSQL types
TYPE_MAPPING: dict[FieldType, str] = {
    FieldType.STRING: "TEXT",
    FieldType.NUMBER: "NUMERIC",
    FieldType.INTEGER: "INTEGER",
    FieldType.BOOLEAN: "BOOLEAN",
    FieldType.DATE: "DATE",
    FieldType.DATETIME: "TIMESTAMP WITH TIME ZONE",
    FieldType.TERM: "TEXT",  # Store the value; term_id stored separately
    FieldType.REFERENCE: "TEXT",  # Store the reference ID
    FieldType.FILE: "TEXT",  # Store the file_id; additional columns for metadata
    FieldType.OBJECT: "JSONB",  # Fallback if not flattened
    FieldType.ARRAY: "JSONB",  # Fallback if not flattened
}


class SchemaManager:
    """Manages PostgreSQL schema for reporting tables."""

    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    def get_table_name(self, template_code: str, config: ReportingConfig | None = None) -> str:
        """Get the PostgreSQL table name for a template."""
        if config and config.table_name:
            return config.table_name
        return f"doc_{template_code.lower()}"

    def _generate_column_ddl(
        self,
        field: TemplateField,
        prefix: str = "",
        config: ReportingConfig | None = None,
    ) -> list[tuple[str, str]]:
        """
        Generate column definitions for a field.

        Returns list of (column_name, column_type) tuples.
        For term fields, generates both value and term_id columns.
        For file fields, generates file_id, filename, and content_type columns (or JSONB for multiple).
        For nested objects, flattens with prefix.
        """
        columns = []
        col_name = f"{prefix}{field.name}" if prefix else field.name

        if field.type == FieldType.TERM:
            # Term fields get two columns: value and term_id
            columns.append((col_name, "TEXT"))
            columns.append((f"{col_name}_term_id", "TEXT"))

        elif field.type == FieldType.FILE:
            # File fields: check if multiple files allowed
            is_multiple = field.file_config and field.file_config.multiple
            if is_multiple:
                # Multiple files stored as JSONB array
                columns.append((col_name, "JSONB"))
            else:
                # Single file gets three columns: file_id, filename, content_type
                columns.append((f"{col_name}_file_id", "TEXT"))
                columns.append((f"{col_name}_filename", "TEXT"))
                columns.append((f"{col_name}_content_type", "TEXT"))

        elif field.type == FieldType.REFERENCE:
            # Reference fields store the referenced ID
            columns.append((col_name, "TEXT"))

        elif field.type == FieldType.OBJECT:
            # Objects are stored as JSONB (flattening handled by transformer)
            columns.append((col_name, "JSONB"))

        elif field.type == FieldType.ARRAY:
            # Arrays stored as JSONB if not flattened
            # Flattening creates multiple rows, handled by transformer
            columns.append((col_name, "JSONB"))

        else:
            # Simple types
            pg_type = TYPE_MAPPING.get(field.type, "TEXT")
            columns.append((col_name, pg_type))

        return columns

    # System column names that cannot be used for data fields
    SYSTEM_COLUMNS = {
        "document_id", "template_id", "template_version", "version",
        "status", "identity_hash", "created_at", "created_by",
        "updated_at", "updated_by", "data_json", "term_references_json",
    }

    def generate_create_table_ddl(
        self,
        template_code: str,
        template_version: int,
        fields: list[TemplateField],
        config: ReportingConfig | None = None,
    ) -> str:
        """Generate CREATE TABLE statement for a template."""
        table_name = self.get_table_name(template_code, config)
        include_metadata = config.include_metadata if config else True

        columns = []

        # System columns (always present)
        columns.extend([
            ("document_id", "TEXT PRIMARY KEY"),
            ("template_id", "TEXT NOT NULL"),
            ("template_version", "INTEGER NOT NULL"),
            ("version", "INTEGER NOT NULL"),
            ("status", "VARCHAR(20) NOT NULL"),
            ("identity_hash", "TEXT NOT NULL"),
        ])

        if include_metadata:
            columns.extend([
                ("created_at", "TIMESTAMP WITH TIME ZONE NOT NULL"),
                ("created_by", "TEXT"),
                ("updated_at", "TIMESTAMP WITH TIME ZONE"),
                ("updated_by", "TEXT"),
            ])

        # Data columns from template fields
        # Prefix with "data_" if field name conflicts with system columns
        for field in fields:
            field_columns = self._generate_column_ddl(field, config=config)
            # Check if the base field name conflicts
            needs_prefix = field.name in self.SYSTEM_COLUMNS
            for col_name, col_type in field_columns:
                if needs_prefix:
                    # Prefix all columns for this field (base and term_id)
                    col_name = f"data_{col_name}"
                columns.append((col_name, col_type))

        # JSON columns for full data (useful for complex queries)
        columns.extend([
            ("data_json", "JSONB"),
            ("term_references_json", "JSONB"),
            ("file_references_json", "JSONB"),
        ])

        # Build the DDL
        column_defs = ",\n    ".join(f'"{name}" {col_type}' for name, col_type in columns)

        ddl = f"""
CREATE TABLE IF NOT EXISTS "{table_name}" (
    {column_defs}
);

-- Indexes
CREATE INDEX IF NOT EXISTS "{table_name}_template_id_idx" ON "{table_name}"(template_id);
CREATE INDEX IF NOT EXISTS "{table_name}_status_idx" ON "{table_name}"(status);
CREATE INDEX IF NOT EXISTS "{table_name}_identity_hash_idx" ON "{table_name}"(identity_hash);
CREATE INDEX IF NOT EXISTS "{table_name}_created_at_idx" ON "{table_name}"(created_at);

-- Partial unique index for active documents by identity
CREATE UNIQUE INDEX IF NOT EXISTS "{table_name}_active_identity_idx"
ON "{table_name}"(identity_hash) WHERE status = 'active';
"""
        return ddl.strip()

    async def table_exists(self, table_name: str) -> bool:
        """Check if a table exists."""
        async with self.pool.acquire() as conn:
            return await conn.fetchval(
                """
                SELECT EXISTS (
                    SELECT FROM information_schema.tables
                    WHERE table_schema = 'public'
                    AND table_name = $1
                )
                """,
                table_name,
            )

    async def get_existing_columns(self, table_name: str) -> set[str]:
        """Get set of existing column names for a table."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = $1
                """,
                table_name,
            )
            return {row["column_name"] for row in rows}

    async def create_table(
        self,
        template_code: str,
        template_version: int,
        fields: list[TemplateField],
        config: ReportingConfig | None = None,
    ) -> str:
        """Create a table for a template."""
        ddl = self.generate_create_table_ddl(
            template_code, template_version, fields, config
        )
        table_name = self.get_table_name(template_code, config)

        logger.info(f"Creating table {table_name} for template {template_code}")

        async with self.pool.acquire() as conn:
            await conn.execute(ddl)

            # Record the migration
            await conn.execute(
                """
                INSERT INTO _wip_schema_migrations (template_code, template_version, migration_sql)
                VALUES ($1, $2, $3)
                ON CONFLICT (template_code, template_version) DO NOTHING
                """,
                template_code,
                template_version,
                ddl,
            )

        logger.info(f"Table {table_name} created successfully")
        return ddl

    async def update_table_schema(
        self,
        template_code: str,
        template_version: int,
        fields: list[TemplateField],
        config: ReportingConfig | None = None,
    ) -> list[str]:
        """
        Update table schema if template has new fields.

        Only adds new columns; never removes or modifies existing columns
        to preserve historical data.
        """
        table_name = self.get_table_name(template_code, config)

        if not await self.table_exists(table_name):
            await self.create_table(template_code, template_version, fields, config)
            return [f"Created table {table_name}"]

        existing_columns = await self.get_existing_columns(table_name)
        migrations = []

        async with self.pool.acquire() as conn:
            for field in fields:
                field_columns = self._generate_column_ddl(field, config=config)
                # Check if the base field name conflicts
                needs_prefix = field.name in self.SYSTEM_COLUMNS
                for col_name, col_type in field_columns:
                    if needs_prefix:
                        # Prefix all columns for this field (base and term_id)
                        col_name = f"data_{col_name}"
                    if col_name not in existing_columns:
                        # Remove PRIMARY KEY, NOT NULL constraints for ALTER TABLE ADD COLUMN
                        clean_type = col_type.replace(" PRIMARY KEY", "").replace(" NOT NULL", "")
                        alter_sql = f'ALTER TABLE "{table_name}" ADD COLUMN "{col_name}" {clean_type}'

                        logger.info(f"Adding column {col_name} to {table_name}")
                        await conn.execute(alter_sql)
                        migrations.append(alter_sql)

            if migrations:
                # Record the migration
                migration_sql = ";\n".join(migrations)
                await conn.execute(
                    """
                    INSERT INTO _wip_schema_migrations (template_code, template_version, migration_sql)
                    VALUES ($1, $2, $3)
                    ON CONFLICT (template_code, template_version) DO UPDATE
                    SET migration_sql = _wip_schema_migrations.migration_sql || E'\n' || $3,
                        applied_at = NOW()
                    """,
                    template_code,
                    template_version,
                    migration_sql,
                )

        return migrations

    async def ensure_table_for_template(
        self,
        template: dict[str, Any],
    ) -> str:
        """
        Ensure a table exists for a template, creating or updating as needed.

        Args:
            template: Full template definition from Template Store

        Returns:
            Table name
        """
        template_code = template["code"]
        template_version = template.get("version", 1)
        fields_data = template.get("fields", [])

        # Parse fields
        fields = []
        for f in fields_data:
            # Parse file_config if present
            file_config = None
            if f.get("file_config"):
                file_config = FileFieldConfig(**f["file_config"])
            array_file_config = None
            if f.get("array_file_config"):
                array_file_config = FileFieldConfig(**f["array_file_config"])

            fields.append(TemplateField(
                name=f["name"],
                label=f.get("label"),
                type=FieldType(f["type"]),
                mandatory=f.get("mandatory", False),
                terminology_ref=f.get("terminology_ref"),
                template_ref=f.get("template_ref"),
                array_item_type=FieldType(f["array_item_type"]) if f.get("array_item_type") else None,
                array_terminology_ref=f.get("array_terminology_ref"),
                array_template_ref=f.get("array_template_ref"),
                file_config=file_config,
                array_file_config=array_file_config,
            ))

        # Parse reporting config if present
        reporting_data = template.get("reporting", {})
        config = ReportingConfig(**reporting_data) if reporting_data else None

        # Check if sync is enabled
        if config and not config.sync_enabled:
            logger.info(f"Sync disabled for template {template_code}, skipping table creation")
            return ""

        table_name = self.get_table_name(template_code, config)

        if await self.table_exists(table_name):
            migrations = await self.update_table_schema(
                template_code, template_version, fields, config
            )
            if migrations:
                logger.info(f"Updated table {table_name} with {len(migrations)} new columns")
        else:
            await self.create_table(template_code, template_version, fields, config)

        return table_name

"""
Schema Manager - Creates and manages PostgreSQL tables from template definitions.

Responsibilities:
- Generate CREATE TABLE DDL from template field definitions
- Handle schema evolution (ALTER TABLE for new fields)
- Track migrations in _wip_schema_migrations table
"""

import logging
from typing import Any, ClassVar, cast

import asyncpg

from .models import FieldType, FileFieldConfig, ReportingConfig, SemanticType, SyncStrategy, TemplateField

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


# Map semantic types to PostgreSQL types (for simple types)
SEMANTIC_TYPE_MAPPING: dict[SemanticType, str] = {
    SemanticType.EMAIL: "TEXT",
    SemanticType.URL: "TEXT",
    SemanticType.LATITUDE: "NUMERIC(9,6)",  # Precision for 6 decimal places
    SemanticType.LONGITUDE: "NUMERIC(10,6)",  # +/- 180 needs 3 digits before decimal
    SemanticType.PERCENTAGE: "NUMERIC(6,3)",  # 0-100 with 3 decimal places
    # DURATION and GEO_POINT are complex types handled separately
}


class SchemaManager:
    """Manages PostgreSQL schema for reporting tables.

    Each WIP namespace maps to its own PostgreSQL schema (CASE-628). A
    namespace's reporting tables live inside its schema with natural,
    unqualified names — ``doc_<value>`` for documents and the fixed metadata
    tables (``terminologies``/``terms``/``templates``/``term_relations``) — so
    two namespaces that define the same template value get two distinct tables
    (``"clinicA".doc_patient`` vs ``"clinicB".doc_patient``) instead of
    silently sharing one. Namespace deletion is ``DROP SCHEMA CASCADE``.
    """

    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    @staticmethod
    def _safe_ident(name: str) -> str:
        """Validate a value destined for a quoted SQL identifier.

        PostgreSQL identifiers are interpolated into DDL by name (they cannot
        be bound as parameters), so a namespace prefix or template value that
        contained a double-quote could break out of the quoting. Namespaces and
        template values are otherwise unconstrained (prefixes legitimately
        contain hyphens, e.g. ``customer-abc``), so we quote rather than
        restrict — but reject the one class that quoting can't contain, and
        enforce Postgres's 63-byte identifier limit so a silent truncation
        can't collide two names.
        """
        if '"' in name or "\x00" in name:
            raise ValueError(f"Unsafe SQL identifier: {name!r}")
        if len(name.encode("utf-8")) > 63:
            raise ValueError(
                f"Identifier exceeds PostgreSQL's 63-byte limit: {name!r}"
            )
        return name

    def schema_for(self, namespace: str) -> str:
        """The PostgreSQL schema name for a WIP namespace (validated)."""
        return self._safe_ident(namespace)

    def get_table_name(self, template_value: str, config: ReportingConfig | None = None) -> str:
        """Get the bare (unqualified) table name for a template.

        This is the name *within* the namespace schema — ``doc_<value>`` by
        default, or the template's optional ``ReportingConfig.table_name``
        cosmetic override. Namespace isolation comes from the schema, not the
        table name, so this no longer carries the namespace.
        """
        if config and config.table_name:
            return self._safe_ident(config.table_name)
        return self._safe_ident(f"doc_{template_value.lower()}")

    def qualified_name(
        self,
        namespace: str,
        template_value: str,
        config: ReportingConfig | None = None,
    ) -> str:
        """Schema-qualified, quote-safe table reference for use in SQL.

        e.g. ``"clinicA"."doc_patient"``. Callers building SQL by hand should
        use this rather than constructing the name.
        """
        schema = self.schema_for(namespace)
        table = self.get_table_name(template_value, config)
        return f'"{schema}"."{table}"'

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
        For semantic types, generates appropriate columns with optimized PostgreSQL types.
        For nested objects, flattens with prefix.
        """
        columns = []
        col_name = f"{prefix}{field.name}" if prefix else field.name

        # Check for semantic types first - they may override base type handling
        if field.semantic_type:
            columns.extend(self._generate_semantic_columns(col_name, field.semantic_type))
            return columns

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

    def _generate_semantic_columns(
        self,
        col_name: str,
        semantic_type: SemanticType,
    ) -> list[tuple[str, str]]:
        """
        Generate columns for semantic types.

        Some semantic types need additional columns for optimized queries:
        - duration: JSONB + normalized seconds + unit term_id
        - geo_point: JSONB + separate lat/lon columns
        - Others: Just use the semantic type mapping
        """
        columns = []

        if semantic_type == SemanticType.DURATION:
            # Duration needs 3 columns for optimal querying
            columns.append((col_name, "JSONB"))  # Original {value, unit} object
            columns.append((f"{col_name}_seconds", "NUMERIC"))  # Normalized to seconds
            columns.append((f"{col_name}_unit_term_id", "TEXT"))  # Reference to time unit term

        elif semantic_type == SemanticType.GEO_POINT:
            # Geo point needs 3 columns for spatial queries
            columns.append((col_name, "JSONB"))  # Original {latitude, longitude} object
            columns.append((f"{col_name}_latitude", "NUMERIC(9,6)"))  # For spatial queries
            columns.append((f"{col_name}_longitude", "NUMERIC(10,6)"))  # For spatial queries

        else:
            # Simple semantic types use the mapped PostgreSQL type
            pg_type = SEMANTIC_TYPE_MAPPING.get(semantic_type, "TEXT")
            columns.append((col_name, pg_type))

        return columns

    # System column names that cannot be used for data fields
    SYSTEM_COLUMNS: ClassVar[set[str]] = {
        "document_id", "template_id", "template_version", "version",
        "status", "identity_hash", "created_at", "created_by",
        "updated_at", "updated_by", "data_json", "term_references_json",
    }

    @staticmethod
    def _full_text_columns(base_col: str) -> list[tuple[str, str]]:
        """Return the (search, tsv) column pair for a full-text-indexed field.

        Two-column shape:
          - <field>_search TEXT — written by the transformer with markdown
            stripped via reporting_sync.transformer._strip_md so query-time
            tokenisation operates on plain prose, not link/code-fence syntax.
          - <field>_tsv tsvector GENERATED ALWAYS AS (...) STORED — the
            query target. Computed automatically from <field>_search;
            never written directly. setweight('B') is the v1 default
            weight (matches the fireside design — uniform 'B' for now;
            per-field weighting comes when a real consumer asks).

        The GENERATED column means we never need to compute tsvector in
        the transformer — Postgres recomputes on every write to the
        _search column. GIN index on _tsv is added separately by the
        DDL generator.
        """
        return [
            (f"{base_col}_search", "TEXT"),
            (
                f"{base_col}_tsv",
                "tsvector GENERATED ALWAYS AS ("
                f'setweight(to_tsvector(\'english\', coalesce("{base_col}_search", \'\')), \'B\')'
                ") STORED",
            ),
        ]

    def generate_create_table_ddl(
        self,
        namespace: str,
        template_value: str,
        template_version: int,
        fields: list[TemplateField],
        config: ReportingConfig | None = None,
        usage: str = "entity",
        identity_fields: list[str] | None = None,
    ) -> str:
        """Generate CREATE TABLE statement for a template.

        For templates with usage='relationship', adds source_ref_id and
        target_ref_id columns + indexes so SQL JOINs against the endpoint
        document tables work cleanly. The columns are populated by the
        transformer from the Phase-6 enriched event payload
        (data.source_ref_resolved / data.target_ref_resolved).

        identity_fields: the template's declared identity_fields list. An
        empty list (or None) is the platform's first-class append-only
        declaration per PoNIF #3 — every doc gets identity_hash="" and
        the partial-unique index on (namespace, identity_hash) WHERE
        status='active' would collide on every doc beyond the first.
        Skip the index in that case; document_id remains the upsert key
        via the worker's ON CONFLICT (document_id), so version-bumps of
        the same doc still work.
        """
        table_name = self.get_table_name(template_value, config)
        qualified = self.qualified_name(namespace, template_value, config)
        include_metadata = config.include_metadata if config else True
        strategy = config.sync_strategy if config else SyncStrategy.LATEST_ONLY

        columns = []

        # System columns (always present)
        # For all_versions strategy, PK is composite (document_id, version)
        # so document_id column does not carry PRIMARY KEY inline
        if strategy == SyncStrategy.ALL_VERSIONS:
            columns.extend([
                ("document_id", "TEXT NOT NULL"),
                ("namespace", "VARCHAR(255) NOT NULL DEFAULT 'wip'"),
                ("template_id", "TEXT NOT NULL"),
                ("template_version", "INTEGER NOT NULL"),
                ("version", "INTEGER NOT NULL"),
                ("status", "VARCHAR(20) NOT NULL"),
                ("identity_hash", "TEXT NOT NULL"),
            ])
        else:
            columns.extend([
                ("document_id", "TEXT PRIMARY KEY"),
                ("namespace", "VARCHAR(255) NOT NULL DEFAULT 'wip'"),
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
        full_text_base_cols: list[str] = []
        for field in fields:
            field_columns = self._generate_column_ddl(field, config=config)
            # Check if the base field name conflicts
            needs_prefix = field.name in self.SYSTEM_COLUMNS
            for col_name, col_type in field_columns:
                if needs_prefix:
                    # Prefix all columns for this field (base and term_id)
                    col_name = f"data_{col_name}"
                columns.append((col_name, col_type))

            # Full-text-indexed string fields get the (search, tsv) pair.
            # Validator at template-creation enforces type=string, so the
            # check here is defensive — silently skip non-string fields
            # rather than emit broken DDL.
            if getattr(field, "full_text_indexed", None) and field.type == FieldType.STRING:
                base_col = f"data_{field.name}" if needs_prefix else field.name
                columns.extend(self._full_text_columns(base_col))
                full_text_base_cols.append(base_col)

        # JSON columns for full data (useful for complex queries)
        columns.extend([
            ("data_json", "JSONB"),
            ("term_references_json", "JSONB"),
            ("file_references_json", "JSONB"),
        ])

        # Phase 7 — relationship templates get explicit canonical-id columns
        # for the two endpoints. JOIN like:
        #   doc_experiment_input rel
        #     JOIN doc_experiment e ON e.identity_hash = rel.source_ref_id
        if usage == "relationship":
            columns.extend([
                ("source_ref_id", "TEXT"),
                ("target_ref_id", "TEXT"),
            ])

        # Build the DDL
        column_defs = ",\n    ".join(f'"{name}" {col_type}' for name, col_type in columns)

        # For all_versions strategy, add composite primary key constraint
        if strategy == SyncStrategy.ALL_VERSIONS:
            column_defs += ",\n    PRIMARY KEY (document_id, version)"

        # Table references are schema-qualified ({qualified}, e.g.
        # "clinicA"."doc_patient"); index NAMES stay bare ("{table_name}_...")
        # — Postgres places an index in its table's schema automatically, and
        # two namespace schemas may each hold an index of the same name.
        ddl = f"""
CREATE TABLE IF NOT EXISTS {qualified} (
    {column_defs}
);

-- Indexes
CREATE INDEX IF NOT EXISTS "{table_name}_namespace_idx" ON {qualified}(namespace);
CREATE INDEX IF NOT EXISTS "{table_name}_ns_template_id_idx" ON {qualified}(namespace, template_id);
CREATE INDEX IF NOT EXISTS "{table_name}_ns_status_idx" ON {qualified}(namespace, status);
CREATE INDEX IF NOT EXISTS "{table_name}_ns_identity_hash_idx" ON {qualified}(namespace, identity_hash);
CREATE INDEX IF NOT EXISTS "{table_name}_ns_created_at_idx" ON {qualified}(namespace, created_at);
"""

        # Partial unique index only applies to latest_only strategy
        # (all_versions tables store multiple versions per document_id).
        # Skip it for identity-less templates: identity_hash is "" for
        # every doc, so the partial-unique constraint on
        # (namespace, identity_hash) WHERE status='active' would admit
        # exactly one active doc per namespace and reject the rest with
        # UniqueViolationError. Empty identity_fields = append-only mode.
        if strategy != SyncStrategy.ALL_VERSIONS and identity_fields:
            ddl += f"""
-- Partial unique index for active documents by identity within namespace
CREATE UNIQUE INDEX IF NOT EXISTS "{table_name}_ns_active_identity_idx"
ON {qualified}(namespace, identity_hash) WHERE status = 'active';
"""

        if usage == "relationship":
            ddl += f"""
-- Relationship endpoint indexes (Phase 7) for SQL JOINs against doc_<endpoint>
CREATE INDEX IF NOT EXISTS "{table_name}_source_ref_id_idx" ON {qualified}(source_ref_id);
CREATE INDEX IF NOT EXISTS "{table_name}_target_ref_id_idx" ON {qualified}(target_ref_id);
"""

        # Full-text-search GIN indexes — one per indexed field. The
        # _tsv column is GENERATED ALWAYS, so the index stays current
        # automatically as documents are written.
        for base_col in full_text_base_cols:
            ddl += (
                f'\nCREATE INDEX IF NOT EXISTS "{table_name}_{base_col}_tsv_idx" '
                f'ON {qualified} USING GIN ("{base_col}_tsv");\n'
            )

        return ddl.strip()

    async def ensure_schema(self, namespace: str) -> str:
        """Create the namespace's PostgreSQL schema if absent. Returns its name."""
        schema = self.schema_for(namespace)
        async with self.pool.acquire() as conn:
            await conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
        return schema

    async def table_exists(self, schema: str, table_name: str) -> bool:
        """Check if a table exists in the given namespace schema."""
        async with self.pool.acquire() as conn:
            return cast(bool, await conn.fetchval(
                """
                SELECT EXISTS (
                    SELECT FROM information_schema.tables
                    WHERE table_schema = $1
                    AND table_name = $2
                )
                """,
                schema,
                table_name,
            ))

    async def get_existing_columns(self, schema: str, table_name: str) -> set[str]:
        """Get set of existing column names for a table in a namespace schema."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = $1 AND table_name = $2
                """,
                schema,
                table_name,
            )
            return {row["column_name"] for row in rows}

    async def create_table(
        self,
        namespace: str,
        template_value: str,
        template_version: int,
        fields: list[TemplateField],
        config: ReportingConfig | None = None,
        usage: str = "entity",
        identity_fields: list[str] | None = None,
    ) -> str:
        """Create a table for a template in the namespace's schema."""
        schema = await self.ensure_schema(namespace)
        ddl = self.generate_create_table_ddl(
            namespace, template_value, template_version, fields, config,
            usage=usage, identity_fields=identity_fields,
        )
        table_name = self.get_table_name(template_value, config)

        logger.info(f'Creating table "{schema}"."{table_name}" for template {template_value}')

        async with self.pool.acquire() as conn:
            await conn.execute(ddl)

            # Record the migration. Keyed on (namespace, template_value,
            # version): template_value is unique only per namespace, so the key
            # must include the namespace or two namespaces' migrations collide.
            await conn.execute(
                """
                INSERT INTO _wip_schema_migrations
                    (namespace, template_value, template_version, migration_sql)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (namespace, template_value, template_version) DO NOTHING
                """,
                namespace,
                template_value,
                template_version,
                ddl,
            )

        logger.info(f'Table "{schema}"."{table_name}" created successfully')
        return ddl

    async def update_table_schema(
        self,
        namespace: str,
        template_value: str,
        template_version: int,
        fields: list[TemplateField],
        config: ReportingConfig | None = None,
        usage: str = "entity",
        identity_fields: list[str] | None = None,
    ) -> list[str]:
        """
        Update table schema if template has new fields.

        Only adds new columns; never removes or modifies existing columns
        to preserve historical data.

        For identity-less templates whose table was created under a
        previous schema-manager version (when the unique-identity index
        was always emitted), drop the now-broken index so identity-less
        sync can land docs.
        """
        schema = self.schema_for(namespace)
        table_name = self.get_table_name(template_value, config)
        qualified = self.qualified_name(namespace, template_value, config)

        if not await self.table_exists(schema, table_name):
            await self.create_table(
                namespace, template_value, template_version, fields, config,
                usage=usage, identity_fields=identity_fields,
            )
            return [f"Created table {table_name}"]

        existing_columns = await self.get_existing_columns(schema, table_name)
        migrations = []

        async with self.pool.acquire() as conn:
            # Identity-less template + legacy unique-identity index?
            # Drop it. The index was created when this schema-manager
            # always emitted it; identity_hash="" makes it collide on
            # every doc beyond the first.
            if not identity_fields:
                idx_name = f"{table_name}_ns_active_identity_idx"
                idx_exists = await conn.fetchval(
                    """
                    SELECT EXISTS (
                        SELECT 1 FROM pg_indexes
                        WHERE schemaname = $1
                          AND tablename = $2
                          AND indexname = $3
                    )
                    """,
                    schema,
                    table_name,
                    idx_name,
                )
                if idx_exists:
                    drop_sql = f'DROP INDEX IF EXISTS "{schema}"."{idx_name}"'
                    await conn.execute(drop_sql)
                    migrations.append(drop_sql)
                    logger.info(
                        f"Dropped legacy unique-identity index {idx_name} "
                        f"(template {template_value} has no identity_fields)"
                    )
            # Phase 7 — relationship templates need source_ref_id / target_ref_id
            # columns + indexes. Add them lazily when missing so legacy
            # relationship templates that pre-date Phase 7 catch up on next
            # event. Index creation is idempotent.
            if usage == "relationship":
                for col in ("source_ref_id", "target_ref_id"):
                    if col not in existing_columns:
                        alter_sql = f'ALTER TABLE {qualified} ADD COLUMN "{col}" TEXT'
                        await conn.execute(alter_sql)
                        migrations.append(alter_sql)
                        idx_sql = (
                            f'CREATE INDEX IF NOT EXISTS '
                            f'"{table_name}_{col}_idx" ON {qualified}({col})'
                        )
                        await conn.execute(idx_sql)
                        migrations.append(idx_sql)

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
                        alter_sql = f'ALTER TABLE {qualified} ADD COLUMN "{col_name}" {clean_type}'

                        logger.info(f"Adding column {col_name} to {table_name}")
                        await conn.execute(alter_sql)
                        migrations.append(alter_sql)

                # Full-text-indexed string fields: ensure the (search,
                # tsv) column pair and the GIN index exist. Idempotent —
                # legacy tables created before FTS landed catch up here.
                if (
                    getattr(field, "full_text_indexed", None)
                    and field.type == FieldType.STRING
                ):
                    base_col = f"data_{field.name}" if needs_prefix else field.name
                    for fts_col, fts_type in self._full_text_columns(base_col):
                        if fts_col not in existing_columns:
                            alter_sql = (
                                f'ALTER TABLE {qualified} '
                                f'ADD COLUMN "{fts_col}" {fts_type}'
                            )
                            logger.info(f"Adding FTS column {fts_col} to {table_name}")
                            await conn.execute(alter_sql)
                            migrations.append(alter_sql)
                    idx_sql = (
                        f'CREATE INDEX IF NOT EXISTS '
                        f'"{table_name}_{base_col}_tsv_idx" ON {qualified} '
                        f'USING GIN ("{base_col}_tsv")'
                    )
                    await conn.execute(idx_sql)
                    migrations.append(idx_sql)

            if migrations:
                # Record the migration (keyed on namespace + template_value +
                # version — see create_table).
                migration_sql = ";\n".join(migrations)
                await conn.execute(
                    """
                    INSERT INTO _wip_schema_migrations
                        (namespace, template_value, template_version, migration_sql)
                    VALUES ($1, $2, $3, $4)
                    ON CONFLICT (namespace, template_value, template_version) DO UPDATE
                    SET migration_sql = _wip_schema_migrations.migration_sql || E'\n' || $4,
                        applied_at = NOW()
                    """,
                    namespace,
                    template_value,
                    template_version,
                    migration_sql,
                )

        return migrations

    async def ensure_terminologies_table(self, namespace: str) -> str:
        """
        Ensure the terminologies table exists in the namespace's schema.

        Fixed-schema table for syncing terminologies from the Def-Store.

        Returns:
            The schema-qualified table reference ("<ns>"."terminologies").
        """
        schema = await self.ensure_schema(namespace)
        table_name = "terminologies"
        qualified = f'"{schema}"."{table_name}"'

        if await self.table_exists(schema, table_name):
            return qualified

        ddl = f"""
CREATE TABLE IF NOT EXISTS {qualified} (
    "terminology_id" TEXT NOT NULL,
    "namespace" VARCHAR(255) NOT NULL DEFAULT 'wip',
    "value" TEXT NOT NULL,
    "label" TEXT,
    "description" TEXT,
    "case_sensitive" BOOLEAN DEFAULT FALSE,
    "allow_multiple" BOOLEAN DEFAULT FALSE,
    "extensible" BOOLEAN DEFAULT TRUE,
    "mutable" BOOLEAN NOT NULL DEFAULT FALSE,
    "status" VARCHAR(20) NOT NULL DEFAULT 'active',
    "term_count" INTEGER DEFAULT 0,
    "created_at" TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    "created_by" TEXT,
    "updated_at" TIMESTAMP WITH TIME ZONE,
    "updated_by" TEXT,
    PRIMARY KEY ("namespace", "terminology_id")
);

CREATE INDEX IF NOT EXISTS "{table_name}_ns_value_idx"
  ON {qualified}("namespace", "value");
CREATE INDEX IF NOT EXISTS "{table_name}_ns_status_idx"
  ON {qualified}("namespace", "status");
"""

        async with self.pool.acquire() as conn:
            await conn.execute(ddl)

        logger.info(f"Created {qualified} table")
        return qualified

    async def ensure_templates_table(self, namespace: str) -> str:
        """
        Ensure the templates metadata table exists in the namespace's schema.

        Fixed-schema table for syncing template status from the Template-Store.

        Returns:
            The schema-qualified table reference ("<ns>"."templates").
        """
        schema = await self.ensure_schema(namespace)
        table_name = "templates"
        qualified = f'"{schema}"."{table_name}"'

        if await self.table_exists(schema, table_name):
            return qualified

        ddl = f"""
CREATE TABLE IF NOT EXISTS {qualified} (
    "template_id" TEXT NOT NULL,
    "namespace" VARCHAR(255) NOT NULL DEFAULT 'wip',
    "value" TEXT NOT NULL,
    "label" TEXT,
    "description" TEXT,
    "version" INTEGER NOT NULL DEFAULT 1,
    "status" VARCHAR(20) NOT NULL DEFAULT 'active',
    "extends" TEXT,
    "extends_version" INTEGER,
    "created_at" TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    "created_by" TEXT,
    "updated_at" TIMESTAMP WITH TIME ZONE,
    "updated_by" TEXT,
    PRIMARY KEY ("namespace", "template_id")
);

CREATE INDEX IF NOT EXISTS "{table_name}_ns_value_idx"
  ON {qualified}("namespace", "value");
CREATE INDEX IF NOT EXISTS "{table_name}_ns_status_idx"
  ON {qualified}("namespace", "status");
"""

        async with self.pool.acquire() as conn:
            await conn.execute(ddl)

        logger.info(f"Created {qualified} table")
        return qualified

    async def ensure_terms_table(self, namespace: str) -> str:
        """
        Ensure the terms table exists in the namespace's schema.

        Fixed-schema table for syncing terms from the Def-Store.

        Returns:
            The schema-qualified table reference ("<ns>"."terms").
        """
        schema = await self.ensure_schema(namespace)
        table_name = "terms"
        qualified = f'"{schema}"."{table_name}"'

        if await self.table_exists(schema, table_name):
            return qualified

        ddl = f"""
CREATE TABLE IF NOT EXISTS {qualified} (
    "term_id" TEXT NOT NULL,
    "namespace" VARCHAR(255) NOT NULL DEFAULT 'wip',
    "terminology_id" TEXT NOT NULL,
    "terminology_value" TEXT,
    "value" TEXT NOT NULL,
    "aliases" JSONB DEFAULT '[]'::jsonb,
    "label" TEXT,
    "description" TEXT,
    "sort_order" INTEGER DEFAULT 0,
    "parent_term_id" TEXT,
    "status" VARCHAR(20) NOT NULL DEFAULT 'active',
    "deprecated_reason" TEXT,
    "replaced_by_term_id" TEXT,
    "created_at" TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    "created_by" TEXT,
    "updated_at" TIMESTAMP WITH TIME ZONE,
    "updated_by" TEXT,
    PRIMARY KEY ("namespace", "term_id")
);

CREATE INDEX IF NOT EXISTS "{table_name}_ns_terminology_idx"
  ON {qualified}("namespace", "terminology_id");
CREATE INDEX IF NOT EXISTS "{table_name}_ns_value_idx"
  ON {qualified}("namespace", "value");
CREATE INDEX IF NOT EXISTS "{table_name}_ns_status_idx"
  ON {qualified}("namespace", "status");
CREATE INDEX IF NOT EXISTS "{table_name}_ns_parent_idx"
  ON {qualified}("namespace", "parent_term_id");
"""

        async with self.pool.acquire() as conn:
            await conn.execute(ddl)

        logger.info(f"Created {qualified} table")
        return qualified

    async def ensure_term_relations_table(self, namespace: str) -> str:
        """
        Ensure the term_relations table exists in the namespace's schema.

        This is a fixed-schema table (not template-driven) for syncing
        ontology term-relations from the Def-Store.

        Returns:
            The schema-qualified table reference ("<ns>"."term_relations").
        """
        schema = await self.ensure_schema(namespace)
        table_name = "term_relations"
        qualified = f'"{schema}"."{table_name}"'

        if await self.table_exists(schema, table_name):
            return qualified

        ddl = f"""
CREATE TABLE IF NOT EXISTS {qualified} (
    "namespace" VARCHAR(255) NOT NULL DEFAULT 'wip',
    "source_term_id" TEXT NOT NULL,
    "target_term_id" TEXT NOT NULL,
    "relation_type" TEXT NOT NULL,
    "source_term_value" TEXT,
    "target_term_value" TEXT,
    "source_terminology_id" TEXT,
    "target_terminology_id" TEXT,
    "metadata" JSONB DEFAULT '{{}}'::jsonb,
    "status" VARCHAR(20) NOT NULL DEFAULT 'active',
    "created_at" TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    "created_by" TEXT,
    PRIMARY KEY ("namespace", "source_term_id", "target_term_id", "relation_type")
);

-- Indexes for efficient traversal queries
CREATE INDEX IF NOT EXISTS "{table_name}_ns_source_type_idx"
  ON {qualified}("namespace", "source_term_id", "relation_type");
CREATE INDEX IF NOT EXISTS "{table_name}_ns_target_type_idx"
  ON {qualified}("namespace", "target_term_id", "relation_type");
CREATE INDEX IF NOT EXISTS "{table_name}_ns_status_idx"
  ON {qualified}("namespace", "status");
CREATE INDEX IF NOT EXISTS "{table_name}_ns_source_terminology_idx"
  ON {qualified}("namespace", "source_terminology_id");
CREATE INDEX IF NOT EXISTS "{table_name}_ns_target_terminology_idx"
  ON {qualified}("namespace", "target_terminology_id");
"""

        async with self.pool.acquire() as conn:
            await conn.execute(ddl)

        logger.info(f"Created {qualified} table")
        return qualified

    async def ensure_table_for_template(
        self,
        namespace: str,
        template: dict[str, Any],
    ) -> str:
        """
        Ensure a table exists for a template in a namespace's schema.

        The table lives in the schema of the *document's* namespace (passed in),
        not the template's — a template shared from another namespace still
        materialises one reporting table per namespace whose documents use it.

        Args:
            namespace: The document's namespace (→ its PostgreSQL schema).
            template: Full template definition from Template Store

        Returns:
            Table name (bare, within the namespace schema)
        """
        template_value = template["value"]
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
            # Parse semantic_type if present
            semantic_type = None
            if f.get("semantic_type"):
                semantic_type = SemanticType(f["semantic_type"])

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
                semantic_type=semantic_type,
                full_text_indexed=f.get("full_text_indexed"),
            ))

        # Parse reporting config if present
        reporting_data = template.get("reporting", {})
        config = ReportingConfig(**reporting_data) if reporting_data else None

        # Check if sync is enabled
        if config and not config.sync_enabled:
            logger.info(f"Sync disabled for template {template_value}, skipping table creation")
            return ""

        schema = self.schema_for(namespace)
        table_name = self.get_table_name(template_value, config)

        usage = template.get("usage", "entity")
        identity_fields = template.get("identity_fields") or []

        if await self.table_exists(schema, table_name):
            migrations = await self.update_table_schema(
                namespace, template_value, template_version, fields, config,
                usage=usage, identity_fields=identity_fields,
            )
            if migrations:
                logger.info(f"Updated table {table_name} with {len(migrations)} new columns")
        else:
            await self.create_table(
                namespace, template_value, template_version, fields, config,
                usage=usage, identity_fields=identity_fields,
            )

        # Return the schema-qualified reference ("<ns>"."<table>") so callers
        # build SQL against it directly, without re-deriving the schema.
        return self.qualified_name(namespace, template_value, config)

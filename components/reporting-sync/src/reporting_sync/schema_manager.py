"""
Schema Manager - Creates and manages PostgreSQL tables from template definitions.

Responsibilities:
- Generate CREATE TABLE DDL from template field definitions
- Handle schema evolution (ALTER TABLE for new fields)
- Track migrations in _wip_schema_migrations table
"""

import contextlib
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

    def get_table_name(
        self,
        template_value: str,
        config: ReportingConfig | None = None,
        version: int | None = None,
    ) -> str:
        """Get the bare (unqualified) relation name for a template.

        The base is ``doc_<value>`` (or the template's optional
        ``ReportingConfig.table_name`` cosmetic override) *within* the
        namespace schema — namespace isolation comes from the schema, not
        the name.

        With ``version``, this is the **physical per-version table**
        ``<base>__v<N>`` — one table per (template, version), each shaped by
        that version's own fields, so a NULL in a row means "submitted
        empty", never "not in this row's schema version". Without
        ``version`` it is the base name, which post-split is the entity
        **view** (identity-core, or the opt-in cross-version view), not a
        table. Template-store rejects values/overrides matching
        ``.*__v[0-9]+$`` so the suffix cannot collide with a real name.
        """
        base = (
            self._safe_ident(config.table_name)
            if config and config.table_name
            else self._safe_ident(f"doc_{template_value.lower()}")
        )
        if version is None:
            return base
        return f"{base}__v{int(version)}"

    def entities_view_name(
        self, template_value: str, config: ReportingConfig | None = None
    ) -> str:
        """The always-present identity-core view: ``<base>__entities``."""
        return f"{self.get_table_name(template_value, config)}__entities"

    def qualified_name(
        self,
        namespace: str,
        template_value: str,
        config: ReportingConfig | None = None,
        version: int | None = None,
    ) -> str:
        """Schema-qualified, quote-safe relation reference for use in SQL.

        e.g. ``"clinicA"."doc_patient__v3"`` (with ``version``) or the
        entity view ``"clinicA"."doc_patient"`` (without). Callers building
        SQL by hand should use this rather than constructing the name.
        """
        schema = self.schema_for(namespace)
        table = self.get_table_name(template_value, config, version)
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
        table_name = self.get_table_name(template_value, config, template_version)
        qualified = self.qualified_name(namespace, template_value, config, template_version)
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
"""

        # The created_at index only exists when metadata columns do — otherwise
        # it references a column that was excluded (include_metadata=False).
        if include_metadata:
            ddl += (
                f'CREATE INDEX IF NOT EXISTS "{table_name}_ns_created_at_idx" '
                f"ON {qualified}(namespace, created_at);\n"
            )

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

    @staticmethod
    def parse_template_fields(fields_data: list[dict]) -> list[TemplateField]:
        """Parse raw template field dicts (as the template-store API returns
        them) into TemplateField models. The single conversion point shared by
        table creation and the parity check — both must see identical fields
        or the parity oracle drifts from what sync actually builds."""
        fields: list[TemplateField] = []
        for f in fields_data:
            file_config = None
            if f.get("file_config"):
                file_config = FileFieldConfig(**f["file_config"])
            array_file_config = None
            if f.get("array_file_config"):
                array_file_config = FileFieldConfig(**f["array_file_config"])
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
        return fields

    def expected_columns_for_template(
        self,
        fields: list[TemplateField],
        config: ReportingConfig | None = None,
        usage: str = "entity",
    ) -> set[str]:
        """Column names the sync pipeline would create for these fields.

        Derived from the same _generate_column_ddl / SYSTEM_COLUMNS-prefix /
        full-text rules create_table and update_table_schema apply, so a
        parity check comparing against information_schema measures exactly
        what sync builds. Covers field-derived columns only — the fixed
        system columns are created with the table and cannot drift
        independently.
        """
        expected: set[str] = set()
        for field in fields:
            needs_prefix = field.name in self.SYSTEM_COLUMNS
            for col_name, _col_type in self._generate_column_ddl(field, config=config):
                expected.add(f"data_{col_name}" if needs_prefix else col_name)
            if field.full_text_indexed and field.type == FieldType.STRING:
                base_col = f"data_{field.name}" if needs_prefix else field.name
                for fts_col, _fts_type in self._full_text_columns(base_col):
                    expected.add(fts_col)
        if usage == "relationship":
            expected.update(("source_ref_id", "target_ref_id"))
        return expected

    async def ensure_schema(self, namespace: str) -> str:
        """Create the namespace's PostgreSQL schema if absent. Returns its name.

        Concurrency-safe on purpose: ``CREATE SCHEMA IF NOT EXISTS`` is NOT —
        two concurrent callers can both pass the exists-check and the loser
        dies on the catalog's unique index (pg_namespace_nspname_index). A
        restore fans out per-template batch syncs that all ensure the same
        namespace schema at once; losing that race must mean "someone else
        just created it", never a failed sync (it halted a live kb restore
        at the structure gate: three templates lost, three tables missing).
        """
        schema = self.schema_for(namespace)
        async with self.pool.acquire() as conn:
            # A concurrent caller can create it between the existence check
            # and the create — the desired state holds either way.
            with contextlib.suppress(asyncpg.UniqueViolationError):
                await conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
        return schema

    async def drop_namespace_schema(self, namespace: str) -> int:
        """Drop the namespace's schema and return how many rows went with it.

        The row count exists for the caller's audit trail: Registry's
        namespace-deletion journal records `postgres_rows` as an integer, and
        DROP SCHEMA alone has no count to offer — a bare "dropped" answer
        forces the caller to invent one (a None here once propagated into the
        int-typed journal and made every namespace DELETE return a validation
        error while the deletion itself succeeded). Counting first is cheap at
        namespace-deletion frequency. Returns 0 when the schema doesn't exist.
        """
        schema = self.schema_for(namespace)
        total = 0
        async with self.pool.acquire() as conn:
            tables = await conn.fetch(
                """
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = $1 AND table_type = 'BASE TABLE'
                """,
                schema,
            )
            for row in tables:
                table = self._safe_ident(row["table_name"])
                count = await conn.fetchval(
                    f'SELECT COUNT(*) FROM "{schema}"."{table}"'
                )
                total += int(count or 0)
            await conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        return total

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
        table_name = self.get_table_name(template_value, config, template_version)

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
        table_name = self.get_table_name(template_value, config, template_version)
        qualified = self.qualified_name(namespace, template_value, config, template_version)

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
            The schema-qualified table reference ('"<ns>"."doc_<value>"'),
            or "" when the template's reporting config disables sync.
        """
        template_value = template["value"]
        template_version = template.get("version", 1)
        fields_data = template.get("fields", [])

        # Parse fields
        fields = []
        fields.extend(self.parse_template_fields(fields_data))

        # Parse reporting config if present
        reporting_data = template.get("reporting", {})
        config = ReportingConfig(**reporting_data) if reporting_data else None

        # Check if sync is enabled
        if config and not config.sync_enabled:
            logger.info(f"Sync disabled for template {template_value}, skipping table creation")
            return ""

        schema = self.schema_for(namespace)
        table_name = self.get_table_name(template_value, config, template_version)

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
            # A new version table changes the entity's union membership —
            # rebuild the views so the bare name and __entities stay honest.
            await self.ensure_views_for_template(namespace, template_value, config)

        # Return the schema-qualified reference to this VERSION's table
        # ("<ns>"."doc_<value>__v<N>") so callers build SQL against it
        # directly, without re-deriving the schema.
        return self.qualified_name(namespace, template_value, config, template_version)

    async def list_version_tables(
        self, namespace: str, template_value: str,
        config: ReportingConfig | None = None,
    ) -> dict[int, str]:
        """The physical per-version tables present for a template: {version: table_name}.

        Discovered from information_schema, so it reflects what actually
        exists — the view builders and parity both consume this rather than
        re-deriving membership from template metadata.
        """
        schema = self.schema_for(namespace)
        base = self.get_table_name(template_value, config)
        # LIKE-escape the base (underscores are LIKE wildcards).
        escaped = base.replace("\\", "\\\\").replace("_", "\\_").replace("%", "\\%")
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                r"""
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = $1
                  AND table_type = 'BASE TABLE'
                  AND table_name LIKE $2 || '\_\_v%'
                """,
                schema,
                escaped,
            )
        out: dict[int, str] = {}
        prefix = f"{base}__v"
        for row in rows:
            name = row["table_name"]
            suffix = name[len(prefix):]
            if name.startswith(prefix) and suffix.isdigit():
                out[int(suffix)] = name
        return out

    async def relation_kind(self, schema: str, name: str) -> str | None:
        """'table' | 'view' | None for a relation in a schema."""
        async with self.pool.acquire() as conn:
            kind = await conn.fetchval(
                """
                SELECT c.relkind FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = $1 AND c.relname = $2
                """,
                schema,
                name,
            )
        if kind is None:
            return None
        # asyncpg returns the "char" relkind as bytes.
        kind_s = kind.decode() if isinstance(kind, bytes) else str(kind)
        return {"r": "table", "v": "view"}.get(kind_s, kind_s)

    async def _shared_columns(
        self, schema: str, tables: list[str]
    ) -> list[tuple[str, str]]:
        """Columns present in EVERY listed table with an identical data type,
        in the column order of the newest table. Generated tsvector columns
        are excluded — full-text search targets the physical tables.

        This intersection IS the identity core plus the provably-unchanged
        columns: identity_fields are immutable across versions (template
        identity design), so their columns always survive; anything typed
        differently between versions drops out, keeping the union honest.
        """
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT table_name, column_name, data_type, ordinal_position,
                       is_generated
                FROM information_schema.columns
                WHERE table_schema = $1 AND table_name = ANY($2::text[])
                ORDER BY table_name, ordinal_position
                """,
                schema,
                tables,
            )
        per_table: dict[str, dict[str, str]] = {t: {} for t in tables}
        order_source: list[str] = []
        for row in rows:
            if row["is_generated"] == "ALWAYS":
                continue
            per_table[row["table_name"]][row["column_name"]] = row["data_type"]
        # Column order follows the last table in sorted-by-version order —
        # callers pass tables sorted ascending, so the newest version wins.
        if tables:
            newest = tables[-1]
            order_source = [
                r["column_name"] for r in rows
                if r["table_name"] == newest and r["is_generated"] != "ALWAYS"
            ]
        shared: list[tuple[str, str]] = []
        for col in order_source:
            dtype = per_table[tables[-1]][col]
            if all(per_table[t].get(col) == dtype for t in tables):
                shared.append((col, dtype))
        return shared

    async def ensure_views_for_template(
        self,
        namespace: str,
        template_value: str,
        config: ReportingConfig | None = None,
    ) -> str | None:
        """(Re)build the entity views over the per-version tables.

        Two views, both plain (never materialized — a refresh lifecycle
        would be a new way to serve authoritative-looking stale data):

        - ``<base>__entities`` — always the identity-core view: the typed
          intersection of all version tables' columns, UNION ALL.
        - ``<base>`` (the bare name) — the default query surface. Identity
          core by default; when the template opts in via
          ``reporting.cross_version_view`` it additionally carries the
          declared column mappings over the selected versions.

        Returns a warning string (and builds nothing) when the bare name is
        occupied by a pre-split physical TABLE — the legacy single-table
        layout. That table shadows the entity view; remediation is an
        explicit rebuild (drop the legacy table, then batch sync), surfaced
        through parity as ``legacy_table`` rather than auto-dropped here.
        """
        schema = self.schema_for(namespace)
        base = self.get_table_name(template_value, config)
        version_tables = await self.list_version_tables(namespace, template_value, config)
        if not version_tables:
            return None

        legacy_kind = await self.relation_kind(schema, base)
        if legacy_kind == "table":
            msg = (
                f'"{schema}"."{base}" is a pre-split physical table shadowing '
                f"the entity view — drop it and re-run the batch sync to "
                f"migrate to per-version tables"
            )
            logger.warning(msg)
            return msg

        ordered_versions = sorted(version_tables)
        tables = [version_tables[v] for v in ordered_versions]
        shared = await self._shared_columns(schema, tables)
        if not shared:
            return None
        col_list = ", ".join(f'"{c}"' for c, _t in shared)

        selects = [
            f'SELECT {col_list} FROM "{schema}"."{t}"' for t in tables
        ]
        entities_view = f"{base}__entities"
        union_sql = "\nUNION ALL\n".join(selects)

        cv = getattr(config, "cross_version_view", None) if config else None
        bare_sql = union_sql
        if cv:
            selected = ordered_versions if cv.versions == "all" else [
                v for v in ordered_versions if v in (cv.versions or [])
            ]
            if selected:
                sel_tables = [version_tables[v] for v in selected]
                shared_sel = await self._shared_columns(schema, sel_tables)
                col_names = {c for c, _t in shared_sel}
                mapped = {
                    target: (spec or {}).get("from") or target
                    for target, spec in (cv.columns or {}).items()
                    if target not in col_names
                }
                # Type of each mapped target: taken from the newest selected
                # table that has the source (or target) column.
                async with self.pool.acquire() as conn:
                    type_rows = await conn.fetch(
                        """
                        SELECT table_name, column_name, data_type
                        FROM information_schema.columns
                        WHERE table_schema = $1 AND table_name = ANY($2::text[])
                        """,
                        schema,
                        sel_tables,
                    )
                col_types: dict[str, dict[str, str]] = {t: {} for t in sel_tables}
                for row in type_rows:
                    col_types[row["table_name"]][row["column_name"]] = row["data_type"]

                mapped_types: dict[str, str] = {}
                for target, source in mapped.items():
                    for t in reversed(sel_tables):
                        dtype = col_types[t].get(target) or col_types[t].get(source)
                        if dtype:
                            mapped_types[target] = dtype
                            break

                shared_cols_sql = ", ".join(f'"{c}"' for c, _t in shared_sel)
                sel_selects = []
                for t in sel_tables:
                    extra = []
                    for target, source in mapped.items():
                        dtype = mapped_types.get(target)
                        if dtype is None:
                            continue
                        if target in col_types[t]:
                            extra.append(f'"{target}"')
                        elif source in col_types[t]:
                            extra.append(f'"{source}" AS "{target}"')
                        else:
                            extra.append(f'NULL::{dtype} AS "{target}"')
                    cols = shared_cols_sql + (", " + ", ".join(extra) if extra else "")
                    sel_selects.append(f'SELECT {cols} FROM "{schema}"."{t}"')
                bare_sql = "\nUNION ALL\n".join(sel_selects)

        async with self.pool.acquire() as conn, conn.transaction():
            await conn.execute(f'DROP VIEW IF EXISTS "{schema}"."{entities_view}"')
            await conn.execute(
                f'CREATE VIEW "{schema}"."{entities_view}" AS\n{union_sql}'
            )
            if legacy_kind in (None, "view"):
                await conn.execute(f'DROP VIEW IF EXISTS "{schema}"."{base}"')
                await conn.execute(
                    f'CREATE VIEW "{schema}"."{base}" AS\n{bare_sql}'
                )
        logger.info(
            f'Rebuilt entity views for "{schema}"."{base}" over versions '
            f"{ordered_versions}"
        )
        return None

    async def delete_from_sibling_version_tables(
        self,
        namespace: str,
        template_value: str,
        config: ReportingConfig | None,
        keep_version: int,
        document_id: str,
    ) -> int:
        """Remove a document's rows from every version table EXCEPT keep_version.

        The latest_only strategy keeps one live row per document; when an
        update validates against a newer template version the row MOVES
        tables (pin-to-what-validated), so the upsert into the new table
        must be paired with a delete from the siblings or the entity views
        would show the document twice.
        """
        version_tables = await self.list_version_tables(namespace, template_value, config)
        schema = self.schema_for(namespace)
        deleted = 0
        async with self.pool.acquire() as conn:
            for version, table in version_tables.items():
                if version == keep_version:
                    continue
                result = await conn.execute(
                    f'DELETE FROM "{schema}"."{table}" WHERE document_id = $1',
                    document_id,
                )
                with contextlib.suppress(ValueError, IndexError):
                    deleted += int(result.split()[-1])
        return deleted

    async def drop_relations_for_template(
        self,
        namespace: str,
        template_value: str,
        config: ReportingConfig | None = None,
    ) -> list[str]:
        """Drop every reporting relation for a template in ONE namespace's
        schema: the entity views, all per-version tables, and — if present —
        a legacy pre-split physical table occupying the bare name (the shape
        ``ensure_views_for_template`` can only report, never fix).

        This is the destructive half of a force rebuild: mis-shaped DDL
        (e.g. a table created from a same-valued foreign template) cannot be
        healed by upserts, only by drop-and-recreate. Namespace-scoped by
        design — table names derive from the template VALUE, not the
        template_id, so a cross-schema sweep could destroy tables belonging
        to a different template that shares the value.

        Views are dropped explicitly before their tables rather than via
        CASCADE: a dependent relation this method does not know about (a
        user-created view over a version table) makes the transaction fail
        loudly instead of being silently cascaded away.

        Returns the dropped relation names, schema-qualified. All-or-nothing
        (single transaction).
        """
        schema = self.schema_for(namespace)
        base = self.get_table_name(template_value, config)
        entities_view = f"{base}__entities"
        version_tables = await self.list_version_tables(namespace, template_value, config)
        entities_kind = await self.relation_kind(schema, entities_view)
        bare_kind = await self.relation_kind(schema, base)

        dropped: list[str] = []
        async with self.pool.acquire() as conn, conn.transaction():
            if entities_kind == "view":
                await conn.execute(f'DROP VIEW IF EXISTS "{schema}"."{entities_view}"')
                dropped.append(f"{schema}.{entities_view}")
            if bare_kind == "view":
                await conn.execute(f'DROP VIEW IF EXISTS "{schema}"."{base}"')
                dropped.append(f"{schema}.{base}")
            elif bare_kind == "table":
                await conn.execute(f'DROP TABLE IF EXISTS "{schema}"."{base}"')
                dropped.append(f"{schema}.{base}")
            for version in sorted(version_tables):
                table = version_tables[version]
                await conn.execute(f'DROP TABLE IF EXISTS "{schema}"."{table}"')
                dropped.append(f"{schema}.{table}")
        if dropped:
            logger.info(
                f'Dropped {len(dropped)} relation(s) for "{schema}"."{base}": '
                f"{dropped}"
            )
        return dropped

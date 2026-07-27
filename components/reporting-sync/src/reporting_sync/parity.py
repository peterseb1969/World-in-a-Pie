"""Reporting-layer parity check — does postgres reflect what sync should have built?

The standing per-namespace verification designed in the restore-verification
fireside: for every sync-enabled template of a namespace, verify the table
exists in the namespace's schema, its columns match what the schema manager
would build from the template (single oracle — no parallel schema
derivation), the bookkeeping row landed, and (optionally) the row count
matches the same document-store query the batch sync consumes.

Two consumers, one code path: the restore orchestrator calls it at phase
boundaries (structure-only after templates restore, counts after documents),
and operators/agents call it ad hoc — the one-request answer to "why does
the reporting layer look empty".

Honest limits, by design:
- Inherited fields (template `extends`) are not resolved; missing-column
  detection covers the template's own fields. Extra actual columns are never
  an issue (sync only ever adds).
- Count comparison is exact only for sync_strategy latest_only (the
  default): expected = the document-store active-document total, the same
  number batch sync pages through. For all_versions the expectation is not
  derivable from one call; those templates report counts_comparable=False.
"""

import logging
from typing import Any

import asyncpg
import httpx
from pydantic import BaseModel, Field

from .config import settings
from .models import ReportingConfig
from .schema_manager import SchemaManager

logger = logging.getLogger(__name__)


class TemplateParity(BaseModel):
    """Per-template parity row.

    Post-split semantics (per-version reporting tables): ``table_present``
    means the LATEST version's physical table exists — the table the next
    write against latest would land in — and ``missing_columns`` is checked
    against it. ``actual_rows`` aggregates across every version table (a
    document lives in exactly one under latest_only, so the sum is the
    entity count). The field names and their pass/fail meaning are stable
    on purpose: the restore engine's structural gate and count-parity
    phases consume them.
    """

    template_value: str
    sync_enabled: bool = True
    table_present: bool = False
    missing_columns: list[str] = Field(default_factory=list)
    bookkeeping_row: bool = False
    counts_comparable: bool = True
    expected_documents: int | None = None
    actual_rows: int | None = None
    counts_match: bool | None = None
    error: str | None = None
    # Per-version split additions
    version_tables: list[int] = Field(default_factory=list)
    view_present: bool = False
    legacy_table: bool = False

    @property
    def structural_ok(self) -> bool:
        return (
            self.table_present
            and not self.missing_columns
            and not self.legacy_table
            and self.error is None
        )


class NamespaceParityResult(BaseModel):
    namespace: str
    schema_name: str
    schema_present: bool
    table_count: int
    bookkeeping_tables_ok: bool = True
    bookkeeping_error: str | None = None
    templates: list[TemplateParity] = Field(default_factory=list)
    templates_skipped_sync_disabled: int = 0
    structural_issues: int = 0
    count_mismatches: int = 0
    ok: bool = False


async def _fetch_namespace_templates(namespace: str) -> list[dict[str, Any]]:
    """All ACTIVE templates of a namespace, latest version each — the
    structural contract: the latest-active version is the one a new write
    lands on, so its table is what must exist.

    Deliberately NOT the same listing the batch sync iterates: the batch
    sync's instance-wide list is status-free (a fully-deactivated
    template's documents must still sync), and its eager table-ensure is
    active-aware to match THIS listing. A template whose every version is
    inactive is absent here and exempt from the structural gate — its
    documents' tables materialize lazily at sync time."""
    templates: list[dict[str, Any]] = []
    page = 1
    async with httpx.AsyncClient() as client:
        while True:
            response = await client.get(
                f"{settings.template_store_url}/api/template-store/templates",
                params={
                    "namespace": namespace,
                    "status": "active",
                    "latest_only": "true",
                    "page": page,
                    "page_size": 100,
                },
                headers={"X-API-Key": settings.api_key},
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()
            items = data.get("items", [])
            templates.extend(items)
            if len(items) < 100:
                return templates
            page += 1


async def _expected_document_total(template_id: str) -> int:
    """Active-document total from document-store — the identical query the
    batch sync pages through, asked for its count only."""
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{settings.document_store_url}/api/document-store/documents",
            params={"template_id": template_id, "status": "active",
                    "page": 1, "page_size": 1},
            headers={"X-API-Key": settings.api_key},
            timeout=30.0,
        )
        response.raise_for_status()
        return int(response.json().get("total", 0))


async def check_namespace_parity(
    pool: asyncpg.Pool,
    namespace: str,
    include_counts: bool = True,
) -> NamespaceParityResult:
    """Run the parity check for one namespace."""
    sm = SchemaManager(pool)
    schema = sm.schema_for(namespace)

    async with pool.acquire() as conn:
        schema_present = bool(await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM information_schema.schemata "
            "WHERE schema_name = $1)", schema,
        ))
        table_count = int(await conn.fetchval(
            "SELECT count(*) FROM pg_tables WHERE schemaname = $1", schema,
        ) or 0)

    result = NamespaceParityResult(
        namespace=namespace,
        schema_name=schema,
        schema_present=schema_present,
        table_count=table_count,
    )

    # The bookkeeping tables must be namespace-keyed for any of the
    # per-template rows below to be meaningful. A database whose tables
    # predate the namespace re-keying fails HERE, loudly, with remediation —
    # instead of every doc-type sync failing one by one.
    async with pool.acquire() as conn:
        try:
            await conn.fetchval(
                "SELECT count(*) FROM _wip_schema_migrations WHERE namespace = $1",
                namespace,
            )
        except asyncpg.UndefinedColumnError:
            result.bookkeeping_tables_ok = False
            result.bookkeeping_error = (
                "_wip_schema_migrations predates namespace keying (no "
                "namespace column) — doc-type sync cannot record migrations. "
                "Remediate by wiping the reporting database (restore ritual) "
                "or applying the namespace-column ALTER."
            )
        except asyncpg.UndefinedTableError:
            # No bookkeeping table at all — a genuinely fresh database;
            # init_postgres_schema creates it at service startup.
            pass

    try:
        templates = await _fetch_namespace_templates(namespace)
    except httpx.HTTPError as e:
        result.bookkeeping_error = (result.bookkeeping_error or "") + (
            f" template-store unavailable: {e}"
        )
        result.ok = False
        return result

    for template in templates:
        template_value = template.get("value", "")
        reporting_data = template.get("reporting") or {}
        config = ReportingConfig(**reporting_data) if reporting_data else ReportingConfig()
        if not config.sync_enabled:
            result.templates_skipped_sync_disabled += 1
            continue

        row = TemplateParity(template_value=template_value)
        try:
            fields = SchemaManager.parse_template_fields(template.get("fields", []))
            usage = template.get("usage", "entity")
            latest_version = int(template.get("version", 1))
            table_name = sm.get_table_name(template_value, config, latest_version)
            base_name = sm.get_table_name(template_value, config)

            # The latest version's table is what the next write against
            # latest lands in — its presence and shape are the structural
            # gate. Older version tables are listed; their columns were
            # shaped by their own version's fields at creation time.
            row.table_present = await sm.table_exists(schema, table_name)
            if row.table_present:
                existing = await sm.get_existing_columns(schema, table_name)
                expected = sm.expected_columns_for_template(fields, config, usage=usage)
                row.missing_columns = sorted(expected - existing)

            version_tables = await sm.list_version_tables(namespace, template_value, config)
            row.version_tables = sorted(version_tables)
            bare_kind = await sm.relation_kind(schema, base_name)
            row.view_present = bare_kind == "view"
            row.legacy_table = bare_kind == "table"

            if result.bookkeeping_tables_ok:
                async with pool.acquire() as conn:
                    row.bookkeeping_row = bool(await conn.fetchval(
                        "SELECT EXISTS (SELECT 1 FROM _wip_schema_migrations "
                        "WHERE namespace = $1 AND template_value = $2)",
                        namespace, template_value,
                    ))

            if include_counts and row.table_present:
                strategy = str(getattr(config, "sync_strategy", "latest_only"))
                if "all_versions" in strategy:
                    row.counts_comparable = False
                else:
                    row.expected_documents = await _expected_document_total(
                        template.get("id") or template.get("template_id") or template_value
                    )
                    # A document lives in exactly one version table under
                    # latest_only — the entity count is the sum across them.
                    total_rows = 0
                    async with pool.acquire() as conn:
                        for _v, vt in version_tables.items():
                            total_rows += int(await conn.fetchval(
                                f'SELECT count(*) FROM "{schema}"."{vt}"'
                            ) or 0)
                    row.actual_rows = total_rows
                    row.counts_match = row.expected_documents == row.actual_rows
        except Exception as e:  # per-template isolation: one bad template must not hide the rest
            row.error = str(e)
            logger.warning(f"Parity check failed for {namespace}/{template_value}: {e}")

        if not row.structural_ok:
            result.structural_issues += 1
        if row.counts_match is False:
            result.count_mismatches += 1
        result.templates.append(row)

    result.ok = (
        result.bookkeeping_tables_ok
        and result.structural_issues == 0
        and result.count_mismatches == 0
    )
    return result

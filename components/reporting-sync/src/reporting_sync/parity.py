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

Beyond the document tables it covers everything else reporting derives for
a namespace, because a check that passes while those are empty is the
failure it exists to catch:
- each view registered in ``_wip_views`` exists in the namespace's schema
  (always checked — presence is structure);
- with counts: active ``terms`` and ``term_relations`` rows match def-store's
  active totals, every active term carrying names has ``term_names`` rows,
  and ``term_closure`` holds the reflexive rows its rebuild writes (one per
  active term per relation type in use) — so a closure that was never
  rebuilt, or is stale against its term set or the relation types in use,
  shows. A closure missing only non-reflexive edges does NOT show here; a
  failed rebuild is retried by the maintainer until it lands. Right after
  a term or relation write the closure lags by its rebuild debounce.

Honest limits, by design:
- Extra actual columns are never an issue (sync only ever adds). A template
  that extends another is checked against its resolved form, inherited
  fields included.
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
    entity count), leaving out rows of soft-deleted documents.
    ``expected_documents`` counts documents whose latest version is active,
    once each. Both sides count live documents. The field names and their pass/fail meaning are stable
    on purpose: the restore engine's structural gate and count-parity
    phases consume them.
    """

    template_value: str
    # The template's own namespace when another namespace owns it (a shared
    # template the checked namespace's documents use); null for its own.
    template_namespace: str | None = None
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
        """An owned template needs its latest version's table. A foreign
        template is checked only because the namespace has live documents on
        it; their tables are made lazily per pinned version, so it needs a
        version table, and the latest one's shape when that one exists."""
        present = self.table_present if self.template_namespace is None else bool(
            self.version_tables
        )
        return (
            present
            and not self.missing_columns
            and not self.legacy_table
            and self.error is None
        )

    @property
    def derived_ok(self) -> bool:
        """The entity view a template's tables are queried through, and its
        bookkeeping row. Not part of structural_ok, which a restore reads
        between phases."""
        return not self.version_tables or (self.view_present and self.bookkeeping_row)


class DerivedTableParity(BaseModel):
    """Parity of one fixed-schema table reporting derives from def-store.

    ``issue`` is a stable code when the table does not hold what it should:
    ``terms_mismatch`` / ``term_relations_mismatch`` (active rows differ
    from def-store's active total), ``term_names_missing`` (active terms
    with names but no name rows; ``expected_rows`` counts such terms and
    ``actual_rows`` how many have rows), ``term_closure_stale`` (reflexive
    rows differ from what a rebuild writes), ``check_failed`` (the check
    itself could not run — ``error`` says why).
    """

    table: str
    expected_rows: int | None = None
    actual_rows: int | None = None
    issue: str | None = None
    error: str | None = None


class ViewParity(BaseModel):
    """A view registered for the namespace, and whether it exists as one."""

    view_name: str
    present: bool


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
    derived_tables: list[DerivedTableParity] = Field(default_factory=list)
    views: list[ViewParity] = Field(default_factory=list)
    # Failing derived tables plus missing registered views. Kept apart from
    # structural_issues, which the restore's structure gate reads between
    # phases — before the term data and views are back.
    derived_issues: int = 0
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
            for item in items:
                # The listing serves templates as stored; one that extends
                # another is re-read resolved, the shape its tables are built
                # from (inherited fields included).
                if item.get("extends"):
                    resolved = await client.get(
                        f"{settings.template_store_url}/api/template-store/templates/"
                        f"{item['template_id']}",
                        params={"version": str(item.get("version", 1))},
                        headers={"X-API-Key": settings.api_key},
                        timeout=30.0,
                    )
                    resolved.raise_for_status()
                    item = resolved.json()
                templates.append(item)
            if len(items) < 100:
                return templates
            page += 1


async def _fetch_foreign_templates(
    namespace: str, owned: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[TemplateParity]]:
    """The templates of OTHER namespaces the namespace's live documents use,
    and a row with an error for each one that could not be read.

    Reporting tables live in the document's namespace schema, so a shared
    template materialises tables here too; the owned listing cannot see it.
    Document Store's template facets name them (live documents, each
    template's own namespace). A foreign template sharing an owned one's
    value is left out: they name the same tables, and the owned template
    defines them. Foreign templates sharing a value with each other (two
    namespaces' ORDER) share their tables too: one is checked, and its count
    covers the documents of all of them (``_count_template_ids``). Each is
    read at its latest version, resolved. A facet listing that fails raises
    (the caller reports the sweep as not done); a template that cannot be
    read is a row with the reason, so one bad facet hides nothing else."""
    owned_values = {str(t.get("value", "")).lower() for t in owned}
    owned_ids = {t.get("template_id") for t in owned}
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{settings.document_store_url}/api/document-store/documents/template-facets",
            params={"namespace": namespace, "status": "active"},
            headers={"X-API-Key": settings.api_key},
            timeout=30.0,
        )
        response.raise_for_status()
        by_value: dict[str, dict[str, Any]] = {}
        failed: list[TemplateParity] = []
        for facet in response.json().get("facets", []):
            value = str(facet.get("template_value") or "").lower()
            if facet.get("template_id") in owned_ids or not facet.get("document_count") or value in owned_values:
                continue
            if value in by_value:
                by_value[value]["_count_template_ids"].append(facet["template_id"])
                continue
            item = await _read_foreign_template(client, facet, failed)
            if item is not None:
                by_value[value] = item
    return list(by_value.values()), failed


async def _read_foreign_template(
    client: httpx.AsyncClient, facet: dict[str, Any], failed: list[TemplateParity],
) -> dict[str, Any] | None:
    """The facet's template at its latest version, or None with a row in
    ``failed`` saying why it could not be read."""
    try:
        template = await client.get(
            f"{settings.template_store_url}/api/template-store/templates/{facet['template_id']}",
            headers={"X-API-Key": settings.api_key},
            timeout=30.0,
        )
        template.raise_for_status()
    except httpx.HTTPError as e:
        gone = isinstance(e, httpx.HTTPStatusError) and e.response.status_code == 404
        failed.append(TemplateParity(
            template_value=str(facet.get("template_value") or facet["template_id"]),
            template_namespace=facet.get("template_namespace"),
            error=(
                f"template {facet['template_id']} not found, though live documents use it"
                if gone else f"template-store unavailable: {e}"
            ),
        ))
        return None
    item = template.json()
    item["_foreign_namespace"] = item.get("namespace") or facet.get("template_namespace")
    item["_count_template_ids"] = [facet["template_id"]]
    return item


async def _expected_document_total(template_id: str, namespace: str) -> int:
    """How many of the namespace's documents of the template are live: those
    whose LATEST version is active, one per document.

    That is what a latest_only reporting table holds a live row for. Counting
    active version rows instead would count a document twice when several of
    its versions are active (a merge with older_versions=keep leaves that),
    and the comparison would fail on correct data.

    Only the namespace's own documents: a document lives in its own
    namespace's reporting schema, so documents another namespace created on
    this template are never rows here, and counting them would report a gap
    no sync can close.
    """
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{settings.document_store_url}/api/document-store/documents",
            params={"template_id": template_id, "namespace": namespace,
                    "status": "active", "latest_only": "true",
                    "page": 1, "page_size": 1},
            headers={"X-API-Key": settings.api_key},
            timeout=30.0,
        )
        response.raise_for_status()
        return int(response.json().get("total", 0))


async def _expected_active_terms(namespace: str) -> int:
    """How many active terms def-store holds for the namespace.

    Summed per terminology over the same terminology listing the term batch
    sync walks, asking each for its active total. The terminology's own
    term_count is not used: it is a denormalized counter, and this check
    must not trust a second copy.
    """
    total = 0
    async with httpx.AsyncClient() as client:
        page = 1
        terminology_ids: list[str] = []
        while True:
            response = await client.get(
                f"{settings.def_store_url}/api/def-store/terminologies",
                params={"namespace": namespace, "page": page, "page_size": 100},
                headers={"X-API-Key": settings.api_key},
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()
            terminology_ids.extend(t["terminology_id"] for t in data.get("items", []))
            if page >= data.get("pages", 1):
                break
            page += 1
        for tid in terminology_ids:
            response = await client.get(
                f"{settings.def_store_url}/api/def-store/terminologies/{tid}/terms",
                params={"namespace": namespace, "status": "active",
                        "page": 1, "page_size": 1},
                headers={"X-API-Key": settings.api_key},
                timeout=30.0,
            )
            response.raise_for_status()
            total += int(response.json().get("total", 0))
    return total


async def _expected_active_relations(namespace: str) -> int:
    """How many active term relations def-store holds for the namespace —
    the listing the relation batch sync pages through."""
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{settings.def_store_url}/api/def-store/ontology/term-relations/all",
            params={"namespace": namespace, "status": "active",
                    "page": 1, "page_size": 1},
            headers={"X-API-Key": settings.api_key},
            timeout=30.0,
        )
        response.raise_for_status()
        return int(response.json().get("total", 0))


async def _count_active(conn: asyncpg.Connection, schema: str, table: str, namespace: str) -> int:
    """Active rows of a namespace in one of its fixed-schema tables; 0 when
    the table was never created (nothing synced yet)."""
    if not await conn.fetchval("SELECT to_regclass($1) IS NOT NULL", f'"{schema}"."{table}"'):
        return 0
    return int(await conn.fetchval(
        f'SELECT count(*) FROM "{schema}"."{table}" '  # nosec B608  # schema from _safe_ident, table a code constant, values bound
        "WHERE namespace = $1 AND status = 'active'",
        namespace,
    ) or 0)


async def _term_names_parity(conn: asyncpg.Connection, schema: str, namespace: str) -> DerivedTableParity:
    """Every active term that carries names must have its name rows.

    ``terms.aliases`` holds every name's value, written in the same sync
    step as the term's ``term_names`` rows, so a term with aliases and no
    name rows is a sync that stopped half way."""
    row = DerivedTableParity(table="term_names", expected_rows=0, actual_rows=0)
    if not await conn.fetchval("SELECT to_regclass($1) IS NOT NULL", f'"{schema}"."terms"'):
        return row
    has_names = await conn.fetchval(
        "SELECT to_regclass($1) IS NOT NULL", f'"{schema}"."term_names"'
    )
    with_rows = (
        f"""count(*) FILTER (WHERE EXISTS (
               SELECT 1 FROM "{schema}"."term_names" n
               WHERE n.namespace = t.namespace AND n.term_id = t.term_id))"""  # nosec B608  # schema from _safe_ident
        if has_names else "0"
    )
    counts = await conn.fetchrow(
        f"""
        SELECT count(*) AS named, {with_rows} AS with_rows
        FROM "{schema}"."terms" t
        WHERE t.namespace = $1 AND t.status = 'active'
          AND jsonb_array_length(coalesce(t.aliases, '[]'::jsonb)) > 0
        """,  # nosec B608  # schema from _safe_ident, values bound
        namespace,
    )
    row.expected_rows, row.actual_rows = int(counts["named"]), int(counts["with_rows"])
    if row.expected_rows != row.actual_rows:
        row.issue = "term_names_missing"
    return row


async def _term_closure_parity(conn: asyncpg.Connection, schema: str, namespace: str) -> DerivedTableParity:
    """The closure must hold the reflexive rows its rebuild writes: one per
    active term per relation type in use (active relations, plus is_a when
    an active term has a parent). A closure that was never rebuilt, or
    predates terms or relation types added since, holds a different number.
    """
    row = DerivedTableParity(table="term_closure")
    has = {
        t: await conn.fetchval("SELECT to_regclass($1) IS NOT NULL", f'"{schema}"."{t}"')
        for t in ("terms", "term_relations", "term_closure")
    }
    if not has["terms"]:
        expected = 0
    else:
        relation_types = (
            f'SELECT relation_type FROM "{schema}"."term_relations" '  # nosec B608  # schema from _safe_ident, values bound
            "WHERE namespace = $1 AND status = 'active' UNION "
            if has["term_relations"] else ""
        )
        expected = int(await conn.fetchval(
            f"""
            SELECT (SELECT count(*) FROM "{schema}"."terms"
                    WHERE namespace = $1 AND status = 'active')
                 * (SELECT count(*) FROM (
                        {relation_types}
                        SELECT 'is_a' FROM "{schema}"."terms"
                        WHERE namespace = $1 AND status = 'active'
                          AND parent_term_id IS NOT NULL
                    ) types)
            """,  # nosec B608  # schema from _safe_ident, values bound
            namespace,
        ) or 0)
    actual = 0
    if has["term_closure"]:
        actual = int(await conn.fetchval(
            f'SELECT count(*) FROM "{schema}"."term_closure" '  # nosec B608  # schema from _safe_ident, values bound
            "WHERE namespace = $1 AND depth = 0",
            namespace,
        ) or 0)
    row.expected_rows, row.actual_rows = expected, actual
    if expected != actual:
        row.issue = "term_closure_stale"
    return row


async def _registered_views_parity(
    sm: SchemaManager, schema: str, namespace: str
) -> list[ViewParity]:
    try:
        names = [n for n, _ in await sm.registered_views(namespace)]
    except asyncpg.UndefinedTableError:
        return []  # no bookkeeping table yet: nothing was ever registered
    return [
        ViewParity(view_name=name, present=await sm.relation_kind(schema, name) == "view")
        for name in names
    ]


async def _derived_table_parity(
    pool: asyncpg.Pool, schema: str, namespace: str
) -> list[DerivedTableParity]:
    """The term tables, each checked independently: one failing check must
    not hide the others."""
    rows: list[DerivedTableParity] = []

    for table, expected_fn, issue in (
        ("terms", _expected_active_terms, "terms_mismatch"),
        ("term_relations", _expected_active_relations, "term_relations_mismatch"),
    ):
        row = DerivedTableParity(table=table)
        try:
            row.expected_rows = await expected_fn(namespace)
            async with pool.acquire() as conn:
                row.actual_rows = await _count_active(conn, schema, table, namespace)
            if row.expected_rows != row.actual_rows:
                row.issue = issue
        except Exception as e:  # def-store down or postgres error: say so, keep going
            row.issue, row.error = "check_failed", str(e)
            logger.warning(f"Parity check of {namespace}/{table} failed: {e}")
        rows.append(row)

    for table, check in (
        ("term_names", _term_names_parity),
        ("term_closure", _term_closure_parity),
    ):
        try:
            async with pool.acquire() as conn:
                rows.append(await check(conn, schema, namespace))
        except Exception as e:
            rows.append(DerivedTableParity(table=table, issue="check_failed", error=str(e)))
            logger.warning(f"Parity check of {namespace}/{table} failed: {e}")
    return rows


async def _templates_to_check(
    namespace: str, result: NamespaceParityResult,
) -> list[dict[str, Any]] | None:
    """The namespace's own templates and the shared ones its documents use;
    None when its own cannot be listed (recorded on ``result``).

    The shared templates' sweep can fail on its own: the namespace's own
    templates are still checked, and the failure is reported on ``result``,
    never read as "no shared templates". A shared template that cannot be
    read is a row with the reason."""
    try:
        templates = await _fetch_namespace_templates(namespace)
    except httpx.HTTPError as e:
        result.bookkeeping_error = (result.bookkeeping_error or "") + (
            f" template-store unavailable: {e}"
        )
        return None
    try:
        foreign, unreadable = await _fetch_foreign_templates(namespace, templates)
    except httpx.HTTPError as e:
        result.bookkeeping_error = (result.bookkeeping_error or "") + (
            f" shared templates not checked (document-store unavailable: {e})"
        )
        return templates
    result.templates.extend(unreadable)
    result.structural_issues += len(unreadable)
    return templates + foreign


async def check_namespace_parity(
    pool: asyncpg.Pool,
    namespace: str,
    include_counts: bool = True,
) -> NamespaceParityResult:
    """Run the parity check for one namespace."""
    sm = SchemaManager(pool)
    schema = sm.schema_for(namespace)
    result = await _schema_presence(pool, namespace, schema)
    await _check_bookkeeping(pool, namespace, result)

    templates = await _templates_to_check(namespace, result)
    if templates is None:
        result.ok = False
        return result

    for template in templates:
        config = ReportingConfig(**(template.get("reporting") or {}))
        if not config.syncs:
            result.templates_skipped_sync_disabled += 1
            continue
        row = await _template_parity(
            pool, sm, schema, namespace, template, config,
            include_counts=include_counts,
            bookkeeping_ok=result.bookkeeping_tables_ok,
        )
        if not row.structural_ok:
            result.structural_issues += 1
        if row.counts_match is False:
            result.count_mismatches += 1
        result.templates.append(row)

    await _derived_parity(pool, sm, schema, namespace, result, include_counts=include_counts)
    result.derived_issues += sum(1 for t in result.templates if not t.derived_ok)
    result.ok = _all_clear(result)
    return result


def _all_clear(result: NamespaceParityResult) -> bool:
    """Usable bookkeeping, and no structural, count or derived issue; and
    the namespace's schema present wherever anything is expected in it."""
    expects_schema = bool(result.templates) or bool(result.views)
    return (
        result.bookkeeping_tables_ok
        and result.bookkeeping_error is None
        and (result.schema_present or not expects_schema)
        and result.structural_issues == 0
        and result.count_mismatches == 0
        and result.derived_issues == 0
    )


async def _derived_parity(
    pool: asyncpg.Pool,
    sm: SchemaManager,
    schema: str,
    namespace: str,
    result: NamespaceParityResult,
    *,
    include_counts: bool,
) -> None:
    """The registered views and, with counts, the derived tables; and how
    many of them have an issue."""
    result.views = await _registered_views_parity(sm, schema, namespace)
    if include_counts:
        result.derived_tables = await _derived_table_parity(pool, schema, namespace)
    result.derived_issues = (
        sum(1 for t in result.derived_tables if t.issue)
        + sum(1 for v in result.views if not v.present)
    )


async def _schema_presence(
    pool: asyncpg.Pool, namespace: str, schema: str,
) -> NamespaceParityResult:
    """The namespace's result, started with whether its schema exists and
    how many tables it holds."""
    async with pool.acquire() as conn:
        schema_present = bool(await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM information_schema.schemata "
            "WHERE schema_name = $1)", schema,
        ))
        table_count = int(await conn.fetchval(
            "SELECT count(*) FROM pg_tables WHERE schemaname = $1", schema,
        ) or 0)
    return NamespaceParityResult(
        namespace=namespace,
        schema_name=schema,
        schema_present=schema_present,
        table_count=table_count,
    )


async def _check_bookkeeping(
    pool: asyncpg.Pool, namespace: str, result: NamespaceParityResult,
) -> None:
    """Whether the bookkeeping tables are namespace-keyed.

    They must be for any of the per-template rows to be meaningful. A
    database whose tables predate the namespace re-keying fails HERE,
    loudly, with remediation — instead of every doc-type sync failing one by
    one. No bookkeeping table at all is a genuinely fresh database;
    init_postgres_schema creates it at service startup.
    """
    async with pool.acquire() as conn:
        try:
            await conn.fetchval(
                "SELECT count(*) FROM public._wip_schema_migrations WHERE namespace = $1",
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
            pass


async def _template_parity(
    pool: asyncpg.Pool,
    sm: SchemaManager,
    schema: str,
    namespace: str,
    template: dict[str, Any],
    config: ReportingConfig,
    *,
    include_counts: bool,
    bookkeeping_ok: bool,
) -> TemplateParity:
    """One template's row. A failure is recorded on the row: one bad
    template must not hide the rest."""
    template_value = template.get("value", "")
    row = TemplateParity(
        template_value=template_value,
        template_namespace=template.get("_foreign_namespace"),
    )
    try:
        version_tables = await _template_structure(sm, row, schema, namespace, template, config)
        if bookkeeping_ok:
            async with pool.acquire() as conn:
                row.bookkeeping_row = bool(await conn.fetchval(
                    "SELECT EXISTS (SELECT 1 FROM public._wip_schema_migrations "
                    "WHERE namespace = $1 AND template_value = $2)",
                    namespace, template_value,
                ))
        if include_counts and (row.table_present or row.version_tables):
            await _template_counts(pool, row, schema, namespace, template, config, version_tables)
    except Exception as e:
        row.error = str(e)
        logger.warning(f"Parity check failed for {namespace}/{template_value}: {e}")
    return row


async def _template_structure(
    sm: SchemaManager,
    row: TemplateParity,
    schema: str,
    namespace: str,
    template: dict[str, Any],
    config: ReportingConfig,
) -> dict[int, str]:
    """Fill the row's structural findings; the template's version tables.

    The latest version's table is what the next write against latest lands
    in — its presence and shape are the structural gate. Older version
    tables are listed; their columns were shaped by their own version's
    fields at creation time.
    """
    template_value = row.template_value
    fields = SchemaManager.parse_template_fields(template.get("fields", []))
    usage = template.get("usage", "entity")
    latest_version = int(template.get("version", 1))
    table_name = sm.get_table_name(template_value, config, latest_version)
    base_name = sm.get_table_name(template_value, config)

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
    return version_tables


async def _template_counts(
    pool: asyncpg.Pool,
    row: TemplateParity,
    schema: str,
    namespace: str,
    template: dict[str, Any],
    config: ReportingConfig,
    version_tables: dict[int, str],
) -> None:
    """Compare the template's live documents with its rows.

    Not comparable under all_versions. Under latest_only a document lives in
    exactly one version table — the entity count is the sum across them. A
    soft-deleted document keeps its row with status 'deleted'; it is not
    live, so it is not counted.
    """
    strategy = str(getattr(config, "sync_strategy", "latest_only"))
    if "all_versions" in strategy:
        row.counts_comparable = False
        return
    # Same-valued foreign templates share these tables: count all their
    # documents (see _fetch_foreign_templates).
    template_ids = template.get("_count_template_ids") or [
        template.get("id") or template.get("template_id") or row.template_value
    ]
    row.expected_documents = sum(
        [await _expected_document_total(tid, namespace) for tid in template_ids]
    )
    total_rows = 0
    async with pool.acquire() as conn:
        for vt in version_tables.values():
            total_rows += int(await conn.fetchval(
                f'SELECT count(*) FROM "{schema}"."{vt}" '  # nosec B608  # identifiers validated by _safe_ident / ensure_*, values bound
                "WHERE status <> 'deleted'"
            ) or 0)
    row.actual_rows = total_rows
    row.counts_match = row.expected_documents == row.actual_rows

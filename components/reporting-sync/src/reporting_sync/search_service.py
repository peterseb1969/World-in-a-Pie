"""
Search Service for Reporting Sync.

Provides unified search across all WIP services and reverse lookups.
"""

import asyncio
import html
import logging
import math
import re
from datetime import UTC, datetime
from typing import Any, Literal, cast
from urllib.parse import quote

import asyncpg
import httpx
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

from .config import settings

logger = logging.getLogger(__name__)


class StrictModel(BaseModel):
    """Base for API request models — rejects unknown fields."""
    model_config = ConfigDict(extra='forbid')


# =============================================================================
# MODELS
# =============================================================================


class SearchResult(BaseModel):
    """A single search result."""

    type: str = Field(..., description="Entity type: terminology, term, template, document")
    id: str = Field(..., description="Entity ID")
    value: str | None = Field(default=None, description="Entity value (if applicable)")
    label: str | None = Field(default=None, description="Entity label")
    status: str | None = Field(default=None, description="Entity status")
    description: str | None = Field(default=None, description="Brief description or context")
    updated_at: datetime | None = Field(default=None, description="Last update time")
    score: float | None = Field(
        default=None,
        description="Relevance score (FTS only — ts_rank). Absent for substring matches.",
    )
    snippet: str | None = Field(
        default=None,
        description=(
            "Highlighted excerpt around match (FTS only — ts_headline). "
            "HTML by default: the document's text HTML-escaped, with "
            "<b>...</b> around hits. Pass snippet_format='text' on the "
            "request for plain, unescaped text."
        ),
    )
    # The hit's template, for naming it after the search (never served).
    _template_id: str | None = PrivateAttr(default=None)


class SearchTypeResults(BaseModel):
    """Per-type paginated bucket.

    Each entity type the search visited gets its own envelope so consumers
    can paginate per type (e.g., "give me page 2 of document hits without
    re-fetching the terminology hits"). Mirrors the platform-wide
    pagination envelope — see `wip://conventions`.
    """

    items: list[SearchResult] = Field(default_factory=list)
    total: int = 0
    page: int = 1
    page_size: int = 50
    pages: int = 0
    complete: bool = Field(
        True,
        description=(
            "False when this type's search could not look everywhere: a "
            "source failed (no items) or answered only part of what it holds "
            "(items found so far). `error` says why. An empty, complete "
            "bucket means nothing matched."
        ),
    )
    error: str | None = Field(
        default=None, description="Why the bucket is incomplete; null when complete."
    )


class SearchResponse(BaseModel):
    """Response from unified search.

    The response groups results per entity type, each with
    its own pagination envelope (see `SearchTypeResults`). The legacy
    flat `results: list` + `counts: dict` shape was removed at the same
    time. Consumers wanting "all hits across types" iterate the values
    of `results`.
    """

    query: str
    mode: str | None = Field(
        default=None,
        description=(
            "Echo of the document-search mode used ('auto', 'fts', or "
            "'substring'). Informational only."
        ),
    )
    results: dict[str, SearchTypeResults] = Field(
        default_factory=dict,
        description=(
            "Per-type paginated buckets keyed by entity type "
            "('terminology', 'term', 'template', 'document', 'file'). "
            "Only types the search visited appear here."
        ),
    )
    total: int = Field(
        0,
        description="Total hits across all types (sum of per-type totals).",
    )
    unmatched_template: str | None = Field(
        default=None,
        description=(
            "Echo of the requested `template` when it matched no reporting "
            "table, so a caller can tell 'this type has nothing' apart from "
            "'this type is not a thing here' — an empty result set alone says "
            "both. NOT an assertion that the template does not exist: "
            "reporting knows tables, not templates, and a template with "
            "sync_enabled=false or nothing yet synced legitimately has no "
            "table. Null whenever the filter matched, or none was given."
        ),
    )


class SearchRequest(StrictModel):
    """Request for unified search.

    Pagination follows the platform-wide convention — see
    `wip://conventions` (page/page_size with max 100 per page). Each
    entity type the search visits is paginated independently; the same
    `page`/`page_size` is applied to every type. The legacy `limit`
    parameter is accepted as a deprecation-window alias for `page_size`:
    it is used only when `page_size` is not given, and an explicit
    `page_size` always wins.
    """

    query: str = Field(..., min_length=1, description="Search string")
    types: list[Literal["terminology", "term", "template", "document", "file"]] | None = Field(
        default=None,
        description=(
            "Entity types to search: terminology, term, template, document, "
            "file (all when omitted). Any other value is refused (422)."
        ),
    )
    namespace: str | None = Field(default=None, description="Filter by namespace")
    status: str | None = Field(default=None, description="Filter by status")
    template: str | None = Field(
        default=None,
        description=(
            "Restrict document search to a single template (by value). "
            "Other entity types ignore this filter."
        ),
    )
    mode: Literal["auto", "fts", "substring"] = Field(
        "auto",
        description=(
            "Document-search strategy. 'auto' (default) uses tsvector "
            "indexing on tables that have it and falls back to substring "
            "elsewhere. 'fts' forces tsvector — tables without indexed "
            "fields are skipped. 'substring' is the legacy ILIKE path "
            "and works on every doc_* table regardless of indexing."
        ),
    )
    include_inactive: bool = Field(
        False,
        description=(
            "When false (default), only active documents are returned — "
            "aligns with PoNIF #1 'inactive means retired, not deleted'. "
            "Set true to surface inactive docs in the result set."
        ),
    )
    snippet_format: Literal["html", "text"] = Field(
        "html",
        description=(
            "Snippet rendering for FTS hits. 'html' (default) wraps "
            "matched terms with <b>...</b>. 'text' returns plain text."
        ),
    )
    page: int = Field(
        1,
        ge=1,
        description="Page number (1-indexed). See `wip://conventions`.",
    )
    page_size: int = Field(
        50,
        ge=1,
        le=100,
        description=(
            "Items per type, capped at 100 to align with the platform-wide "
            "pagination max (see `wip://conventions`). For broader recall "
            "use a refined query; for full enumeration use the paginated "
            "query endpoints (e.g., query_documents)."
        ),
    )
    limit: int | None = Field(
        default=None,
        ge=1,
        le=100,
        description=(
            "DEPRECATED: alias for `page_size`, used only when `page_size` "
            "is not given. Pass `page_size` instead. Will be removed in a "
            "future release."
        ),
    )


class ActivityItem(BaseModel):
    """A single activity/change item."""

    type: str = Field(..., description="Entity type")
    action: str = Field(..., description="Action: created, updated, deleted, deprecated")
    entity_id: str
    entity_value: str | None = None
    entity_label: str | None = None
    timestamp: datetime
    user: str | None = None
    version: int | None = None
    details: dict[str, Any] | None = None


class ActivityResponse(BaseModel):
    """Response from recent activity endpoint."""

    activities: list[ActivityItem] = Field(default_factory=list)
    total: int = 0
    incomplete_sources: list[str] = Field(
        default_factory=list,
        description=(
            "Entity types whose source could not be read (its service down or "
            "answering an error): their recent activity is missing from "
            "`activities`. Empty when every requested source answered."
        ),
    )


class DocumentReference(BaseModel):
    """A document that references a term."""

    document_id: str
    template_id: str
    template_value: str | None = None
    field_path: str
    status: str
    created_at: datetime | None = None


class TermDocumentsResponse(BaseModel):
    """Response from term → documents lookup."""

    term_id: str
    documents: list[DocumentReference] = Field(default_factory=list)
    total: int = 0


class EntityReference(BaseModel):
    """A reference from one entity to another."""

    ref_type: str = Field(..., description="Type of reference: template, terminology, term")
    ref_id: str = Field(..., description="Referenced entity ID")
    ref_value: str | None = Field(default=None, description="Referenced entity value")
    ref_label: str | None = Field(default=None, description="Referenced entity label")
    field_path: str | None = Field(default=None, description="Field that holds the reference")
    status: str = Field(..., description="Reference status: valid, broken, inactive")
    error: str | None = Field(default=None, description="Error message if broken")


class EntityDetails(BaseModel):
    """Full entity details with reference information."""

    entity_type: str
    entity_id: str
    entity_value: str | None = None
    entity_label: str | None = None
    entity_status: str | None = None
    namespace: str | None = Field(
        default=None, description="Namespace the entity lives in"
    )
    version: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    # Raw entity data
    data: dict[str, Any] | None = None
    # Outgoing references (what this entity references)
    references: list[EntityReference] = Field(default_factory=list)
    # Summary counts
    valid_refs: int = 0
    broken_refs: int = 0
    inactive_refs: int = 0


class EntityReferencesResponse(BaseModel):
    """Response from entity references lookup."""

    entity: EntityDetails | None = None
    error: str | None = None


class IncomingReference(BaseModel):
    """An entity that references the target entity."""

    entity_type: str = Field(..., description="Type: document, template")
    entity_id: str = Field(..., description="ID of the referencing entity")
    entity_value: str | None = Field(default=None, description="Value (for templates)")
    entity_label: str | None = Field(default=None, description="Label or description")
    entity_status: str | None = Field(default=None, description="Status of the referencing entity")
    field_path: str | None = Field(default=None, description="Field containing the reference")
    reference_type: str = Field(..., description="How it references: uses_template, extends, terminology_ref, template_ref, term_ref")


class ReferencedByResponse(BaseModel):
    """Response from 'referenced by' lookup."""

    entity_type: str
    entity_id: str
    entity_value: str | None = None
    entity_label: str | None = None
    referenced_by: list[IncomingReference] = Field(default_factory=list)
    total: int = 0
    error: str | None = None
    # Where the target lives, for the route's read check on it, and whether
    # its store said it does not exist. Never served.
    _target_namespace: str | None = PrivateAttr(default=None)
    _target_missing: bool = PrivateAttr(default=False)


# =============================================================================
# SEARCH SERVICE
# =============================================================================

# Per-type ceiling on results held in memory. CASE-329 chose per-type
# pagination as the platform-conformant shape; a hard cap keeps the
# memory cost bounded and tells consumers when a query is too broad to
# enumerate fully. Anything above this cap returns `total = MAX` with
# pagination over the first MAX hits — refine the query for more.
MAX_RESULTS_PER_TYPE = 1000

# Physical reporting tables are per template VERSION (doc_<value>__v<N>); the
# bare name is an entity view over them. Search must read the physical tables —
# the tsvector columns FTS matches on exist only there, not on the view — but a
# caller asks about a logical type, so neither the filter nor the reported type
# may carry the version. Without this, `template=CASE_RECORD` built the exact
# name `doc_case_record`, matched no BASE TABLE post-split, and returned zero
# hits, while results reported themselves as `CASE_RECORD__V1` — a filter value
# that changes on every template version event.
_VERSION_SUFFIX_RE = re.compile(r"__v\d+$")


def entity_base(table_name: str) -> str:
    """Logical entity name for a reporting table: doc_case_record__v2 -> case_record."""
    base = table_name[4:] if table_name.startswith("doc_") else table_name
    return _VERSION_SUFFIX_RE.sub("", base)


def _with_namespace(params: dict[str, Any], namespace: str | None) -> dict[str, Any]:
    """Add a namespace filter to upstream list params when one is given."""
    if namespace:
        return {**params, "namespace": namespace}
    return params


def _paginate(
    items: list["SearchResult"], page: int, page_size: int
) -> tuple[list["SearchResult"], int]:
    """Slice a sorted list of hits into a (page items, total pages) pair."""
    total = len(items)
    start = (page - 1) * page_size
    end = start + page_size
    pages = math.ceil(total / page_size) if total > 0 else 0
    return items[start:end], pages


def _document_hit(row: dict[str, Any], tname: str, fts: bool) -> SearchResult:
    """A document search hit. Its value and label are filled in later from
    the template store (_name_document_templates): a table's name is not
    the template's value (case is lost, and reporting.table_name may
    replace it). The description names the table it matched in, so the
    provenance is not lost. Only FTS hits carry a score and a snippet."""
    score = row.get("score") if fts else None
    hit = SearchResult(
        type="document",
        id=row["doc_id"] or "",
        value=None,
        label=None,
        status=row.get("status") or "active",
        description=f"Matched in {tname}",
        updated_at=row.get("updated_at"),
        score=float(score) if score is not None else None,
        snippet=row.get("snippet") if fts else None,
    )
    hit._template_id = row.get("template_id")
    return hit


# Per-query bound on the search path (catalog reads and per-table queries)
# and on waiting for a reader connection: an interactive search must not
# hold the small reader pool for the reader role's 300 s default.
SEARCH_QUERY_TIMEOUT = 30.0

# Columns every doc_* table carries that are not document content.
_UNSEARCHED_COLUMNS = frozenset({
    "document_id", "namespace", "template_id", "identity_hash", "status",
    "created_by", "updated_by", "source_ref_id", "target_ref_id",
})

# ts_headline marks for HTML snippets: no character HTML-escaping changes,
# so they survive the escaping of the text around them.
_MARK_START = "⁣wip-b⁣"
_MARK_STOP = "⁣/wip-b⁣"


class _SearchColumns:
    """What one table offers a document search."""

    def __init__(
        self, *, tsv: list[str], text: list[str], id_column: str | None,
        has_updated_at: bool, has_template_id: bool,
    ):
        self.tsv = tsv
        self.text = text
        self.id_column = id_column
        self.has_updated_at = has_updated_at
        self.has_template_id = has_template_id


class _DocumentFilters:
    """The row filters and snippet format every table of one search shares."""

    def __init__(
        self, namespace: str | None, status: str | None,
        include_inactive: bool, snippet_format: str,
    ):
        self.namespace = namespace
        self.status = status
        self.include_inactive = include_inactive
        self.snippet_format = snippet_format

    def clauses(self, params: list[Any], prefix: str) -> str:
        """The AND-ed status and namespace conditions, binding values into
        ``params``. An explicit status wins; without one, only active
        documents unless include_inactive."""
        sql = ""
        if self.status:
            params.append(self.status)
            sql += f" AND {prefix}status = ${len(params)}"
        elif not self.include_inactive:
            sql += f" AND {prefix}status = 'active'"
        if self.namespace:
            params.append(self.namespace)
            sql += f" AND {prefix}namespace = ${len(params)}"
        return sql


def _like_literal(text: str) -> str:
    """``text`` with LIKE's wildcards escaped, so it matches literally."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _html_snippet(snippet: str | None) -> str | None:
    """A ts_headline snippet as HTML: the document's text escaped, the
    matched words in <b>…</b>."""
    if snippet is None:
        return None
    return (
        html.escape(snippet, quote=False)
        .replace(_MARK_START, "<b>")
        .replace(_MARK_STOP, "</b>")
    )


class _Hits(list[SearchResult]):
    """One type's hits, and why they may not be all of them."""

    incomplete: str | None = None


def _hits(items: list[SearchResult], incomplete: str | None = None) -> "_Hits":
    hits = _Hits(items)
    hits.incomplete = incomplete
    return hits


def _first_hit_per_document(hits: list[SearchResult]) -> list[SearchResult]:
    """The hits with every document's later rows dropped (input is sorted
    best first)."""
    seen: set[str] = set()
    kept = []
    for hit in hits:
        if hit.id in seen:
            continue
        seen.add(hit.id)
        kept.append(hit)
    return kept


async def _name_document_templates(hits: list[SearchResult]) -> None:
    """Fill each document hit's value and label with its template's value,
    read from the template store. A template it cannot read leaves them
    empty rather than guessed."""
    ids = {h._template_id for h in hits if h._template_id}
    if not ids:
        return
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            values = await _template_values(client, ids)
    except (UpstreamError, httpx.HTTPError) as e:
        logger.warning("Could not name search hits' templates: %s", e)
        return
    for hit in hits:
        value = values.get(hit._template_id or "")
        if value:
            hit.value = value
            hit.label = f"{value} document"


class SearchService:
    """Service for unified search and activity tracking."""

    def __init__(self, postgres_pool: asyncpg.Pool | None = None):
        self.postgres_pool = postgres_pool

    @staticmethod
    def _sort_by_relevance(
        items: list[SearchResult], query_lower: str
    ) -> list[SearchResult]:
        """Sort a per-type result bucket: exact value > exact id > substring > other.

        Preserves the global relevance ranking the pre-CASE-329 cross-type
        sort applied — just scoped to one type at a time now.
        """
        _epoch = datetime.min.replace(tzinfo=UTC)

        def _sort_dt(dt: datetime | None) -> datetime:
            if dt is None:
                return _epoch
            if dt.tzinfo is None:
                return dt.replace(tzinfo=UTC)
            return dt

        items_sorted = sorted(
            items,
            key=lambda r: (
                0 if r.value and r.value.lower() == query_lower else
                1 if r.id and r.id.lower() == query_lower else
                2 if r.value and query_lower in r.value.lower() else
                3,
                # Negate timestamp so newer items rise within each tier.
                -_sort_dt(r.updated_at).timestamp(),
            ),
        )
        return items_sorted

    async def search(
        self, request: SearchRequest, within: list[str] | None = None,
    ) -> SearchResponse:
        """Search across all entity types with per-type pagination (CASE-329).

        Each entity type returns its own paginated envelope keyed by
        type ('terminology', 'term', 'template', 'document', 'file').
        Pagination follows the platform-wide convention — see
        `wip://conventions`.

        ``within``: when the request names no namespace, the namespaces the
        reporting tables are read in (the caller's readable ones). The other
        types are read from their stores, which answer within the readable
        namespaces of reporting-sync's own key.
        """
        query = request.query.strip()
        types = request.types or ["terminology", "term", "template", "document", "file"]
        namespace = request.namespace
        status = request.status
        page = request.page
        # Deprecation alias: `limit` stands in for `page_size` only when
        # the caller did not give `page_size` (an explicit page_size=50 is
        # not "the default"). Remove the alias in a future release.
        page_size = request.page_size
        if request.limit is not None and "page_size" not in request.model_fields_set:
            page_size = request.limit

        # Run searches in parallel. Each search returns the FULL matching
        # set (capped at MAX_RESULTS_PER_TYPE) so the orchestrator can
        # compute accurate per-type totals and slice the requested page.
        tasks = []
        if "terminology" in types:
            tasks.append(("terminology", self._search_terminologies(query, namespace, status)))
        if "term" in types:
            tasks.append(("term", self._search_terms(query, namespace, status)))
        if "template" in types:
            tasks.append(("template", self._search_templates(query, namespace, status)))
        if "document" in types:
            tasks.append(("document", self._search_documents(
                query, namespace, status,
                template=request.template,
                mode=request.mode,
                include_inactive=request.include_inactive,
                snippet_format=request.snippet_format,
                within=within,
            )))
        if "file" in types:
            tasks.append(("file", self._search_files(query, namespace, status)))

        gathered = await asyncio.gather(*[t[1] for t in tasks], return_exceptions=True)

        results: dict[str, SearchTypeResults] = {}
        cross_total = 0
        for (entity_type, _), result in zip(tasks, gathered, strict=False):
            if isinstance(result, BaseException):
                # A source that could not answer is not "no matches".
                logger.error(f"Search failed for {entity_type}: {result}")
                results[entity_type] = SearchTypeResults(
                    items=[], total=0, page=page, page_size=page_size, pages=0,
                    complete=False, error=f"search failed: {result}",
                )
                continue
            items = result
            page_items, pages = _paginate(items, page, page_size)
            total = len(items)
            cross_total += total
            incomplete = getattr(items, "incomplete", None)
            results[entity_type] = SearchTypeResults(
                items=page_items,
                total=total,
                page=page,
                page_size=page_size,
                pages=pages,
                complete=incomplete is None,
                error=incomplete,
            )

        # Only asked on the path that is actually ambiguous: a template filter
        # was given and the document bucket came back empty. Everywhere else
        # the result set already answers the caller's question.
        unmatched_template: str | None = None
        doc_results = results.get("document")
        if (
            request.template
            and doc_results is not None
            and doc_results.total == 0
            and not await self.template_matches_a_table(request.template, namespace, within)
        ):
            unmatched_template = request.template

        return SearchResponse(
            query=query,
            mode=request.mode,
            results=results,
            total=cross_total,
            unmatched_template=unmatched_template,
        )

    async def template_matches_a_table(
        self, template: str, namespace: str | None = None, within: list[str] | None = None,
    ) -> bool:
        """Whether `template` names any reporting table, versioned or not.

        Uses the same logical-name comparison as the search filter itself
        (`entity_base`), so the two cannot disagree about what "matches" means.

        Returns True when PostgreSQL is unavailable: without the reporting
        layer we cannot tell a bad filter from a good one, and claiming
        "unmatched" on no evidence would be the same overreach this signal
        exists to prevent.
        """
        if not self.postgres_pool:
            return True
        want = await _template_table_bases(template, namespace)
        async with self.postgres_pool.acquire() as conn:
            tables = await _document_tables(conn, namespace, within)
        return any(entity_base(r["table_name"]) in want for r in tables)

    async def _search_terminologies(
        self, query: str, namespace: str | None, status: str | None,
    ) -> list[SearchResult]:
        """Search terminologies in Def-Store.

        Returns ALL matching items sorted by relevance (CASE-329). The
        orchestrator slices for the requested page. Capped at
        MAX_RESULTS_PER_TYPE.
        """
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                # Page through def-store until exhausted or cap hit.
                all_items, scanned_all = await _scan_listing(
                    client, f"{settings.def_store_url}/api/def-store/terminologies",
                    {}, namespace, status,
                )

                results = []
                query_lower = query.lower()
                for item in all_items:
                    if (query_lower in item.get("value", "").lower() or
                        query_lower in item.get("label", "").lower() or
                        query_lower in item.get("terminology_id", "").lower()):
                        results.append(SearchResult(
                            type="terminology",
                            id=cast(str, item.get("terminology_id")),
                            value=item.get("value"),
                            label=item.get("label"),
                            status=item.get("status"),
                            description=item.get("description"),
                            updated_at=self._parse_datetime(item.get("updated_at"))
                        ))

                results = self._sort_by_relevance(results, query_lower)
                return _hits(
                    results[:MAX_RESULTS_PER_TYPE],
                    None if scanned_all else
                    f"matched against the first {len(all_items)} entries only",
                )
        except httpx.HTTPError as e:
            raise UpstreamError(f"terminology listing failed: {e}") from e

    async def _search_terms(
        self, query: str, namespace: str | None, status: str | None,
    ) -> list[SearchResult]:
        """Search terms via def-store's ranked search, one call per namespace.

        Def-store's /terms/search scans each namespace's full term corpus
        (value, label and every name) — the old shape here fetched ONE page
        of 100 terms from the first 20 terminologies and substring-matched
        in Python, which made any vocabulary beyond 100 terms effectively
        invisible. namespace=None is the admin cross-namespace case: the
        namespaces are enumerated from the terminology listing (paged
        fully, not one page).

        Returns ALL matching items sorted by relevance (CASE-329).
        Capped at MAX_RESULTS_PER_TYPE; the orchestrator slices.
        """
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                headers = {"X-API-Key": settings.api_key}

                namespaces = [namespace] if namespace else await _term_namespaces(client)

                results = []
                capped: list[str] = []
                for ns in namespaces:
                    params: dict[str, Any] = {
                        "q": query,
                        "namespace": ns,
                        "mode": "substring",
                        "limit": 100,
                    }
                    if status:
                        params["statuses"] = status

                    resp = await client.get(
                        f"{settings.def_store_url}/api/def-store/terms/search",
                        params=params,
                        headers=headers,
                    )
                    if resp.status_code != 200:
                        raise UpstreamError(
                            f"term search in {ns} answered HTTP {resp.status_code}"
                        )

                    hits = resp.json().get("items", [])
                    if len(hits) >= 100:
                        capped.append(ns)
                    for hit in hits:
                        term = hit.get("term", {})
                        context = f"In {term.get('terminology_value')}"
                        if hit.get("matched_via") == "alias":
                            context += f" (matched name: {hit.get('matched_value')})"
                        results.append(SearchResult(
                            type="term",
                            id=term.get("term_id"),
                            value=term.get("value"),
                            label=term.get("label"),
                            status=term.get("status"),
                            description=context,
                            updated_at=self._parse_datetime(term.get("updated_at")),
                        ))

                    if len(results) >= MAX_RESULTS_PER_TYPE and ns != namespaces[-1]:
                        capped.append("(namespaces not searched)")
                        break

                results = self._sort_by_relevance(results, query.lower())
                return _hits(
                    results[:MAX_RESULTS_PER_TYPE],
                    f"more matches than returned in: {', '.join(capped)}" if capped else None,
                )
        except httpx.HTTPError as e:
            raise UpstreamError(f"term search failed: {e}") from e

    async def _search_templates(
        self, query: str, namespace: str | None, status: str | None,
    ) -> list[SearchResult]:
        """Search templates in Template Store.

        Returns ALL matching items sorted by relevance (CASE-329).
        Capped at MAX_RESULTS_PER_TYPE.
        """
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                all_items, scanned_all = await _scan_listing(
                    client, f"{settings.template_store_url}/api/template-store/templates",
                    {"latest_only": True}, namespace, status,
                )

                results = []
                query_lower = query.lower()
                for item in all_items:
                    if (query_lower in item.get("value", "").lower() or
                        query_lower in item.get("label", "").lower() or
                        query_lower in item.get("template_id", "").lower()):
                        results.append(SearchResult(
                            type="template",
                            id=cast(str, item.get("template_id")),
                            value=item.get("value"),
                            label=item.get("label"),
                            status=item.get("status"),
                            description=f"v{item.get('version', 1)}",
                            updated_at=self._parse_datetime(item.get("updated_at"))
                        ))

                results = self._sort_by_relevance(results, query_lower)
                return _hits(
                    results[:MAX_RESULTS_PER_TYPE],
                    None if scanned_all else
                    f"matched against the first {len(all_items)} entries only",
                )
        except httpx.HTTPError as e:
            raise UpstreamError(f"template listing failed: {e}") from e

    async def _search_documents(
        self,
        query: str,
        namespace: str | None,
        status: str | None,
        *,
        template: str | None = None,
        mode: str = "auto",
        include_inactive: bool = False,
        snippet_format: str = "html",
        within: list[str] | None = None,
    ) -> list[SearchResult]:
        """Search documents via PostgreSQL reporting tables.

        Mode dispatch (per table):
          - 'fts': use tsvector + GIN if the table has any *_tsv column;
            else skip the table.
          - 'substring': always ILIKE across the table's data text columns
            (legacy path). System columns (ids, namespace, status, authors)
            are not searched: "active" would match every active row.
          - 'auto': FTS when *_tsv columns exist, substring otherwise.

        Status: an explicit ``status`` returns documents in that status only.
        Without one, only active documents are returned (PoNIF #1), unless
        include_inactive=True.

        Returns ALL FTS+substring hits across doc_* tables sorted by
        score (CASE-329), one hit per document: a document kept in several
        rows (all_versions tables) counts once, at its best-scoring row.
        The orchestrator slices to the requested page; per-table queries are
        capped at MAX_RESULTS_PER_TYPE so a single bad table can't blow
        memory, and each query is bounded by SEARCH_QUERY_TIMEOUT.

        Raises UpstreamError when PostgreSQL is not there to search: the
        document bucket then reports the failure, never "no matches". A
        table whose query fails, or the cap, marks the hits incomplete.
        """
        if not self.postgres_pool:
            raise UpstreamError("PostgreSQL not connected: documents cannot be searched")

        filters = _DocumentFilters(namespace, status, include_inactive, snippet_format)
        skipped: list[str] = []
        capped = False
        async with self.postgres_pool.acquire(timeout=SEARCH_QUERY_TIMEOUT) as conn:
            tables = await self._document_search_tables(conn, namespace, template, within)
            results: list[SearchResult] = []
            for tbl_row in tables:
                remaining = MAX_RESULTS_PER_TYPE - len(results)
                if remaining <= 0:
                    capped = True
                    break
                hits = await self._search_document_table(
                    conn, tbl_row["table_schema"], tbl_row["table_name"], query,
                    mode, filters, remaining,
                )
                if hits is None:
                    skipped.append(f'{tbl_row["table_schema"]}.{tbl_row["table_name"]}')
                    continue
                results += hits

        # FTS results carry scores — sort the merged set by score descending
        # so the best matches across templates rise. Substring matches have
        # no score; they sort after FTS hits.
        results.sort(key=lambda r: (0 if r.score is not None else 1, -(r.score or 0.0)))
        results = _first_hit_per_document(results)[:MAX_RESULTS_PER_TYPE]
        await _name_document_templates(results)
        reasons = []
        if skipped:
            reasons.append(f"tables that could not be searched: {', '.join(skipped)}")
        if capped:
            reasons.append(f"stopped at {MAX_RESULTS_PER_TYPE} hits")
        return _hits(results, "; ".join(reasons) or None)

    async def _document_search_tables(
        self, conn: asyncpg.Connection, namespace: str | None, template: str | None,
        within: list[str] | None = None,
    ) -> list[Any]:
        """The document tables to search, across namespace schemas.

        Each WIP namespace is its own schema; a search with no namespace
        filter spans the schemas of ``within`` (all of them when None), a
        namespace filter narrows to that one schema.
        A document table is recognised by its columns, not its name, so a
        template's reporting.table_name override is searched too. The
        template filter is applied after the fetch, by comparing the
        logical entity name with the template's default base name and its
        override. An exact SQL name match cannot work post-split (the
        physical tables carry __vN), and a LIKE pattern would be wrong too:
        template values contain '_', which LIKE treats as a single-character
        wildcard, so 'doc_case_record__v%' also matches doc_caseXrecord__v1.
        """
        tables = await _document_tables(conn, namespace, within)
        if not template:
            return tables
        want = await _template_table_bases(template, namespace)
        return [r for r in tables if entity_base(r["table_name"]) in want]

    async def _search_document_table(
        self,
        conn: asyncpg.Connection,
        schema: str,
        tname: str,
        query: str,
        mode: str,
        filters: "_DocumentFilters",
        limit: int,
    ) -> list[SearchResult] | None:
        """The hits in one table: FTS when the mode allows it and the table
        has *_tsv columns, else substring (skipped in 'fts' mode, which is a
        choice, not a failure: []). A failing query answers None (the
        caller marks the search incomplete); a failing column discovery
        propagates."""
        columns = await self._search_columns(conn, schema, tname)
        if columns is None:
            return []
        if mode == "fts" and not columns.tsv:
            return []
        use_fts = bool(columns.tsv) and mode in ("auto", "fts")
        try:
            if use_fts:
                rows = await self._fts_query(conn, schema, tname, columns, query, filters, limit)
            else:
                rows = await self._substring_query(
                    conn, schema, tname, columns, query, filters, limit,
                )
        except Exception as table_err:
            logger.warning("Search in table %s failed: %s", tname, table_err)
            return None
        return [_document_hit(row, tname, use_fts) for row in rows]

    @staticmethod
    async def _search_columns(
        conn: asyncpg.Connection, schema: str, tname: str,
    ) -> "_SearchColumns | None":
        """The table's searchable columns, or None when it has no columns."""
        rows = await conn.fetch(
            """
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_schema = $1 AND table_name = $2
            """,
            schema,
            tname,
            timeout=SEARCH_QUERY_TIMEOUT,
        )
        if not rows:
            return None
        names = {r["column_name"] for r in rows}
        return _SearchColumns(
            tsv=[r["column_name"] for r in rows if r["data_type"] == "tsvector"],
            text=[
                r["column_name"] for r in rows
                if r["data_type"] in ("text", "character varying")
                and r["column_name"] not in _UNSEARCHED_COLUMNS
            ],
            id_column=(
                "document_id" if "document_id" in names
                else ("entity_id" if "entity_id" in names else None)
            ),
            has_updated_at="updated_at" in names,
            has_template_id="template_id" in names,
        )

    async def _fts_query(
        self,
        conn: asyncpg.Connection,
        schema: str,
        tname: str,
        columns: "_SearchColumns",
        query: str,
        filters: "_DocumentFilters",
        limit: int,
    ) -> list[dict]:
        """Run a PostgreSQL FTS query against one doc_* table.

        Uses an OR-of-tsvectors WHERE clause so each *_tsv GIN index can
        be hit independently. Rank is computed against the concatenation;
        equal ranks are ordered by document, so every page of a search (each
        re-runs the query) sees the same order. Snippet stitches the matching *_search
        columns together for ts_headline. A table without updated_at
        (include_metadata: false) reports none.
        """
        tsv_cols = columns.tsv
        # OR clause: any indexed column matches.
        or_clause = " OR ".join(f'"{c}" @@ q.query' for c in tsv_cols)
        # Concat of tsvectors for ts_rank.
        rank_expr = " || ".join(f'"{c}"' for c in tsv_cols)
        # Parallel _search columns (drop the _tsv suffix).
        search_cols = [c[: -len("_tsv")] + "_search" for c in tsv_cols]
        # Snippet input — coalesce nulls and join with a separator so
        # ts_headline can pick fragments from any of the indexed sources.
        snippet_input = " || ' ... ' || ".join(
            f"coalesce(\"{c}\", '')" for c in search_cols
        )

        if filters.snippet_format == "text":
            # Empty marks: ts_headline's defaults are <b> and </b>.
            headline_options = 'StartSel="",StopSel="",MaxFragments=2,MaxWords=20,MinWords=5'
        else:
            # Placeholder marks, turned into <b>/</b> after the document
            # text around them is HTML-escaped (_html_snippet).
            headline_options = (
                f'StartSel="{_MARK_START}",StopSel="{_MARK_STOP}",'
                "MaxFragments=2,MaxWords=20,MinWords=5"
            )

        params: list[Any] = [query]
        where = filters.clauses(params, "d.")
        params.append(limit)
        limit_param = f"${len(params)}"
        updated_at = "d.updated_at" if columns.has_updated_at else "NULL::timestamptz"
        template_id = "d.template_id" if columns.has_template_id else "NULL::text"

        sql = f"""
            WITH q AS (SELECT plainto_tsquery('english', $1) AS query)
            SELECT
                d.document_id AS doc_id,
                d.status AS status,
                {template_id} AS template_id,
                {updated_at} AS updated_at,
                ts_rank({rank_expr}, q.query) AS score,
                ts_headline(
                    'english',
                    {snippet_input},
                    q.query,
                    '{headline_options}'
                ) AS snippet
            FROM "{schema}"."{tname}" d, q
            WHERE ({or_clause}){where}
            ORDER BY score DESC, doc_id
            LIMIT {limit_param}
        """  # nosec B608  # catalog identifiers and code-built fragments, values bound
        rows = await conn.fetch(sql, *params, timeout=SEARCH_QUERY_TIMEOUT)
        hits = [dict(r) for r in rows]
        if filters.snippet_format != "text":
            for hit in hits:
                hit["snippet"] = _html_snippet(hit.get("snippet"))
        return hits

    async def _substring_query(
        self,
        conn: asyncpg.Connection,
        schema: str,
        tname: str,
        columns: "_SearchColumns",
        query: str,
        filters: "_DocumentFilters",
        limit: int,
    ) -> list[dict]:
        """Run an ILIKE substring query (legacy path, no tsvector).

        Searches the table's data text columns only; the system columns
        are not content (a document's id is found by reading it, not by
        searching for it). The query text matches literally: % and _ are
        not wildcards. updated_at is returned as NULL since substring
        matches don't carry ranking metadata. Hits are ordered by
        document: physical order changes whenever the sync rewrites a row,
        and every page of a search re-runs the query.
        """
        if not columns.text:
            return []
        or_clauses = " OR ".join(f'"{c}" ILIKE $1' for c in columns.text)
        id_expr = f'"{columns.id_column}"' if columns.id_column else "''"
        template_id = "template_id" if columns.has_template_id else "NULL::text"

        params: list[Any] = [f"%{_like_literal(query)}%"]
        where = filters.clauses(params, "")
        params.append(limit)
        limit_param = f"${len(params)}"

        sql = f"""
            SELECT
                {id_expr} AS doc_id,
                status AS status,
                {template_id} AS template_id,
                NULL::timestamp AS updated_at
            FROM "{schema}"."{tname}"
            WHERE ({or_clauses}){where}
            ORDER BY doc_id
            LIMIT {limit_param}
        """  # nosec B608  # catalog identifiers and code-built fragments, values bound
        rows = await conn.fetch(sql, *params, timeout=SEARCH_QUERY_TIMEOUT)
        return [dict(r) for r in rows]

    async def get_recent_activity(
        self,
        types: list[str] | None = None,
        limit: int = 50,
        namespace: str | None = None,
    ) -> ActivityResponse:
        """
        Get recent activity across all entity types.

        Aggregates recent changes from all services. ``namespace`` restricts
        every source to that namespace; None spans all of them.
        """
        types = types or ["terminology", "term", "template", "document", "file"]
        activities: list[ActivityItem] = []

        # Fetch recent items from each service in parallel
        tasks = []
        if "terminology" in types:
            tasks.append(("terminology", self._get_recent_terminologies(limit, namespace)))
        if "term" in types:
            tasks.append(("term", self._get_recent_terms(limit, namespace)))
        if "template" in types:
            tasks.append(("template", self._get_recent_templates(limit, namespace)))
        if "document" in types:
            tasks.append(("document", self._get_recent_documents(limit, namespace)))
        if "file" in types:
            tasks.append(("file", self._get_recent_files(limit, namespace)))

        results = await asyncio.gather(*[t[1] for t in tasks], return_exceptions=True)

        # A source that could not be read is named, never read as "nothing
        # happened": the caller cannot otherwise tell it from a quiet system.
        incomplete: list[str] = []
        for (entity_type, _), result in zip(tasks, results, strict=False):
            if isinstance(result, BaseException):
                logger.error(f"Activity fetch failed for {entity_type}: {result}")
                incomplete.append(entity_type)
            else:
                activities.extend(result)

        # Sort by timestamp descending
        activities.sort(key=lambda a: a.timestamp, reverse=True)

        return ActivityResponse(
            activities=activities[:limit],
            total=len(activities),
            incomplete_sources=incomplete,
        )

    async def _get_recent_terminologies(
        self, limit: int, namespace: str | None = None
    ) -> list[ActivityItem]:
        """Get recently modified terminologies."""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    f"{settings.def_store_url}/api/def-store/terminologies",
                    params=_with_namespace({"page_size": limit}, namespace),
                    headers={"X-API-Key": settings.api_key}
                )

                if response.status_code != 200:
                    raise UpstreamError(f"{response.url} answered HTTP {response.status_code}")

                items = []
                for item in response.json().get("items", []):
                    updated_at = self._parse_datetime(item.get("updated_at"))
                    created_at = self._parse_datetime(item.get("created_at"))

                    # Determine action based on timestamps
                    if updated_at and created_at and updated_at > created_at:
                        action = "updated"
                        timestamp = updated_at
                    else:
                        action = "created"
                        timestamp = created_at or datetime.now(UTC)

                    items.append(ActivityItem(
                        type="terminology",
                        action=action,
                        entity_id=item.get("terminology_id"),
                        entity_value=item.get("value"),
                        entity_label=item.get("label"),
                        timestamp=timestamp,
                        user=item.get("updated_by") or item.get("created_by")
                    ))

                return items
        except Exception as e:
            logger.error(f"Failed to get recent terminologies: {e}")
            raise

    async def _get_recent_terms(
        self, limit: int, namespace: str | None = None
    ) -> list[ActivityItem]:
        """Get recent term audit log entries."""
        # Terms have an audit log - fetch from it if available
        # For now, just get recent terms similar to terminologies
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                # Get terminologies first
                term_response = await client.get(
                    f"{settings.def_store_url}/api/def-store/terminologies",
                    params=_with_namespace(
                        {"status": "active", "page_size": 20}, namespace
                    ),
                    headers={"X-API-Key": settings.api_key}
                )

                if term_response.status_code != 200:
                    raise UpstreamError(f"{term_response.url} answered HTTP {term_response.status_code}")

                items = []
                for terminology in term_response.json().get("items", [])[:10]:
                    term_id = terminology.get("terminology_id")

                    terms_response = await client.get(
                        f"{settings.def_store_url}/api/def-store/terminologies/{term_id}/terms",
                        params={"page_size": 20},
                        headers={"X-API-Key": settings.api_key}
                    )

                    if terms_response.status_code != 200:
                        raise UpstreamError(f"{terms_response.url} answered HTTP {terms_response.status_code}")

                    for term in terms_response.json().get("items", []):
                        updated_at = self._parse_datetime(term.get("updated_at"))
                        created_at = self._parse_datetime(term.get("created_at"))

                        if updated_at and created_at and updated_at > created_at:
                            action = "updated"
                            timestamp = updated_at
                        else:
                            action = "created"
                            timestamp = created_at or datetime.now(UTC)

                        items.append(ActivityItem(
                            type="term",
                            action=action,
                            entity_id=term.get("term_id"),
                            entity_value=term.get("value"),
                            entity_label=term.get("label"),
                            timestamp=timestamp,
                            user=term.get("updated_by") or term.get("created_by"),
                            details={"terminology": terminology.get("value")}
                        ))

                return sorted(items, key=lambda x: x.timestamp, reverse=True)[:limit]
        except Exception as e:
            logger.error(f"Failed to get recent terms: {e}")
            raise

    async def _get_recent_templates(
        self, limit: int, namespace: str | None = None
    ) -> list[ActivityItem]:
        """Get recently modified templates."""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    f"{settings.template_store_url}/api/template-store/templates",
                    params=_with_namespace({"page_size": limit}, namespace),
                    headers={"X-API-Key": settings.api_key}
                )

                if response.status_code != 200:
                    raise UpstreamError(f"{response.url} answered HTTP {response.status_code}")

                items = []
                for item in response.json().get("items", []):
                    updated_at = self._parse_datetime(item.get("updated_at"))
                    created_at = self._parse_datetime(item.get("created_at"))
                    version = item.get("version", 1)

                    # Version > 1 means updated
                    if version > 1:
                        action = "updated"
                        timestamp = updated_at or created_at or datetime.now(UTC)
                    else:
                        action = "created"
                        timestamp = created_at or datetime.now(UTC)

                    items.append(ActivityItem(
                        type="template",
                        action=action,
                        entity_id=item.get("template_id"),
                        entity_value=item.get("value"),
                        entity_label=item.get("label"),
                        timestamp=timestamp,
                        user=item.get("updated_by") or item.get("created_by"),
                        version=version
                    ))

                return items
        except Exception as e:
            logger.error(f"Failed to get recent templates: {e}")
            raise

    async def _get_recent_documents(
        self, limit: int, namespace: str | None = None
    ) -> list[ActivityItem]:
        """Get recently modified documents."""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                # Each listed document carries its template_value.
                response = await client.get(
                    f"{settings.document_store_url}/api/document-store/documents",
                    params=_with_namespace({"page_size": limit}, namespace),
                    headers={"X-API-Key": settings.api_key}
                )

                if response.status_code != 200:
                    raise UpstreamError(f"{response.url} answered HTTP {response.status_code}")

                items = []
                for item in response.json().get("items", []):
                    updated_at = self._parse_datetime(item.get("updated_at"))
                    created_at = self._parse_datetime(item.get("created_at"))
                    version = item.get("version", 1)

                    if version > 1:
                        action = "updated"
                        timestamp = updated_at or created_at or datetime.now(UTC)
                    else:
                        action = "created"
                        timestamp = created_at or datetime.now(UTC)

                    template_value = item.get("template_value") or "unknown"

                    items.append(ActivityItem(
                        type="document",
                        action=action,
                        entity_id=item.get("document_id"),
                        entity_value=template_value,
                        entity_label=f"{template_value} document",
                        timestamp=timestamp,
                        user=item.get("updated_by") or item.get("created_by"),
                        version=version
                    ))

                return items
        except Exception as e:
            logger.error(f"Failed to get recent documents: {e}")
            raise

    async def get_term_documents(
        self,
        term_id: str,
        limit: int = 100,
        namespace: str | None = None,
        within: list[str] | None = None,
    ) -> TermDocumentsResponse:
        """
        Get documents that reference a specific term.

        The term is read from def-store first, so any identifier it accepts
        (canonical UUID, 'ns:terminology:value') finds the documents that
        hold the term's canonical ID, which is what documents store. A term
        def-store does not know has no documents. ``total`` counts every
        referencing document; ``documents`` holds the first ``limit``.

        Raises UpstreamError or a PostgreSQL error when an answer cannot be
        given: an outage is not "no documents".
        """
        if not self.postgres_pool:
            raise UpstreamError("PostgreSQL not connected")
        async with httpx.AsyncClient(timeout=30.0) as client:
            term = await _fetch_target(
                client, f"{settings.def_store_url}/api/def-store/terms/{_seg(term_id)}"
            )
        if term is None:
            return TermDocumentsResponse(term_id=term_id, documents=[], total=0)
        rows = await self._documents_referencing_term(
            term.get("term_id") or term_id, namespace, within
        )
        page = rows[:limit]
        async with httpx.AsyncClient(timeout=30.0) as client:
            values = await _template_values(client, {row["template_id"] for row in page})
        return TermDocumentsResponse(
            term_id=term_id,
            documents=[
                DocumentReference(
                    document_id=row["document_id"],
                    template_id=row["template_id"],
                    template_value=values.get(row["template_id"]),
                    field_path=row["field_path"] or "unknown",
                    status=row["status"],
                    created_at=row["created_at"],
                )
                for row in page
            ],
            total=len(rows),
        )

    async def get_entity_references(
        self,
        entity_type: str,
        entity_id: str
    ) -> EntityReferencesResponse:
        """
        Get an entity's details and validate all its references.

        Returns the entity with a list of all outgoing references and their status.
        """
        try:
            if entity_type == "document":
                return await self._get_document_references(entity_id)
            elif entity_type == "template":
                return await self._get_template_references(entity_id)
            elif entity_type == "terminology":
                return await self._get_terminology_references(entity_id)
            elif entity_type == "term":
                return await self._get_term_references(entity_id)
            elif entity_type == "file":
                return await self._get_file_references(entity_id)
            else:
                return EntityReferencesResponse(error=f"Unknown entity type: {entity_type}")
        except (UpstreamError, httpx.HTTPError):
            # A store that did not answer: the caller gets a 503, not
            # references reported broken.
            raise
        except Exception as e:
            logger.error(f"Failed to get entity references: {e}")
            return EntityReferencesResponse(error=str(e))

    async def _get_document_references(self, document_id: str) -> EntityReferencesResponse:
        """Get document details and validate its references."""
        async with httpx.AsyncClient(timeout=10.0) as client:
            # Fetch document
            doc_response = await client.get(
                f"{settings.document_store_url}/api/document-store/documents/{document_id}",
                headers={"X-API-Key": settings.api_key}
            )
            _raise_unless_answered(doc_response)

            if doc_response.status_code != 200:
                return EntityReferencesResponse(error=f"Document not found: {document_id}")

            doc = doc_response.json()
            references: list[EntityReference] = []
            valid_count = 0
            broken_count = 0
            inactive_count = 0

            # Get template reference
            template_id = doc.get("template_id")
            if template_id:
                tpl_response = await client.get(
                    f"{settings.template_store_url}/api/template-store/templates/{template_id}",
                    headers={"X-API-Key": settings.api_key}
                )
                _raise_unless_answered(tpl_response)

                if tpl_response.status_code == 200:
                    tpl = tpl_response.json()
                    tpl_status = tpl.get("status", "active")
                    if tpl_status == "active":
                        ref_status = "valid"
                        valid_count += 1
                    else:
                        ref_status = "inactive"
                        inactive_count += 1

                    references.append(EntityReference(
                        ref_type="template",
                        ref_id=template_id,
                        ref_value=tpl.get("value"),
                        ref_label=tpl.get("label"),
                        field_path=None,
                        status=ref_status,
                        error=f"Template is {tpl_status}" if tpl_status != "active" else None
                    ))
                else:
                    broken_count += 1
                    references.append(EntityReference(
                        ref_type="template",
                        ref_id=template_id,
                        ref_value=None,
                        ref_label=None,
                        field_path=None,
                        status="broken",
                        error="Template not found"
                    ))

            # Get term references (array format)
            term_refs = doc.get("term_references", [])
            if term_refs:
                # Collect all unique term IDs
                term_ids = set()
                term_field_map: dict[str, list[str]] = {}  # term_id -> [field_paths]

                for ref in term_refs:
                    field_path = ref.get("field_path", "")
                    term_id = ref.get("term_id", "")
                    if term_id:
                        term_ids.add(term_id)
                        term_field_map.setdefault(term_id, []).append(field_path)

                # Validate terms via Def-Store
                for term_id in term_ids:
                    term_response = await client.get(
                        f"{settings.def_store_url}/api/def-store/terms/{term_id}",
                        headers={"X-API-Key": settings.api_key}
                    )
                    _raise_unless_answered(term_response)

                    field_paths = term_field_map.get(term_id, [])
                    field_path_str = ", ".join(field_paths) if field_paths else None

                    if term_response.status_code == 200:
                        term = term_response.json()
                        term_status = term.get("status", "active")
                        if term_status == "active":
                            ref_status = "valid"
                            valid_count += 1
                        else:
                            ref_status = "inactive"
                            inactive_count += 1

                        references.append(EntityReference(
                            ref_type="term",
                            ref_id=term_id,
                            ref_value=term.get("value"),
                            ref_label=term.get("label"),
                            field_path=field_path_str,
                            status=ref_status,
                            error=f"Term is {term_status}" if term_status != "active" else None
                        ))
                    else:
                        broken_count += 1
                        references.append(EntityReference(
                            ref_type="term",
                            ref_id=term_id,
                            ref_value=None,
                            ref_label=None,
                            field_path=field_path_str,
                            status="broken",
                            error="Term not found"
                        ))

            # Build template value from lookup if needed
            template_value = None
            for ref in references:
                if ref.ref_type == "template":
                    template_value = ref.ref_value
                    break

            return EntityReferencesResponse(
                entity=EntityDetails(
                    entity_type="document",
                    namespace=doc.get("namespace"),
                    entity_id=document_id,
                    entity_value=template_value,
                    entity_label=f"{template_value or 'Unknown'} document",
                    entity_status=doc.get("status"),
                    version=doc.get("version"),
                    created_at=self._parse_datetime(doc.get("created_at")),
                    updated_at=self._parse_datetime(doc.get("updated_at")),
                    data=doc.get("data"),
                    references=references,
                    valid_refs=valid_count,
                    broken_refs=broken_count,
                    inactive_refs=inactive_count
                )
            )

    async def _get_template_references(self, template_id: str) -> EntityReferencesResponse:
        """Get template details and validate its references."""
        async with httpx.AsyncClient(timeout=10.0) as client:
            # Fetch template
            tpl_response = await client.get(
                f"{settings.template_store_url}/api/template-store/templates/{template_id}",
                headers={"X-API-Key": settings.api_key}
            )
            _raise_unless_answered(tpl_response)

            if tpl_response.status_code != 200:
                return EntityReferencesResponse(error=f"Template not found: {template_id}")

            tpl = tpl_response.json()
            references: list[EntityReference] = []
            valid_count = 0
            broken_count = 0
            inactive_count = 0

            # Check parent template reference (extends stores template_id, not code)
            extends = tpl.get("extends")
            if extends:
                parent_response = await client.get(
                    f"{settings.template_store_url}/api/template-store/templates/{extends}",
                    headers={"X-API-Key": settings.api_key}
                )
                _raise_unless_answered(parent_response)

                if parent_response.status_code == 200:
                    parent = parent_response.json()
                    parent_status = parent.get("status", "active")
                    if parent_status == "active":
                        ref_status = "valid"
                        valid_count += 1
                    else:
                        ref_status = "inactive"
                        inactive_count += 1

                    references.append(EntityReference(
                        ref_type="template",
                        ref_id=extends,  # extends stores template_id
                        ref_value=parent.get("value"),
                        ref_label=parent.get("label"),
                        field_path="extends",
                        status=ref_status,
                        error=f"Parent template is {parent_status}" if parent_status != "active" else None
                    ))
                else:
                    broken_count += 1
                    references.append(EntityReference(
                        ref_type="template",
                        ref_id=extends,
                        ref_value=None,  # Code unknown for missing template
                        ref_label=None,
                        field_path="extends",
                        status="broken",
                        error="Parent template not found"
                    ))

            # Check terminology references in fields
            fields = tpl.get("fields", [])
            for field in fields:
                field_name = field.get("name")
                field_type = field.get("type")

                if field_type == "term":
                    term_ref = field.get("terminology_ref")
                    if term_ref:
                        # Try to fetch terminology
                        term_response = await client.get(
                            f"{settings.def_store_url}/api/def-store/terminologies/{term_ref}",
                            headers={"X-API-Key": settings.api_key}
                        )
                        _raise_unless_answered(term_response)

                        if term_response.status_code == 200:
                            terminology = term_response.json()
                            term_status = terminology.get("status", "active")
                            if term_status == "active":
                                ref_status = "valid"
                                valid_count += 1
                            else:
                                ref_status = "inactive"
                                inactive_count += 1

                            references.append(EntityReference(
                                ref_type="terminology",
                                ref_id=term_ref,
                                ref_value=terminology.get("value"),
                                ref_label=terminology.get("label"),
                                field_path=f"fields.{field_name}.terminology_ref",
                                status=ref_status,
                                error=f"Terminology is {term_status}" if term_status != "active" else None
                            ))
                        else:
                            broken_count += 1
                            references.append(EntityReference(
                                ref_type="terminology",
                                ref_id=term_ref,
                                ref_value=None,
                                ref_label=None,
                                field_path=f"fields.{field_name}.terminology_ref",
                                status="broken",
                                error="Terminology not found"
                            ))

                # Check nested template references (template_ref stores canonical template_id)
                if field_type == "object":
                    nested_ref = field.get("template_ref")
                    if nested_ref:
                        nested_response = await client.get(
                            f"{settings.template_store_url}/api/template-store/templates/{nested_ref}",
                            headers={"X-API-Key": settings.api_key}
                        )
                        _raise_unless_answered(nested_response)

                        if nested_response.status_code == 200:
                            nested = nested_response.json()
                            nested_status = nested.get("status", "active")
                            if nested_status == "active":
                                ref_status = "valid"
                                valid_count += 1
                            else:
                                ref_status = "inactive"
                                inactive_count += 1

                            references.append(EntityReference(
                                ref_type="template",
                                ref_id=nested.get("template_id"),
                                ref_value=nested_ref,
                                ref_label=nested.get("label"),
                                field_path=f"fields.{field_name}.template_ref",
                                status=ref_status,
                                error=f"Referenced template is {nested_status}" if nested_status != "active" else None
                            ))
                        else:
                            broken_count += 1
                            references.append(EntityReference(
                                ref_type="template",
                                ref_id=nested_ref,
                                ref_value=nested_ref,
                                ref_label=None,
                                field_path=f"fields.{field_name}.template_ref",
                                status="broken",
                                error="Referenced template not found"
                            ))

            return EntityReferencesResponse(
                entity=EntityDetails(
                    entity_type="template",
                    namespace=tpl.get("namespace"),
                    entity_id=template_id,
                    entity_value=tpl.get("value"),
                    entity_label=tpl.get("label"),
                    entity_status=tpl.get("status"),
                    version=tpl.get("version"),
                    created_at=self._parse_datetime(tpl.get("created_at")),
                    updated_at=self._parse_datetime(tpl.get("updated_at")),
                    data={"fields": len(fields), "extends": extends},
                    references=references,
                    valid_refs=valid_count,
                    broken_refs=broken_count,
                    inactive_refs=inactive_count
                )
            )

    async def _get_terminology_references(self, terminology_id: str) -> EntityReferencesResponse:
        """Get terminology details (terminologies don't have outgoing references)."""
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{settings.def_store_url}/api/def-store/terminologies/{terminology_id}",
                headers={"X-API-Key": settings.api_key}
            )
            _raise_unless_answered(response)

            if response.status_code != 200:
                return EntityReferencesResponse(error=f"Terminology not found: {terminology_id}")

            terminology = response.json()

            # Terminologies don't have outgoing references
            return EntityReferencesResponse(
                entity=EntityDetails(
                    entity_type="terminology",
                    namespace=terminology.get("namespace"),
                    entity_id=terminology_id,
                    entity_value=terminology.get("value"),
                    entity_label=terminology.get("label"),
                    entity_status=terminology.get("status"),
                    version=None,
                    created_at=self._parse_datetime(terminology.get("created_at")),
                    updated_at=self._parse_datetime(terminology.get("updated_at")),
                    data={"term_count": terminology.get("term_count", 0)},
                    references=[],
                    valid_refs=0,
                    broken_refs=0,
                    inactive_refs=0
                )
            )

    async def _get_term_references(self, term_id: str) -> EntityReferencesResponse:
        """Get term details and its terminology reference."""
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{settings.def_store_url}/api/def-store/terms/{term_id}",
                headers={"X-API-Key": settings.api_key}
            )
            _raise_unless_answered(response)

            if response.status_code != 200:
                return EntityReferencesResponse(error=f"Term not found: {term_id}")

            term = response.json()
            references: list[EntityReference] = []
            valid_count = 0
            broken_count = 0
            inactive_count = 0

            # Terms reference their terminology
            terminology_id = term.get("terminology_id")
            if terminology_id:
                term_response = await client.get(
                    f"{settings.def_store_url}/api/def-store/terminologies/{terminology_id}",
                    headers={"X-API-Key": settings.api_key}
                )
                _raise_unless_answered(term_response)

                if term_response.status_code == 200:
                    terminology = term_response.json()
                    term_status = terminology.get("status", "active")
                    if term_status == "active":
                        ref_status = "valid"
                        valid_count += 1
                    else:
                        ref_status = "inactive"
                        inactive_count += 1

                    references.append(EntityReference(
                        ref_type="terminology",
                        ref_id=terminology_id,
                        ref_value=terminology.get("value"),
                        ref_label=terminology.get("label"),
                        field_path="terminology_id",
                        status=ref_status,
                        error=f"Terminology is {term_status}" if term_status != "active" else None
                    ))
                else:
                    broken_count += 1
                    references.append(EntityReference(
                        ref_type="terminology",
                        ref_id=terminology_id,
                        ref_value=None,
                        ref_label=None,
                        field_path="terminology_id",
                        status="broken",
                        error="Terminology not found"
                    ))

            return EntityReferencesResponse(
                entity=EntityDetails(
                    entity_type="term",
                    namespace=term.get("namespace"),
                    entity_id=term_id,
                    entity_value=term.get("value"),
                    entity_label=term.get("label"),
                    entity_status=term.get("status"),
                    version=None,
                    created_at=self._parse_datetime(term.get("created_at")),
                    updated_at=self._parse_datetime(term.get("updated_at")),
                    data={"aliases": term.get("aliases", [])},
                    references=references,
                    valid_refs=valid_count,
                    broken_refs=broken_count,
                    inactive_refs=inactive_count
                )
            )

    async def _search_files(
        self, query: str, namespace: str | None, status: str | None,
    ) -> list[SearchResult]:
        """Search files in Document Store.

        Returns ALL matching items sorted by relevance (CASE-329).
        Capped at MAX_RESULTS_PER_TYPE.
        """
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                all_items, scanned_all = await _scan_listing(
                    client, f"{settings.document_store_url}/api/document-store/files",
                    {}, namespace, status,
                )

                results = []
                query_lower = query.lower()
                for item in all_items:
                    file_id = item.get("file_id", "")
                    filename = item.get("filename", "")
                    metadata = item.get("metadata", {})
                    description = metadata.get("description", "") or ""
                    category = metadata.get("category", "") or ""
                    tags = metadata.get("tags", []) or []

                    if (query_lower in file_id.lower() or
                        query_lower in filename.lower() or
                        query_lower in description.lower() or
                        query_lower in category.lower() or
                        any(query_lower in tag.lower() for tag in tags)):
                        results.append(SearchResult(
                            type="file",
                            id=file_id,
                            value=None,
                            label=filename,
                            status=item.get("status"),
                            description=f"{item.get('content_type', '')} • {self._format_file_size(item.get('size_bytes', 0))}",
                            updated_at=self._parse_datetime(item.get("updated_at") or item.get("uploaded_at"))
                        ))

                results = self._sort_by_relevance(results, query_lower)
                return _hits(
                    results[:MAX_RESULTS_PER_TYPE],
                    None if scanned_all else
                    f"matched against the first {len(all_items)} entries only",
                )
        except httpx.HTTPError as e:
            raise UpstreamError(f"file listing failed: {e}") from e

    async def _get_recent_files(
        self, limit: int, namespace: str | None = None
    ) -> list[ActivityItem]:
        """Get recently uploaded/modified files."""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    f"{settings.document_store_url}/api/document-store/files",
                    params=_with_namespace({"page_size": limit}, namespace),
                    headers={"X-API-Key": settings.api_key}
                )

                if response.status_code != 200:
                    raise UpstreamError(f"{response.url} answered HTTP {response.status_code}")

                items = []
                for item in response.json().get("items", []):
                    uploaded_at = self._parse_datetime(item.get("uploaded_at"))
                    updated_at = self._parse_datetime(item.get("updated_at"))

                    status = item.get("status", "")
                    if status == "inactive":
                        action = "deleted"
                        timestamp = updated_at or uploaded_at or datetime.now(UTC)
                    elif updated_at and uploaded_at and updated_at > uploaded_at:
                        action = "updated"
                        timestamp = updated_at
                    else:
                        action = "created"
                        timestamp = uploaded_at or datetime.now(UTC)

                    items.append(ActivityItem(
                        type="file",
                        action=action,
                        entity_id=item.get("file_id"),
                        entity_value=None,
                        entity_label=item.get("filename"),
                        timestamp=timestamp,
                        user=item.get("updated_by") or item.get("uploaded_by"),
                    ))

                return items
        except Exception as e:
            logger.error(f"Failed to get recent files: {e}")
            raise

    async def _get_file_references(self, file_id: str) -> EntityReferencesResponse:
        """Get file details (files don't have outgoing references to other entities)."""
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{settings.document_store_url}/api/document-store/files/{file_id}",
                headers={"X-API-Key": settings.api_key}
            )

            if response.status_code != 200:
                return EntityReferencesResponse(error=f"File not found: {file_id}")

            file_data = response.json()
            metadata = file_data.get("metadata", {})

            return EntityReferencesResponse(
                entity=EntityDetails(
                    entity_type="file",
                    namespace=file_data.get("namespace"),
                    entity_id=file_id,
                    entity_value=None,
                    entity_label=file_data.get("filename"),
                    entity_status=file_data.get("status"),
                    version=None,
                    created_at=self._parse_datetime(file_data.get("uploaded_at")),
                    updated_at=self._parse_datetime(file_data.get("updated_at")),
                    data={
                        "content_type": file_data.get("content_type"),
                        "size_bytes": file_data.get("size_bytes"),
                        "reference_count": file_data.get("reference_count", 0),
                        "checksum": file_data.get("checksum"),
                        "description": metadata.get("description"),
                        "category": metadata.get("category"),
                        "tags": metadata.get("tags", []),
                    },
                    references=[],
                    valid_refs=0,
                    broken_refs=0,
                    inactive_refs=0
                )
            )

    async def _get_file_referenced_by(
        self, file_id: str, limit: int, namespace: str | None = None
    ) -> ReferencedByResponse:
        """Find documents that reference a file."""
        references: list[IncomingReference] = []

        async with httpx.AsyncClient(timeout=10.0) as client:
            # Get file details
            file_response = await client.get(
                f"{settings.document_store_url}/api/document-store/files/{file_id}",
                headers={"X-API-Key": settings.api_key}
            )

            if file_response.status_code == 404:
                return _target_missing("file", file_id, "File")
            if file_response.status_code != 200:
                raise UpstreamError(f"file read answered HTTP {file_response.status_code}")

            file_data = file_response.json()

            # A restricted caller sees a file's references only when the file
            # lives in its namespace — the same rule document-store applies to
            # its file→documents listing. Answer as not found otherwise.
            if namespace and file_data.get("namespace") != namespace:
                return ReferencedByResponse(
                    entity_type="file",
                    entity_id=file_id,
                    error=f"File not found: {file_id}"
                )

            # Query the file→documents endpoint
            docs_response = await client.get(
                f"{settings.document_store_url}/api/document-store/files/{file_id}/documents",
                params={"page_size": limit},
                headers={"X-API-Key": settings.api_key}
            )

            if docs_response.status_code == 200:
                docs_data = docs_response.json()
                for doc in docs_data.get("items", []):
                    references.append(IncomingReference(
                        entity_type="document",
                        entity_id=doc.get("document_id"),
                        entity_value=doc.get("template_value"),
                        entity_label=f"{doc.get('template_value', 'Unknown')} document",
                        entity_status=doc.get("status"),
                        field_path=doc.get("field_path"),
                        reference_type="file_ref"
                    ))

        return _found(
            ReferencedByResponse(
                entity_type="file",
                entity_id=file_id,
                entity_value=None,
                entity_label=file_data.get("filename"),
                referenced_by=references[:limit],
                total=len(references)
            ),
            file_data.get("namespace"),
        )

    @staticmethod
    def _format_file_size(size_bytes: int) -> str:
        """Format file size for display."""
        if size_bytes < 1024:
            return f"{size_bytes} B"
        if size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.1f} KB"
        return f"{size_bytes / (1024 * 1024):.2f} MB"

    @staticmethod
    def _parse_datetime(value: Any) -> datetime | None:
        """Parse datetime from various formats."""
        if value is None:
            return None
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            try:
                # Try ISO format
                if value.endswith("Z"):
                    value = value[:-1] + "+00:00"
                return datetime.fromisoformat(value)
            except ValueError:
                pass
        return None

    # =========================================================================
    # REFERENCED BY (INCOMING REFERENCES)
    # =========================================================================

    async def get_referenced_by(
        self,
        entity_type: str,
        entity_id: str,
        limit: int = 100,
        namespace: str | None = None,
        within: list[str] | None = None,
    ) -> ReferencedByResponse:
        """
        Find all entities that reference the given entity.

        - Template: documents using it, templates extending it, templates
          with a template_ref / array_template_ref to it
        - Terminology: templates with a terminology_ref / array_terminology_ref to it
        - Term: documents whose term references hold it
        - Document: relationship (edge) documents whose source_ref or target_ref is it
        - File: documents whose file fields hold it

        The target is read first and matched by its canonical ID, so any
        identifier the target's store accepts finds the same referrers. The
        response remembers where the target lives (never served), so the
        route can check the caller may read it.

        Args:
            entity_type: document, template, terminology, term, file
            entity_id: The entity's ID or any identifier its store resolves
            limit: Max referrers to return; ``total`` counts them all
            namespace: Only referrers in this namespace (None: all)
            within: With no namespace, the namespaces the reporting tables
                are read in (None: all); the stores answer within the
                readable namespaces of reporting-sync's own key
        """
        finders = {
            "template": lambda: self._get_template_referenced_by(entity_id, limit, namespace),
            "terminology": lambda: self._get_terminology_referenced_by(entity_id, limit, namespace),
            "term": lambda: self._get_term_referenced_by(entity_id, limit, namespace, within),
            "document": lambda: self._get_document_referenced_by(entity_id, limit, namespace, within),
            "file": lambda: self._get_file_referenced_by(entity_id, limit, namespace),
        }
        finder = finders.get(entity_type)
        if finder is None:
            return ReferencedByResponse(
                entity_type=entity_type,
                entity_id=entity_id,
                error=f"Unknown entity type: {entity_type}"
            )
        try:
            return await finder()
        except Exception as e:
            logger.error(f"Failed to get referenced-by for {entity_type}/{entity_id}: {e}")
            return ReferencedByResponse(
                entity_type=entity_type,
                entity_id=entity_id,
                error=str(e)
            )

    async def _get_template_referenced_by(
        self, template_id: str, limit: int, namespace: str | None = None
    ) -> ReferencedByResponse:
        """Documents using a template, and templates extending or embedding it."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            tpl = await _fetch_target(
                client, f"{settings.template_store_url}/api/template-store/templates/{_seg(template_id)}"
            )
            if tpl is None:
                return _target_missing("template", template_id, "Template")
            canonical = tpl.get("template_id") or template_id
            template_value = tpl.get("value")

            documents = await _get_json(
                client,
                f"{settings.document_store_url}/api/document-store/documents",
                _with_namespace({"template_id": canonical, "page_size": limit}, namespace),
            )
            doc_items = documents.get("items", [])
            references = [
                IncomingReference(
                    entity_type="document",
                    entity_id=doc.get("document_id"),
                    entity_value=None,
                    entity_label=f"{template_value} document",
                    entity_status=doc.get("status"),
                    field_path="template_id",
                    reference_type="uses_template"
                )
                for doc in doc_items
            ]
            doc_total = documents.get("total")
            total = doc_total if isinstance(doc_total, int) and doc_total >= 0 else len(doc_items)

            templates = await _all_templates(client, namespace)
        template_refs = _template_referrers(
            templates,
            lambda _t, ref: ref == canonical,
            extends=True,
            attrs=(("template_ref", "template_ref"), ("array_template_ref", "template_ref")),
        )
        total += len(template_refs)
        references += template_refs
        return _found(
            ReferencedByResponse(
                entity_type="template",
                entity_id=template_id,
                entity_value=template_value,
                entity_label=tpl.get("label"),
                referenced_by=references[:limit],
                total=total,
            ),
            tpl.get("namespace"),
        )

    async def _get_terminology_referenced_by(
        self, terminology_id: str, limit: int, namespace: str | None = None
    ) -> ReferencedByResponse:
        """Templates whose term fields draw from a terminology."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            terminology = await _fetch_target(
                client,
                f"{settings.def_store_url}/api/def-store/terminologies/{_seg(terminology_id)}",
            )
            if terminology is None:
                return _target_missing("terminology", terminology_id, "Terminology")
            templates = await _all_templates(client, namespace)

        canonical = terminology.get("terminology_id") or terminology_id
        value = terminology.get("value")
        owner = terminology.get("namespace")

        def names_it(template: dict[str, Any], ref: Any) -> bool:
            # The canonical id names it anywhere; "ns:VALUE" names it
            # explicitly; a bare value names it only from its own namespace.
            if not isinstance(ref, str):
                return False
            if ref == canonical or (owner and ref == f"{owner}:{value}"):
                return True
            return ref == value and template.get("namespace") == owner

        references = _template_referrers(
            templates,
            names_it,
            extends=False,
            attrs=(
                ("terminology_ref", "terminology_ref"),
                ("array_terminology_ref", "terminology_ref"),
            ),
        )
        return _found(
            ReferencedByResponse(
                entity_type="terminology",
                entity_id=terminology_id,
                entity_value=value,
                entity_label=terminology.get("label"),
                referenced_by=references[:limit],
                total=len(references),
            ),
            owner,
        )

    async def _get_term_referenced_by(
        self, term_id: str, limit: int, namespace: str | None = None,
        within: list[str] | None = None,
    ) -> ReferencedByResponse:
        """Documents whose term references hold a term."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            term = await _fetch_target(
                client, f"{settings.def_store_url}/api/def-store/terms/{_seg(term_id)}"
            )
        if term is None:
            return _target_missing("term", term_id, "Term")
        if not self.postgres_pool:
            raise RuntimeError("PostgreSQL not connected")
        rows = await self._documents_referencing_term(
            term.get("term_id") or term_id, namespace, within
        )
        references = [
            IncomingReference(
                entity_type="document",
                entity_id=row["document_id"],
                entity_value=None,
                entity_label=f"Document {row['document_id'][:8]}...",
                entity_status=row["status"],
                field_path=row["field_path"],
                reference_type="term_ref"
            )
            for row in rows[:limit]
        ]
        return _found(
            ReferencedByResponse(
                entity_type="term",
                entity_id=term_id,
                entity_value=term.get("value"),
                entity_label=term.get("label"),
                referenced_by=references,
                total=len(rows),
            ),
            term.get("namespace"),
        )

    async def _get_document_referenced_by(
        self, document_id: str, limit: int, namespace: str | None = None,
        within: list[str] | None = None,
    ) -> ReferencedByResponse:
        """Relationship documents whose source_ref or target_ref is a document.

        Edge types (usage: relationship) are the platform's document-to-
        document references; their reporting tables carry the resolved
        endpoint ids as source_ref_id / target_ref_id.
        """
        async with httpx.AsyncClient(timeout=30.0) as client:
            doc = await _fetch_target(
                client,
                f"{settings.document_store_url}/api/document-store/documents/{_seg(document_id)}",
            )
        if doc is None:
            return _target_missing("document", document_id, "Document")
        if not self.postgres_pool:
            raise RuntimeError("PostgreSQL not connected")
        rows = await self._edges_touching(
            doc.get("document_id") or document_id, namespace, within
        )
        references = [
            IncomingReference(
                entity_type="document",
                entity_id=row["document_id"],
                entity_value=None,
                entity_label=f"Relationship {row['document_id'][:8]}...",
                entity_status=row["status"],
                field_path=row["field_path"],
                reference_type="relationship"
            )
            for row in rows[:limit]
        ]
        return _found(
            ReferencedByResponse(
                entity_type="document",
                entity_id=document_id,
                referenced_by=references,
                total=len(rows),
            ),
            doc.get("namespace"),
        )

    async def _documents_referencing_term(
        self, term_id: str, namespace: str | None, within: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Every document whose term references hold ``term_id`` (canonical).

        One row per document, ordered by document_id: a document kept in
        several rows (all_versions tables, one row per version) is reported
        once, from its highest version that holds the term. field_path is
        the first field that holds it.
        """
        if self.postgres_pool is None:
            raise UpstreamError("PostgreSQL not connected")
        async with self.postgres_pool.acquire() as conn:
            tables = await _doc_tables_with_column(
                conn, "term_references_json", namespace, within
            )
            found: dict[str, dict[str, Any]] = {}
            for table in tables:
                created = "created_at" if table["has_created_at"] else "NULL::timestamptz"
                rows = await conn.fetch(
                    f"""
                    SELECT DISTINCT ON (document_id)
                        document_id, template_id, status, version,
                        {created} AS created_at,
                        (SELECT r->>'field_path'
                           FROM jsonb_array_elements(term_references_json) r
                          WHERE r->>'term_id' = $1 LIMIT 1) AS field_path
                    FROM "{table['table_schema']}"."{table['table_name']}"
                    WHERE jsonb_typeof(term_references_json) = 'array'
                      AND term_references_json @> jsonb_build_array(
                            jsonb_build_object('term_id', $1::text))
                    ORDER BY document_id, version DESC
                    """,  # nosec B608  # identifiers read from the PostgreSQL catalog, values bound
                    term_id,
                )
                for row in rows:
                    _keep_highest_version(found, dict(row, table_name=table["table_name"]))
        return [found[k] for k in sorted(found)]

    async def _edges_touching(
        self, document_id: str, namespace: str | None, within: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Every relationship document whose source_ref_id or target_ref_id
        is ``document_id``, one row per edge, ordered by document_id."""
        if self.postgres_pool is None:
            raise UpstreamError("PostgreSQL not connected")
        async with self.postgres_pool.acquire() as conn:
            tables = await _doc_tables_with_column(conn, "source_ref_id", namespace, within)
            found: dict[str, dict[str, Any]] = {}
            for table in tables:
                rows = await conn.fetch(
                    f"""
                    SELECT DISTINCT ON (document_id)
                        document_id, status, version,
                        CASE WHEN source_ref_id = $1 THEN 'source_ref'
                             ELSE 'target_ref' END AS field_path
                    FROM "{table['table_schema']}"."{table['table_name']}"
                    WHERE source_ref_id = $1 OR target_ref_id = $1
                    ORDER BY document_id, version DESC
                    """,  # nosec B608  # identifiers read from the PostgreSQL catalog, values bound
                    document_id,
                )
                for row in rows:
                    _keep_highest_version(found, dict(row, table_name=table["table_name"]))
        return [found[k] for k in sorted(found)]


class UpstreamError(RuntimeError):
    """A store answered something other than the entity or 'not found'."""


def _seg(identifier: str) -> str:
    """An identifier as one URL path segment."""
    return quote(identifier, safe=":")


def _raise_unless_answered(response: httpx.Response) -> None:
    """Raise UpstreamError unless the store answered the read: 200 (found) or
    404 (not found). Anything else is an outage or a refusal, and reading it
    as "not found" reports a healthy reference as broken."""
    if response.status_code not in (200, 404):
        raise UpstreamError(f"{response.url} answered HTTP {response.status_code}")


async def _fetch_target(client: httpx.AsyncClient, url: str) -> dict[str, Any] | None:
    """The entity at ``url`` read with the service key; None when the store
    says it does not exist. Any other answer raises: an outage is not a
    missing entity."""
    response = await client.get(url, headers={"X-API-Key": settings.api_key})
    if response.status_code == 404:
        return None
    if response.status_code != 200:
        raise UpstreamError(f"{url} answered HTTP {response.status_code}")
    return cast(dict[str, Any], response.json())


async def _get_json(
    client: httpx.AsyncClient, url: str, params: dict[str, Any]
) -> dict[str, Any]:
    response = await client.get(url, params=params, headers={"X-API-Key": settings.api_key})
    if response.status_code != 200:
        raise UpstreamError(f"{url} answered HTTP {response.status_code}")
    return cast(dict[str, Any], response.json())


async def _document_tables(
    conn: asyncpg.Connection, namespace: str | None, within: list[str] | None = None,
) -> list[Any]:
    """Every document base table (in ``namespace``'s schema, else in the
    schemas of ``within``, else in every namespace schema): the base tables
    that carry the document identity columns, whatever they are named."""
    params: list[Any] = []
    scope = ""
    if namespace:
        params.append(namespace)
        scope = f" AND t.table_schema = ${len(params)}"
    elif within is not None:
        params.append(within)
        scope = f" AND t.table_schema = ANY(${len(params)}::text[])"
    return list(await conn.fetch(
        f"""
        SELECT t.table_schema, t.table_name
        FROM information_schema.tables t
        WHERE t.table_type = 'BASE TABLE'
          AND t.table_schema NOT IN ('public', 'pg_catalog', 'information_schema', 'pg_toast')
          AND EXISTS (
            SELECT 1 FROM pg_attribute a
            WHERE a.attrelid = format('%I.%I', t.table_schema, t.table_name)::regclass
              AND a.attname = 'identity_hash' AND NOT a.attisdropped
          ){scope}
        ORDER BY t.table_schema, t.table_name
        """,  # nosec B608  # SQL fragments are code constants, values bound
        *params,
        timeout=SEARCH_QUERY_TIMEOUT,
    ))


async def _template_table_bases(template: str, namespace: str | None) -> set[str]:
    """The logical table names a template's documents can live under: the
    default doc_<value> base, and the template's reporting.table_name
    override when the template store names one. The override is looked up
    best-effort: when the template store cannot answer, only the default
    name is matched, and that is logged."""
    bases = {entity_base(template.lower())}
    params = {"namespace": namespace} if namespace else {}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{settings.template_store_url}/api/template-store/templates/by-value/"
                f"{quote(template, safe='')}",
                params=params,
                headers={"X-API-Key": settings.api_key},
            )
    except httpx.HTTPError as e:
        logger.warning("Template filter %s: table_name override not looked up: %s", template, e)
        return bases
    if response.status_code == 200:
        override = (response.json().get("reporting") or {}).get("table_name")
        if override:
            bases.add(entity_base(override.lower()))
    elif response.status_code != 404:
        logger.warning(
            "Template filter %s: table_name override not looked up: HTTP %s",
            template, response.status_code,
        )
    return bases


async def _term_namespaces(client: httpx.AsyncClient) -> list[str]:
    """Every namespace holding an active terminology, from the full
    terminology listing (all pages)."""
    found: set[str] = set()
    page = 1
    while True:
        response = await client.get(
            f"{settings.def_store_url}/api/def-store/terminologies",
            params={"status": "active", "page_size": 100, "page": page},
            headers={"X-API-Key": settings.api_key},
        )
        if response.status_code != 200:
            raise UpstreamError(f"terminology listing answered HTTP {response.status_code}")
        data = response.json()
        found.update(t["namespace"] for t in data.get("items", []) if t.get("namespace"))
        if page >= data.get("pages", 1):
            return sorted(found)
        page += 1


async def _scan_listing(
    client: httpx.AsyncClient, url: str, params: dict[str, Any],
    namespace: str | None, status: str | None,
) -> tuple[list[dict[str, Any]], bool]:
    """Page a store listing (100 per page) up to MAX_RESULTS_PER_TYPE
    entries: (entries, whether that was all of them). A page the store does
    not answer raises UpstreamError."""
    params = {**params, "page_size": 100}
    if namespace:
        params["namespace"] = namespace
    if status:
        params["status"] = status
    items: list[dict[str, Any]] = []
    page = 1
    while True:
        response = await client.get(
            url, params={**params, "page": page}, headers={"X-API-Key": settings.api_key},
        )
        if response.status_code != 200:
            raise UpstreamError(f"listing answered HTTP {response.status_code}")
        batch = response.json().get("items", [])
        items.extend(batch)
        if len(batch) < 100:
            return items, True
        if len(items) >= MAX_RESULTS_PER_TYPE:
            return items, False
        page += 1


async def _template_values(
    client: httpx.AsyncClient, template_ids: set[str]
) -> dict[str, str]:
    """Each template's value, read from the template store. A template it
    cannot find is left out: its value is unknown, not made up."""
    values: dict[str, str] = {}
    for template_id in sorted(template_ids):
        tpl = await _fetch_target(
            client, f"{settings.template_store_url}/api/template-store/templates/{_seg(template_id)}"
        )
        if tpl and tpl.get("value"):
            values[template_id] = tpl["value"]
    return values


_TEMPLATE_PAGE = 1000


async def _all_templates(
    client: httpx.AsyncClient, namespace: str | None
) -> list[dict[str, Any]]:
    """Every template version (in ``namespace``, or everywhere), all pages."""
    templates: list[dict[str, Any]] = []
    page = 1
    while True:
        body = await _get_json(
            client,
            f"{settings.template_store_url}/api/template-store/templates",
            _with_namespace({"page": page, "page_size": _TEMPLATE_PAGE}, namespace),
        )
        items = body.get("items", [])
        templates += items
        if len(items) < _TEMPLATE_PAGE:
            return templates
        page += 1


def _template_referrers(
    templates: list[dict[str, Any]],
    names_target: Any,
    *,
    extends: bool,
    attrs: tuple[tuple[str, str], ...],
) -> list[IncomingReference]:
    """The templates that reference a target, one entry per template and
    referencing slot (a template's versions share one entry).

    ``names_target(template, ref)`` says whether a stored reference names
    the target; ``attrs`` pairs each field attribute to check with the
    reference_type it reports.
    """
    seen: set[tuple[str, str]] = set()
    references: list[IncomingReference] = []

    def add(t: dict[str, Any], field_path: str, reference_type: str) -> None:
        key = (str(t.get("template_id")), field_path)
        if key in seen:
            return
        seen.add(key)
        references.append(IncomingReference(
            entity_type="template",
            entity_id=str(t.get("template_id")),
            entity_value=t.get("value"),
            entity_label=t.get("label"),
            entity_status=t.get("status"),
            field_path=field_path,
            reference_type=reference_type,
        ))

    for t in templates:
        if extends and t.get("extends") and names_target(t, t.get("extends")):
            add(t, "extends", "extends")
        for field in t.get("fields", []):
            for attr, reference_type in attrs:
                if names_target(t, field.get(attr)):
                    add(t, f"fields.{field.get('name')}.{attr}", reference_type)
    return references


async def _doc_tables_with_column(
    conn: asyncpg.Connection, column: str, namespace: str | None,
    within: list[str] | None = None,
) -> list[Any]:
    """The doc_* base tables (never the entity views over them, which would
    report each row twice) that have ``column``, with whether each has
    created_at (include_metadata: false tables do not). In ``namespace``'s
    schema, else in the schemas of ``within``, else in every schema."""
    return list(await conn.fetch(
        r"""
        SELECT c.table_schema, c.table_name,
               bool_or(c.column_name = 'created_at') AS has_created_at
        FROM information_schema.columns c
        JOIN information_schema.tables t
          ON t.table_schema = c.table_schema AND t.table_name = c.table_name
        WHERE t.table_type = 'BASE TABLE'
          AND c.table_schema NOT IN ('public', 'pg_catalog', 'information_schema', 'pg_toast')
          AND c.table_name LIKE 'doc\_%'
          AND ($2::text IS NULL OR c.table_schema = $2)
          AND ($3::text[] IS NULL OR c.table_schema = ANY($3::text[]))
        GROUP BY c.table_schema, c.table_name
        HAVING bool_or(c.column_name = $1)
        ORDER BY c.table_schema, c.table_name
        """,
        column, namespace, within,
    ))


def _keep_highest_version(found: dict[str, dict[str, Any]], row: dict[str, Any]) -> None:
    current = found.get(row["document_id"])
    if current is None or row["version"] > current["version"]:
        found[row["document_id"]] = row


def _target_missing(entity_type: str, entity_id: str, noun: str) -> ReferencedByResponse:
    response = ReferencedByResponse(
        entity_type=entity_type,
        entity_id=entity_id,
        error=f"{noun} not found: {entity_id}",
    )
    response._target_missing = True
    return response


def _found(response: ReferencedByResponse, namespace: str | None) -> ReferencedByResponse:
    response._target_namespace = namespace
    return response

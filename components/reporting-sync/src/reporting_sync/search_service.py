"""
Search Service for Reporting Sync.

Provides unified search across all WIP services and reverse lookups.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Optional

import asyncpg
import httpx
from pydantic import BaseModel, Field

from .config import settings

logger = logging.getLogger(__name__)


# =============================================================================
# MODELS
# =============================================================================


class SearchResult(BaseModel):
    """A single search result."""

    type: str = Field(..., description="Entity type: terminology, term, template, document")
    id: str = Field(..., description="Entity ID")
    code: str | None = Field(None, description="Entity code (if applicable)")
    name: str | None = Field(None, description="Entity name/value")
    status: str | None = Field(None, description="Entity status")
    description: str | None = Field(None, description="Brief description or context")
    updated_at: datetime | None = Field(None, description="Last update time")


class SearchResponse(BaseModel):
    """Response from unified search."""

    query: str
    results: list[SearchResult] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)
    total: int = 0


class SearchRequest(BaseModel):
    """Request for unified search."""

    query: str = Field(..., min_length=1, description="Search string")
    types: list[str] | None = Field(
        None,
        description="Entity types to search: terminology, term, template, document"
    )
    status: str | None = Field(None, description="Filter by status")
    limit: int = Field(50, ge=1, le=200, description="Max results per type")


class ActivityItem(BaseModel):
    """A single activity/change item."""

    type: str = Field(..., description="Entity type")
    action: str = Field(..., description="Action: created, updated, deleted, deprecated")
    entity_id: str
    entity_code: str | None = None
    entity_name: str | None = None
    timestamp: datetime
    user: str | None = None
    version: int | None = None
    details: dict[str, Any] | None = None


class ActivityResponse(BaseModel):
    """Response from recent activity endpoint."""

    activities: list[ActivityItem] = Field(default_factory=list)
    total: int = 0


class DocumentReference(BaseModel):
    """A document that references a term."""

    document_id: str
    template_id: str
    template_code: str | None = None
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
    ref_code: str | None = Field(None, description="Referenced entity code")
    ref_name: str | None = Field(None, description="Referenced entity name")
    field_path: str | None = Field(None, description="Field that holds the reference")
    status: str = Field(..., description="Reference status: valid, broken, inactive")
    error: str | None = Field(None, description="Error message if broken")


class EntityDetails(BaseModel):
    """Full entity details with reference information."""

    entity_type: str
    entity_id: str
    entity_code: str | None = None
    entity_name: str | None = None
    entity_status: str | None = None
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
    entity_code: str | None = Field(None, description="Code (for templates)")
    entity_name: str | None = Field(None, description="Name or description")
    entity_status: str | None = Field(None, description="Status of the referencing entity")
    field_path: str | None = Field(None, description="Field containing the reference")
    reference_type: str = Field(..., description="How it references: uses_template, extends, terminology_ref, template_ref, term_ref")


class ReferencedByResponse(BaseModel):
    """Response from 'referenced by' lookup."""

    entity_type: str
    entity_id: str
    entity_code: str | None = None
    entity_name: str | None = None
    referenced_by: list[IncomingReference] = Field(default_factory=list)
    total: int = 0
    error: str | None = None


# =============================================================================
# SEARCH SERVICE
# =============================================================================


class SearchService:
    """Service for unified search and activity tracking."""

    def __init__(self, postgres_pool: asyncpg.Pool | None = None):
        self.postgres_pool = postgres_pool

    async def search(self, request: SearchRequest) -> SearchResponse:
        """
        Search across all entity types.

        Queries each service in parallel and aggregates results.
        """
        query = request.query.strip()
        types = request.types or ["terminology", "term", "template", "document"]
        status = request.status
        limit = request.limit

        # Run searches in parallel
        tasks = []
        if "terminology" in types:
            tasks.append(("terminology", self._search_terminologies(query, status, limit)))
        if "term" in types:
            tasks.append(("term", self._search_terms(query, status, limit)))
        if "template" in types:
            tasks.append(("template", self._search_templates(query, status, limit)))
        if "document" in types:
            tasks.append(("document", self._search_documents(query, status, limit)))

        # Gather results
        all_results: list[SearchResult] = []
        counts: dict[str, int] = {}

        results = await asyncio.gather(*[t[1] for t in tasks], return_exceptions=True)

        for (entity_type, _), result in zip(tasks, results):
            if isinstance(result, Exception):
                logger.error(f"Search failed for {entity_type}: {result}")
                counts[entity_type] = 0
            else:
                all_results.extend(result)
                counts[entity_type] = len(result)

        # Sort by relevance (exact matches first, then by updated_at)
        query_lower = query.lower()
        all_results.sort(
            key=lambda r: (
                0 if r.code and r.code.lower() == query_lower else
                1 if r.id.lower() == query_lower else
                2 if r.code and query_lower in r.code.lower() else
                3,
                r.updated_at or datetime.min.replace(tzinfo=timezone.utc)
            ),
            reverse=False  # Lower score = better match
        )

        return SearchResponse(
            query=query,
            results=all_results[:limit * 2],  # Cap total results
            counts=counts,
            total=sum(counts.values())
        )

    async def _search_terminologies(
        self, query: str, status: str | None, limit: int
    ) -> list[SearchResult]:
        """Search terminologies in Def-Store."""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                # Fetch more items than limit to search through, then filter
                params = {"page_size": 100}
                if status:
                    params["status"] = status

                response = await client.get(
                    f"{settings.def_store_url}/api/def-store/terminologies",
                    params=params,
                    headers={"X-API-Key": settings.api_key}
                )

                if response.status_code != 200:
                    return []

                data = response.json()
                results = []
                query_lower = query.lower()

                for item in data.get("items", []):
                    # Filter by query
                    if (query_lower in item.get("code", "").lower() or
                        query_lower in item.get("name", "").lower() or
                        query_lower in item.get("terminology_id", "").lower()):
                        results.append(SearchResult(
                            type="terminology",
                            id=item.get("terminology_id"),
                            code=item.get("code"),
                            name=item.get("name"),
                            status=item.get("status"),
                            description=item.get("description"),
                            updated_at=self._parse_datetime(item.get("updated_at"))
                        ))

                return results[:limit]
        except Exception as e:
            logger.error(f"Terminology search failed: {e}")
            return []

    async def _search_terms(
        self, query: str, status: str | None, limit: int
    ) -> list[SearchResult]:
        """Search terms across all terminologies."""
        # First get all terminologies, then search terms in each
        # This is not ideal for large datasets, but works for now
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                # Get terminologies first
                term_response = await client.get(
                    f"{settings.def_store_url}/api/def-store/terminologies",
                    params={"status": "active", "page_size": 100},
                    headers={"X-API-Key": settings.api_key}
                )

                if term_response.status_code != 200:
                    return []

                terminologies = term_response.json().get("items", [])
                results = []
                query_lower = query.lower()

                # Search terms in each terminology (limit parallel requests)
                for terminology in terminologies[:20]:  # Limit to 20 terminologies
                    term_id = terminology.get("terminology_id")
                    params = {"page_size": 100}
                    if status:
                        params["status"] = status

                    terms_response = await client.get(
                        f"{settings.def_store_url}/api/def-store/terminologies/{term_id}/terms",
                        params=params,
                        headers={"X-API-Key": settings.api_key}
                    )

                    if terms_response.status_code != 200:
                        continue

                    for term in terms_response.json().get("items", []):
                        # Filter by query
                        if (query_lower in term.get("code", "").lower() or
                            query_lower in term.get("value", "").lower() or
                            query_lower in term.get("term_id", "").lower() or
                            any(query_lower in alias.lower() for alias in term.get("aliases", []))):
                            results.append(SearchResult(
                                type="term",
                                id=term.get("term_id"),
                                code=term.get("code"),
                                name=term.get("value"),
                                status=term.get("status"),
                                description=f"In {terminology.get('code')}",
                                updated_at=self._parse_datetime(term.get("updated_at"))
                            ))

                    if len(results) >= limit:
                        break

                return results[:limit]
        except Exception as e:
            logger.error(f"Term search failed: {e}")
            return []

    async def _search_templates(
        self, query: str, status: str | None, limit: int
    ) -> list[SearchResult]:
        """Search templates in Template Store."""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                # Fetch more items than limit to search through, then filter
                params = {"page_size": 100, "latest_only": True}
                if status:
                    params["status"] = status

                response = await client.get(
                    f"{settings.template_store_url}/api/template-store/templates",
                    params=params,
                    headers={"X-API-Key": settings.api_key}
                )

                if response.status_code != 200:
                    return []

                data = response.json()
                results = []
                query_lower = query.lower()

                for item in data.get("items", []):
                    # Filter by query
                    if (query_lower in item.get("code", "").lower() or
                        query_lower in item.get("name", "").lower() or
                        query_lower in item.get("template_id", "").lower()):
                        results.append(SearchResult(
                            type="template",
                            id=item.get("template_id"),
                            code=item.get("code"),
                            name=item.get("name"),
                            status=item.get("status"),
                            description=f"v{item.get('version', 1)}",
                            updated_at=self._parse_datetime(item.get("updated_at"))
                        ))

                return results[:limit]
        except Exception as e:
            logger.error(f"Template search failed: {e}")
            return []

    async def _search_documents(
        self, query: str, status: str | None, limit: int
    ) -> list[SearchResult]:
        """Search documents in Document Store."""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                # First fetch templates to build template_id -> code mapping
                # (Document list API returns template_code as null)
                template_response = await client.get(
                    f"{settings.template_store_url}/api/template-store/templates",
                    params={"page_size": 100},
                    headers={"X-API-Key": settings.api_key}
                )

                template_map: dict[str, str] = {}
                if template_response.status_code == 200:
                    for tpl in template_response.json().get("items", []):
                        template_map[tpl.get("template_id")] = tpl.get("code", "")

                # Fetch more items than limit to search through, then filter
                params = {"page_size": 100}
                if status:
                    params["status"] = status

                response = await client.get(
                    f"{settings.document_store_url}/api/document-store/documents",
                    params=params,
                    headers={"X-API-Key": settings.api_key}
                )

                if response.status_code != 200:
                    return []

                data = response.json()
                results = []
                query_lower = query.lower()

                for item in data.get("items", []):
                    doc_id = item.get("document_id", "")
                    template_id = item.get("template_id", "")
                    # Get template_code from our lookup (API returns null)
                    template_code = template_map.get(template_id, item.get("template_code") or "unknown")

                    # Filter by query (search in ID and template code)
                    if (query_lower in doc_id.lower() or
                        query_lower in template_code.lower()):
                        results.append(SearchResult(
                            type="document",
                            id=doc_id,
                            code=None,
                            name=f"{template_code} document",
                            status=item.get("status"),
                            description=f"v{item.get('version', 1)} • {template_code}",
                            updated_at=self._parse_datetime(item.get("updated_at"))
                        ))

                return results[:limit]
        except Exception as e:
            logger.error(f"Document search failed: {e}")
            return []

    async def get_recent_activity(
        self,
        types: list[str] | None = None,
        limit: int = 50
    ) -> ActivityResponse:
        """
        Get recent activity across all entity types.

        Aggregates recent changes from all services.
        """
        types = types or ["terminology", "term", "template", "document"]
        activities: list[ActivityItem] = []

        # Fetch recent items from each service in parallel
        tasks = []
        if "terminology" in types:
            tasks.append(("terminology", self._get_recent_terminologies(limit)))
        if "term" in types:
            tasks.append(("term", self._get_recent_terms(limit)))
        if "template" in types:
            tasks.append(("template", self._get_recent_templates(limit)))
        if "document" in types:
            tasks.append(("document", self._get_recent_documents(limit)))

        results = await asyncio.gather(*[t[1] for t in tasks], return_exceptions=True)

        for (entity_type, _), result in zip(tasks, results):
            if isinstance(result, Exception):
                logger.error(f"Activity fetch failed for {entity_type}: {result}")
            else:
                activities.extend(result)

        # Sort by timestamp descending
        activities.sort(key=lambda a: a.timestamp, reverse=True)

        return ActivityResponse(
            activities=activities[:limit],
            total=len(activities)
        )

    async def _get_recent_terminologies(self, limit: int) -> list[ActivityItem]:
        """Get recently modified terminologies."""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    f"{settings.def_store_url}/api/def-store/terminologies",
                    params={"page_size": limit},
                    headers={"X-API-Key": settings.api_key}
                )

                if response.status_code != 200:
                    return []

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
                        timestamp = created_at or datetime.now(timezone.utc)

                    items.append(ActivityItem(
                        type="terminology",
                        action=action,
                        entity_id=item.get("terminology_id"),
                        entity_code=item.get("code"),
                        entity_name=item.get("name"),
                        timestamp=timestamp,
                        user=item.get("updated_by") or item.get("created_by")
                    ))

                return items
        except Exception as e:
            logger.error(f"Failed to get recent terminologies: {e}")
            return []

    async def _get_recent_terms(self, limit: int) -> list[ActivityItem]:
        """Get recent term audit log entries."""
        # Terms have an audit log - fetch from it if available
        # For now, just get recent terms similar to terminologies
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                # Get terminologies first
                term_response = await client.get(
                    f"{settings.def_store_url}/api/def-store/terminologies",
                    params={"status": "active", "page_size": 20},
                    headers={"X-API-Key": settings.api_key}
                )

                if term_response.status_code != 200:
                    return []

                items = []
                for terminology in term_response.json().get("items", [])[:10]:
                    term_id = terminology.get("terminology_id")

                    terms_response = await client.get(
                        f"{settings.def_store_url}/api/def-store/terminologies/{term_id}/terms",
                        params={"page_size": 20},
                        headers={"X-API-Key": settings.api_key}
                    )

                    if terms_response.status_code != 200:
                        continue

                    for term in terms_response.json().get("items", []):
                        updated_at = self._parse_datetime(term.get("updated_at"))
                        created_at = self._parse_datetime(term.get("created_at"))

                        if updated_at and created_at and updated_at > created_at:
                            action = "updated"
                            timestamp = updated_at
                        else:
                            action = "created"
                            timestamp = created_at or datetime.now(timezone.utc)

                        items.append(ActivityItem(
                            type="term",
                            action=action,
                            entity_id=term.get("term_id"),
                            entity_code=term.get("code"),
                            entity_name=term.get("value"),
                            timestamp=timestamp,
                            user=term.get("updated_by") or term.get("created_by"),
                            details={"terminology": terminology.get("code")}
                        ))

                return sorted(items, key=lambda x: x.timestamp, reverse=True)[:limit]
        except Exception as e:
            logger.error(f"Failed to get recent terms: {e}")
            return []

    async def _get_recent_templates(self, limit: int) -> list[ActivityItem]:
        """Get recently modified templates."""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    f"{settings.template_store_url}/api/template-store/templates",
                    params={"page_size": limit},
                    headers={"X-API-Key": settings.api_key}
                )

                if response.status_code != 200:
                    return []

                items = []
                for item in response.json().get("items", []):
                    updated_at = self._parse_datetime(item.get("updated_at"))
                    created_at = self._parse_datetime(item.get("created_at"))
                    version = item.get("version", 1)

                    # Version > 1 means updated
                    if version > 1:
                        action = "updated"
                        timestamp = updated_at or created_at or datetime.now(timezone.utc)
                    else:
                        action = "created"
                        timestamp = created_at or datetime.now(timezone.utc)

                    items.append(ActivityItem(
                        type="template",
                        action=action,
                        entity_id=item.get("template_id"),
                        entity_code=item.get("code"),
                        entity_name=item.get("name"),
                        timestamp=timestamp,
                        user=item.get("updated_by") or item.get("created_by"),
                        version=version
                    ))

                return items
        except Exception as e:
            logger.error(f"Failed to get recent templates: {e}")
            return []

    async def _get_recent_documents(self, limit: int) -> list[ActivityItem]:
        """Get recently modified documents."""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                # First fetch templates to build template_id -> code mapping
                # (Document list API returns template_code as null)
                template_response = await client.get(
                    f"{settings.template_store_url}/api/template-store/templates",
                    params={"page_size": 100},
                    headers={"X-API-Key": settings.api_key}
                )

                template_map: dict[str, str] = {}
                if template_response.status_code == 200:
                    for tpl in template_response.json().get("items", []):
                        template_map[tpl.get("template_id")] = tpl.get("code", "")

                # Fetch documents
                response = await client.get(
                    f"{settings.document_store_url}/api/document-store/documents",
                    params={"page_size": limit},
                    headers={"X-API-Key": settings.api_key}
                )

                if response.status_code != 200:
                    return []

                items = []
                for item in response.json().get("items", []):
                    updated_at = self._parse_datetime(item.get("updated_at"))
                    created_at = self._parse_datetime(item.get("created_at"))
                    version = item.get("version", 1)

                    if version > 1:
                        action = "updated"
                        timestamp = updated_at or created_at or datetime.now(timezone.utc)
                    else:
                        action = "created"
                        timestamp = created_at or datetime.now(timezone.utc)

                    # Get template_code from our lookup (API returns null)
                    template_id = item.get("template_id")
                    template_code = template_map.get(template_id, item.get("template_code") or "unknown")

                    items.append(ActivityItem(
                        type="document",
                        action=action,
                        entity_id=item.get("document_id"),
                        entity_code=template_code,
                        entity_name=f"{template_code} document",
                        timestamp=timestamp,
                        user=item.get("updated_by") or item.get("created_by"),
                        version=version
                    ))

                return items
        except Exception as e:
            logger.error(f"Failed to get recent documents: {e}")
            return []

    async def get_term_documents(
        self,
        term_id: str,
        limit: int = 100
    ) -> TermDocumentsResponse:
        """
        Get documents that reference a specific term.

        Uses PostgreSQL reporting database to find documents with term_references
        containing the given term_id.
        """
        if not self.postgres_pool:
            logger.warning("PostgreSQL not available for term->document lookup")
            return TermDocumentsResponse(term_id=term_id, documents=[], total=0)

        try:
            async with self.postgres_pool.acquire() as conn:
                # Get all document tables
                tables = await conn.fetch("""
                    SELECT table_name
                    FROM information_schema.tables
                    WHERE table_schema = 'public'
                    AND table_name LIKE 'doc_%'
                """)

                documents = []

                for table_row in tables:
                    table_name = table_row["table_name"]

                    # Check if table has term_references_json column
                    has_column = await conn.fetchval("""
                        SELECT EXISTS (
                            SELECT 1 FROM information_schema.columns
                            WHERE table_schema = 'public'
                            AND table_name = $1
                            AND column_name = 'term_references_json'
                        )
                    """, table_name)

                    if not has_column:
                        continue

                    # Search for documents with this term_id in term_references
                    # JSONB search: find where any value in the JSON contains the term_id
                    rows = await conn.fetch(f"""
                        SELECT
                            document_id,
                            template_id,
                            status,
                            created_at,
                            term_references_json
                        FROM "{table_name}"
                        WHERE term_references_json::text LIKE $1
                        LIMIT $2
                    """, f'%"{term_id}"%', limit - len(documents))

                    # Extract template_code from table name
                    template_code = table_name[4:].upper()  # Remove 'doc_' prefix

                    for row in rows:
                        # Find which field references this term
                        term_refs = row["term_references_json"] or {}
                        field_path = None
                        for field, ref in term_refs.items():
                            if ref == term_id:
                                field_path = field
                                break
                            elif isinstance(ref, list) and term_id in ref:
                                field_path = f"{field}[]"
                                break

                        documents.append(DocumentReference(
                            document_id=row["document_id"],
                            template_id=row["template_id"],
                            template_code=template_code,
                            field_path=field_path or "unknown",
                            status=row["status"],
                            created_at=row["created_at"]
                        ))

                    if len(documents) >= limit:
                        break

                return TermDocumentsResponse(
                    term_id=term_id,
                    documents=documents[:limit],
                    total=len(documents)
                )

        except Exception as e:
            logger.error(f"Failed to get term documents: {e}")
            return TermDocumentsResponse(term_id=term_id, documents=[], total=0)

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
            else:
                return EntityReferencesResponse(error=f"Unknown entity type: {entity_type}")
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
                        ref_code=tpl.get("code"),
                        ref_name=tpl.get("name"),
                        field_path=None,
                        status=ref_status,
                        error=f"Template is {tpl_status}" if tpl_status != "active" else None
                    ))
                else:
                    broken_count += 1
                    references.append(EntityReference(
                        ref_type="template",
                        ref_id=template_id,
                        ref_code=None,
                        ref_name=None,
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
                            ref_code=term.get("code"),
                            ref_name=term.get("value"),
                            field_path=field_path_str,
                            status=ref_status,
                            error=f"Term is {term_status}" if term_status != "active" else None
                        ))
                    else:
                        broken_count += 1
                        references.append(EntityReference(
                            ref_type="term",
                            ref_id=term_id,
                            ref_code=None,
                            ref_name=None,
                            field_path=field_path_str,
                            status="broken",
                            error="Term not found"
                        ))

            # Build template code from lookup if needed
            template_code = None
            for ref in references:
                if ref.ref_type == "template":
                    template_code = ref.ref_code
                    break

            return EntityReferencesResponse(
                entity=EntityDetails(
                    entity_type="document",
                    entity_id=document_id,
                    entity_code=template_code,
                    entity_name=f"{template_code or 'Unknown'} document",
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
                        ref_code=parent.get("code"),
                        ref_name=parent.get("name"),
                        field_path="extends",
                        status=ref_status,
                        error=f"Parent template is {parent_status}" if parent_status != "active" else None
                    ))
                else:
                    broken_count += 1
                    references.append(EntityReference(
                        ref_type="template",
                        ref_id=extends,
                        ref_code=None,  # Code unknown for missing template
                        ref_name=None,
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
                                ref_code=terminology.get("code"),
                                ref_name=terminology.get("name"),
                                field_path=f"fields.{field_name}.terminology_ref",
                                status=ref_status,
                                error=f"Terminology is {term_status}" if term_status != "active" else None
                            ))
                        else:
                            broken_count += 1
                            references.append(EntityReference(
                                ref_type="terminology",
                                ref_id=term_ref,
                                ref_code=None,
                                ref_name=None,
                                field_path=f"fields.{field_name}.terminology_ref",
                                status="broken",
                                error="Terminology not found"
                            ))

                # Check nested template references
                if field_type == "object":
                    nested_ref = field.get("template_ref")
                    if nested_ref:
                        nested_response = await client.get(
                            f"{settings.template_store_url}/api/template-store/templates/by-code/{nested_ref}",
                            headers={"X-API-Key": settings.api_key}
                        )

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
                                ref_code=nested_ref,
                                ref_name=nested.get("name"),
                                field_path=f"fields.{field_name}.template_ref",
                                status=ref_status,
                                error=f"Referenced template is {nested_status}" if nested_status != "active" else None
                            ))
                        else:
                            broken_count += 1
                            references.append(EntityReference(
                                ref_type="template",
                                ref_id=nested_ref,
                                ref_code=nested_ref,
                                ref_name=None,
                                field_path=f"fields.{field_name}.template_ref",
                                status="broken",
                                error="Referenced template not found"
                            ))

            return EntityReferencesResponse(
                entity=EntityDetails(
                    entity_type="template",
                    entity_id=template_id,
                    entity_code=tpl.get("code"),
                    entity_name=tpl.get("name"),
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

            if response.status_code != 200:
                return EntityReferencesResponse(error=f"Terminology not found: {terminology_id}")

            terminology = response.json()

            # Terminologies don't have outgoing references
            return EntityReferencesResponse(
                entity=EntityDetails(
                    entity_type="terminology",
                    entity_id=terminology_id,
                    entity_code=terminology.get("code"),
                    entity_name=terminology.get("name"),
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
                        ref_code=terminology.get("code"),
                        ref_name=terminology.get("name"),
                        field_path="terminology_id",
                        status=ref_status,
                        error=f"Terminology is {term_status}" if term_status != "active" else None
                    ))
                else:
                    broken_count += 1
                    references.append(EntityReference(
                        ref_type="terminology",
                        ref_id=terminology_id,
                        ref_code=None,
                        ref_name=None,
                        field_path="terminology_id",
                        status="broken",
                        error="Terminology not found"
                    ))

            return EntityReferencesResponse(
                entity=EntityDetails(
                    entity_type="term",
                    entity_id=term_id,
                    entity_code=term.get("code"),
                    entity_name=term.get("value"),
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
        limit: int = 100
    ) -> ReferencedByResponse:
        """
        Find all entities that reference the given entity.

        - Template: documents using it, templates extending it, templates with template_ref
        - Terminology: templates with terminology_ref to it
        - Term: documents with term_references to it

        Args:
            entity_type: document, template, terminology, term
            entity_id: The entity ID
            limit: Max results to return

        Returns:
            ReferencedByResponse with list of referencing entities
        """
        try:
            if entity_type == "template":
                return await self._get_template_referenced_by(entity_id, limit)
            elif entity_type == "terminology":
                return await self._get_terminology_referenced_by(entity_id, limit)
            elif entity_type == "term":
                return await self._get_term_referenced_by(entity_id, limit)
            elif entity_type == "document":
                # Documents don't get referenced by other entities
                return ReferencedByResponse(
                    entity_type="document",
                    entity_id=entity_id,
                    referenced_by=[],
                    total=0
                )
            else:
                return ReferencedByResponse(
                    entity_type=entity_type,
                    entity_id=entity_id,
                    error=f"Unknown entity type: {entity_type}"
                )
        except Exception as e:
            logger.error(f"Failed to get referenced-by for {entity_type}/{entity_id}: {e}")
            return ReferencedByResponse(
                entity_type=entity_type,
                entity_id=entity_id,
                error=str(e)
            )

    async def _get_template_referenced_by(
        self, template_id: str, limit: int
    ) -> ReferencedByResponse:
        """Find entities referencing a template."""
        references: list[IncomingReference] = []

        async with httpx.AsyncClient(timeout=30.0) as client:
            # First, get the template details to know its code
            tpl_response = await client.get(
                f"{settings.template_store_url}/api/template-store/templates/{template_id}",
                headers={"X-API-Key": settings.api_key}
            )

            if tpl_response.status_code != 200:
                return ReferencedByResponse(
                    entity_type="template",
                    entity_id=template_id,
                    error=f"Template not found: {template_id}"
                )

            tpl = tpl_response.json()
            template_code = tpl.get("code")

            # 1. Find documents using this template
            doc_response = await client.get(
                f"{settings.document_store_url}/api/document-store/documents",
                params={"template_id": template_id, "page_size": limit},
                headers={"X-API-Key": settings.api_key}
            )

            if doc_response.status_code == 200:
                docs = doc_response.json()
                for doc in docs.get("items", []):
                    references.append(IncomingReference(
                        entity_type="document",
                        entity_id=doc.get("document_id"),
                        entity_code=None,
                        entity_name=f"{template_code} document",
                        entity_status=doc.get("status"),
                        field_path="template_id",
                        reference_type="uses_template"
                    ))

            # 2. Find templates that extend this one
            extend_response = await client.get(
                f"{settings.template_store_url}/api/template-store/templates",
                params={"extends": template_id, "page_size": limit},
                headers={"X-API-Key": settings.api_key}
            )

            if extend_response.status_code == 200:
                templates = extend_response.json()
                for t in templates.get("items", []):
                    references.append(IncomingReference(
                        entity_type="template",
                        entity_id=t.get("template_id"),
                        entity_code=t.get("code"),
                        entity_name=t.get("name"),
                        entity_status=t.get("status"),
                        field_path="extends",
                        reference_type="extends"
                    ))

            # 3. Find templates with template_ref to this template's code
            # This requires scanning all templates - do a full list and filter
            all_tpl_response = await client.get(
                f"{settings.template_store_url}/api/template-store/templates",
                params={"page_size": 100},  # Get templates (max page size)
                headers={"X-API-Key": settings.api_key}
            )

            if all_tpl_response.status_code == 200:
                all_templates = all_tpl_response.json()
                for t in all_templates.get("items", []):
                    # Check fields for template_ref matching our code
                    for field in t.get("fields", []):
                        if field.get("template_ref") == template_code:
                            references.append(IncomingReference(
                                entity_type="template",
                                entity_id=t.get("template_id"),
                                entity_code=t.get("code"),
                                entity_name=t.get("name"),
                                entity_status=t.get("status"),
                                field_path=f"fields.{field.get('name')}.template_ref",
                                reference_type="template_ref"
                            ))
                        if field.get("array_template_ref") == template_code:
                            references.append(IncomingReference(
                                entity_type="template",
                                entity_id=t.get("template_id"),
                                entity_code=t.get("code"),
                                entity_name=t.get("name"),
                                entity_status=t.get("status"),
                                field_path=f"fields.{field.get('name')}.array_template_ref",
                                reference_type="template_ref"
                            ))

        return ReferencedByResponse(
            entity_type="template",
            entity_id=template_id,
            entity_code=template_code,
            entity_name=tpl.get("name"),
            referenced_by=references[:limit],
            total=len(references)
        )

    async def _get_terminology_referenced_by(
        self, terminology_id: str, limit: int
    ) -> ReferencedByResponse:
        """Find templates referencing a terminology."""
        references: list[IncomingReference] = []

        async with httpx.AsyncClient(timeout=30.0) as client:
            # Get terminology details
            term_response = await client.get(
                f"{settings.def_store_url}/api/def-store/terminologies/{terminology_id}",
                headers={"X-API-Key": settings.api_key}
            )

            if term_response.status_code != 200:
                return ReferencedByResponse(
                    entity_type="terminology",
                    entity_id=terminology_id,
                    error=f"Terminology not found: {terminology_id}"
                )

            terminology = term_response.json()
            terminology_code = terminology.get("code")

            # Find templates with terminology_ref to this terminology
            all_tpl_response = await client.get(
                f"{settings.template_store_url}/api/template-store/templates",
                params={"page_size": 100},  # Max page size
                headers={"X-API-Key": settings.api_key}
            )

            if all_tpl_response.status_code == 200:
                all_templates = all_tpl_response.json()
                for t in all_templates.get("items", []):
                    for field in t.get("fields", []):
                        # Check terminology_ref (could be ID or code)
                        term_ref = field.get("terminology_ref")
                        if term_ref and (term_ref == terminology_id or term_ref == terminology_code):
                            references.append(IncomingReference(
                                entity_type="template",
                                entity_id=t.get("template_id"),
                                entity_code=t.get("code"),
                                entity_name=t.get("name"),
                                entity_status=t.get("status"),
                                field_path=f"fields.{field.get('name')}.terminology_ref",
                                reference_type="terminology_ref"
                            ))
                        # Check array_terminology_ref
                        array_term_ref = field.get("array_terminology_ref")
                        if array_term_ref and (array_term_ref == terminology_id or array_term_ref == terminology_code):
                            references.append(IncomingReference(
                                entity_type="template",
                                entity_id=t.get("template_id"),
                                entity_code=t.get("code"),
                                entity_name=t.get("name"),
                                entity_status=t.get("status"),
                                field_path=f"fields.{field.get('name')}.array_terminology_ref",
                                reference_type="terminology_ref"
                            ))

        return ReferencedByResponse(
            entity_type="terminology",
            entity_id=terminology_id,
            entity_code=terminology_code,
            entity_name=terminology.get("name"),
            referenced_by=references[:limit],
            total=len(references)
        )

    async def _get_term_referenced_by(
        self, term_id: str, limit: int
    ) -> ReferencedByResponse:
        """Find documents referencing a term."""
        references: list[IncomingReference] = []

        async with httpx.AsyncClient(timeout=30.0) as client:
            # Get term details
            term_response = await client.get(
                f"{settings.def_store_url}/api/def-store/terms/{term_id}",
                headers={"X-API-Key": settings.api_key}
            )

            if term_response.status_code != 200:
                return ReferencedByResponse(
                    entity_type="term",
                    entity_id=term_id,
                    error=f"Term not found: {term_id}"
                )

            term = term_response.json()

            # Use the existing term->documents endpoint on Document Store
            # Or query PostgreSQL if available
            if self.postgres_pool:
                # Query PostgreSQL for documents with this term_id in term_references
                try:
                    async with self.postgres_pool.acquire() as conn:
                        # Search across all doc_ tables for term references
                        # This is a simplified approach - in production you'd want
                        # to track term references in a dedicated table
                        tables = await conn.fetch("""
                            SELECT tablename FROM pg_tables
                            WHERE schemaname = 'public' AND tablename LIKE 'doc_%'
                        """)

                        for table in tables:
                            table_name = table["tablename"]
                            # Check if term_references_json column exists
                            try:
                                rows = await conn.fetch(f"""
                                    SELECT document_id, template_id, status
                                    FROM {table_name}
                                    WHERE term_references_json::text LIKE $1
                                    LIMIT $2
                                """, f'%"{term_id}"%', limit)

                                for row in rows:
                                    references.append(IncomingReference(
                                        entity_type="document",
                                        entity_id=row["document_id"],
                                        entity_code=None,
                                        entity_name=f"Document {row['document_id'][:8]}...",
                                        entity_status=row["status"],
                                        field_path="term_references",
                                        reference_type="term_ref"
                                    ))
                            except Exception:
                                # Table might not have term_references_json
                                continue
                except Exception as e:
                    logger.warning(f"PostgreSQL query failed for term references: {e}")

            # Also try the Document Store API if we don't have enough results
            if len(references) < limit:
                # The document store doesn't have a direct term->documents endpoint
                # We'd need to add one or do a full scan
                # For now, we rely on PostgreSQL or return partial results
                pass

        return ReferencedByResponse(
            entity_type="term",
            entity_id=term_id,
            entity_code=term.get("code"),
            entity_name=term.get("value"),
            referenced_by=references[:limit],
            total=len(references)
        )

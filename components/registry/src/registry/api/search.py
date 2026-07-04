"""Search API endpoints."""


from fastapi import APIRouter, Body, Depends

from wip_auth import UserIdentity

from ..models.api_models import (
    SearchBulkResponse,
    SearchByTermItem,
    SearchItem,
    SearchResponse,
    SearchResult,
)
from ..models.entry import RegistryEntry
from ..services.auth import require_api_key
from ..services.search import SearchService

router = APIRouter()


@router.post(
    "/by-fields",
    response_model=SearchBulkResponse,
    summary="Search by field values (bulk)"
)
async def search_by_fields(
    items: list[SearchItem] = Body(...),
    identity: UserIdentity = Depends(require_api_key)
) -> SearchBulkResponse:
    """Search for registry entries by field values in composite keys."""
    results = []

    for i, item in enumerate(items):
        try:
            query = SearchService.build_field_query(
                field_criteria=item.field_criteria,
                restrict_to_namespaces=item.restrict_to_namespaces,
                restrict_to_entity_types=item.restrict_to_entity_types,
                include_inactive=item.include_inactive
            )

            entries = await RegistryEntry.find(query).to_list()

            search_results = []
            for entry in entries:
                matched_in, matched_ns, matched_et, matched_key = SearchService.find_match_location(
                    entry, item.field_criteria
                )
                search_results.append(SearchResult(
                    registry_id=entry.entry_id,
                    namespace=entry.namespace,
                    entity_type=entry.entity_type,
                    matched_in=matched_in,
                    matched_namespace=matched_ns,
                    matched_entity_type=matched_et,
                    matched_composite_key=matched_key,
                    all_synonyms=entry.synonyms,
                ))

            results.append(SearchResponse(
                input_index=i,
                results=search_results,
                total_matches=len(search_results),
            ))

        except Exception:
            results.append(SearchResponse(
                input_index=i, results=[], total_matches=0,
            ))

    return SearchBulkResponse(results=results)


@router.post(
    "/by-term",
    response_model=SearchBulkResponse,
    summary="Search by free-text term (bulk)"
)
async def search_by_term(
    items: list[SearchByTermItem] = Body(...),
    identity: UserIdentity = Depends(require_api_key)
) -> SearchBulkResponse:
    """Search for a term across any field in any composite key."""
    results = []

    for i, item in enumerate(items):
        try:
            # CASE-568: regex search is the primary (and only) path. The old
            # $text attempt always threw — no text index is declared on
            # RegistryEntry — so every call paid a guaranteed-failing query
            # before this fallback served the actual result.
            query = SearchService.build_regex_search_query(
                term=item.term,
                restrict_to_namespaces=item.restrict_to_namespaces,
                restrict_to_entity_types=item.restrict_to_entity_types,
                include_inactive=item.include_inactive
            )
            # CASE-572: count before limiting so total_matches is the true
            # match count even when the fetch is bounded.
            total = await RegistryEntry.find(query).count()
            find_query = RegistryEntry.find(query)
            if item.limit is not None:
                find_query = find_query.limit(item.limit)
            entries = await find_query.to_list()

            search_results = []
            for entry in entries:
                matched_in = "primary"
                matched_ns = entry.namespace
                matched_et = entry.entity_type
                matched_key = entry.primary_composite_key

                term_lower = item.term.lower()
                found_in_primary = any(
                    term_lower in str(v).lower()
                    for v in entry.primary_composite_key.values()
                )

                if not found_in_primary:
                    for syn in entry.synonyms:
                        found_in_syn = any(
                            term_lower in str(v).lower()
                            for v in syn.composite_key.values()
                        )
                        if found_in_syn:
                            matched_in = "synonym"
                            matched_ns = syn.namespace
                            matched_et = syn.entity_type
                            matched_key = syn.composite_key
                            break

                search_results.append(SearchResult(
                    registry_id=entry.entry_id,
                    namespace=entry.namespace,
                    entity_type=entry.entity_type,
                    matched_in=matched_in,
                    matched_namespace=matched_ns,
                    matched_entity_type=matched_et,
                    matched_composite_key=matched_key,
                    all_synonyms=entry.synonyms,
                ))

            results.append(SearchResponse(
                input_index=i,
                results=search_results,
                total_matches=total,
            ))

        except Exception:
            results.append(SearchResponse(
                input_index=i, results=[], total_matches=0,
            ))

    return SearchBulkResponse(results=results)


@router.post(
    "/across-namespaces",
    response_model=SearchBulkResponse,
    summary="Search across all namespaces (bulk)"
)
async def search_across_namespaces(
    items: list[SearchItem] = Body(...),
    identity: UserIdentity = Depends(require_api_key)
) -> SearchBulkResponse:
    """Search for entries across ALL namespaces."""
    modified_items = []
    for item in items:
        modified_items.append(SearchItem(
            field_criteria=item.field_criteria,
            restrict_to_namespaces=None,
            restrict_to_entity_types=item.restrict_to_entity_types,
            include_inactive=item.include_inactive,
        ))

    return await search_by_fields(modified_items, identity)

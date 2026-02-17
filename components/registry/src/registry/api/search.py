"""Search API endpoints."""

from typing import List

from fastapi import APIRouter, Body, Depends

from ..models.entry import RegistryEntry
from ..models.api_models import (
    SearchItem,
    SearchByTermItem,
    SearchResult,
    SearchResponse,
    SearchBulkResponse,
)
from ..services.search import SearchService
from ..services.auth import require_api_key

router = APIRouter()


@router.post(
    "/by-fields",
    response_model=SearchBulkResponse,
    summary="Search by field values (bulk)"
)
async def search_by_fields(
    items: List[SearchItem] = Body(...),
    api_key: str = Depends(require_api_key)
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
    items: List[SearchByTermItem] = Body(...),
    api_key: str = Depends(require_api_key)
) -> SearchBulkResponse:
    """Search for a term across any field in any composite key."""
    results = []

    for i, item in enumerate(items):
        try:
            try:
                query = SearchService.build_text_search_query(
                    term=item.term,
                    restrict_to_namespaces=item.restrict_to_namespaces,
                    restrict_to_entity_types=item.restrict_to_entity_types,
                    include_inactive=item.include_inactive
                )
                entries = await RegistryEntry.find(query).to_list()
            except Exception:
                query = SearchService.build_regex_search_query(
                    term=item.term,
                    restrict_to_namespaces=item.restrict_to_namespaces,
                    restrict_to_entity_types=item.restrict_to_entity_types,
                    include_inactive=item.include_inactive
                )
                entries = await RegistryEntry.find(query).to_list()

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
                total_matches=len(search_results),
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
    items: List[SearchItem] = Body(...),
    api_key: str = Depends(require_api_key)
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

    return await search_by_fields(modified_items, api_key)

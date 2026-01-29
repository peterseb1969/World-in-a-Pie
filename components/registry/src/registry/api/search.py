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
    """
    Search for registry entries by field values in composite keys.

    This searches across both primary composite keys AND synonym composite keys.
    The search properly handles the nested structure of composite keys.

    Example:
    ```json
    {
        "field_criteria": {"vendor_sku": "AB-123"},
        "restrict_to_namespaces": ["vendor1", "vendor2"]
    }
    ```

    This will find entries where:
    - primary_composite_key.vendor_sku = "AB-123" OR
    - synonyms[].composite_key.vendor_sku = "AB-123"
    """
    results = []

    for i, item in enumerate(items):
        try:
            # Build the properly structured MongoDB query
            query = SearchService.build_field_query(
                field_criteria=item.field_criteria,
                restrict_to_namespaces=item.restrict_to_namespaces,
                include_inactive=item.include_inactive
            )

            # Execute search
            entries = await RegistryEntry.find(query).to_list()

            # Convert to search results, identifying match location
            search_results = []
            for entry in entries:
                matched_in, matched_ns, matched_key = SearchService.find_match_location(
                    entry, item.field_criteria
                )
                search_results.append(SearchResult(
                    registry_id=entry.entry_id,
                    namespace=entry.primary_namespace,
                    matched_in=matched_in,
                    matched_namespace=matched_ns,
                    matched_composite_key=matched_key,
                    all_synonyms=entry.synonyms,
                    additional_ids=entry.additional_ids,
                ))

            results.append(SearchResponse(
                input_index=i,
                results=search_results,
                total_matches=len(search_results),
            ))

        except ValueError as e:
            # Invalid field name or other validation error
            results.append(SearchResponse(
                input_index=i,
                results=[],
                total_matches=0,
            ))

        except Exception as e:
            # Log error but return empty results
            results.append(SearchResponse(
                input_index=i,
                results=[],
                total_matches=0,
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
    """
    Search for a term across any field in any composite key.

    This performs a broad text search that finds the term in any value
    within primary composite keys or synonym composite keys.

    Example:
    ```json
    {
        "term": "Berlin",
        "restrict_to_namespaces": null
    }
    ```

    This will find entries where "Berlin" appears in any field value.
    """
    results = []

    for i, item in enumerate(items):
        try:
            # Build text search query
            # First try MongoDB text search if index exists
            try:
                query = SearchService.build_text_search_query(
                    term=item.term,
                    restrict_to_namespaces=item.restrict_to_namespaces,
                    include_inactive=item.include_inactive
                )
                entries = await RegistryEntry.find(query).to_list()
            except Exception:
                # Fall back to regex search if text index not available
                query = SearchService.build_regex_search_query(
                    term=item.term,
                    restrict_to_namespaces=item.restrict_to_namespaces,
                    include_inactive=item.include_inactive
                )
                entries = await RegistryEntry.find(query).to_list()

            # For text search, we need to scan results to find exact match location
            search_results = []
            for entry in entries:
                # Check where the term appears
                matched_in = "primary"
                matched_ns = entry.primary_namespace
                matched_key = entry.primary_composite_key

                # Check if term appears in primary key values
                term_lower = item.term.lower()
                found_in_primary = any(
                    term_lower in str(v).lower()
                    for v in entry.primary_composite_key.values()
                )

                if not found_in_primary:
                    # Check synonyms
                    for syn in entry.synonyms:
                        found_in_syn = any(
                            term_lower in str(v).lower()
                            for v in syn.composite_key.values()
                        )
                        if found_in_syn:
                            matched_in = "synonym"
                            matched_ns = syn.namespace
                            matched_key = syn.composite_key
                            break

                search_results.append(SearchResult(
                    registry_id=entry.entry_id,
                    namespace=entry.primary_namespace,
                    matched_in=matched_in,
                    matched_namespace=matched_ns,
                    matched_composite_key=matched_key,
                    all_synonyms=entry.synonyms,
                    additional_ids=entry.additional_ids,
                ))

            results.append(SearchResponse(
                input_index=i,
                results=search_results,
                total_matches=len(search_results),
            ))

        except Exception as e:
            results.append(SearchResponse(
                input_index=i,
                results=[],
                total_matches=0,
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
    """
    Search for entries across ALL namespaces.

    This is a convenience endpoint that explicitly searches without
    namespace restriction. It's equivalent to calling /by-fields
    with restrict_to_namespaces=null.

    Use this when you have a value (like a vendor SKU) and want to
    find it regardless of which namespace it was registered in.
    """
    # Remove any namespace restrictions and delegate to by-fields
    modified_items = []
    for item in items:
        modified_items.append(SearchItem(
            field_criteria=item.field_criteria,
            restrict_to_namespaces=None,  # Force search all namespaces
            include_inactive=item.include_inactive,
        ))

    return await search_by_fields(modified_items, api_key)

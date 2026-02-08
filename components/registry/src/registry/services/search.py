"""Search service for registry entries."""

import re
from typing import Any, Optional

from ..models.entry import RegistryEntry, Synonym
from ..models.api_models import SearchResult
from .hash import HashService


class SearchService:
    """Service for searching registry entries."""

    @staticmethod
    def build_field_query(
        field_criteria: dict[str, Any],
        restrict_to_pools: Optional[list[str]] = None,
        include_inactive: bool = False
    ) -> dict[str, Any]:
        """
        Build a MongoDB query for searching composite key fields.

        This properly handles searching within nested composite_key dictionaries
        in both primary keys and synonyms.

        Args:
            field_criteria: Field-value pairs to search for
            restrict_to_pools: Optional list of namespaces to restrict search
            include_inactive: Whether to include inactive entries

        Returns:
            MongoDB query dictionary
        """
        # Build conditions for matching fields
        or_conditions = []

        for field_name, field_value in field_criteria.items():
            # Sanitize field name to prevent injection
            safe_field_name = SearchService._sanitize_field_name(field_name)

            # Search in primary composite key
            primary_condition = {
                f"primary_composite_key.{safe_field_name}": field_value
            }

            # Search in synonym composite keys
            synonym_condition = {
                "synonyms": {
                    "$elemMatch": {
                        f"composite_key.{safe_field_name}": field_value
                    }
                }
            }

            # Add namespace restriction to synonym search if specified
            if restrict_to_pools:
                synonym_condition["synonyms"]["$elemMatch"]["namespace"] = {
                    "$in": restrict_to_pools
                }

            or_conditions.append(primary_condition)
            or_conditions.append(synonym_condition)

        # Combine all conditions
        query: dict[str, Any] = {"$or": or_conditions}

        # Add namespace restriction for primary key
        if restrict_to_pools:
            # Either primary namespace matches OR a synonym namespace matches
            query = {
                "$and": [
                    query,
                    {
                        "$or": [
                            {"primary_namespace": {"$in": restrict_to_pools}},
                            {"synonyms.namespace": {"$in": restrict_to_pools}}
                        ]
                    }
                ]
            }

        # Filter by status unless including inactive
        if not include_inactive:
            if "$and" in query:
                query["$and"].append({"status": "active"})
            else:
                query = {"$and": [query, {"status": "active"}]}

        return query

    @staticmethod
    def build_text_search_query(
        term: str,
        restrict_to_pools: Optional[list[str]] = None,
        include_inactive: bool = False
    ) -> dict[str, Any]:
        """
        Build a MongoDB query for free-text search across composite keys.

        Uses MongoDB text index for efficient search.

        Args:
            term: Search term
            restrict_to_pools: Optional list of namespaces to restrict search
            include_inactive: Whether to include inactive entries

        Returns:
            MongoDB query dictionary
        """
        # Use MongoDB text search
        query: dict[str, Any] = {
            "$text": {"$search": term}
        }

        # Add namespace restriction
        if restrict_to_pools:
            query["$or"] = [
                {"primary_namespace": {"$in": restrict_to_pools}},
                {"synonyms.namespace": {"$in": restrict_to_pools}}
            ]

        # Filter by status
        if not include_inactive:
            query["status"] = "active"

        return query

    @staticmethod
    def build_regex_search_query(
        term: str,
        restrict_to_pools: Optional[list[str]] = None,
        include_inactive: bool = False
    ) -> dict[str, Any]:
        """
        Build a MongoDB query using regex for partial matching.

        Fallback when text index is not available or for more flexible matching.

        Args:
            term: Search term (will be escaped for regex safety)
            restrict_to_pools: Optional list of namespaces
            include_inactive: Whether to include inactive entries

        Returns:
            MongoDB query dictionary
        """
        # Escape special regex characters
        escaped_term = re.escape(term)
        regex_pattern = {"$regex": escaped_term, "$options": "i"}

        # Build conditions to search across all string values in composite keys
        # This is a simplified approach - searches for the term in any field value
        or_conditions: list[dict] = []

        # We'll use a special aggregation or $where for deep search
        # For now, use a practical approach with common patterns

        # Search in primary composite key (assumes string values)
        # MongoDB doesn't support regex on nested unknown fields directly,
        # so we use $where or accept that we search specific fields

        # Practical approach: search for term anywhere in the document
        query: dict[str, Any] = {
            "$or": [
                # Search in primary key values (converted to string representation)
                {"primary_composite_key": {"$regex": escaped_term, "$options": "i"}},
                # Search in synonym key values
                {"synonyms.composite_key": {"$regex": escaped_term, "$options": "i"}}
            ]
        }

        # Add namespace restriction
        if restrict_to_pools:
            query = {
                "$and": [
                    query,
                    {
                        "$or": [
                            {"primary_namespace": {"$in": restrict_to_pools}},
                            {"synonyms.namespace": {"$in": restrict_to_pools}}
                        ]
                    }
                ]
            }

        # Filter by status
        if not include_inactive:
            if "$and" in query:
                query["$and"].append({"status": "active"})
            else:
                query["status"] = "active"

        return query

    @staticmethod
    def _sanitize_field_name(field_name: str) -> str:
        """
        Sanitize a field name to prevent NoSQL injection.

        Removes or escapes dangerous characters.
        """
        # Remove MongoDB operators
        if field_name.startswith("$"):
            raise ValueError(f"Invalid field name: {field_name}")

        # Only allow alphanumeric, underscore, and dot (for nested fields)
        if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_.]*$', field_name):
            raise ValueError(f"Invalid field name format: {field_name}")

        return field_name

    @staticmethod
    def entry_to_search_result(
        entry: RegistryEntry,
        matched_in: str,
        matched_pool_id: str,
        matched_composite_key: dict[str, Any]
    ) -> SearchResult:
        """
        Convert a RegistryEntry to a SearchResult.

        Args:
            entry: The registry entry
            matched_in: Where the match was found ("primary" or "synonym")
            matched_pool_id: ID pool where match was found
            matched_composite_key: The matching composite key

        Returns:
            SearchResult object
        """
        return SearchResult(
            registry_id=entry.entry_id,
            pool_id=entry.primary_namespace,
            matched_in=matched_in,
            matched_pool_id=matched_pool_id,
            matched_composite_key=matched_composite_key,
            all_synonyms=entry.synonyms,
            additional_ids=entry.additional_ids
        )

    @staticmethod
    def find_match_location(
        entry: RegistryEntry,
        field_criteria: dict[str, Any]
    ) -> tuple[str, str, dict[str, Any]]:
        """
        Determine where in the entry the match was found.

        Args:
            entry: The registry entry
            field_criteria: The search criteria

        Returns:
            Tuple of (matched_in, matched_namespace, matched_composite_key)
        """
        # Check primary key first
        if SearchService._matches_criteria(entry.primary_composite_key, field_criteria):
            return "primary", entry.primary_namespace, entry.primary_composite_key

        # Check synonyms
        for synonym in entry.synonyms:
            if SearchService._matches_criteria(synonym.composite_key, field_criteria):
                return "synonym", synonym.namespace, synonym.composite_key

        # Default to primary (shouldn't happen if query was correct)
        return "primary", entry.primary_namespace, entry.primary_composite_key

    @staticmethod
    def _matches_criteria(composite_key: dict[str, Any], criteria: dict[str, Any]) -> bool:
        """Check if a composite key matches the search criteria."""
        for field, value in criteria.items():
            if field not in composite_key:
                return False
            if composite_key[field] != value:
                return False
        return True

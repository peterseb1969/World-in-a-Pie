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
        restrict_to_namespaces: Optional[list[str]] = None,
        restrict_to_entity_types: Optional[list[str]] = None,
        include_inactive: bool = False
    ) -> dict[str, Any]:
        """
        Build a MongoDB query for searching composite key fields.

        Args:
            field_criteria: Field-value pairs to search for
            restrict_to_namespaces: Optional list of namespaces to restrict search
            restrict_to_entity_types: Optional list of entity types to restrict search
            include_inactive: Whether to include inactive entries

        Returns:
            MongoDB query dictionary
        """
        or_conditions = []

        for field_name, field_value in field_criteria.items():
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

            # Add namespace/entity_type restriction to synonym search
            if restrict_to_namespaces:
                synonym_condition["synonyms"]["$elemMatch"]["namespace"] = {
                    "$in": restrict_to_namespaces
                }
            if restrict_to_entity_types:
                synonym_condition["synonyms"]["$elemMatch"]["entity_type"] = {
                    "$in": restrict_to_entity_types
                }

            or_conditions.append(primary_condition)
            or_conditions.append(synonym_condition)

        query: dict[str, Any] = {"$or": or_conditions}

        # Add namespace/entity_type restriction for primary entries
        restriction_conditions = []
        if restrict_to_namespaces:
            restriction_conditions.append({
                "$or": [
                    {"namespace": {"$in": restrict_to_namespaces}},
                    {"synonyms.namespace": {"$in": restrict_to_namespaces}}
                ]
            })
        if restrict_to_entity_types:
            restriction_conditions.append({
                "$or": [
                    {"entity_type": {"$in": restrict_to_entity_types}},
                    {"synonyms.entity_type": {"$in": restrict_to_entity_types}}
                ]
            })

        if restriction_conditions:
            query = {"$and": [query] + restriction_conditions}

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
        restrict_to_namespaces: Optional[list[str]] = None,
        restrict_to_entity_types: Optional[list[str]] = None,
        include_inactive: bool = False
    ) -> dict[str, Any]:
        """
        Build a MongoDB query for free-text search across composite keys.

        Args:
            term: Search term
            restrict_to_namespaces: Optional list of namespaces to restrict search
            restrict_to_entity_types: Optional list of entity types to restrict search
            include_inactive: Whether to include inactive entries

        Returns:
            MongoDB query dictionary
        """
        query: dict[str, Any] = {
            "$text": {"$search": term}
        }

        if restrict_to_namespaces:
            query["$or"] = [
                {"namespace": {"$in": restrict_to_namespaces}},
                {"synonyms.namespace": {"$in": restrict_to_namespaces}}
            ]

        if restrict_to_entity_types:
            et_condition = {
                "$or": [
                    {"entity_type": {"$in": restrict_to_entity_types}},
                    {"synonyms.entity_type": {"$in": restrict_to_entity_types}}
                ]
            }
            if "$or" in query:
                query = {"$and": [
                    {"$text": {"$search": term}},
                    {"$or": query["$or"]},
                    et_condition,
                ]}
            else:
                query.update(et_condition)

        if not include_inactive:
            query["status"] = "active"

        return query

    @staticmethod
    def build_regex_search_query(
        term: str,
        restrict_to_namespaces: Optional[list[str]] = None,
        restrict_to_entity_types: Optional[list[str]] = None,
        include_inactive: bool = False
    ) -> dict[str, Any]:
        """
        Build a MongoDB query using regex for partial matching.

        Args:
            term: Search term (will be escaped for regex safety)
            restrict_to_namespaces: Optional list of namespaces
            restrict_to_entity_types: Optional list of entity types
            include_inactive: Whether to include inactive entries

        Returns:
            MongoDB query dictionary
        """
        escaped_term = re.escape(term)

        query: dict[str, Any] = {
            "$or": [
                {"primary_composite_key": {"$regex": escaped_term, "$options": "i"}},
                {"synonyms.composite_key": {"$regex": escaped_term, "$options": "i"}}
            ]
        }

        restriction_conditions = []
        if restrict_to_namespaces:
            restriction_conditions.append({
                "$or": [
                    {"namespace": {"$in": restrict_to_namespaces}},
                    {"synonyms.namespace": {"$in": restrict_to_namespaces}}
                ]
            })
        if restrict_to_entity_types:
            restriction_conditions.append({
                "$or": [
                    {"entity_type": {"$in": restrict_to_entity_types}},
                    {"synonyms.entity_type": {"$in": restrict_to_entity_types}}
                ]
            })

        if restriction_conditions:
            query = {"$and": [query] + restriction_conditions}

        if not include_inactive:
            if "$and" in query:
                query["$and"].append({"status": "active"})
            else:
                query["status"] = "active"

        return query

    @staticmethod
    def _sanitize_field_name(field_name: str) -> str:
        """Sanitize a field name to prevent NoSQL injection."""
        if field_name.startswith("$"):
            raise ValueError(f"Invalid field name: {field_name}")
        if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_.]*$', field_name):
            raise ValueError(f"Invalid field name format: {field_name}")
        return field_name

    @staticmethod
    def find_match_location(
        entry: RegistryEntry,
        field_criteria: dict[str, Any]
    ) -> tuple[str, str, str, dict[str, Any]]:
        """
        Determine where in the entry the match was found.

        Returns:
            Tuple of (matched_in, matched_namespace, matched_entity_type, matched_composite_key)
        """
        if SearchService._matches_criteria(entry.primary_composite_key, field_criteria):
            return "primary", entry.namespace, entry.entity_type, entry.primary_composite_key

        for synonym in entry.synonyms:
            if SearchService._matches_criteria(synonym.composite_key, field_criteria):
                return "synonym", synonym.namespace, synonym.entity_type, synonym.composite_key

        return "primary", entry.namespace, entry.entity_type, entry.primary_composite_key

    @staticmethod
    def _matches_criteria(composite_key: dict[str, Any], criteria: dict[str, Any]) -> bool:
        """Check if a composite key matches the search criteria."""
        for field, value in criteria.items():
            if field not in composite_key:
                return False
            if composite_key[field] != value:
                return False
        return True

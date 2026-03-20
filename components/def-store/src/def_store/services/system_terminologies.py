"""
System Terminologies for WIP.

This module defines and bootstraps system-provided terminologies that are
automatically created when Def-Store starts. These terminologies support
WIP's built-in semantic types.

System terminologies are identified by the `_` prefix in their value.
Users can add terms to system terminologies but should not delete
the built-in terms.
"""

from datetime import UTC, datetime
from typing import Any

from ..models.term import Term
from ..models.terminology import Terminology, TerminologyMetadata
from .registry_client import RegistryError, get_registry_client

# System terminology definitions
# The `_` prefix indicates a system-managed terminology
SYSTEM_TERMINOLOGIES: list[dict[str, Any]] = [
    {
        "value": "_TIME_UNITS",
        "label": "Time Units",
        "description": "System terminology for duration semantic type. "
                       "Each term represents a time unit with a conversion factor to seconds.",
        "case_sensitive": False,
        "metadata": {
            "source": "WIP System",
            "version": "1.0",
            "language": "en",
            "custom": {
                "system_managed": True,
                "semantic_type": "duration"
            }
        },
        "terms": [
            {
                "value": "seconds",
                "label": "Seconds",
                "aliases": ["sec", "s", "second"],
                "metadata": {"factor": 1},
                "sort_order": 1
            },
            {
                "value": "minutes",
                "label": "Minutes",
                "aliases": ["min", "m", "minute"],
                "metadata": {"factor": 60},
                "sort_order": 2
            },
            {
                "value": "hours",
                "label": "Hours",
                "aliases": ["hr", "h", "hour"],
                "metadata": {"factor": 3600},
                "sort_order": 3
            },
            {
                "value": "days",
                "label": "Days",
                "aliases": ["d", "day"],
                "metadata": {"factor": 86400},
                "sort_order": 4
            },
            {
                "value": "weeks",
                "label": "Weeks",
                "aliases": ["wk", "w", "week"],
                "metadata": {"factor": 604800},
                "sort_order": 5
            },
        ]
    },
    {
        "value": "_ONTOLOGY_RELATIONSHIP_TYPES",
        "label": "Ontology Relationship Types",
        "description": "System terminology defining relationship types for ontology support. "
                       "Each term represents a typed relationship between concepts.",
        "case_sensitive": False,
        "metadata": {
            "source": "WIP System",
            "version": "1.0",
            "language": "en",
            "custom": {
                "system_managed": True,
                "ontology": True
            }
        },
        "terms": [
            {
                "value": "is_a",
                "label": "Is a",
                "description": "Subsumption / SKOS broader",
                "aliases": ["broader", "subClassOf"],
                "metadata": {"inverse": "has_subtype", "transitive": True},
                "sort_order": 1
            },
            {
                "value": "has_subtype",
                "label": "Has subtype",
                "description": "Inverse of is_a / SKOS narrower",
                "aliases": ["narrower"],
                "metadata": {"inverse": "is_a", "transitive": True},
                "sort_order": 2
            },
            {
                "value": "part_of",
                "label": "Part of",
                "description": "Mereological part-whole relationship",
                "aliases": [],
                "metadata": {"inverse": "has_part", "transitive": True},
                "sort_order": 3
            },
            {
                "value": "has_part",
                "label": "Has part",
                "description": "Inverse of part_of",
                "aliases": [],
                "metadata": {"inverse": "part_of", "transitive": True},
                "sort_order": 4
            },
            {
                "value": "maps_to",
                "label": "Maps to",
                "description": "Cross-vocabulary mapping",
                "aliases": ["exactMatch", "closeMatch"],
                "metadata": {"inverse": "mapped_from", "transitive": False},
                "sort_order": 5
            },
            {
                "value": "mapped_from",
                "label": "Mapped from",
                "description": "Inverse of maps_to",
                "aliases": [],
                "metadata": {"inverse": "maps_to", "transitive": False},
                "sort_order": 6
            },
            {
                "value": "related_to",
                "label": "Related to",
                "description": "Associative / SKOS related",
                "aliases": ["related"],
                "metadata": {"inverse": "related_to", "transitive": False},
                "sort_order": 7
            },
            {
                "value": "finding_site",
                "label": "Finding site",
                "description": "SNOMED-style anatomical site attribute",
                "aliases": [],
                "metadata": {"transitive": False},
                "sort_order": 8
            },
            {
                "value": "causative_agent",
                "label": "Causative agent",
                "description": "SNOMED-style causative agent attribute",
                "aliases": [],
                "metadata": {"transitive": False},
                "sort_order": 9
            },
        ]
    }
]


# Constant for the relationship types terminology value
RELATIONSHIP_TYPES_TERMINOLOGY_VALUE = "_ONTOLOGY_RELATIONSHIP_TYPES"


async def ensure_system_terminologies() -> dict[str, Any]:
    """
    Ensure all system terminologies exist in the database.

    This function is idempotent - it will only create terminologies and terms
    that don't already exist. Existing ones are left unchanged.

    Returns:
        Summary of what was created/found:
        {
            "terminologies_created": int,
            "terminologies_existed": int,
            "terms_created": int,
            "terms_existed": int,
            "errors": list[str]
        }
    """
    registry = get_registry_client()

    summary = {
        "terminologies_created": 0,
        "terminologies_existed": 0,
        "terms_created": 0,
        "terms_existed": 0,
        "errors": []
    }

    for term_def in SYSTEM_TERMINOLOGIES:
        try:
            # Check if terminology already exists
            existing = await Terminology.find_one({"value": term_def["value"]})

            if existing:
                print(f"  System terminology '{term_def['value']}' already exists")
                summary["terminologies_existed"] += 1
                terminology_id = existing.terminology_id
            else:
                # Register with Registry to get ID
                try:
                    terminology_id = await registry.register_terminology(
                        value=term_def["value"],
                        label=term_def["label"],
                        created_by="system:bootstrap"
                    )
                except RegistryError as e:
                    error_msg = f"Failed to register terminology '{term_def['value']}' with Registry: {e}"
                    print(f"  ERROR: {error_msg}")
                    summary["errors"].append(error_msg)
                    continue

                # Create terminology document
                metadata = TerminologyMetadata(**term_def.get("metadata", {}))

                terminology = Terminology(
                    terminology_id=terminology_id,
                    value=term_def["value"],
                    label=term_def["label"],
                    description=term_def.get("description"),
                    case_sensitive=term_def.get("case_sensitive", False),
                    allow_multiple=term_def.get("allow_multiple", False),
                    extensible=True,  # System terminologies can be extended
                    metadata=metadata,
                    status="active",
                    created_at=datetime.now(UTC),
                    created_by="system:bootstrap",
                    updated_at=datetime.now(UTC),
                    updated_by="system:bootstrap",
                    term_count=0
                )

                await terminology.insert()
                print(f"  Created system terminology '{term_def['value']}' with ID {terminology_id}")
                summary["terminologies_created"] += 1

            # Process terms
            terms_to_create = []
            for term_data in term_def.get("terms", []):
                # Check if term already exists
                existing_term = await Term.find_one({
                    "terminology_id": terminology_id,
                    "value": term_data["value"]
                })

                if existing_term:
                    summary["terms_existed"] += 1
                    continue

                terms_to_create.append(term_data)

            if terms_to_create:
                # Register all new terms with Registry in bulk
                try:
                    results = await registry.register_terms_bulk(
                        terminology_id=terminology_id,
                        terms=[{"value": t["value"]} for t in terms_to_create],
                        created_by="system:bootstrap"
                    )
                except RegistryError as e:
                    error_msg = f"Failed to register terms for '{term_def['value']}' with Registry: {e}"
                    print(f"  ERROR: {error_msg}")
                    summary["errors"].append(error_msg)
                    continue

                # Create term documents
                for i, term_data in enumerate(terms_to_create):
                    term_id = results[i]["registry_id"]

                    term = Term(
                        term_id=term_id,
                        terminology_id=terminology_id,
                        terminology_value=term_def["value"],
                        value=term_data["value"],
                        aliases=term_data.get("aliases", []),
                        label=term_data.get("label", term_data["value"]),
                        description=term_data.get("description"),
                        sort_order=term_data.get("sort_order", 0),
                        metadata=term_data.get("metadata", {}),
                        status="active",
                        created_at=datetime.now(UTC),
                        created_by="system:bootstrap",
                        updated_at=datetime.now(UTC),
                        updated_by="system:bootstrap"
                    )

                    await term.insert()
                    summary["terms_created"] += 1

                print(f"  Created {len(terms_to_create)} terms for '{term_def['value']}'")

                # Update term count on terminology
                terminology_doc = await Terminology.find_one({"terminology_id": terminology_id})
                if terminology_doc:
                    terminology_doc.term_count = await Term.find(
                        {"terminology_id": terminology_id, "status": "active"}
                    ).count()
                    await terminology_doc.save()

        except Exception as e:
            error_msg = f"Error processing system terminology '{term_def['value']}': {e}"
            print(f"  ERROR: {error_msg}")
            summary["errors"].append(error_msg)

    return summary


def get_time_unit_factor(term_value: str) -> int | None:
    """
    Get the conversion factor for a time unit term.

    This is a convenience function for validation/transformation that
    looks up the factor from the system terminology definition.

    Args:
        term_value: The term value (e.g., 'days', 'hours')

    Returns:
        Factor in seconds, or None if not found
    """
    for term_def in SYSTEM_TERMINOLOGIES:
        if term_def["value"] == "_TIME_UNITS":
            for term in term_def["terms"]:
                if term["value"] == term_value:
                    return term["metadata"]["factor"]
    return None


# Constant for the time units terminology value
TIME_UNITS_TERMINOLOGY_VALUE = "_TIME_UNITS"

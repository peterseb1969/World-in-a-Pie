"""
System Terminologies for WIP.

This module defines and bootstraps system-provided terminologies that are
automatically created when Def-Store starts. These terminologies support
WIP's built-in semantic types.

System terminologies are identified by the `_` prefix in their value.
Users can add terms to system terminologies but should not delete
the built-in terms.
"""

from typing import Any, cast

from ..models.api_models import CreateTerminologyRequest, CreateTermRequest
from ..models.term import Term
from ..models.terminology import Terminology, TerminologyMetadata
from .terminology_service import TerminologyService

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
        "label": "Ontology Relation Types",
        "description": "System terminology defining relation types for ontology support. "
                       "Each term represents a typed relation between concepts.",
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
                "description": "Mereological part-whole relation",
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
            {
                "value": "regulates",
                "label": "Regulates",
                "description": "GO/RO regulatory relation (RO_0002211)",
                "aliases": [],
                "metadata": {"transitive": False},
                "sort_order": 10
            },
            {
                "value": "positively_regulates",
                "label": "Positively regulates",
                "description": "GO/RO positive regulatory relation (RO_0002213)",
                "aliases": [],
                "metadata": {"transitive": False},
                "sort_order": 11
            },
            {
                "value": "negatively_regulates",
                "label": "Negatively regulates",
                "description": "GO/RO negative regulatory relation (RO_0002212)",
                "aliases": [],
                "metadata": {"transitive": False},
                "sort_order": 12
            },
        ]
    }
]


# Constant for the term-relation types terminology value.
# Note: the data identifier "_ONTOLOGY_RELATIONSHIP_TYPES" is retained
# because apps' "_ONTOLOGY_RELATIONSHIP_TYPES_EXT.json" extension files
# match against this value. Renaming the value would require coordinating
# updates across every app repo that extends it.
TERM_RELATION_TYPES_TERMINOLOGY_VALUE = "_ONTOLOGY_RELATIONSHIP_TYPES"


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
    summary: dict[str, Any] = {
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
                # Route through the normal create path so a system terminology
                # gets the SAME Registry registration + value-form auto-synonym
                # ({ns, type, value}) every other terminology does — the synonym
                # that makes it resolvable by value, not just by canonical id
                # (CASE-791). Hand-rolling register + insert here is exactly the
                # second create path that drifted out of sync and left system
                # terminologies unresolvable by value; one door prevents recurrence.
                created = await TerminologyService.create_terminology(
                    CreateTerminologyRequest(
                        namespace="wip",
                        value=term_def["value"],
                        label=term_def["label"],
                        description=term_def.get("description"),
                        case_sensitive=term_def.get("case_sensitive", False),
                        allow_multiple=term_def.get("allow_multiple", False),
                        extensible=True,  # System terminologies can be extended
                        metadata=TerminologyMetadata(**term_def.get("metadata", {})),
                    ),
                    namespace="wip",
                    actor="system:bootstrap",
                )
                terminology_id = created.terminology_id
                print(f"  Created system terminology '{term_def['value']}' with ID {terminology_id}")
                summary["terminologies_created"] += 1

            # Process terms — also through the normal bulk create, so each term
            # gets its own value-form auto-synonym (system terms had the same
            # CASE-791 gap). create_terms_bulk maintains the parent term_count.
            term_requests = []
            for term_data in term_def.get("terms", []):
                # Check if term already exists
                existing_term = await Term.find_one({
                    "terminology_id": terminology_id,
                    "value": term_data["value"]
                })

                if existing_term:
                    summary["terms_existed"] += 1
                    continue

                term_requests.append(CreateTermRequest(
                    value=term_data["value"],
                    aliases=term_data.get("aliases", []),
                    label=term_data.get("label"),
                    description=term_data.get("description"),
                    sort_order=term_data.get("sort_order", 0),
                    metadata=term_data.get("metadata", {}),
                ))

            if term_requests:
                await TerminologyService.create_terms_bulk(
                    terminology_id=terminology_id,
                    terms=term_requests,
                    created_by="system:bootstrap",
                )
                summary["terms_created"] += len(term_requests)
                print(f"  Created {len(term_requests)} terms for '{term_def['value']}'")

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
                    return cast(int | None, term["metadata"]["factor"])
    return None


# Constant for the time units terminology value
TIME_UNITS_TERMINOLOGY_VALUE = "_TIME_UNITS"

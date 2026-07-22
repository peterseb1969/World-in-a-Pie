"""Shared fixtures for the archive-format tests."""

import pytest


@pytest.fixture
def sample_registry_data():
    """Sample _registry metadata for entities."""
    return {
        "terminology": {
            "entry_id": "0190a000-0000-7000-0000-000000000001",
            "namespace": "wip",
            "entity_type": "terminologies",
            "primary_composite_key": {"value": "COUNTRY", "label": "Country"},
            "synonyms": [
                {
                    "namespace": "wip",
                    "entity_type": "terminologies",
                    "composite_key": {"external_code": "ISO-3166"},
                },
            ],
            "source_info": None,
        },
        "template": {
            "entry_id": "0190c000-0000-7000-0000-000000000001",
            "namespace": "wip",
            "entity_type": "templates",
            "primary_composite_key": {},
            "synonyms": [],
            "source_info": None,
        },
        "document_with_identity": {
            "entry_id": "019abc00-0000-7000-8000-000000000001",
            "namespace": "wip",
            "entity_type": "documents",
            "primary_composite_key": {
                "namespace": "wip",
                "identity_hash": "abc123hash",
                "template_id": "0190c000-0000-7000-0000-000000000001",
            },
            "synonyms": [
                {
                    "namespace": "wip",
                    "entity_type": "documents",
                    "composite_key": {"vendor_id": "VND-001"},
                },
            ],
            "source_info": None,
        },
        "document_no_identity": {
            "entry_id": "019abc00-0000-7000-8000-000000000002",
            "namespace": "wip",
            "entity_type": "documents",
            "primary_composite_key": {},
            "synonyms": [],
            "source_info": None,
        },
    }

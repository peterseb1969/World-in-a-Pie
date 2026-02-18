"""Shared test fixtures."""

import pytest


@pytest.fixture
def sample_terminologies():
    """Sample terminology entities for testing."""
    return [
        {
            "terminology_id": "TERM-000001",
            "namespace": "wip",
            "value": "COUNTRY",
            "label": "Country",
            "status": "active",
            "fields": [],
        },
        {
            "terminology_id": "TERM-000002",
            "namespace": "wip",
            "value": "DOC_STATUS",
            "label": "Document Status",
            "status": "active",
            "fields": [],
        },
    ]


@pytest.fixture
def sample_terms():
    """Sample term entities for testing."""
    return [
        {
            "term_id": "T-000001",
            "terminology_id": "TERM-000001",
            "namespace": "wip",
            "value": "United Kingdom",
        },
        {
            "term_id": "T-000002",
            "terminology_id": "TERM-000001",
            "namespace": "wip",
            "value": "France",
        },
        {
            "term_id": "T-000003",
            "terminology_id": "TERM-000002",
            "namespace": "wip",
            "value": "active",
        },
    ]


@pytest.fixture
def sample_templates():
    """Sample template entities with various reference types."""
    return [
        {
            "template_id": "TPL-000001",
            "namespace": "wip",
            "value": "BASE_PERSON",
            "label": "Base Person",
            "version": 1,
            "extends": None,
            "fields": [
                {"name": "name", "type": "string"},
                {"name": "country", "type": "term", "terminology_ref": "TERM-000001"},
            ],
        },
        {
            "template_id": "TPL-000002",
            "namespace": "wip",
            "value": "EMPLOYEE",
            "label": "Employee",
            "version": 1,
            "extends": "TPL-000001",
            "fields": [
                {"name": "department", "type": "string"},
                {"name": "status", "type": "term", "terminology_ref": "TERM-000002"},
                {"name": "manager", "type": "reference", "target_templates": ["TPL-000002"]},
            ],
        },
        {
            "template_id": "TPL-000003",
            "namespace": "wip",
            "value": "PROJECT",
            "label": "Project",
            "version": 1,
            "extends": None,
            "fields": [
                {"name": "title", "type": "string"},
                {"name": "lead", "type": "object", "template_ref": "TPL-000002"},
                {"name": "tags", "type": "array", "array_terminology_ref": "TERM-000003"},
                {"name": "related", "type": "reference", "target_templates": ["TPL-000003"]},
                {"name": "categories", "type": "reference",
                 "target_terminologies": ["TERM-000004"]},
            ],
        },
    ]


@pytest.fixture
def sample_documents():
    """Sample document entities."""
    return [
        {
            "document_id": "019abc00-0000-7000-8000-000000000001",
            "namespace": "wip",
            "template_id": "TPL-000001",
            "version": 1,
            "identity_hash": "hash1",
            "data": {"name": "John", "country": "United Kingdom"},
            "term_references": [
                {"field_path": "country", "term_id": "T-000001", "terminology_ref": "TERM-000001"},
            ],
            "references": [],
            "file_references": [],
        },
        {
            "document_id": "019abc00-0000-7000-8000-000000000002",
            "namespace": "wip",
            "template_id": "TPL-000002",
            "version": 1,
            "identity_hash": None,
            "data": {"department": "Engineering"},
            "term_references": [
                {"field_path": "status", "term_id": "T-000003", "terminology_ref": "TERM-000002"},
            ],
            "references": [
                {
                    "field_path": "manager",
                    "reference_type": "document",
                    "resolved": {
                        "document_id": "019abc00-0000-7000-8000-000000000001",
                        "template_id": "TPL-000001",
                        "identity_hash": "hash1",
                    },
                },
            ],
            "file_references": [
                {"field_path": "avatar", "file_id": "FILE-000001"},
            ],
        },
    ]


@pytest.fixture
def sample_registry_data():
    """Sample _registry metadata for entities."""
    return {
        "terminology": {
            "entry_id": "TERM-000001",
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
            "entry_id": "TPL-000001",
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
                "template_id": "TPL-000001",
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

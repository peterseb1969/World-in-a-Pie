"""Shared test fixtures."""

import os

# CLI tests string-match rendered output, so rendering must not depend on the
# caller's terminal. A shell exporting FORCE_COLOR (Ghostty sets FORCE_COLOR=3)
# makes rich emit ANSI escapes inside CliRunner's captured output — "TPL-1"
# becomes "TPL-\x1b[1;36m1\x1b[0m" — and every substring assertion on a rendered
# table fails for reasons unrelated to the code under test. The toolkit builds
# its rich Consoles at import time, so this has to happen before wip_toolkit is
# imported: at conftest module level, not in a fixture. rich honors NO_COLOR and
# a dumb TERM; FORCE_COLOR is removed so it cannot override them.
os.environ.pop("FORCE_COLOR", None)
os.environ.pop("CLICOLOR_FORCE", None)
os.environ["NO_COLOR"] = "1"
os.environ["TERM"] = "dumb"

import pytest


@pytest.fixture
def sample_terminologies():
    """Sample terminology entities for testing."""
    return [
        {
            "terminology_id": "0190a000-0000-7000-0000-000000000001",
            "namespace": "wip",
            "value": "COUNTRY",
            "label": "Country",
            "status": "active",
            "fields": [],
        },
        {
            "terminology_id": "0190a000-0000-7000-0000-000000000002",
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
            "term_id": "0190b000-0000-7000-0000-000000000001",
            "terminology_id": "0190a000-0000-7000-0000-000000000001",
            "namespace": "wip",
            "value": "United Kingdom",
        },
        {
            "term_id": "0190b000-0000-7000-0000-000000000002",
            "terminology_id": "0190a000-0000-7000-0000-000000000001",
            "namespace": "wip",
            "value": "France",
        },
        {
            "term_id": "0190b000-0000-7000-0000-000000000003",
            "terminology_id": "0190a000-0000-7000-0000-000000000002",
            "namespace": "wip",
            "value": "active",
        },
    ]


@pytest.fixture
def sample_templates():
    """Sample template entities with various reference types."""
    return [
        {
            "template_id": "0190c000-0000-7000-0000-000000000001",
            "namespace": "wip",
            "value": "BASE_PERSON",
            "label": "Base Person",
            "version": 1,
            "extends": None,
            "fields": [
                {"name": "name", "type": "string"},
                {"name": "country", "type": "term", "terminology_ref": "0190a000-0000-7000-0000-000000000001"},
            ],
        },
        {
            "template_id": "0190c000-0000-7000-0000-000000000002",
            "namespace": "wip",
            "value": "EMPLOYEE",
            "label": "Employee",
            "version": 1,
            "extends": "0190c000-0000-7000-0000-000000000001",
            "fields": [
                {"name": "department", "type": "string"},
                {"name": "status", "type": "term", "terminology_ref": "0190a000-0000-7000-0000-000000000002"},
                {"name": "manager", "type": "reference", "target_templates": ["0190c000-0000-7000-0000-000000000002"]},
            ],
        },
        {
            "template_id": "0190c000-0000-7000-0000-000000000003",
            "namespace": "wip",
            "value": "PROJECT",
            "label": "Project",
            "version": 1,
            "extends": None,
            "fields": [
                {"name": "title", "type": "string"},
                {"name": "lead", "type": "object", "template_ref": "0190c000-0000-7000-0000-000000000002"},
                {"name": "tags", "type": "array", "array_terminology_ref": "0190a000-0000-7000-0000-000000000003"},
                {"name": "related", "type": "reference", "target_templates": ["0190c000-0000-7000-0000-000000000003"]},
                {"name": "categories", "type": "reference",
                 "target_terminologies": ["0190a000-0000-7000-0000-000000000004"]},
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
            "template_id": "0190c000-0000-7000-0000-000000000001",
            "version": 1,
            "identity_hash": "hash1",
            "data": {"name": "John", "country": "United Kingdom"},
            "term_references": [
                {"field_path": "country", "term_id": "0190b000-0000-7000-0000-000000000001", "terminology_ref": "0190a000-0000-7000-0000-000000000001"},
            ],
            "references": [],
            "file_references": [],
        },
        {
            "document_id": "019abc00-0000-7000-8000-000000000002",
            "namespace": "wip",
            "template_id": "0190c000-0000-7000-0000-000000000002",
            "version": 1,
            "identity_hash": None,
            "data": {"department": "Engineering"},
            "term_references": [
                {"field_path": "status", "term_id": "0190b000-0000-7000-0000-000000000003", "terminology_ref": "0190a000-0000-7000-0000-000000000002"},
            ],
            "references": [
                {
                    "field_path": "manager",
                    "reference_type": "document",
                    "resolved": {
                        "document_id": "019abc00-0000-7000-8000-000000000001",
                        "template_id": "0190c000-0000-7000-0000-000000000001",
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

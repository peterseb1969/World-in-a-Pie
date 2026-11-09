"""Term and document reference lookups against real reporting rows.

The earlier beds mocked a pool that returned no rows, so the row-reading
code never ran: get_term_documents walked the stored term references as a
{field: id} map while they are stored as a list of
{field_path, term_id, ...}, and every match ended in "no documents". These
beds write rows the way the sync writes them (SchemaManager tables,
DocumentTransformer rows, an entity view over the table) and read them back.

What they pin:
- documents are found by the term's canonical ID, whatever identifier the
  caller used (def-store resolves it);
- each document is reported once, not once per table and once per view;
- a term id that merely appears inside another reference's
  terminology_ref does not match;
- total counts every referencing document, the list holds the first limit;
- relationship documents are a document's referrers (source_ref /
  target_ref).
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from reporting_sync import search_service
from reporting_sync.main import init_postgres_schema
from reporting_sync.models import ReportingConfig
from reporting_sync.schema_manager import SchemaManager
from reporting_sync.search_service import SearchService
from reporting_sync.transformer import DocumentTransformer

from .conftest import requires_postgres

NS = "rs1218"
TERM = "019a0000-0000-7000-8000-00000000000a"
OTHER = "019a0000-0000-7000-8000-00000000000b"

VISIT = {
    "template_id": "tpl-visit", "value": "VISIT", "version": 1,
    "fields": [{"name": "title", "type": "string"},
               {"name": "kind", "type": "term", "terminology_ref": "KIND"}],
    "identity_fields": ["title"],
    "reporting": {"sync_enabled": True, "include_metadata": False},
}
LINK = {
    "template_id": "tpl-link", "value": "LINK", "version": 1, "usage": "relationship",
    "fields": [{"name": "source_ref", "type": "reference", "reference_type": "document"},
               {"name": "target_ref", "type": "reference", "reference_type": "document"}],
    "identity_fields": ["source_ref", "target_ref"],
    "reporting": {"sync_enabled": True},
}


def _visit(doc_id, term_id, status="active", terminology_ref="KIND"):
    return {
        "document_id": doc_id, "namespace": NS, "template_id": "tpl-visit",
        "template_version": 1, "version": 1, "status": status, "identity_hash": doc_id,
        "data": {"title": doc_id, "kind": "x"},
        "term_references": [{"field_path": "kind", "term_id": term_id,
                             "terminology_ref": terminology_ref, "matched_via": "value"}],
    }


def _link(doc_id, source, target):
    return {
        "document_id": doc_id, "namespace": NS, "template_id": "tpl-link",
        "template_version": 1, "version": 1, "status": "active", "identity_hash": doc_id,
        "created_at": "2026-11-09T10:00:00Z",
        "data": {"source_ref": source, "target_ref": target,
                 "source_ref_resolved": source, "target_ref_resolved": target},
    }


async def _write(pool, template, docs):
    sm = SchemaManager(pool)
    table = await sm.ensure_table_for_template(NS, template)
    reporting = template.get("reporting")
    transformer = DocumentTransformer(ReportingConfig(**reporting) if reporting else None)
    async with pool.acquire() as conn:
        for doc in docs:
            for row in transformer.transform(doc, template):
                sql, params = transformer.generate_upsert_sql(table, row)
                await conn.execute(sql, *params)
    return table


@pytest_asyncio.fixture
async def rows(pg_pool, monkeypatch):
    await init_postgres_schema(pg_pool)
    await _write(pg_pool, VISIT, [
        _visit("d-1", TERM), _visit("d-2", OTHER), _visit("d-3", TERM, status="inactive"),
        # TERM appears in this reference only as a terminology_ref value
        _visit("d-4", OTHER, terminology_ref=TERM),
    ])
    await _write(pg_pool, LINK, [
        _link("e-1", "d-1", "d-2"), _link("e-2", "d-2", "d-1"), _link("e-3", "d-2", "d-4"),
    ])
    async with pg_pool.acquire() as conn:
        # The sync keeps entity views over each template's tables; a lookup
        # that also read them would report every document twice.
        views = await conn.fetchval(
            "SELECT count(*) FROM information_schema.views WHERE table_schema = $1"
            " AND table_name IN ('doc_visit', 'doc_visit__entities')", NS,
        )
    assert views == 2

    async def fetch_target(client, url):
        if "/terms/" in url:
            return {"term_id": TERM, "value": "routine", "namespace": NS}
        if "/documents/" in url:
            return {"document_id": "d-1", "namespace": NS}
        if "/templates/" in url:
            return {"template_id": "tpl-visit", "value": "VISIT"}
        return None

    monkeypatch.setattr(search_service, "_fetch_target", fetch_target)
    return SearchService(pg_pool)


@requires_postgres
@pytest.mark.asyncio
async def test_term_documents_are_found_by_the_canonical_id(rows):
    # The caller names the term by its qualified value; def-store resolves it.
    result = await rows.get_term_documents(f"{NS}:KIND:routine", namespace=NS)
    assert [(d.document_id, d.field_path, d.status, d.template_value) for d in result.documents] == [
        ("d-1", "kind", "active", "VISIT"),
        ("d-3", "kind", "inactive", "VISIT"),
    ]
    assert result.total == 2
    assert result.documents[0].created_at is None  # include_metadata: false table


@requires_postgres
@pytest.mark.asyncio
async def test_total_counts_past_the_limit(rows):
    result = await rows.get_term_documents(TERM, limit=1, namespace=NS)
    assert [d.document_id for d in result.documents] == ["d-1"]
    assert result.total == 2


@requires_postgres
@pytest.mark.asyncio
async def test_term_referenced_by_matches_the_canonical_id(rows):
    result = await rows.get_referenced_by("term", f"{NS}:KIND:routine", namespace=NS)
    assert result.error is None
    assert [(r.entity_id, r.field_path) for r in result.referenced_by] == [
        ("d-1", "kind"), ("d-3", "kind"),
    ]
    assert result.total == 2
    assert result._target_namespace == NS


@requires_postgres
@pytest.mark.asyncio
async def test_a_document_is_referenced_by_its_relationships(rows):
    result = await rows.get_referenced_by("document", "d-1", namespace=NS)
    assert result.error is None
    assert [(r.entity_id, r.field_path, r.reference_type) for r in result.referenced_by] == [
        ("e-1", "source_ref", "relationship"), ("e-2", "target_ref", "relationship"),
    ]
    assert result.total == 2
    assert result._target_namespace == NS


@requires_postgres
@pytest.mark.asyncio
async def test_a_missing_term_has_no_documents(rows, monkeypatch):
    async def missing(client, url):
        return None

    monkeypatch.setattr(search_service, "_fetch_target", missing)
    result = await rows.get_term_documents("nope", namespace=NS)
    assert (result.documents, result.total) == ([], 0)
    by = await rows.get_referenced_by("term", "nope", namespace=NS)
    assert by._target_missing and by._target_namespace is None


# ---------------------------------------------------------------------------
# Template and terminology referrers (no PostgreSQL: the template store is
# stubbed). Every page of templates is read, array refs count, a template's
# versions are one referrer, and a bare terminology value names the
# terminology only from its own namespace.
# ---------------------------------------------------------------------------


def _tpl(tid, ns, fields, version=1, extends=None):
    return {"template_id": tid, "namespace": ns, "value": tid.upper(), "label": tid,
            "status": "active", "version": version, "extends": extends, "fields": fields}


@pytest.fixture
def stores(monkeypatch):
    pages: dict[int, list] = {}
    calls: list = []

    async def fetch_target(client, url):
        if "/terminologies/" in url:
            return {"terminology_id": "trm-1", "value": "KIND", "namespace": "ns-a"}
        return {"template_id": "tpl-target", "value": "TARGET", "namespace": "ns-a"}

    async def get_json(client, url, params):
        calls.append((url, params))
        if url.endswith("/documents"):
            return {"items": [{"document_id": "doc-1", "status": "active"}], "total": 7}
        return {"items": pages.get(params["page"], [])}

    monkeypatch.setattr(search_service, "_fetch_target", fetch_target)
    monkeypatch.setattr(search_service, "_get_json", get_json)
    monkeypatch.setattr(search_service, "_TEMPLATE_PAGE", 2)
    return pages, calls


@pytest.mark.asyncio
async def test_template_referrers_span_every_page_and_array_refs(stores):
    pages, calls = stores
    pages[1] = [
        _tpl("child", "ns-a", [], extends="tpl-target"),
        _tpl("holder", "ns-a", [{"name": "parts", "type": "array",
                                 "array_template_ref": "tpl-target"}]),
    ]
    pages[2] = [
        _tpl("holder", "ns-a", [{"name": "parts", "type": "array",
                                 "array_template_ref": "tpl-target"}], version=2),
        _tpl("embed", "ns-a", [{"name": "addr", "type": "object", "template_ref": "tpl-target"}]),
    ]
    result = await SearchService(None).get_referenced_by("template", "TARGET", 10, "ns-a")
    assert result.error is None
    assert [(r.entity_id, r.field_path) for r in result.referenced_by] == [
        ("doc-1", "template_id"),
        ("child", "extends"),
        ("holder", "fields.parts.array_template_ref"),
        ("embed", "fields.addr.template_ref"),
    ]
    # the document store's own total, plus every template referrer
    assert result.total == 7 + 3
    # documents were asked for by the canonical id, not the value given
    assert calls[0][1]["template_id"] == "tpl-target"
    assert [p["page"] for u, p in calls if u.endswith("/templates")] == [1, 2, 3]


@pytest.mark.asyncio
async def test_terminology_referrers_resolve_a_bare_value_in_its_own_namespace(stores):
    pages, _ = stores
    pages[1] = [
        _tpl("by-id", "ns-b", [{"name": "k", "type": "term", "terminology_ref": "trm-1"}]),
        _tpl("by-value", "ns-a", [{"name": "k", "type": "array",
                                   "array_terminology_ref": "KIND"}]),
    ]
    pages[2] = [
        # ns-b's bare KIND is ns-b's own terminology, not this one
        _tpl("other-kind", "ns-b", [{"name": "k", "type": "term", "terminology_ref": "KIND"}]),
        _tpl("qualified", "ns-b", [{"name": "k", "type": "term",
                                    "terminology_ref": "ns-a:KIND"}]),
    ]
    result = await SearchService(None).get_referenced_by("terminology", "KIND", 10, None)
    assert result.error is None
    assert [(r.entity_id, r.field_path) for r in result.referenced_by] == [
        ("by-id", "fields.k.terminology_ref"),
        ("by-value", "fields.k.array_terminology_ref"),
        ("qualified", "fields.k.terminology_ref"),
    ]
    assert result.total == 3
    assert result._target_namespace == "ns-a"

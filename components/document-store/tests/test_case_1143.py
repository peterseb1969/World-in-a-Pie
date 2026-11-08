"""Validation and query gaps where a declared constraint did not hold.

Provenance: CASE-1143.

- NaN and Infinity are refused anywhere in a document's data.
- pattern and range rules are evaluated.
- A rule that cannot be evaluated is refused per document, never a 500.
- Repeated filters on one field are ANDed.
- The integrity check reads the template version a document is pinned to.
- A terminology reference resolves in the document's namespace, fresh.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from document_store.models.api_models import DocumentQueryRequest, QueryFilter
from document_store.services import integrity_service
from document_store.services.def_store_client import DefStoreClient
from document_store.services.document_service import DocumentService

CANDIDATE = "/api/document-store/validation/validate-candidate"


def _candidate(fields: list[dict], rules: list[dict] | None = None) -> dict:
    return {
        "template_id": "candidate", "value": "CANDIDATE_1143", "label": "C",
        "version": 1, "status": "active", "namespace": "wip",
        "identity_fields": [], "fields": fields, "rules": rules or [],
    }


async def _validate(client, auth_headers, fields, documents, rules=None, raw=None):
    """Error codes per document. ``raw`` replaces substrings of the encoded
    body, to send JSON literals (NaN) a JSON encoder refuses to write."""
    body = json.dumps({
        "namespace": "wip", "template_definition": _candidate(fields, rules),
        "documents": documents,
    })
    for old, new in (raw or {}).items():
        body = body.replace(old, new)
    resp = await client.post(
        CANDIDATE, content=body,
        headers={**auth_headers, "Content-Type": "application/json"},
    )
    assert resp.status_code == 200, resp.text
    return [r["validation"]["errors"] for r in resp.json()["results"]]


def _codes(errors_per_doc):
    return [[e["code"] for e in errs] for errs in errors_per_doc]


# ── non-finite numbers ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_nan_and_infinity_are_refused_anywhere_in_data(client, auth_headers):
    fields = [
        {"name": "n", "label": "n", "type": "number"},
        {"name": "free", "label": "free", "type": "object"},
        {"name": "items", "label": "items", "type": "array"},
    ]
    errors = await _validate(
        client, auth_headers, fields,
        [{"n": 1.5}, {"free": {"deep": 2.5}}, {"items": [1, 3.5]}, {"n": 4.5}],
        raw={"1.5": "NaN", "2.5": "Infinity", "3.5": "-Infinity"},
    )
    assert _codes(errors) == [["non_finite_number"]] * 3 + [[]]
    assert [e[0]["field"] for e in errors[:3]] == ["n", "free.deep", "items[1]"]


# ── pattern and range rules ────────────────────────────────────────────

FIELDS = [
    {"name": "kind", "label": "kind", "type": "string"},
    {"name": "code", "label": "code", "type": "string"},
    {"name": "n", "label": "n", "type": "number"},
]


@pytest.mark.asyncio
async def test_pattern_and_range_rules_are_evaluated(client, auth_headers):
    rules = [
        {"type": "pattern", "target_field": "code", "pattern": "[A-Z]{3}"},
        {"type": "range", "target_field": "n", "minimum": 0, "maximum": 10},
    ]
    codes = _codes(await _validate(client, auth_headers, FIELDS, [
        {"code": "ABC", "n": 10},
        {"code": "ABCD", "n": 10.5},
        {"code": "abc", "n": -1},
        {},  # absent targets: nothing to check
    ], rules))
    assert codes == [[], ["pattern", "range"], ["pattern", "range"], []]


@pytest.mark.asyncio
async def test_pattern_and_range_rules_hold_only_under_their_conditions(client, auth_headers):
    when_ship = [{"field": "kind", "operator": "equals", "value": "ship"}]
    rules = [
        {"type": "pattern", "target_field": "code", "pattern": "S-[0-9]+", "conditions": when_ship},
        {"type": "range", "target_field": "n", "maximum": 5, "conditions": when_ship},
    ]
    codes = _codes(await _validate(client, auth_headers, FIELDS, [
        {"kind": "ship", "code": "S-12", "n": 5},
        {"kind": "ship", "code": "X-12", "n": 6},
        {"kind": "boat", "code": "X-12", "n": 6},
    ], rules))
    assert codes == [[], ["pattern", "range"], []]


# ── rules that cannot be evaluated ─────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("rule", [
    {"type": "conditional_required", "target_field": "code",
     "conditions": [{"field": "kind", "operator": "in", "value": 5}]},
    {"type": "dependency", "conditions": [{"field": "kind", "operator": "exists"}]},
    {"type": "pattern", "target_field": "code"},
    {"type": "no_such_rule", "target_field": "code"},
])
async def test_a_rule_that_cannot_be_evaluated_is_refused_not_a_500(client, auth_headers, rule):
    errors = await _validate(client, auth_headers, FIELDS, [{"kind": "ship", "code": "X"}], [rule])
    assert _codes(errors) == [["invalid_rule"]]
    assert errors[0][0]["details"]["rule_index"] == 0


@pytest.mark.asyncio
async def test_in_and_not_in_evaluate_against_a_list(client, auth_headers):
    rules = [
        {"type": "conditional_required", "target_field": "code",
         "conditions": [{"field": "kind", "operator": "in", "value": ["ship", "boat"]}]},
        {"type": "conditional_required", "target_field": "n",
         "conditions": [{"field": "kind", "operator": "not_in", "value": ["ship", "boat"]}]},
    ]
    codes = _codes(await _validate(client, auth_headers, FIELDS, [
        {"kind": "ship"}, {"kind": "car"},
    ], rules))
    assert codes == [["conditional_required"], ["conditional_required"]]


# ── repeated filters ───────────────────────────────────────────────────


def _query(*filters, **kwargs):
    request = DocumentQueryRequest(
        filters=[QueryFilter(field=f, operator=o, value=v) for f, o, v in filters], **kwargs,
    )
    return DocumentService.__new__(DocumentService)._build_query(request)


def test_two_filters_on_one_field_both_apply():
    query = _query(("data.x", "gte", 2), ("data.x", "lte", 3), ("data.y", "eq", 1))
    assert query["data.x"] == {"$gte": 2}
    assert query["$and"] == [{"data.x": {"$lte": 3}}]
    assert query["data.y"] == 1


def test_a_filter_does_not_replace_the_requests_own_template_or_status():
    query = _query(("template_id", "eq", "OTHER"), ("status", "eq", "inactive"),
                   template_id="T", status="active")
    assert query["template_id"] == "T" and query["status"] == "active"
    assert query["$and"] == [{"template_id": "OTHER"}, {"status": "inactive"}]


# ── integrity reads the pinned version ─────────────────────────────────


@pytest.mark.asyncio
async def test_integrity_reads_each_documents_pinned_template_version():
    integrity_service.clear_integrity_cache()
    store = AsyncMock()
    store.get_template.side_effect = lambda template_id, version: {
        1: {"status": "inactive"}, 2: {"status": "active"},
    }[version]
    issues: list = []
    with patch.object(integrity_service, "get_template_store_client", return_value=store):
        for version in (1, 2, 1):
            doc = MagicMock(document_id=f"d{version}", template_id="T",
                            template_version=version, version=1)
            await integrity_service.check_template_reference("T", doc, issues)
    assert [i.document_id for i in issues] == ["d1", "d1"]
    assert {c.kwargs["version"] for c in store.get_template.await_args_list} == {1, 2}
    assert store.get_template.await_count == 2  # one fetch per (template, version)
    integrity_service.clear_integrity_cache()


# ── terminology references ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_terminology_lookup_names_its_namespace_and_is_never_cached():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"terminology_id": "TL", "status": "active"})

    client = DefStoreClient(base_url="http://def-store", api_key="k")
    with patch("document_store.services.def_store_client.get_def_store_transport",
               return_value=httpx.MockTransport(handler)):
        await client.get_terminology("COUNTRY", "appns")
        await client.get_terminology("COUNTRY", "appns")
    assert len(seen) == 2
    assert all(r.url.params["namespace"] == "appns" for r in seen)
    assert seen[0].url.path == "/api/def-store/terminologies/COUNTRY"


@pytest.mark.asyncio
async def test_a_missing_pinned_version_is_not_an_orphaned_template():
    """Only a template missing whole is orphaned; a missing version of an
    existing one is the identity check's template_version_not_found."""
    integrity_service.clear_integrity_cache()
    store = AsyncMock()
    store.get_template.side_effect = lambda template_id, version=None: (
        {"status": "active"} if template_id == "T" and version is None else None
    )
    issues: list = []
    with patch.object(integrity_service, "get_template_store_client", return_value=store):
        await integrity_service.check_template_reference(
            "T", MagicMock(document_id="d", template_id="T", template_version=9, version=1), issues,
        )
        await integrity_service.check_template_reference(
            "GONE", MagicMock(document_id="g", template_id="GONE", template_version=1, version=1), issues,
        )
    assert [(i.document_id, i.type) for i in issues] == [("g", "orphaned_template_ref")]
    assert integrity_service._template_check_cache[("T", 9)] == (True, "version_missing")
    integrity_service.clear_integrity_cache()

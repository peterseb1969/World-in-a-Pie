"""CASE-466 — a query filter with an unsupported operator must fail loud (422),
not silently over-return.

`QueryFilter.operator` is a free `str` (not an enum), so an unrecognized value
passes request parsing and reaches `_build_query`. Before CASE-466 the filter
loop had no `else`: an unknown operator matched no branch, the field was never
added to the mongo query, and the filter was silently dropped — the query then
returned MORE rows than intended (no filtering on that field) with no error.

`_build_query` is a pure builder (no DB), so these are plain unit tests. The
route maps the raised ValueError to HTTP 422 (documents.py query route).

Scope: this validates the OPERATOR only. Unknown filter FIELDS stay free by
design (CLAUDE.md §5 — query filters are ad-hoc reads, not declarative
commitments); that half of CASE-466 is a deferred design decision, not tested
here.
"""

import pytest

from document_store.models.api_models import DocumentQueryRequest, QueryFilter
from document_store.services.document_service import DocumentService


def _svc() -> DocumentService:
    # __init__ only builds a ValidationService — no DB connection needed.
    return DocumentService()


def test_supported_operator_eq_builds_query():
    req = DocumentQueryRequest(
        filters=[QueryFilter(field="data.status", operator="eq", value="active")],
        status=None,
    )
    q = _svc()._build_query(req)
    assert q["data.status"] == "active"


def test_supported_operator_gte_builds_mongo_gte():
    req = DocumentQueryRequest(
        filters=[QueryFilter(field="updated_at", operator="gte", value="2026-06-12")],
        status=None,
    )
    q = _svc()._build_query(req)
    assert q["updated_at"] == {"$gte": "2026-06-12"}


def test_unsupported_operator_raises_valueerror():
    req = DocumentQueryRequest(
        filters=[QueryFilter(field="data.x", operator="approx", value=1)],
        status=None,
    )
    with pytest.raises(ValueError, match="Unsupported filter operator 'approx'"):
        _svc()._build_query(req)


def test_unsupported_operator_does_not_silently_drop_filter():
    """The regression guard: the offending field must NOT just be absent from
    the query (the old silent-drop behavior) — it must raise."""
    req = DocumentQueryRequest(
        filters=[QueryFilter(field="data.y", operator="between", value=[1, 2])],
        status=None,
    )
    try:
        q = _svc()._build_query(req)
    except ValueError:
        return  # correct: loud failure
    pytest.fail(f"unknown operator silently dropped; query built without error: {q}")

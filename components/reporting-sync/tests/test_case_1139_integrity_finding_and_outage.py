"""A store whose scan found something and could not run every check is
named in services_incomplete.

A store's own status is decided by its worst finding first: document-store
answers warning or error even when summary.checks_not_run is non-empty, and
template-store answers error before it considers checks_skipped. The
aggregate read only the status, so a finding hid the outage: the store was
listed as fully checked, and what went unchecked was never named. The
skipped-check counters now decide it, whatever the status.
"""

from __future__ import annotations

import pytest

from .test_case_1213_low_findings import P, _call, _integrity_upstream


def _issue(severity: str, **ids) -> dict:
    return {"type": "t", "severity": severity, "reference": "r", "message": "m", **ids}


CLEAN_TPL = {"status": "healthy", "summary": {}}
CLEAN_DOC = {"status": "healthy", "summary": {"total_documents": 5, "documents_checked": 5}}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tpl,doc,status,incomplete",
    [
        # document-store found a retired reference, and def-store was down
        (CLEAN_TPL,
         {"status": "warning", "issues": [_issue("warning", document_id="d")],
          "summary": {"total_documents": 5, "documents_checked": 5,
                      "checks_not_run": {"term_reference:def-store": 3}}},
         "warning", ["document-store"]),
        # document-store found an orphan, and template-store was down
        (CLEAN_TPL,
         {"status": "error", "issues": [_issue("error", document_id="d")],
          "summary": {"total_documents": 5, "documents_checked": 5,
                      "checks_not_run": {"template_reference:template-store": 1}}},
         "error", ["document-store"]),
        # template-store found an orphan, and def-store was down
        ({"status": "error", "issues": [_issue("error", template_id="t")],
          "summary": {"orphaned_terminology_refs": 1, "checks_skipped": 2}},
         CLEAN_DOC, "error", ["template-store"]),
        # a finding with every check run stays fully checked
        (CLEAN_TPL,
         {"status": "warning", "issues": [_issue("warning", document_id="d")],
          "summary": {"total_documents": 5, "documents_checked": 5, "checks_not_run": {}}},
         "warning", []),
    ],
)
async def test_a_finding_does_not_hide_the_checks_that_did_not_run(
    monkeypatch, tpl, doc, status, incomplete,
):
    _integrity_upstream(monkeypatch, tpl, doc)
    body = (await _call("GET", f"{P}/health/integrity")).json()
    assert (body["status"], body["services_incomplete"]) == (status, incomplete)

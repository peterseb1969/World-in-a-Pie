"""Version-change guardrail surface in document-store.

Three pieces of the template version-change tooling that live on the
document side:

- /documents/impact-stats — advisory live-document counts (per template
  version + per-field non-empty) consumed by the template create-as-upsert
  to report a version event's consequences.
- Declared renames in migrate — the target version's {new: old} map re-keys
  document data before target validation, making a declared rename
  losslessly migratable where an undeclared one fails with unknown_field.
- /validation/validate-candidate — documents validated against an INLINE
  candidate definition; nothing persisted, cached, or registered.
"""

import copy

import pytest
from httpx import AsyncClient

from document_store.models.document import Document, DocumentStatus

from .conftest import SAMPLE_TEMPLATES, VERSIONED_TEMPLATE_OVERRIDES

DOCUMENTS = "/api/document-store/documents"
IMPACT = "/api/document-store/documents/impact-stats"
MIGRATE = "/api/document-store/documents/migrate"
CANDIDATE = "/api/document-store/validation/validate-candidate"


@pytest.fixture(autouse=True)
def _clear_overrides():
    VERSIONED_TEMPLATE_OVERRIDES.clear()
    yield
    VERSIONED_TEMPLATE_OVERRIDES.clear()


async def _create_person(client: AsyncClient, auth_headers: dict, national_id: str, **extra) -> dict:
    data = {
        "national_id": national_id,
        "first_name": "Ada",
        "last_name": "Impact",
    }
    data.update(extra)
    resp = await client.post(
        DOCUMENTS,
        headers=auth_headers,
        json=[{"namespace": "wip", "template_id": "PERSON", "data": data}],
    )
    assert resp.status_code == 200, resp.text
    item = resp.json()["results"][0]
    assert item["status"] in ("created", "updated"), item
    return item


# ---------------------------------------------------------------------------
# impact-stats
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_impact_stats_counts_versions_and_nonempty_fields(
    client: AsyncClient, auth_headers: dict
):
    """Per-version counts plus non-empty field counts: an absent field and an
    empty-string value do not count as non-empty; real values do."""
    a = await _create_person(client, auth_headers, "711000001", age=40)
    await _create_person(client, auth_headers, "711000002")
    await _create_person(client, auth_headers, "711000003")

    # Simulate legacy data carrying an explicit empty string (create-path
    # validation would reject it today; the counts must still classify it
    # as empty).
    doc_a = await Document.find_one({
        "document_id": a["document_id"], "status": DocumentStatus.ACTIVE.value,
    })
    doc_a.data["last_name"] = ""
    await doc_a.save()

    resp = await client.get(
        IMPACT,
        headers=auth_headers,
        params={
            "template_id": "PERSON",
            "namespace": "wip",
            "fields": "age,first_name,last_name",
        },
    )
    assert resp.status_code == 200, resp.text
    stats = resp.json()
    assert stats["total_live_docs"] == 3
    assert stats["docs_per_version"] == {"1": 3}
    assert stats["field_nonempty_counts"]["age"] == 1
    assert stats["field_nonempty_counts"]["first_name"] == 3
    # last_name: two real values + one explicit "" → 2
    assert stats["field_nonempty_counts"]["last_name"] == 2


@pytest.mark.asyncio
async def test_impact_stats_requires_namespace_context(
    client: AsyncClient, auth_headers: dict
):
    resp = await client.get(
        IMPACT, headers=auth_headers, params={"template_id": "PERSON"}
    )
    # The test key is multi-namespace: omitting namespace is a 422.
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# declared renames in migrate
# ---------------------------------------------------------------------------


def _person_v2_with_rename(tid: str) -> dict:
    """v2 renames birth_date -> date_of_birth (same type)."""
    tpl = copy.deepcopy(SAMPLE_TEMPLATES[tid])
    tpl["version"] = 2
    tpl["fields"] = [
        ({**f, "name": "date_of_birth", "label": "Date of Birth"}
         if f["name"] == "birth_date" else f)
        for f in tpl["fields"]
    ]
    tpl["renames"] = {"date_of_birth": "birth_date"}
    return tpl


@pytest.mark.asyncio
async def test_migrate_applies_declared_renames(client: AsyncClient, auth_headers: dict):
    """A doc carrying the old key migrates cleanly to the renaming version:
    the declared rename re-keys the data before target validation, and the
    applied new document version stores the new key."""
    item = await _create_person(
        client, auth_headers, "711000009", birth_date="1990-01-01"
    )
    tid = SAMPLE_TEMPLATES["PERSON"]["template_id"]
    VERSIONED_TEMPLATE_OVERRIDES[(tid, 2)] = _person_v2_with_rename(tid)

    # Dry-run green: without the rename mapping this doc would fail with
    # unknown_field (birth_date is gone in v2).
    dry = await client.post(
        MIGRATE,
        headers=auth_headers,
        params={"namespace": "wip"},
        json={"template_id": "PERSON", "from_version": 1, "to_version": 2, "dry_run": True},
    )
    assert dry.status_code == 200, dry.text
    dry_body = dry.json()
    ours = [r for r in dry_body["results"] if r["document_id"] == item["document_id"]]
    assert ours and ours[0]["status"] == "updated", dry_body

    apply = await client.post(
        MIGRATE,
        headers=auth_headers,
        params={"namespace": "wip"},
        json={"template_id": "PERSON", "from_version": 1, "to_version": 2, "dry_run": False},
    )
    assert apply.status_code == 200, apply.text

    doc = await Document.find_one({
        "document_id": item["document_id"], "status": DocumentStatus.ACTIVE.value,
    })
    assert doc.template_version == 2
    assert doc.data.get("date_of_birth") == "1990-01-01"
    assert "birth_date" not in doc.data


# ---------------------------------------------------------------------------
# validate-candidate
# ---------------------------------------------------------------------------


def _candidate_from_person(**changes) -> dict:
    tpl = copy.deepcopy(SAMPLE_TEMPLATES["PERSON"])
    tpl.update(changes)
    return tpl


@pytest.mark.asyncio
async def test_candidate_sample_mode_reports_per_doc_results(
    client: AsyncClient, auth_headers: dict
):
    """Sampled documents validate against an inline candidate; a candidate
    that makes an absent field mandatory flags exactly those documents."""
    await _create_person(client, auth_headers, "711000011", age=30)
    await _create_person(client, auth_headers, "711000012")

    candidate = _candidate_from_person()
    candidate["fields"] = [
        ({**f, "mandatory": True} if f["name"] == "age" else f)
        for f in candidate["fields"]
    ]

    resp = await client.post(
        CANDIDATE,
        headers=auth_headers,
        json={
            "namespace": "wip",
            "template_definition": candidate,
            "sample_template_id": "PERSON",
            "sample_limit": 50,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 2
    assert body["valid_count"] == 1
    assert body["invalid_count"] == 1
    invalid = [r for r in body["results"] if not r["validation"]["valid"]]
    assert invalid and invalid[0]["document_id"] is not None
    # Sampled results always carry the source document_id.
    assert all(r["document_id"] for r in body["results"])


@pytest.mark.asyncio
async def test_candidate_explicit_documents_mode(client: AsyncClient, auth_headers: dict):
    candidate = _candidate_from_person()
    resp = await client.post(
        CANDIDATE,
        headers=auth_headers,
        json={
            "namespace": "wip",
            "template_definition": candidate,
            "documents": [
                {"national_id": "123456789", "first_name": "A", "last_name": "B"},
                {"first_name": "missing-identity-and-lastname"},
            ],
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 2
    assert body["valid_count"] == 1
    assert body["results"][0]["document_id"] is None


@pytest.mark.asyncio
async def test_candidate_requires_exactly_one_input_mode(
    client: AsyncClient, auth_headers: dict
):
    candidate = _candidate_from_person()
    neither = await client.post(
        CANDIDATE,
        headers=auth_headers,
        json={"namespace": "wip", "template_definition": candidate},
    )
    assert neither.status_code == 422
    both = await client.post(
        CANDIDATE,
        headers=auth_headers,
        json={
            "namespace": "wip",
            "template_definition": candidate,
            "documents": [{}],
            "sample_template_id": "PERSON",
        },
    )
    assert both.status_code == 422


@pytest.mark.asyncio
async def test_candidate_honors_declared_renames(
    client: AsyncClient, auth_headers: dict
):
    """A candidate carrying renames {new: old} validates sampled documents
    as-if re-keyed (the same semantics an applied migration uses) — the
    sanctioned rename flow must not false-fail the what-if loop with
    unknown_field on the old name plus missing-mandatory on the new one."""
    await _create_person(client, auth_headers, "711000021")

    candidate = _candidate_from_person()
    candidate["fields"] = [
        ({**f, "name": "family_name"} if f["name"] == "last_name" else f)
        for f in candidate["fields"]
    ]
    candidate["renames"] = {"family_name": "last_name"}

    resp = await client.post(
        CANDIDATE,
        headers=auth_headers,
        json={
            "namespace": "wip",
            "template_definition": candidate,
            "sample_template_id": "PERSON",
            "sample_limit": 50,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] >= 1
    assert body["invalid_count"] == 0, body["results"]

    # Without the declaration the same reshape fails both ways — the
    # contrast that proves the re-key is doing the work.
    del candidate["renames"]
    resp = await client.post(
        CANDIDATE,
        headers=auth_headers,
        json={
            "namespace": "wip",
            "template_definition": candidate,
            "sample_template_id": "PERSON",
            "sample_limit": 50,
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["invalid_count"] >= 1

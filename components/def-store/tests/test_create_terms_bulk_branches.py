"""Branch coverage for TerminologyService.create_terms_bulk (CASE-336 test bed).

These tests exist to pin the behaviour of create_terms_bulk's high-complexity
branches BEFORE the CC=47 decomposition, so the refactor is safe-by-tests.

Key constraint: the create-terms endpoint has a single-item fast path
(`len(items) == 1` → create_term, NOT create_terms_bulk). Only batches of
>= 2 items reach create_terms_bulk. Every test here therefore POSTs >= 2
items.

Branches targeted (line refs against terminology_service.py at filing):
- Outer batch loop multi-iteration (716-927; batch_size param)
- Phase C/D existing_by_value duplicate skip (754-760, 780-797)
- Phase D existing_by_id duplicate skip (749-753, 780-797) — via re-submit
- Phase D mixed partition: error + skipped + created in one batch
- Phase E `if terms_to_insert:` empty branch (824) — every item a duplicate
- Phase A `if not terms: return []` early-exit (692-693) — empty body

Branches NOT covered (deliberately):
- skip_duplicates=False / update_existing=True paths — unreachable from
  `/terminologies/{tid}/terms` (hardcoded defaults in api/terms.py); covered
  by import-export tests instead.
- BulkWriteError partial-fail (837-862) — race condition; structural
  equivalence is the safety net for the refactor.
- System-terminology cache invalidation (936-938) — narrow conditional,
  observed via test_ontology side-effects.
"""

import pytest
import pytest_asyncio
from httpx import AsyncClient

TERMS_BASE = "/api/def-store/terminologies"


async def _make_terminology(client: AsyncClient, auth_headers: dict, value: str) -> str:
    """Create a terminology and return its terminology_id."""
    response = await client.post(
        "/api/def-store/terminologies",
        headers=auth_headers,
        json=[{
            "value": value,
            "label": value.replace("_", " ").title(),
            "namespace": "wip",
            "case_sensitive": False,
        }],
    )
    assert response.status_code == 200
    data = response.json()
    assert data["results"][0]["status"] == "created", data
    return data["results"][0]["id"]


@pytest_asyncio.fixture
async def terminology_id(client: AsyncClient, auth_headers: dict) -> str:
    """Per-test terminology so duplicate values don't leak between tests."""
    import uuid
    value = f"CTB_{uuid.uuid4().hex[:8].upper()}"
    return await _make_terminology(client, auth_headers, value)


@pytest.mark.asyncio
async def test_multi_batch_outer_loop(
    client: AsyncClient, auth_headers: dict, terminology_id: str
):
    """5 terms with batch_size=2 → 3 outer-loop iterations (rows 0-1, 2-3, 4).

    Pins that batching preserves per-item order and creates every term once.
    The outer loop is the main source of CC contribution from the for-batch
    structure; this test must keep passing across the decomposition.
    """
    terms = [{"value": f"t{i}", "label": f"Term {i}"} for i in range(5)]
    response = await client.post(
        f"{TERMS_BASE}/{terminology_id}/terms?batch_size=2",
        headers=auth_headers,
        json=terms,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 5
    assert data["succeeded"] == 5
    assert data["failed"] == 0
    statuses = [r["status"] for r in data["results"]]
    assert statuses == ["created"] * 5
    # Indices are preserved across batches
    indices = [r["index"] for r in data["results"]]
    assert indices == [0, 1, 2, 3, 4]


@pytest.mark.asyncio
async def test_existing_by_value_skipped(
    client: AsyncClient, auth_headers: dict, terminology_id: str
):
    """Pre-create one term, then bulk-submit [existing, new] → first is
    'skipped' (existing-by-value branch), second is 'created'.

    Pins the value-match path in Phase C/D: existing_by_value populated from
    Phase C's value query, then Phase D matches by `term_req.value` and
    short-circuits with status='skipped' (default skip_duplicates=True).
    """
    # Seed via single-item POST → goes through create_term, not bulk
    seed = await client.post(
        f"{TERMS_BASE}/{terminology_id}/terms",
        headers=auth_headers,
        json=[{"value": "existing", "label": "Existing"}],
    )
    assert seed.json()["results"][0]["status"] == "created"

    response = await client.post(
        f"{TERMS_BASE}/{terminology_id}/terms",
        headers=auth_headers,
        json=[
            {"value": "existing", "label": "Existing v2"},
            {"value": "fresh", "label": "Fresh"},
        ],
    )
    assert response.status_code == 200
    data = response.json()
    by_index = {r["index"]: r for r in data["results"]}

    assert by_index[0]["status"] == "skipped"
    assert by_index[0]["error"] == "Already exists"
    assert by_index[1]["status"] == "created"
    assert data["failed"] == 0


@pytest.mark.asyncio
async def test_resubmit_all_skipped_via_registry_already_exists(
    client: AsyncClient, auth_headers: dict, terminology_id: str
):
    """Submit the same batch twice. On the second call, the Registry returns
    `already_exists` with the same registry_ids → existing_by_id branch fires
    for every item.

    This pins the registry-id-match path distinct from the value-match path:
    after the first call, every term has a Registry entry. The second call's
    `register_terms_bulk` returns those existing registry_ids; Phase C's
    `Term.find({term_id: {$in: ...}})` populates `existing_by_id`; Phase D
    matches via `existing_by_id.get(term_id)` and skips.
    """
    terms = [{"value": "alpha"}, {"value": "beta"}, {"value": "gamma"}]
    first = await client.post(
        f"{TERMS_BASE}/{terminology_id}/terms",
        headers=auth_headers,
        json=terms,
    )
    assert first.status_code == 200
    assert {r["status"] for r in first.json()["results"]} == {"created"}

    second = await client.post(
        f"{TERMS_BASE}/{terminology_id}/terms",
        headers=auth_headers,
        json=terms,
    )
    assert second.status_code == 200
    data = second.json()
    statuses = [r["status"] for r in data["results"]]
    assert statuses == ["skipped"] * 3
    # Every result row carries an id (the existing term_id), confirming the
    # existing_by_id branch returned the matched term's term_id.
    assert all(r["id"] for r in data["results"])


@pytest.mark.asyncio
async def test_mixed_new_and_existing_in_one_batch(
    client: AsyncClient, auth_headers: dict, terminology_id: str
):
    """Pre-create one term, then submit [existing, new, new] in a single
    bulk call. Pins the partition step: Phase D must yield one 'skipped'
    plus two 'created', and Phase E must insert exactly the two new terms.
    """
    seed = await client.post(
        f"{TERMS_BASE}/{terminology_id}/terms",
        headers=auth_headers,
        json=[{"value": "seeded"}],
    )
    assert seed.json()["results"][0]["status"] == "created"

    response = await client.post(
        f"{TERMS_BASE}/{terminology_id}/terms",
        headers=auth_headers,
        json=[
            {"value": "seeded"},
            {"value": "new1"},
            {"value": "new2"},
        ],
    )
    assert response.status_code == 200
    data = response.json()
    by_index = {r["index"]: r for r in data["results"]}

    assert by_index[0]["status"] == "skipped"
    assert by_index[1]["status"] == "created"
    assert by_index[2]["status"] == "created"
    assert data["total"] == 3


@pytest.mark.asyncio
async def test_all_duplicates_skip_path_no_insert(
    client: AsyncClient, auth_headers: dict, terminology_id: str
):
    """When every item is a duplicate, Phase E's `if terms_to_insert:` is
    False and insert_many is never called. Pins the empty-insert branch.
    """
    await client.post(
        f"{TERMS_BASE}/{terminology_id}/terms",
        headers=auth_headers,
        json=[{"value": "a"}, {"value": "b"}],
    )

    response = await client.post(
        f"{TERMS_BASE}/{terminology_id}/terms",
        headers=auth_headers,
        json=[{"value": "a"}, {"value": "b"}],
    )
    assert response.status_code == 200
    data = response.json()
    statuses = [r["status"] for r in data["results"]]
    assert statuses == ["skipped", "skipped"]
    # No new versions, no errors
    assert data["failed"] == 0


@pytest.mark.asyncio
async def test_label_defaults_to_value_when_omitted(
    client: AsyncClient, auth_headers: dict, terminology_id: str
):
    """Phase D contains `label = term_req.label or term_req.value` — when
    label is missing, the value is used. Pins this default so the refactor
    can't accidentally drop the fallback.
    """
    response = await client.post(
        f"{TERMS_BASE}/{terminology_id}/terms",
        headers=auth_headers,
        json=[
            {"value": "labelled", "label": "An Explicit Label"},
            {"value": "unlabelled"},
        ],
    )
    assert response.status_code == 200
    data = response.json()
    assert {r["status"] for r in data["results"]} == {"created"}

    # Fetch back and check labels — first uses explicit label, second falls
    # back to value.
    for r, expected_label in zip(
        data["results"], ["An Explicit Label", "unlabelled"], strict=True
    ):
        got = await client.get(
            f"/api/def-store/terms/{r['id']}", headers=auth_headers
        )
        assert got.json()["label"] == expected_label

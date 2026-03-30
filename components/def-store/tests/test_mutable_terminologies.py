"""Tests for mutable terminology feature: hard-delete behaviour, mutable flag rules, and extensible auto-set."""

import pytest
from httpx import AsyncClient

API = "/api/def-store"


# =============================================================================
# HELPERS
# =============================================================================

async def create_terminology(
    client: AsyncClient,
    auth_headers: dict,
    value: str = "MUTABLE_TEST",
    label: str = "Mutable Test",
    mutable: bool = False,
    extensible: bool = False,
) -> dict:
    """Create a terminology and return the full GET response."""
    resp = await client.post(
        f"{API}/terminologies",
        headers=auth_headers,
        json=[{
            "value": value,
            "label": label,
            "namespace": "wip",
            "mutable": mutable,
            "extensible": extensible,
        }],
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["succeeded"] == 1, f"Failed to create terminology '{value}': {data}"
    terminology_id = data["results"][0]["id"]

    get_resp = await client.get(
        f"{API}/terminologies/{terminology_id}",
        headers=auth_headers,
    )
    assert get_resp.status_code == 200
    return get_resp.json()


async def create_term(
    client: AsyncClient,
    auth_headers: dict,
    terminology_id: str,
    value: str,
    label: str | None = None,
) -> str:
    """Create a term and return its ID."""
    body = {"value": value}
    if label:
        body["label"] = label
    resp = await client.post(
        f"{API}/terminologies/{terminology_id}/terms",
        headers=auth_headers,
        json=[body],
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["succeeded"] == 1, f"Failed to create term '{value}': {data}"
    return data["results"][0]["id"]


async def create_relationship(
    client: AsyncClient,
    auth_headers: dict,
    source_id: str,
    target_id: str,
    rel_type: str = "is_a",
) -> dict:
    """Create an ontology relationship and return the response."""
    resp = await client.post(
        f"{API}/ontology/relationships",
        json=[{
            "source_term_id": source_id,
            "target_term_id": target_id,
            "relationship_type": rel_type,
        }],
        headers=auth_headers,
        params={"namespace": "wip"},
    )
    assert resp.status_code == 200
    return resp.json()


async def delete_term(client: AsyncClient, auth_headers: dict, term_id: str) -> dict:
    """Delete a term via the bulk DELETE endpoint and return the response."""
    resp = await client.request(
        "DELETE",
        f"{API}/terms",
        headers=auth_headers,
        content=f'[{{"id": "{term_id}"}}]',
    )
    assert resp.status_code == 200
    return resp.json()


async def delete_terminology(
    client: AsyncClient,
    auth_headers: dict,
    terminology_id: str,
    force: bool = False,
) -> dict:
    """Delete a terminology via the bulk DELETE endpoint and return the response."""
    resp = await client.request(
        "DELETE",
        f"{API}/terminologies",
        headers=auth_headers,
        content=f'[{{"id": "{terminology_id}", "force": {"true" if force else "false"}}}]',
    )
    assert resp.status_code == 200
    return resp.json()


# =============================================================================
# TERMINOLOGY CREATION — MUTABLE FLAG AND EXTENSIBLE AUTO-SET
# =============================================================================

class TestMutableTerminologyCreation:
    """Tests for mutable flag behaviour during terminology creation."""

    @pytest.mark.asyncio
    async def test_create_mutable_true_sets_extensible_true(
        self, client: AsyncClient, auth_headers: dict
    ):
        """Creating a terminology with mutable=true should auto-set extensible=true."""
        terminology = await create_terminology(
            client, auth_headers, value="MUT_EXT", label="Mutable Extensible",
            mutable=True, extensible=False,
        )

        assert terminology["mutable"] is True
        assert terminology["extensible"] is True

    @pytest.mark.asyncio
    async def test_create_mutable_true_extensible_false_overridden(
        self, client: AsyncClient, auth_headers: dict
    ):
        """Explicitly setting extensible=false with mutable=true should still result in extensible=true."""
        resp = await client.post(
            f"{API}/terminologies",
            headers=auth_headers,
            json=[{
                "value": "MUT_EXT_OVERRIDE",
                "label": "Override Test",
                "namespace": "wip",
                "mutable": True,
                "extensible": False,
            }],
        )
        assert resp.status_code == 200
        terminology_id = resp.json()["results"][0]["id"]

        get_resp = await client.get(
            f"{API}/terminologies/{terminology_id}",
            headers=auth_headers,
        )
        detail = get_resp.json()
        assert detail["mutable"] is True
        assert detail["extensible"] is True

    @pytest.mark.asyncio
    async def test_create_mutable_false_default(
        self, client: AsyncClient, auth_headers: dict
    ):
        """Default creation (no mutable flag) should have mutable=false."""
        terminology = await create_terminology(
            client, auth_headers, value="IMMUTABLE_DEFAULT", label="Immutable Default",
        )

        assert terminology["mutable"] is False

    @pytest.mark.asyncio
    async def test_get_terminology_includes_mutable_field(
        self, client: AsyncClient, auth_headers: dict
    ):
        """GET terminology response should include the mutable field."""
        terminology = await create_terminology(
            client, auth_headers, value="CHECK_FIELD", label="Check Field",
            mutable=True,
        )

        assert "mutable" in terminology
        assert terminology["mutable"] is True


# =============================================================================
# MUTABLE FLAG UPDATE RULES
# =============================================================================

class TestMutableFlagUpdateRules:
    """Tests for restrictions on changing the mutable flag after creation."""

    @pytest.mark.asyncio
    async def test_update_mutable_false_to_true_no_terms(
        self, client: AsyncClient, auth_headers: dict
    ):
        """Updating mutable from false to true should succeed when terminology has 0 terms."""
        terminology = await create_terminology(
            client, auth_headers, value="UPGRADE_MUT", label="Upgrade Mutable",
            mutable=False,
        )
        terminology_id = terminology["terminology_id"]

        resp = await client.put(
            f"{API}/terminologies",
            headers=auth_headers,
            json=[{
                "terminology_id": terminology_id,
                "mutable": True,
            }],
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["succeeded"] == 1
        assert data["results"][0]["status"] == "updated"

        # Verify via GET
        get_resp = await client.get(
            f"{API}/terminologies/{terminology_id}",
            headers=auth_headers,
        )
        detail = get_resp.json()
        assert detail["mutable"] is True
        assert detail["extensible"] is True  # auto-set

    @pytest.mark.asyncio
    async def test_update_mutable_false_to_true_with_terms_rejected(
        self, client: AsyncClient, auth_headers: dict
    ):
        """Updating mutable from false to true should be REJECTED when terms exist."""
        terminology = await create_terminology(
            client, auth_headers, value="LOCKED_MUT", label="Locked Mutable",
            mutable=False, extensible=True,
        )
        terminology_id = terminology["terminology_id"]

        # Add a term
        await create_term(client, auth_headers, terminology_id, "some_term")

        # Try to change mutable
        resp = await client.put(
            f"{API}/terminologies",
            headers=auth_headers,
            json=[{
                "terminology_id": terminology_id,
                "mutable": True,
            }],
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["failed"] == 1
        assert "cannot change mutable" in data["results"][0]["error"].lower()

    @pytest.mark.asyncio
    async def test_update_mutable_true_to_false_with_terms_rejected(
        self, client: AsyncClient, auth_headers: dict
    ):
        """Updating mutable from true to false should be REJECTED when terms exist."""
        terminology = await create_terminology(
            client, auth_headers, value="DOWNGRADE_MUT", label="Downgrade Mutable",
            mutable=True,
        )
        terminology_id = terminology["terminology_id"]

        # Add a term
        await create_term(client, auth_headers, terminology_id, "another_term")

        # Try to change mutable to false
        resp = await client.put(
            f"{API}/terminologies",
            headers=auth_headers,
            json=[{
                "terminology_id": terminology_id,
                "mutable": False,
            }],
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["failed"] == 1
        assert "cannot change mutable" in data["results"][0]["error"].lower()


# =============================================================================
# HARD-DELETE TERMS (MUTABLE TERMINOLOGY)
# =============================================================================

class TestHardDeleteTerms:
    """Tests for hard-deleting terms in mutable terminologies."""

    @pytest.mark.asyncio
    async def test_delete_term_mutable_hard_deletes(
        self, client: AsyncClient, auth_headers: dict
    ):
        """Deleting a term from a mutable terminology should hard-delete it (404 on GET)."""
        terminology = await create_terminology(
            client, auth_headers, value="HARD_DEL", label="Hard Delete",
            mutable=True,
        )
        terminology_id = terminology["terminology_id"]

        term_id = await create_term(client, auth_headers, terminology_id, "ephemeral")

        # Delete the term
        del_data = await delete_term(client, auth_headers, term_id)
        assert del_data["succeeded"] == 1
        assert del_data["results"][0]["status"] == "deleted"

        # Verify term is GONE (404), not just inactive
        get_resp = await client.get(
            f"{API}/terms/{term_id}",
            headers=auth_headers,
        )
        assert get_resp.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_term_mutable_cascades_relationships(
        self, client: AsyncClient, auth_headers: dict
    ):
        """Deleting a term from a mutable terminology should also hard-delete its relationships."""
        terminology = await create_terminology(
            client, auth_headers, value="REL_CASCADE", label="Rel Cascade",
            mutable=True,
        )
        terminology_id = terminology["terminology_id"]

        parent = await create_term(client, auth_headers, terminology_id, "parent_term")
        child = await create_term(client, auth_headers, terminology_id, "child_term")

        # Create a relationship
        rel_data = await create_relationship(client, auth_headers, child, parent, "is_a")
        assert rel_data["succeeded"] == 1

        # Verify relationship exists
        rel_resp = await client.get(
            f"{API}/ontology/relationships",
            params={"term_id": child, "direction": "outgoing", "namespace": "wip"},
            headers=auth_headers,
        )
        assert rel_resp.json()["total"] == 1

        # Delete the child term
        await delete_term(client, auth_headers, child)

        # Verify term is gone
        get_resp = await client.get(
            f"{API}/terms/{child}",
            headers=auth_headers,
        )
        assert get_resp.status_code == 404

        # Verify relationship is gone too
        rel_resp2 = await client.get(
            f"{API}/ontology/relationships",
            params={"term_id": child, "direction": "outgoing", "namespace": "wip"},
            headers=auth_headers,
        )
        assert rel_resp2.json()["total"] == 0

    @pytest.mark.asyncio
    async def test_delete_term_mutable_response_includes_info(
        self, client: AsyncClient, auth_headers: dict
    ):
        """The delete response should include the term ID in results."""
        terminology = await create_terminology(
            client, auth_headers, value="DEL_INFO", label="Delete Info",
            mutable=True,
        )
        terminology_id = terminology["terminology_id"]

        term_id = await create_term(client, auth_headers, terminology_id, "info_term")

        del_data = await delete_term(client, auth_headers, term_id)
        assert del_data["succeeded"] == 1
        assert del_data["results"][0]["id"] == term_id
        assert del_data["results"][0]["status"] == "deleted"


# =============================================================================
# SOFT-DELETE TERMS (IMMUTABLE TERMINOLOGY — EXISTING BEHAVIOUR PRESERVED)
# =============================================================================

class TestSoftDeleteTerms:
    """Tests verifying that immutable terminologies still use soft-delete for terms."""

    @pytest.mark.asyncio
    async def test_delete_term_immutable_soft_deletes(
        self, client: AsyncClient, auth_headers: dict
    ):
        """Deleting a term from an immutable terminology should soft-delete (status=inactive)."""
        terminology = await create_terminology(
            client, auth_headers, value="SOFT_DEL", label="Soft Delete",
            mutable=False, extensible=True,
        )
        terminology_id = terminology["terminology_id"]

        term_id = await create_term(client, auth_headers, terminology_id, "persistent")

        # Delete the term
        del_data = await delete_term(client, auth_headers, term_id)
        assert del_data["succeeded"] == 1
        assert del_data["results"][0]["status"] == "deleted"

        # Verify term still exists but is inactive
        get_resp = await client.get(
            f"{API}/terms/{term_id}",
            headers=auth_headers,
        )
        assert get_resp.status_code == 200
        assert get_resp.json()["status"] == "inactive"


# =============================================================================
# HARD-DELETE TERMINOLOGY (MUTABLE)
# =============================================================================

class TestHardDeleteTerminology:
    """Tests for hard-deleting mutable terminologies."""

    @pytest.mark.asyncio
    async def test_delete_mutable_terminology_with_terms(
        self, client: AsyncClient, auth_headers: dict
    ):
        """Deleting a mutable terminology should hard-delete it and all its terms."""
        terminology = await create_terminology(
            client, auth_headers, value="TERM_HARD_DEL", label="Terminology Hard Delete",
            mutable=True,
        )
        terminology_id = terminology["terminology_id"]

        # Add terms
        term1_id = await create_term(client, auth_headers, terminology_id, "t1")
        term2_id = await create_term(client, auth_headers, terminology_id, "t2")

        # Delete the terminology
        del_data = await delete_terminology(client, auth_headers, terminology_id)
        assert del_data["succeeded"] == 1
        assert del_data["results"][0]["status"] == "deleted"

        # Verify terminology is GONE (404)
        get_resp = await client.get(
            f"{API}/terminologies/{terminology_id}",
            headers=auth_headers,
        )
        assert get_resp.status_code == 404

        # Verify all terms are GONE (404)
        for tid in [term1_id, term2_id]:
            get_term = await client.get(
                f"{API}/terms/{tid}",
                headers=auth_headers,
            )
            assert get_term.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_mutable_terminology_cascades_relationships(
        self, client: AsyncClient, auth_headers: dict
    ):
        """Deleting a mutable terminology should hard-delete terms and their relationships."""
        terminology = await create_terminology(
            client, auth_headers, value="TERM_REL_CASCADE", label="Term Rel Cascade",
            mutable=True,
        )
        terminology_id = terminology["terminology_id"]

        parent = await create_term(client, auth_headers, terminology_id, "parent")
        child = await create_term(client, auth_headers, terminology_id, "child")

        # Create relationship
        rel_data = await create_relationship(client, auth_headers, child, parent, "is_a")
        assert rel_data["succeeded"] == 1

        # Delete the terminology
        del_data = await delete_terminology(client, auth_headers, terminology_id)
        assert del_data["succeeded"] == 1

        # Verify terminology is gone
        get_resp = await client.get(
            f"{API}/terminologies/{terminology_id}",
            headers=auth_headers,
        )
        assert get_resp.status_code == 404

        # Verify terms are gone
        for tid in [parent, child]:
            get_term = await client.get(
                f"{API}/terms/{tid}",
                headers=auth_headers,
            )
            assert get_term.status_code == 404

        # Verify relationships are gone
        for tid in [parent, child]:
            rel_resp = await client.get(
                f"{API}/ontology/relationships",
                params={"term_id": tid, "namespace": "wip"},
                headers=auth_headers,
            )
            assert rel_resp.json()["total"] == 0


# =============================================================================
# SOFT-DELETE TERMINOLOGY (IMMUTABLE — EXISTING BEHAVIOUR PRESERVED)
# =============================================================================

class TestSoftDeleteTerminology:
    """Tests verifying that immutable terminologies still use soft-delete."""

    @pytest.mark.asyncio
    async def test_delete_immutable_terminology_soft_deletes(
        self, client: AsyncClient, auth_headers: dict
    ):
        """Deleting an immutable terminology with force=true should soft-delete (status=inactive)."""
        terminology = await create_terminology(
            client, auth_headers, value="TERM_SOFT_DEL", label="Terminology Soft Delete",
            mutable=False,
        )
        terminology_id = terminology["terminology_id"]

        # Add a term
        term_id = await create_term(
            client, auth_headers, terminology_id, "kept_term",
        )

        # Delete with force (no dependency check)
        del_data = await delete_terminology(client, auth_headers, terminology_id, force=True)
        assert del_data["succeeded"] == 1
        assert del_data["results"][0]["status"] == "deleted"

        # Verify terminology is inactive, NOT gone
        get_resp = await client.get(
            f"{API}/terminologies/{terminology_id}",
            headers=auth_headers,
        )
        assert get_resp.status_code == 200
        assert get_resp.json()["status"] == "inactive"

        # Verify term still exists (inactive)
        get_term = await client.get(
            f"{API}/terms/{term_id}",
            headers=auth_headers,
        )
        assert get_term.status_code == 200
        assert get_term.json()["status"] == "inactive"


# =============================================================================
# EDGE CASES
# =============================================================================

class TestMutableEdgeCases:
    """Edge cases for mutable terminology behaviour."""

    @pytest.mark.asyncio
    async def test_multiple_term_hard_deletes(
        self, client: AsyncClient, auth_headers: dict
    ):
        """Adding and deleting multiple terms from a mutable terminology should hard-delete each."""
        terminology = await create_terminology(
            client, auth_headers, value="MULTI_DEL", label="Multi Delete",
            mutable=True,
        )
        terminology_id = terminology["terminology_id"]

        # Create several terms
        term_ids = []
        for i in range(4):
            tid = await create_term(
                client, auth_headers, terminology_id, f"term_{i}",
            )
            term_ids.append(tid)

        # Delete them one by one
        for tid in term_ids:
            del_data = await delete_term(client, auth_headers, tid)
            assert del_data["succeeded"] == 1

        # Verify all are gone
        for tid in term_ids:
            get_resp = await client.get(
                f"{API}/terms/{tid}",
                headers=auth_headers,
            )
            assert get_resp.status_code == 404

        # Verify terminology term_count is 0
        get_term_resp = await client.get(
            f"{API}/terminologies/{terminology_id}",
            headers=auth_headers,
        )
        assert get_term_resp.json()["term_count"] == 0

    @pytest.mark.asyncio
    async def test_mutable_field_in_list_terminologies(
        self, client: AsyncClient, auth_headers: dict
    ):
        """The mutable field should appear in the list terminologies response."""
        await create_terminology(
            client, auth_headers, value="LIST_MUT_TRUE", label="List Mutable True",
            mutable=True,
        )
        await create_terminology(
            client, auth_headers, value="LIST_MUT_FALSE", label="List Mutable False",
            mutable=False,
        )

        resp = await client.get(
            f"{API}/terminologies",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 2

        items_by_value = {item["value"]: item for item in data["items"]}

        assert "mutable" in items_by_value["LIST_MUT_TRUE"]
        assert items_by_value["LIST_MUT_TRUE"]["mutable"] is True

        assert "mutable" in items_by_value["LIST_MUT_FALSE"]
        assert items_by_value["LIST_MUT_FALSE"]["mutable"] is False

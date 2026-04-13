"""Tests for hard-delete support in Def-Store.

Covers:
- Hard-delete immutable terminology in 'full' namespace
- Hard-delete term in 'full' namespace
- Hard-delete relationship in 'full' namespace
- Registry cleanup after hard-delete
- Rejection in 'retain' namespace
- Soft-delete regression (unchanged when hard_delete=False)
"""

import pytest
import pytest_asyncio

from registry.models.namespace import Namespace


# =========================================================================
# Fixtures
# =========================================================================


@pytest_asyncio.fixture
async def full_deletion_namespace():
    """Set 'wip' namespace to full deletion mode for hard-delete tests."""
    ns = await Namespace.find_one({"prefix": "wip"})
    original_mode = getattr(ns, "deletion_mode", "retain")
    ns.deletion_mode = "full"
    await ns.save()
    yield
    ns.deletion_mode = original_mode
    await ns.save()


# =========================================================================
# Helpers
# =========================================================================


async def create_terminology(client, auth_headers, value, label, mutable=False):
    """Create a terminology and return its ID."""
    response = await client.post(
        "/api/def-store/terminologies",
        headers=auth_headers,
        json=[{"value": value, "label": label, "mutable": mutable, "namespace": "wip"}],
    )
    assert response.status_code == 200
    data = response.json()
    assert data["succeeded"] >= 1, f"Failed to create terminology: {data}"
    return data["results"][0]["id"]


async def create_term(client, auth_headers, terminology_id, value, label=None):
    """Create a term and return its ID."""
    response = await client.post(
        f"/api/def-store/terminologies/{terminology_id}/terms",
        headers=auth_headers,
        json=[{"value": value, "label": label or value}],
    )
    assert response.status_code == 200
    data = response.json()
    assert data["succeeded"] >= 1, f"Failed to create term: {data}"
    return data["results"][0]["id"]


async def create_relationship(client, auth_headers, source_id, target_id, rel_type="is_a"):
    """Create a relationship between two terms."""
    response = await client.post(
        "/api/def-store/ontology/relationships",
        headers=auth_headers,
        params={"namespace": "wip"},
        json=[{
            "source_term_id": source_id,
            "target_term_id": target_id,
            "relationship_type": rel_type,
        }],
    )
    assert response.status_code == 200
    data = response.json()
    assert data["succeeded"] >= 1, f"Failed to create relationship: {data}"


async def delete_terminology(client, auth_headers, terminology_id, force=False, hard_delete=False):
    """Delete a terminology via bulk endpoint."""
    response = await client.request(
        "DELETE",
        "/api/def-store/terminologies",
        headers=auth_headers,
        json=[{"id": terminology_id, "force": force, "hard_delete": hard_delete}],
    )
    assert response.status_code == 200
    return response.json()


async def delete_term(client, auth_headers, term_id, hard_delete=False):
    """Delete a term via the single-term endpoint."""
    response = await client.request(
        "DELETE",
        f"/api/def-store/terms/{term_id}",
        headers=auth_headers,
        json={"hard_delete": hard_delete} if hard_delete else {},
    )
    return response


async def delete_term_bulk(client, auth_headers, term_id, hard_delete=False):
    """Delete a term via bulk endpoint."""
    response = await client.request(
        "DELETE",
        "/api/def-store/terms",
        headers=auth_headers,
        json=[{"id": term_id, "hard_delete": hard_delete}],
    )
    assert response.status_code == 200
    return response.json()


async def delete_relationships(client, auth_headers, namespace, items):
    """Delete relationships via bulk endpoint."""
    response = await client.request(
        "DELETE",
        f"/api/def-store/ontology/relationships?namespace={namespace}",
        headers=auth_headers,
        json=items,
    )
    assert response.status_code == 200
    return response.json()


# =========================================================================
# Hard-Delete Terminology
# =========================================================================


class TestHardDeleteTerminology:
    """Tests for hard-deleting terminologies in 'full' deletion_mode namespaces."""

    @pytest.mark.asyncio
    async def test_hard_delete_immutable_terminology_in_full_namespace(
        self, client, auth_headers, full_deletion_namespace
    ):
        """Immutable terminology can be hard-deleted when namespace has deletion_mode='full'."""
        tid = await create_terminology(client, auth_headers, "HD_IMMUT", "Hard Delete Immutable")
        t1 = await create_term(client, auth_headers, tid, "VAL1", "Value 1")

        data = await delete_terminology(client, auth_headers, tid, hard_delete=True)

        assert data["succeeded"] == 1

        # Verify terminology is gone (404, not just inactive)
        get_resp = await client.get(
            f"/api/def-store/terminologies/{tid}",
            headers=auth_headers,
        )
        assert get_resp.status_code == 404

        # Verify terms are also gone
        get_term = await client.get(
            f"/api/def-store/terms/{t1}",
            headers=auth_headers,
        )
        assert get_term.status_code == 404

    @pytest.mark.asyncio
    async def test_hard_delete_rejected_in_retain_namespace(self, client, auth_headers):
        """Hard-delete fails for immutable terminology when namespace is 'retain'."""
        tid = await create_terminology(client, auth_headers, "HD_RETAIN", "Retain Test")

        # Default namespace deletion_mode is 'retain' — no fixture needed
        data = await delete_terminology(client, auth_headers, tid, hard_delete=True)

        assert data["failed"] == 1
        assert "deletion_mode" in data["results"][0]["error"]

        # Terminology should still exist and be active
        get_resp = await client.get(
            f"/api/def-store/terminologies/{tid}",
            headers=auth_headers,
        )
        assert get_resp.status_code == 200
        assert get_resp.json()["status"] == "active"

    @pytest.mark.asyncio
    async def test_hard_delete_cascades_relationships(self, client, auth_headers):
        """Hard-deleting a terminology also removes all relationships involving its terms."""

        tid = await create_terminology(client, auth_headers, "HD_CASCADE", "Cascade Test", mutable=True)
        t1 = await create_term(client, auth_headers, tid, "PARENT", "Parent")
        t2 = await create_term(client, auth_headers, tid, "CHILD", "Child")
        await create_relationship(client, auth_headers, t2, t1, "is_a")

        # Mutable terminologies hard-delete without needing deletion_mode check
        data = await delete_terminology(client, auth_headers, tid, force=True)
        assert data["succeeded"] == 1

        # All gone
        assert (await client.get(f"/api/def-store/terminologies/{tid}", headers=auth_headers)).status_code == 404
        assert (await client.get(f"/api/def-store/terms/{t1}", headers=auth_headers)).status_code == 404
        assert (await client.get(f"/api/def-store/terms/{t2}", headers=auth_headers)).status_code == 404


# =========================================================================
# Hard-Delete Term
# =========================================================================


class TestHardDeleteTerm:
    """Tests for hard-deleting individual terms."""

    @pytest.mark.asyncio
    async def test_hard_delete_term_in_full_namespace(
        self, client, auth_headers, full_deletion_namespace
    ):
        """Term can be hard-deleted when namespace has deletion_mode='full'."""
        tid = await create_terminology(client, auth_headers, "HD_TERM_NS", "Term HD NS")
        t1 = await create_term(client, auth_headers, tid, "REMOVE_ME", "Remove Me")
        t2 = await create_term(client, auth_headers, tid, "KEEP_ME", "Keep Me")

        data = await delete_term_bulk(client, auth_headers, t1, hard_delete=True)

        assert data["succeeded"] == 1

        # t1 should be gone
        assert (await client.get(f"/api/def-store/terms/{t1}", headers=auth_headers)).status_code == 404

        # t2 should still exist
        resp = await client.get(f"/api/def-store/terms/{t2}", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["status"] == "active"

    @pytest.mark.asyncio
    async def test_hard_delete_term_cascades_relationships(self, client, auth_headers):
        """Hard-deleting a term removes relationships where it's source or target."""

        tid = await create_terminology(client, auth_headers, "HD_TERM_REL", "Term Rel HD", mutable=True)
        t1 = await create_term(client, auth_headers, tid, "A")
        t2 = await create_term(client, auth_headers, tid, "B")
        t3 = await create_term(client, auth_headers, tid, "C")
        await create_relationship(client, auth_headers, t1, t2, "is_a")
        await create_relationship(client, auth_headers, t3, t1, "is_a")

        # Delete t1 (mutable = auto hard-delete)
        data = await delete_term_bulk(client, auth_headers, t1)
        assert data["succeeded"] == 1

        # t1 gone
        assert (await client.get(f"/api/def-store/terms/{t1}", headers=auth_headers)).status_code == 404

        # t2 and t3 still exist
        assert (await client.get(f"/api/def-store/terms/{t2}", headers=auth_headers)).status_code == 200
        assert (await client.get(f"/api/def-store/terms/{t3}", headers=auth_headers)).status_code == 200

    @pytest.mark.asyncio
    async def test_hard_delete_term_rejected_in_retain_namespace(self, client, auth_headers):
        """Hard-delete fails for immutable term when namespace is 'retain'."""
        tid = await create_terminology(client, auth_headers, "HD_TERM_RETAIN", "Term Retain")
        t1 = await create_term(client, auth_headers, tid, "RETAINED")

        # Default namespace deletion_mode is 'retain' — no fixture needed
        data = await delete_term_bulk(client, auth_headers, t1, hard_delete=True)

        assert data["failed"] == 1
        assert "deletion_mode" in data["results"][0]["error"]


# =========================================================================
# Hard-Delete Relationship
# =========================================================================


class TestHardDeleteRelationship:
    """Tests for hard-deleting relationships."""

    @pytest.mark.asyncio
    async def test_hard_delete_relationship_in_full_namespace(
        self, client, auth_headers, full_deletion_namespace
    ):
        """Relationship can be hard-deleted when namespace has deletion_mode='full'."""
        tid = await create_terminology(client, auth_headers, "HD_REL_NS", "Rel HD NS")
        t1 = await create_term(client, auth_headers, tid, "SRC")
        t2 = await create_term(client, auth_headers, tid, "TGT")
        await create_relationship(client, auth_headers, t1, t2, "is_a")

        data = await delete_relationships(client, auth_headers, "wip", [{
            "source_term_id": t1,
            "target_term_id": t2,
            "relationship_type": "is_a",
            "hard_delete": True,
        }])

        assert data["succeeded"] == 1

        # Verify relationship is gone by listing
        list_resp = await client.get(
            "/api/def-store/ontology/relationships",
            headers=auth_headers,
            params={"namespace": "wip", "term_id": t1, "direction": "both"},
        )
        assert list_resp.status_code == 200
        items = list_resp.json().get("items", [])
        assert len(items) == 0

    @pytest.mark.asyncio
    async def test_hard_delete_relationship_rejected_in_retain(self, client, auth_headers):
        """Hard-delete relationship fails when namespace is 'retain'."""
        tid = await create_terminology(client, auth_headers, "HD_REL_RETAIN", "Rel Retain")
        t1 = await create_term(client, auth_headers, tid, "SRC_R")
        t2 = await create_term(client, auth_headers, tid, "TGT_R")
        await create_relationship(client, auth_headers, t1, t2, "is_a")

        # Default namespace deletion_mode is 'retain' — no fixture needed
        data = await delete_relationships(client, auth_headers, "wip", [{
            "source_term_id": t1,
            "target_term_id": t2,
            "relationship_type": "is_a",
            "hard_delete": True,
        }])

        assert data["failed"] == 1
        assert "deletion_mode" in data["results"][0]["error"]


# =========================================================================
# Soft-Delete Regression
# =========================================================================


class TestSoftDeleteRegression:
    """Verify soft-delete behavior is unchanged when hard_delete=False."""

    @pytest.mark.asyncio
    async def test_soft_delete_terminology_sets_inactive(self, client, auth_headers):
        """Default delete (hard_delete=False) soft-deletes terminology."""
        tid = await create_terminology(client, auth_headers, "SOFT_DEL_T", "Soft Del")
        await create_term(client, auth_headers, tid, "SOFT_TERM")

        data = await delete_terminology(client, auth_headers, tid)
        assert data["succeeded"] == 1

        # Terminology still exists, just inactive
        get_resp = await client.get(
            f"/api/def-store/terminologies/{tid}",
            headers=auth_headers,
        )
        assert get_resp.status_code == 200
        assert get_resp.json()["status"] == "inactive"

    @pytest.mark.asyncio
    async def test_soft_delete_term_sets_inactive(self, client, auth_headers):
        """Default delete on immutable term soft-deletes it."""
        tid = await create_terminology(client, auth_headers, "SOFT_TERM_T", "Soft Term Del")
        t1 = await create_term(client, auth_headers, tid, "SOFTIE")

        data = await delete_term_bulk(client, auth_headers, t1)
        assert data["succeeded"] == 1

        # Term still exists, just inactive
        get_resp = await client.get(
            f"/api/def-store/terms/{t1}",
            headers=auth_headers,
        )
        assert get_resp.status_code == 200
        assert get_resp.json()["status"] == "inactive"

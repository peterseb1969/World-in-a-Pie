"""Tests for ontology relationship CRUD and traversal endpoints."""

import pytest

API = "/api/def-store"


# =============================================================================
# HELPERS
# =============================================================================

async def create_terminology(client, auth_headers, value="TEST_ONTOLOGY", label="Test Ontology"):
    """Create a terminology and return its ID."""
    resp = await client.post(
        f"{API}/terminologies",
        json=[{"value": value, "label": label}],
        headers=auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["succeeded"] == 1
    return data["results"][0]["id"]


async def create_term(client, auth_headers, terminology_id, value, parent_term_id=None):
    """Create a term and return its ID."""
    body = {"value": value}
    if parent_term_id:
        body["parent_term_id"] = parent_term_id
    resp = await client.post(
        f"{API}/terminologies/{terminology_id}/terms",
        json=[body],
        headers=auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["succeeded"] == 1, f"Failed to create term '{value}': {data}"
    return data["results"][0]["id"]


async def create_relationship(client, auth_headers, source_id, target_id, rel_type="is_a", namespace="wip"):
    """Create a relationship and return the response."""
    resp = await client.post(
        f"{API}/ontology/relationships",
        json=[{
            "source_term_id": source_id,
            "target_term_id": target_id,
            "relationship_type": rel_type,
        }],
        headers=auth_headers,
        params={"namespace": namespace},
    )
    assert resp.status_code == 200
    return resp.json()


# =============================================================================
# PHASE 1: RELATIONSHIP CRUD TESTS
# =============================================================================

class TestCreateRelationships:
    """Tests for POST /ontology/relationships."""

    @pytest.mark.asyncio
    async def test_create_single_relationship(self, client, auth_headers):
        tid = await create_terminology(client, auth_headers)
        a = await create_term(client, auth_headers, tid, "Lung Disease")
        b = await create_term(client, auth_headers, tid, "Pneumonia")

        data = await create_relationship(client, auth_headers, b, a, "is_a")
        assert data["succeeded"] == 1
        assert data["results"][0]["status"] == "created"

    @pytest.mark.asyncio
    async def test_create_multiple_relationships(self, client, auth_headers):
        tid = await create_terminology(client, auth_headers)
        a = await create_term(client, auth_headers, tid, "Disease")
        b = await create_term(client, auth_headers, tid, "Lung Disease")
        c = await create_term(client, auth_headers, tid, "Pneumonia")

        resp = await client.post(
            f"{API}/ontology/relationships",
            json=[
                {"source_term_id": b, "target_term_id": a, "relationship_type": "is_a"},
                {"source_term_id": c, "target_term_id": b, "relationship_type": "is_a"},
            ],
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert data["succeeded"] == 2

    @pytest.mark.asyncio
    async def test_create_duplicate_relationship_fails(self, client, auth_headers):
        tid = await create_terminology(client, auth_headers)
        a = await create_term(client, auth_headers, tid, "Parent")
        b = await create_term(client, auth_headers, tid, "Child")

        # First creation
        data1 = await create_relationship(client, auth_headers, b, a, "is_a")
        assert data1["succeeded"] == 1

        # Duplicate
        data2 = await create_relationship(client, auth_headers, b, a, "is_a")
        assert data2["failed"] == 1
        assert "already exists" in data2["results"][0]["error"]

    @pytest.mark.asyncio
    async def test_create_relationship_nonexistent_source(self, client, auth_headers):
        tid = await create_terminology(client, auth_headers)
        a = await create_term(client, auth_headers, tid, "Exists")

        data = await create_relationship(client, auth_headers, "FAKE-ID", a, "is_a")
        assert data["failed"] == 1
        assert "not found" in data["results"][0]["error"]

    @pytest.mark.asyncio
    async def test_create_relationship_nonexistent_target(self, client, auth_headers):
        tid = await create_terminology(client, auth_headers)
        a = await create_term(client, auth_headers, tid, "Exists")

        data = await create_relationship(client, auth_headers, a, "FAKE-ID", "is_a")
        assert data["failed"] == 1
        assert "not found" in data["results"][0]["error"]

    @pytest.mark.asyncio
    async def test_create_self_referencing_relationship_fails(self, client, auth_headers):
        tid = await create_terminology(client, auth_headers)
        a = await create_term(client, auth_headers, tid, "Self")

        data = await create_relationship(client, auth_headers, a, a, "is_a")
        assert data["failed"] == 1
        assert "same" in data["results"][0]["error"].lower()

    @pytest.mark.asyncio
    async def test_create_cross_terminology_relationship(self, client, auth_headers):
        tid1 = await create_terminology(client, auth_headers, "ANATOMY", "Anatomy")
        tid2 = await create_terminology(client, auth_headers, "CONDITIONS", "Conditions")
        lung = await create_term(client, auth_headers, tid1, "Lung")
        pneumonia = await create_term(client, auth_headers, tid2, "Pneumonia")

        data = await create_relationship(client, auth_headers, pneumonia, lung, "finding_site")
        assert data["succeeded"] == 1

    @pytest.mark.asyncio
    async def test_create_relationship_with_metadata(self, client, auth_headers):
        tid = await create_terminology(client, auth_headers)
        a = await create_term(client, auth_headers, tid, "Parent")
        b = await create_term(client, auth_headers, tid, "Child")

        resp = await client.post(
            f"{API}/ontology/relationships",
            json=[{
                "source_term_id": b,
                "target_term_id": a,
                "relationship_type": "is_a",
                "metadata": {"source_ontology": "SNOMED-CT", "confidence": 1.0},
            }],
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["succeeded"] == 1


class TestListRelationships:
    """Tests for GET /ontology/relationships."""

    @pytest.mark.asyncio
    async def test_list_outgoing(self, client, auth_headers):
        tid = await create_terminology(client, auth_headers)
        a = await create_term(client, auth_headers, tid, "Parent")
        b = await create_term(client, auth_headers, tid, "Child")
        await create_relationship(client, auth_headers, b, a, "is_a")

        resp = await client.get(
            f"{API}/ontology/relationships",
            params={"term_id": b, "direction": "outgoing"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["target_term_id"] == a

    @pytest.mark.asyncio
    async def test_list_incoming(self, client, auth_headers):
        tid = await create_terminology(client, auth_headers)
        a = await create_term(client, auth_headers, tid, "Parent")
        b = await create_term(client, auth_headers, tid, "Child")
        await create_relationship(client, auth_headers, b, a, "is_a")

        resp = await client.get(
            f"{API}/ontology/relationships",
            params={"term_id": a, "direction": "incoming"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["source_term_id"] == b

    @pytest.mark.asyncio
    async def test_list_filter_by_type(self, client, auth_headers):
        tid = await create_terminology(client, auth_headers)
        a = await create_term(client, auth_headers, tid, "A")
        b = await create_term(client, auth_headers, tid, "B")
        c = await create_term(client, auth_headers, tid, "C")
        await create_relationship(client, auth_headers, b, a, "is_a")
        await create_relationship(client, auth_headers, b, c, "part_of")

        # Filter for is_a only
        resp = await client.get(
            f"{API}/ontology/relationships",
            params={"term_id": b, "direction": "outgoing", "relationship_type": "is_a"},
            headers=auth_headers,
        )
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["relationship_type"] == "is_a"

    @pytest.mark.asyncio
    async def test_list_empty(self, client, auth_headers):
        resp = await client.get(
            f"{API}/ontology/relationships",
            params={"term_id": "NONEXISTENT"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["total"] == 0


class TestDeleteRelationships:
    """Tests for DELETE /ontology/relationships."""

    @pytest.mark.asyncio
    async def test_delete_relationship(self, client, auth_headers):
        tid = await create_terminology(client, auth_headers)
        a = await create_term(client, auth_headers, tid, "Parent")
        b = await create_term(client, auth_headers, tid, "Child")
        await create_relationship(client, auth_headers, b, a, "is_a")

        resp = await client.request(
            "DELETE",
            f"{API}/ontology/relationships",
            json=[{"source_term_id": b, "target_term_id": a, "relationship_type": "is_a"}],
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["results"][0]["status"] == "deleted"

        # Verify it no longer appears in active list
        list_resp = await client.get(
            f"{API}/ontology/relationships",
            params={"term_id": b, "direction": "outgoing"},
            headers=auth_headers,
        )
        assert list_resp.json()["total"] == 0

    @pytest.mark.asyncio
    async def test_delete_nonexistent_relationship(self, client, auth_headers):
        resp = await client.request(
            "DELETE",
            f"{API}/ontology/relationships",
            json=[{"source_term_id": "X", "target_term_id": "Y", "relationship_type": "is_a"}],
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["results"][0]["status"] == "error"


# =============================================================================
# PHASE 2: TRAVERSAL TESTS
# =============================================================================

class TestAncestors:
    """Tests for GET /ontology/terms/{term_id}/ancestors."""

    @pytest.mark.asyncio
    async def test_linear_chain(self, client, auth_headers):
        """A is_a B is_a C → ancestors of A = [B(1), C(2)]."""
        tid = await create_terminology(client, auth_headers)
        c = await create_term(client, auth_headers, tid, "C")
        b = await create_term(client, auth_headers, tid, "B")
        a = await create_term(client, auth_headers, tid, "A")
        await create_relationship(client, auth_headers, a, b, "is_a")
        await create_relationship(client, auth_headers, b, c, "is_a")

        resp = await client.get(
            f"{API}/ontology/terms/{a}/ancestors",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2

        by_depth = {n["depth"]: n for n in data["nodes"]}
        assert by_depth[1]["term_id"] == b
        assert by_depth[2]["term_id"] == c

    @pytest.mark.asyncio
    async def test_polyhierarchy(self, client, auth_headers):
        """
        A is_a B, A is_a C, B is_a D, C is_a D
        → ancestors of A = [B(1), C(1), D(2)] — D only once.
        """
        tid = await create_terminology(client, auth_headers)
        d = await create_term(client, auth_headers, tid, "D")
        c = await create_term(client, auth_headers, tid, "C")
        b = await create_term(client, auth_headers, tid, "B")
        a = await create_term(client, auth_headers, tid, "A")
        await create_relationship(client, auth_headers, a, b, "is_a")
        await create_relationship(client, auth_headers, a, c, "is_a")
        await create_relationship(client, auth_headers, b, d, "is_a")
        await create_relationship(client, auth_headers, c, d, "is_a")

        resp = await client.get(
            f"{API}/ontology/terms/{a}/ancestors",
            headers=auth_headers,
        )
        data = resp.json()
        assert data["total"] == 3

        term_ids = {n["term_id"] for n in data["nodes"]}
        assert term_ids == {b, c, d}

        # D should appear only once
        d_nodes = [n for n in data["nodes"] if n["term_id"] == d]
        assert len(d_nodes) == 1
        assert d_nodes[0]["depth"] == 2

    @pytest.mark.asyncio
    async def test_cycle_detection(self, client, auth_headers):
        """A is_a B, B is_a A → terminates without infinite loop."""
        tid = await create_terminology(client, auth_headers)
        a = await create_term(client, auth_headers, tid, "A")
        b = await create_term(client, auth_headers, tid, "B")
        await create_relationship(client, auth_headers, a, b, "is_a")
        await create_relationship(client, auth_headers, b, a, "is_a")

        resp = await client.get(
            f"{API}/ontology/terms/{a}/ancestors",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        # Should find B but not loop back to A
        assert data["total"] == 1
        assert data["nodes"][0]["term_id"] == b

    @pytest.mark.asyncio
    async def test_max_depth(self, client, auth_headers):
        """Chain of 5 terms, max_depth=2 → only 2 ancestors."""
        tid = await create_terminology(client, auth_headers)
        terms = []
        for i in range(5):
            t = await create_term(client, auth_headers, tid, f"Level{i}")
            terms.append(t)
        # 0 is_a 1 is_a 2 is_a 3 is_a 4
        for i in range(4):
            await create_relationship(client, auth_headers, terms[i], terms[i + 1], "is_a")

        resp = await client.get(
            f"{API}/ontology/terms/{terms[0]}/ancestors",
            params={"max_depth": 2},
            headers=auth_headers,
        )
        data = resp.json()
        assert data["total"] == 2
        assert data["max_depth_reached"] is True

    @pytest.mark.asyncio
    async def test_ancestors_with_parent_term_id(self, client, auth_headers):
        """Term with parent_term_id (no explicit relationship) is found via traversal."""
        tid = await create_terminology(client, auth_headers)
        parent = await create_term(client, auth_headers, tid, "Parent")
        child = await create_term(client, auth_headers, tid, "Child", parent_term_id=parent)

        resp = await client.get(
            f"{API}/ontology/terms/{child}/ancestors",
            headers=auth_headers,
        )
        data = resp.json()
        assert data["total"] == 1
        assert data["nodes"][0]["term_id"] == parent

    @pytest.mark.asyncio
    async def test_ancestors_include_term_values(self, client, auth_headers):
        """Traversal result nodes include denormalized term values."""
        tid = await create_terminology(client, auth_headers)
        parent = await create_term(client, auth_headers, tid, "Lung Disease")
        child = await create_term(client, auth_headers, tid, "Pneumonia")
        await create_relationship(client, auth_headers, child, parent, "is_a")

        resp = await client.get(
            f"{API}/ontology/terms/{child}/ancestors",
            headers=auth_headers,
        )
        data = resp.json()
        assert data["nodes"][0]["value"] == "Lung Disease"


class TestDescendants:
    """Tests for GET /ontology/terms/{term_id}/descendants."""

    @pytest.mark.asyncio
    async def test_descendants(self, client, auth_headers):
        """C is_a B is_a A → descendants of A = [B(1), C(2)]."""
        tid = await create_terminology(client, auth_headers)
        a = await create_term(client, auth_headers, tid, "A")
        b = await create_term(client, auth_headers, tid, "B")
        c = await create_term(client, auth_headers, tid, "C")
        await create_relationship(client, auth_headers, b, a, "is_a")
        await create_relationship(client, auth_headers, c, b, "is_a")

        resp = await client.get(
            f"{API}/ontology/terms/{a}/descendants",
            headers=auth_headers,
        )
        data = resp.json()
        assert data["total"] == 2
        term_ids = {n["term_id"] for n in data["nodes"]}
        assert term_ids == {b, c}

    @pytest.mark.asyncio
    async def test_descendants_with_parent_term_id(self, client, auth_headers):
        """Children via parent_term_id appear in descendants."""
        tid = await create_terminology(client, auth_headers)
        parent = await create_term(client, auth_headers, tid, "Parent")
        child = await create_term(client, auth_headers, tid, "Child", parent_term_id=parent)

        resp = await client.get(
            f"{API}/ontology/terms/{parent}/descendants",
            headers=auth_headers,
        )
        data = resp.json()
        assert data["total"] == 1
        assert data["nodes"][0]["term_id"] == child


class TestParentsChildren:
    """Tests for GET /ontology/terms/{term_id}/parents and /children."""

    @pytest.mark.asyncio
    async def test_parents_direct_only(self, client, auth_headers):
        """Parents returns only direct parents, not grandparents."""
        tid = await create_terminology(client, auth_headers)
        gp = await create_term(client, auth_headers, tid, "Grandparent")
        p = await create_term(client, auth_headers, tid, "Parent")
        c = await create_term(client, auth_headers, tid, "Child")
        await create_relationship(client, auth_headers, c, p, "is_a")
        await create_relationship(client, auth_headers, p, gp, "is_a")

        resp = await client.get(
            f"{API}/ontology/terms/{c}/parents",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["target_term_id"] == p

    @pytest.mark.asyncio
    async def test_children_direct_only(self, client, auth_headers):
        """Children returns only direct children, not grandchildren."""
        tid = await create_terminology(client, auth_headers)
        gp = await create_term(client, auth_headers, tid, "Grandparent")
        p = await create_term(client, auth_headers, tid, "Parent")
        c = await create_term(client, auth_headers, tid, "Child")
        await create_relationship(client, auth_headers, c, p, "is_a")
        await create_relationship(client, auth_headers, p, gp, "is_a")

        resp = await client.get(
            f"{API}/ontology/terms/{gp}/children",
            headers=auth_headers,
        )
        data = resp.json()
        assert len(data) == 1
        assert data[0]["source_term_id"] == p

    @pytest.mark.asyncio
    async def test_parents_combines_relationship_and_parent_term_id(self, client, auth_headers):
        """Parents from both TermRelationship and parent_term_id, deduplicated."""
        tid = await create_terminology(client, auth_headers)
        p1 = await create_term(client, auth_headers, tid, "Parent1")
        p2 = await create_term(client, auth_headers, tid, "Parent2")
        # Child has parent_term_id=p1 and also a relationship to p2
        child = await create_term(client, auth_headers, tid, "Child", parent_term_id=p1)
        await create_relationship(client, auth_headers, child, p2, "is_a")

        resp = await client.get(
            f"{API}/ontology/terms/{child}/parents",
            headers=auth_headers,
        )
        data = resp.json()
        assert len(data) == 2
        parent_ids = {r["target_term_id"] for r in data}
        assert parent_ids == {p1, p2}


class TestNonIsATraversal:
    """Tests for traversal with relationship types other than is_a."""

    @pytest.mark.asyncio
    async def test_part_of_traversal(self, client, auth_headers):
        """Traversal works with part_of relationships."""
        tid = await create_terminology(client, auth_headers)
        body = await create_term(client, auth_headers, tid, "Body")
        torso = await create_term(client, auth_headers, tid, "Torso")
        heart = await create_term(client, auth_headers, tid, "Heart")
        await create_relationship(client, auth_headers, torso, body, "part_of")
        await create_relationship(client, auth_headers, heart, torso, "part_of")

        resp = await client.get(
            f"{API}/ontology/terms/{heart}/ancestors",
            params={"relationship_type": "part_of"},
            headers=auth_headers,
        )
        data = resp.json()
        assert data["total"] == 2
        assert data["relationship_type"] == "part_of"

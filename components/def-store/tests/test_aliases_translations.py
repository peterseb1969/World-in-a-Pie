"""Tests for term aliases, translations, sort_order, and metadata."""

import pytest
import pytest_asyncio
from httpx import AsyncClient


@pytest_asyncio.fixture
async def test_terminology(client: AsyncClient, auth_headers: dict):
    """Create a test terminology for alias/translation tests."""
    response = await client.post(
        "/api/def-store/terminologies",
        headers=auth_headers,
        json=[{
            "value": "TITLE",
            "label": "Title",
            "case_sensitive": False
        }]
    )
    data = response.json()
    terminology_id = data["results"][0]["id"]

    # Fetch the full terminology so callers get the complete object
    get_response = await client.get(
        f"/api/def-store/terminologies/{terminology_id}",
        headers=auth_headers
    )
    return get_response.json()


# =============================================================================
# ALIASES
# =============================================================================

@pytest.mark.asyncio
async def test_create_term_with_aliases(client: AsyncClient, auth_headers: dict, test_terminology):
    """Test creating a term with aliases — aliases stored and returned in response."""
    terminology_id = test_terminology["terminology_id"]

    response = await client.post(
        f"/api/def-store/terminologies/{terminology_id}/terms",
        headers=auth_headers,
        json=[{
            "value": "mr",
            "label": "Mr",
            "aliases": ["MR.", "Mr.", "Mister"]
        }]
    )

    assert response.status_code == 200
    data = response.json()
    assert data["succeeded"] == 1
    assert data["failed"] == 0
    term_id = data["results"][0]["id"]

    # Verify aliases are returned via GET
    get_response = await client.get(
        f"/api/def-store/terms/{term_id}",
        headers=auth_headers
    )
    assert get_response.status_code == 200
    detail = get_response.json()
    assert detail["aliases"] == ["MR.", "Mr.", "Mister"]


@pytest.mark.asyncio
async def test_get_term_includes_aliases(client: AsyncClient, auth_headers: dict, test_terminology):
    """Test that GET term response includes aliases field."""
    terminology_id = test_terminology["terminology_id"]

    create_response = await client.post(
        f"/api/def-store/terminologies/{terminology_id}/terms",
        headers=auth_headers,
        json=[{
            "value": "mrs",
            "label": "Mrs",
            "aliases": ["MRS.", "Mrs.", "Missus"]
        }]
    )
    term_id = create_response.json()["results"][0]["id"]

    get_response = await client.get(
        f"/api/def-store/terms/{term_id}",
        headers=auth_headers
    )
    assert get_response.status_code == 200
    detail = get_response.json()
    assert "aliases" in detail
    assert detail["aliases"] == ["MRS.", "Mrs.", "Missus"]


@pytest.mark.asyncio
async def test_update_term_aliases(client: AsyncClient, auth_headers: dict, test_terminology):
    """Test updating term aliases — aliases replaced with new list."""
    terminology_id = test_terminology["terminology_id"]

    create_response = await client.post(
        f"/api/def-store/terminologies/{terminology_id}/terms",
        headers=auth_headers,
        json=[{
            "value": "dr",
            "label": "Dr",
            "aliases": ["DR.", "Dr."]
        }]
    )
    term_id = create_response.json()["results"][0]["id"]

    # Update aliases
    response = await client.put(
        "/api/def-store/terms",
        headers=auth_headers,
        json=[{
            "term_id": term_id,
            "aliases": ["DR.", "Dr.", "Doctor", "Doc"]
        }]
    )

    assert response.status_code == 200
    data = response.json()
    assert data["succeeded"] == 1
    assert data["results"][0]["status"] == "updated"

    # Verify the update via GET
    get_response = await client.get(
        f"/api/def-store/terms/{term_id}",
        headers=auth_headers
    )
    assert get_response.json()["aliases"] == ["DR.", "Dr.", "Doctor", "Doc"]


@pytest.mark.asyncio
async def test_validate_by_alias(client: AsyncClient, auth_headers: dict, test_terminology):
    """Test that validation matches by alias value."""
    terminology_id = test_terminology["terminology_id"]

    await client.post(
        f"/api/def-store/terminologies/{terminology_id}/terms",
        headers=auth_headers,
        json=[{
            "value": "mr",
            "label": "Mr",
            "aliases": ["MR.", "Mr.", "Mister"]
        }]
    )

    # Validate using an alias value
    response = await client.post(
        "/api/def-store/validate",
        headers=auth_headers,
        json={
            "terminology_id": terminology_id,
            "value": "Mister"
        }
    )

    assert response.status_code == 200
    data = response.json()
    assert data["valid"] is True
    assert data["matched_term"]["value"] == "mr"
    assert data["matched_via"] == "alias"


@pytest.mark.asyncio
async def test_validate_by_alias_case_insensitive(client: AsyncClient, auth_headers: dict, test_terminology):
    """Test case-insensitive alias matching during validation."""
    terminology_id = test_terminology["terminology_id"]

    await client.post(
        f"/api/def-store/terminologies/{terminology_id}/terms",
        headers=auth_headers,
        json=[{
            "value": "ms",
            "label": "Ms",
            "aliases": ["MS.", "Ms."]
        }]
    )

    # Validate using lowercase alias (terminology is case_sensitive=False)
    response = await client.post(
        "/api/def-store/validate",
        headers=auth_headers,
        json={
            "terminology_id": terminology_id,
            "value": "ms."
        }
    )

    assert response.status_code == 200
    data = response.json()
    assert data["valid"] is True
    assert data["matched_term"]["value"] == "ms"
    assert data["matched_via"] == "alias"


@pytest.mark.asyncio
async def test_create_term_with_empty_aliases(client: AsyncClient, auth_headers: dict, test_terminology):
    """Test creating a term with an empty aliases list works fine."""
    terminology_id = test_terminology["terminology_id"]

    response = await client.post(
        f"/api/def-store/terminologies/{terminology_id}/terms",
        headers=auth_headers,
        json=[{
            "value": "prof",
            "label": "Professor",
            "aliases": []
        }]
    )

    assert response.status_code == 200
    data = response.json()
    assert data["succeeded"] == 1
    term_id = data["results"][0]["id"]

    get_response = await client.get(
        f"/api/def-store/terms/{term_id}",
        headers=auth_headers
    )
    assert get_response.json()["aliases"] == []


@pytest.mark.asyncio
async def test_create_term_with_duplicate_aliases(client: AsyncClient, auth_headers: dict, test_terminology):
    """Test creating a term with duplicate alias values — stored as-is."""
    terminology_id = test_terminology["terminology_id"]

    response = await client.post(
        f"/api/def-store/terminologies/{terminology_id}/terms",
        headers=auth_headers,
        json=[{
            "value": "sir",
            "label": "Sir",
            "aliases": ["Sir.", "Sir.", "SIR"]
        }]
    )

    assert response.status_code == 200
    data = response.json()
    assert data["succeeded"] == 1
    term_id = data["results"][0]["id"]

    get_response = await client.get(
        f"/api/def-store/terms/{term_id}",
        headers=auth_headers
    )
    # Duplicates are stored as provided
    assert get_response.json()["aliases"] == ["Sir.", "Sir.", "SIR"]


@pytest.mark.asyncio
async def test_create_term_without_aliases_defaults_empty(client: AsyncClient, auth_headers: dict, test_terminology):
    """Test that omitting aliases defaults to an empty list."""
    terminology_id = test_terminology["terminology_id"]

    response = await client.post(
        f"/api/def-store/terminologies/{terminology_id}/terms",
        headers=auth_headers,
        json=[{
            "value": "rev",
            "label": "Reverend"
        }]
    )

    assert response.status_code == 200
    term_id = response.json()["results"][0]["id"]

    get_response = await client.get(
        f"/api/def-store/terms/{term_id}",
        headers=auth_headers
    )
    assert get_response.json()["aliases"] == []


# =============================================================================
# TRANSLATIONS
# =============================================================================

@pytest.mark.asyncio
async def test_create_term_with_translations(client: AsyncClient, auth_headers: dict, test_terminology):
    """Test creating a term with translations — stored and returned."""
    terminology_id = test_terminology["terminology_id"]

    response = await client.post(
        f"/api/def-store/terminologies/{terminology_id}/terms",
        headers=auth_headers,
        json=[{
            "value": "mr",
            "label": "Mr",
            "translations": [
                {"language": "fr", "label": "M.", "description": "Monsieur"},
                {"language": "de", "label": "Herr", "description": None}
            ]
        }]
    )

    assert response.status_code == 200
    data = response.json()
    assert data["succeeded"] == 1
    term_id = data["results"][0]["id"]

    get_response = await client.get(
        f"/api/def-store/terms/{term_id}",
        headers=auth_headers
    )
    detail = get_response.json()
    assert len(detail["translations"]) == 2
    fr_translation = next(t for t in detail["translations"] if t["language"] == "fr")
    assert fr_translation["label"] == "M."
    assert fr_translation["description"] == "Monsieur"
    de_translation = next(t for t in detail["translations"] if t["language"] == "de")
    assert de_translation["label"] == "Herr"


@pytest.mark.asyncio
async def test_get_term_includes_translations(client: AsyncClient, auth_headers: dict, test_terminology):
    """Test that GET term response includes translations field."""
    terminology_id = test_terminology["terminology_id"]

    create_response = await client.post(
        f"/api/def-store/terminologies/{terminology_id}/terms",
        headers=auth_headers,
        json=[{
            "value": "mrs",
            "label": "Mrs",
            "translations": [
                {"language": "es", "label": "Sra."}
            ]
        }]
    )
    term_id = create_response.json()["results"][0]["id"]

    get_response = await client.get(
        f"/api/def-store/terms/{term_id}",
        headers=auth_headers
    )
    assert get_response.status_code == 200
    detail = get_response.json()
    assert "translations" in detail
    assert len(detail["translations"]) == 1
    assert detail["translations"][0]["language"] == "es"
    assert detail["translations"][0]["label"] == "Sra."


@pytest.mark.asyncio
async def test_update_translations(client: AsyncClient, auth_headers: dict, test_terminology):
    """Test updating term translations — replaces existing list."""
    terminology_id = test_terminology["terminology_id"]

    create_response = await client.post(
        f"/api/def-store/terminologies/{terminology_id}/terms",
        headers=auth_headers,
        json=[{
            "value": "dr",
            "label": "Dr",
            "translations": [
                {"language": "fr", "label": "Dr"}
            ]
        }]
    )
    term_id = create_response.json()["results"][0]["id"]

    # Update translations — replaces the entire list
    response = await client.put(
        "/api/def-store/terms",
        headers=auth_headers,
        json=[{
            "term_id": term_id,
            "translations": [
                {"language": "fr", "label": "Dr", "description": "Docteur"},
                {"language": "ja", "label": "\u535a\u58eb"}
            ]
        }]
    )

    assert response.status_code == 200
    data = response.json()
    assert data["succeeded"] == 1

    get_response = await client.get(
        f"/api/def-store/terms/{term_id}",
        headers=auth_headers
    )
    detail = get_response.json()
    assert len(detail["translations"]) == 2
    fr = next(t for t in detail["translations"] if t["language"] == "fr")
    assert fr["description"] == "Docteur"
    ja = next(t for t in detail["translations"] if t["language"] == "ja")
    assert ja["label"] == "\u535a\u58eb"


@pytest.mark.asyncio
async def test_multiple_translations_different_languages(client: AsyncClient, auth_headers: dict, test_terminology):
    """Test creating a term with translations in many languages."""
    terminology_id = test_terminology["terminology_id"]

    translations = [
        {"language": "fr", "label": "M."},
        {"language": "de", "label": "Herr"},
        {"language": "es", "label": "Sr."},
        {"language": "ja", "label": "\u6C0F"},
        {"language": "zh", "label": "\u5148\u751F"},
    ]

    response = await client.post(
        f"/api/def-store/terminologies/{terminology_id}/terms",
        headers=auth_headers,
        json=[{
            "value": "mr",
            "label": "Mr",
            "translations": translations
        }]
    )

    assert response.status_code == 200
    term_id = response.json()["results"][0]["id"]

    get_response = await client.get(
        f"/api/def-store/terms/{term_id}",
        headers=auth_headers
    )
    detail = get_response.json()
    assert len(detail["translations"]) == 5
    languages = {t["language"] for t in detail["translations"]}
    assert languages == {"fr", "de", "es", "ja", "zh"}


@pytest.mark.asyncio
async def test_create_term_without_translations_defaults_empty(client: AsyncClient, auth_headers: dict, test_terminology):
    """Test that omitting translations defaults to an empty list."""
    terminology_id = test_terminology["terminology_id"]

    response = await client.post(
        f"/api/def-store/terminologies/{terminology_id}/terms",
        headers=auth_headers,
        json=[{
            "value": "miss",
            "label": "Miss"
        }]
    )

    assert response.status_code == 200
    term_id = response.json()["results"][0]["id"]

    get_response = await client.get(
        f"/api/def-store/terms/{term_id}",
        headers=auth_headers
    )
    assert get_response.json()["translations"] == []


# =============================================================================
# SORT ORDER
# =============================================================================

@pytest.mark.asyncio
async def test_create_terms_with_sort_order(client: AsyncClient, auth_headers: dict, test_terminology):
    """Test creating terms with sort_order — returned in specified order."""
    terminology_id = test_terminology["terminology_id"]

    terms = [
        {"value": "mr", "label": "Mr", "sort_order": 3},
        {"value": "mrs", "label": "Mrs", "sort_order": 1},
        {"value": "ms", "label": "Ms", "sort_order": 2},
    ]

    for term in terms:
        await client.post(
            f"/api/def-store/terminologies/{terminology_id}/terms",
            headers=auth_headers,
            json=[term]
        )

    # List terms — verify sort_order is stored
    response = await client.get(
        f"/api/def-store/terminologies/{terminology_id}/terms",
        headers=auth_headers
    )
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 3

    # Verify each term has correct sort_order
    sort_orders = {item["value"]: item["sort_order"] for item in items}
    assert sort_orders["mr"] == 3
    assert sort_orders["mrs"] == 1
    assert sort_orders["ms"] == 2


@pytest.mark.asyncio
async def test_update_sort_order(client: AsyncClient, auth_headers: dict, test_terminology):
    """Test updating sort_order on an existing term."""
    terminology_id = test_terminology["terminology_id"]

    create_response = await client.post(
        f"/api/def-store/terminologies/{terminology_id}/terms",
        headers=auth_headers,
        json=[{
            "value": "mr",
            "label": "Mr",
            "sort_order": 1
        }]
    )
    term_id = create_response.json()["results"][0]["id"]

    # Update sort_order
    response = await client.put(
        "/api/def-store/terms",
        headers=auth_headers,
        json=[{
            "term_id": term_id,
            "sort_order": 10
        }]
    )

    assert response.status_code == 200
    assert response.json()["succeeded"] == 1

    get_response = await client.get(
        f"/api/def-store/terms/{term_id}",
        headers=auth_headers
    )
    assert get_response.json()["sort_order"] == 10


@pytest.mark.asyncio
async def test_default_sort_order_is_zero(client: AsyncClient, auth_headers: dict, test_terminology):
    """Test that sort_order defaults to 0 when not specified."""
    terminology_id = test_terminology["terminology_id"]

    response = await client.post(
        f"/api/def-store/terminologies/{terminology_id}/terms",
        headers=auth_headers,
        json=[{
            "value": "mr",
            "label": "Mr"
        }]
    )

    assert response.status_code == 200
    term_id = response.json()["results"][0]["id"]

    get_response = await client.get(
        f"/api/def-store/terms/{term_id}",
        headers=auth_headers
    )
    assert get_response.json()["sort_order"] == 0


# =============================================================================
# METADATA
# =============================================================================

@pytest.mark.asyncio
async def test_create_term_with_metadata(client: AsyncClient, auth_headers: dict, test_terminology):
    """Test creating a term with metadata dict — stored and returned."""
    terminology_id = test_terminology["terminology_id"]

    response = await client.post(
        f"/api/def-store/terminologies/{terminology_id}/terms",
        headers=auth_headers,
        json=[{
            "value": "mr",
            "label": "Mr",
            "metadata": {
                "color": "#FF0000",
                "icon": "user-male",
                "weight": 1.5
            }
        }]
    )

    assert response.status_code == 200
    term_id = response.json()["results"][0]["id"]

    get_response = await client.get(
        f"/api/def-store/terms/{term_id}",
        headers=auth_headers
    )
    detail = get_response.json()
    assert detail["metadata"]["color"] == "#FF0000"
    assert detail["metadata"]["icon"] == "user-male"
    assert detail["metadata"]["weight"] == 1.5


@pytest.mark.asyncio
async def test_update_metadata_merges(client: AsyncClient, auth_headers: dict, test_terminology):
    """Test updating metadata — merges with existing dict."""
    terminology_id = test_terminology["terminology_id"]

    create_response = await client.post(
        f"/api/def-store/terminologies/{terminology_id}/terms",
        headers=auth_headers,
        json=[{
            "value": "mrs",
            "label": "Mrs",
            "metadata": {
                "color": "#00FF00",
                "icon": "user-female"
            }
        }]
    )
    term_id = create_response.json()["results"][0]["id"]

    # Update metadata — should merge, not replace
    response = await client.put(
        "/api/def-store/terms",
        headers=auth_headers,
        json=[{
            "term_id": term_id,
            "metadata": {
                "color": "#0000FF",
                "priority": "high"
            }
        }]
    )

    assert response.status_code == 200
    assert response.json()["succeeded"] == 1

    get_response = await client.get(
        f"/api/def-store/terms/{term_id}",
        headers=auth_headers
    )
    detail = get_response.json()
    # color should be overwritten, icon should remain, priority should be added
    assert detail["metadata"]["color"] == "#0000FF"
    assert detail["metadata"]["icon"] == "user-female"
    assert detail["metadata"]["priority"] == "high"


@pytest.mark.asyncio
async def test_create_term_without_metadata_defaults_empty(client: AsyncClient, auth_headers: dict, test_terminology):
    """Test that omitting metadata defaults to an empty dict."""
    terminology_id = test_terminology["terminology_id"]

    response = await client.post(
        f"/api/def-store/terminologies/{terminology_id}/terms",
        headers=auth_headers,
        json=[{
            "value": "ms",
            "label": "Ms"
        }]
    )

    assert response.status_code == 200
    term_id = response.json()["results"][0]["id"]

    get_response = await client.get(
        f"/api/def-store/terms/{term_id}",
        headers=auth_headers
    )
    assert get_response.json()["metadata"] == {}


# =============================================================================
# COMBINED: ALIASES + TRANSLATIONS + METADATA + SORT ORDER
# =============================================================================

@pytest.mark.asyncio
async def test_create_term_with_all_fields(client: AsyncClient, auth_headers: dict, test_terminology):
    """Test creating a term with aliases, translations, metadata, and sort_order together."""
    terminology_id = test_terminology["terminology_id"]

    response = await client.post(
        f"/api/def-store/terminologies/{terminology_id}/terms",
        headers=auth_headers,
        json=[{
            "value": "mr",
            "label": "Mr",
            "description": "Mister honorific",
            "aliases": ["MR.", "Mister"],
            "translations": [
                {"language": "fr", "label": "M.", "description": "Monsieur"},
                {"language": "de", "label": "Herr"}
            ],
            "metadata": {"color": "#333", "formal": True},
            "sort_order": 5
        }]
    )

    assert response.status_code == 200
    data = response.json()
    assert data["succeeded"] == 1
    term_id = data["results"][0]["id"]

    get_response = await client.get(
        f"/api/def-store/terms/{term_id}",
        headers=auth_headers
    )
    detail = get_response.json()
    assert detail["value"] == "mr"
    assert detail["label"] == "Mr"
    assert detail["description"] == "Mister honorific"
    assert detail["aliases"] == ["MR.", "Mister"]
    assert len(detail["translations"]) == 2
    assert detail["metadata"]["color"] == "#333"
    assert detail["metadata"]["formal"] is True
    assert detail["sort_order"] == 5


@pytest.mark.asyncio
async def test_list_terms_includes_aliases_and_translations(client: AsyncClient, auth_headers: dict, test_terminology):
    """Test that list terms endpoint includes aliases and translations in each item."""
    terminology_id = test_terminology["terminology_id"]

    await client.post(
        f"/api/def-store/terminologies/{terminology_id}/terms",
        headers=auth_headers,
        json=[{
            "value": "mr",
            "label": "Mr",
            "aliases": ["Mister"],
            "translations": [{"language": "fr", "label": "M."}],
            "metadata": {"icon": "male"},
            "sort_order": 1
        }]
    )

    response = await client.get(
        f"/api/def-store/terminologies/{terminology_id}/terms",
        headers=auth_headers
    )

    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 1
    item = items[0]
    assert item["aliases"] == ["Mister"]
    assert len(item["translations"]) == 1
    assert item["translations"][0]["language"] == "fr"
    assert item["metadata"]["icon"] == "male"
    assert item["sort_order"] == 1

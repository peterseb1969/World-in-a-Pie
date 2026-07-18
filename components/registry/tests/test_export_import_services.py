"""Unit tests for ExportService + ImportService (CASE-342).

Both services had 0% coverage when CASE-334's audit ran. They drive
wip-toolkit's cross-instance migration and DR workflows — the most
incident-critical paths in the registry.

The services are HTTP-shaped: they fetch from / push to def-store,
template-store, document-store via httpx, plus operate on Namespace +
RegistryEntry via Beanie. These tests use the standard `client`
fixture pattern (real Mongo via test-mongo container per CASE-320)
for the Beanie surface, and mock httpx for the cross-service calls.
"""

from __future__ import annotations

import json
import os
import zipfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from registry.models.entry import RegistryEntry
from registry.models.namespace import Namespace
from registry.services.export_service import ExportService
from registry.services.import_service import ImportService

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_paginated_httpx_response(items: list[dict], status_code: int = 200):
    """Build a mock httpx Response whose .json() returns a paginated payload."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json = MagicMock(return_value={"items": items, "page": 1, "pages": 1, "total": len(items)})
    return resp


def _make_post_response(status_code: int = 201, body: dict | None = None, text: str = ""):
    """Build a mock httpx Response for a POST."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json = MagicMock(return_value=body or {})
    resp.text = text or json.dumps(body or {})
    return resp


def _patch_async_client(*, responses_by_method: dict[str, list[MagicMock]] | None = None,
                       single_response: MagicMock | None = None):
    """Yield a patch for httpx.AsyncClient that returns scripted responses.

    responses_by_method: {"get": [resp1, resp2, ...], "post": [...]} — pop in order.
    single_response: shorthand when only one response needed for any method.
    """
    responses_by_method = responses_by_method or {}
    counters = {"get": 0, "post": 0}

    async def fake_get(url, params=None, headers=None, **kw):
        responses = responses_by_method.get("get", [])
        if not responses:
            return single_response or _make_paginated_httpx_response([])
        idx = min(counters["get"], len(responses) - 1)
        counters["get"] += 1
        return responses[idx]

    async def fake_post(url, json=None, headers=None, **kw):
        responses = responses_by_method.get("post", [])
        if not responses:
            return single_response or _make_post_response(status_code=201)
        idx = min(counters["post"], len(responses) - 1)
        counters["post"] += 1
        return responses[idx]

    mock_client = MagicMock()
    mock_client.get = AsyncMock(side_effect=fake_get)
    mock_client.post = AsyncMock(side_effect=fake_post)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    return mock_client


# ---------------------------------------------------------------------------
# ExportService
# ---------------------------------------------------------------------------


class TestExportServiceFetchAllPaginated:
    """_fetch_all_paginated reads multiple pages until items < page_size."""

    @pytest.mark.asyncio
    async def test_single_page_returns_items(self, client):
        svc = ExportService(
            def_store_url="http://def",
            template_store_url="http://tpl",
            document_store_url="http://doc",
            api_key="k",
        )
        mock_client = _patch_async_client(
            responses_by_method={"get": [_make_paginated_httpx_response(
                [{"terminology_id": "T1"}, {"terminology_id": "T2"}]
            )]},
        )
        with patch("httpx.AsyncClient", return_value=mock_client):
            items = await svc._fetch_all_paginated("http://def/foo", {"namespace": "kb"})
        assert len(items) == 2
        assert items[0]["terminology_id"] == "T1"

    @pytest.mark.asyncio
    async def test_paginates_until_short_page(self, client):
        svc = ExportService(
            def_store_url="http://def",
            template_store_url="http://tpl",
            document_store_url="http://doc",
            api_key="k",
        )
        # First page: 100 items (full page). Second page: 5 items (short → stop).
        page1 = _make_paginated_httpx_response([{"i": i} for i in range(100)])
        page2 = _make_paginated_httpx_response([{"i": i} for i in range(100, 105)])
        mock_client = _patch_async_client(
            responses_by_method={"get": [page1, page2]},
        )
        with patch("httpx.AsyncClient", return_value=mock_client):
            items = await svc._fetch_all_paginated("http://def/foo", {"namespace": "kb"})
        assert len(items) == 105

    @pytest.mark.asyncio
    async def test_stops_on_non_200(self, client):
        svc = ExportService(
            def_store_url="http://def",
            template_store_url="http://tpl",
            document_store_url="http://doc",
            api_key="k",
        )
        err_resp = MagicMock()
        err_resp.status_code = 500
        mock_client = _patch_async_client(
            responses_by_method={"get": [err_resp]},
        )
        with patch("httpx.AsyncClient", return_value=mock_client):
            items = await svc._fetch_all_paginated("http://def/foo", {"namespace": "kb"})
        assert items == []


class TestExportServiceDownloadFile:
    """_download_file fetches /files/{file_id}/content and writes bytes."""

    @pytest.mark.asyncio
    async def test_success_writes_content(self, client, tmp_path):
        svc = ExportService(
            def_store_url="http://def",
            template_store_url="http://tpl",
            document_store_url="http://doc",
            api_key="k",
        )
        resp = MagicMock()
        resp.status_code = 200
        resp.content = b"binary-file-bytes"
        mock_client = _patch_async_client(single_response=resp)
        dest = tmp_path / "blob.bin"
        with patch("httpx.AsyncClient", return_value=mock_client):
            ok = await svc._download_file("f1", str(dest))
        assert ok is True
        assert dest.read_bytes() == b"binary-file-bytes"

    @pytest.mark.asyncio
    async def test_non_200_returns_false(self, client, tmp_path):
        svc = ExportService(
            def_store_url="http://def",
            template_store_url="http://tpl",
            document_store_url="http://doc",
            api_key="k",
        )
        resp = MagicMock()
        resp.status_code = 404
        mock_client = _patch_async_client(single_response=resp)
        with patch("httpx.AsyncClient", return_value=mock_client):
            ok = await svc._download_file("f1", str(tmp_path / "blob.bin"))
        assert ok is False

    @pytest.mark.asyncio
    async def test_exception_returns_false(self, client, tmp_path):
        svc = ExportService(
            def_store_url="http://def",
            template_store_url="http://tpl",
            document_store_url="http://doc",
            api_key="k",
        )
        mock_client = MagicMock()
        mock_client.get = AsyncMock(side_effect=ConnectionError("boom"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        with patch("httpx.AsyncClient", return_value=mock_client):
            ok = await svc._download_file("f1", str(tmp_path / "blob.bin"))
        assert ok is False


class TestExportNamespaceEmptyNamespace:
    """Export of a namespace with no entities still produces a valid manifest + zip."""

    @pytest.mark.asyncio
    async def test_empty_namespace_zip_contains_manifest(self, client, tmp_path):
        # Create the namespace (no registry entries, no service data)
        ns = Namespace(prefix="empty-ns", description="test", created_by="test")
        await ns.create()

        svc = ExportService(
            def_store_url="http://def",
            template_store_url="http://tpl",
            document_store_url="http://doc",
            api_key="k",
        )
        # Every service endpoint returns empty pages
        empty = _make_paginated_httpx_response([])
        mock_client = _patch_async_client(
            responses_by_method={"get": [empty, empty, empty, empty]},
        )

        with patch("httpx.AsyncClient", return_value=mock_client):
            zip_path, stats = await svc.export_namespace(ns)

        try:
            assert os.path.exists(zip_path)
            assert stats == {
                "terminologies": 0,
                "terms": 0,
                "templates": 0,
                "documents": 0,
                "files": 0,
                "registry_entries": 0,
            }
            with zipfile.ZipFile(zip_path) as zf:
                names = zf.namelist()
                assert "manifest.json" in names
                with zf.open("manifest.json") as m:
                    manifest = json.loads(m.read())
                assert manifest["prefix"] == "empty-ns"
                assert manifest["version"] == "2.0"
                assert "id_config" in manifest
                assert manifest["stats"] == stats
        finally:
            os.unlink(zip_path)


class TestExportNamespaceRegistryEntries:
    """Export captures registry entries for the namespace."""

    @pytest.mark.asyncio
    async def test_writes_registry_entries_jsonl(self, client, tmp_path):
        ns = Namespace(prefix="export-entries-test", description="test", created_by="test")
        await ns.create()

        # Add two registry entries via the model — these get serialized
        await RegistryEntry(
            namespace="export-entries-test",
            entity_type="terminologies",
            entry_id="T-001",
            primary_composite_key={"value": "GENDER"},
            primary_composite_key_hash="hash1",
        ).create()
        await RegistryEntry(
            namespace="export-entries-test",
            entity_type="terms",
            entry_id="TERM-001",
            primary_composite_key={"terminology_id": "T-001", "value": "M"},
            primary_composite_key_hash="hash2",
        ).create()

        svc = ExportService(
            def_store_url="http://def",
            template_store_url="http://tpl",
            document_store_url="http://doc",
            api_key="k",
        )
        empty = _make_paginated_httpx_response([])
        mock_client = _patch_async_client(
            responses_by_method={"get": [empty, empty, empty, empty]},
        )
        with patch("httpx.AsyncClient", return_value=mock_client):
            zip_path, stats = await svc.export_namespace(ns)

        try:
            assert stats["registry_entries"] == 2
            with zipfile.ZipFile(zip_path) as zf:
                assert "registry-entries.jsonl" in zf.namelist()
                with zf.open("registry-entries.jsonl") as f:
                    lines = [line for line in f.read().decode().splitlines() if line]
                assert len(lines) == 2
                first = json.loads(lines[0])
                assert first["namespace"] == "export-entries-test"
                assert first["entry_id"] in ("T-001", "TERM-001")
        finally:
            os.unlink(zip_path)


class TestExportNamespaceWithContent:
    """Export pulls data from each service endpoint and writes to JSONL."""

    @pytest.mark.asyncio
    async def test_terminologies_and_terms_written(self, client, tmp_path):
        ns = Namespace(prefix="export-content-test", description="test", created_by="test")
        await ns.create()

        svc = ExportService(
            def_store_url="http://def",
            template_store_url="http://tpl",
            document_store_url="http://doc",
            api_key="k",
        )
        # Sequence of GET responses, in order of service calls per export_namespace():
        # 1. terminologies list
        # 2. terms for terminology[0] (one per terminology — here, 1 terminology = 1 call)
        # 3. templates list
        # 4. documents list
        # 5. files list
        terminologies_resp = _make_paginated_httpx_response(
            [{"terminology_id": "T-001", "value": "GENDER"}]
        )
        terms_resp = _make_paginated_httpx_response(
            [{"term_id": "TERM-1", "value": "M"}, {"term_id": "TERM-2", "value": "F"}]
        )
        templates_resp = _make_paginated_httpx_response(
            [{"template_id": "TPL-1", "value": "RECORD"}]
        )
        documents_resp = _make_paginated_httpx_response(
            [{"document_id": "DOC-1", "data": {"x": 1}}]
        )
        files_resp = _make_paginated_httpx_response([])

        mock_client = _patch_async_client(
            responses_by_method={"get": [
                terminologies_resp, terms_resp, templates_resp,
                documents_resp, files_resp,
            ]},
        )

        with patch("httpx.AsyncClient", return_value=mock_client):
            zip_path, stats = await svc.export_namespace(ns)

        try:
            assert stats == {
                "terminologies": 1,
                "terms": 2,
                "templates": 1,
                "documents": 1,
                "files": 0,
                "registry_entries": 0,
            }
            with zipfile.ZipFile(zip_path) as zf:
                names = zf.namelist()
                assert "terminologies.jsonl" in names
                assert "terms.jsonl" in names
                assert "templates.jsonl" in names
                assert "documents.jsonl" in names
                # files.jsonl is only created when there are files to write —
                # the export service opens jsonl files in append mode inside
                # the per-entity loop. Empty entity-type → file absent.
                assert "files.jsonl" not in names
                with zf.open("terms.jsonl") as f:
                    term_lines = [line for line in f.read().decode().splitlines() if line]
                assert len(term_lines) == 2
        finally:
            os.unlink(zip_path)


# ---------------------------------------------------------------------------
# ImportService
# ---------------------------------------------------------------------------


def _build_archive(tmp_path, manifest: dict, jsonl_files: dict[str, list[dict]]) -> str:
    """Build a minimal export zip with manifest + jsonl files."""
    zip_path = tmp_path / "test-export.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        for filename, items in jsonl_files.items():
            content = "\n".join(json.dumps(item) for item in items)
            zf.writestr(filename, content)
    return str(zip_path)


class TestImportServiceRemapNamespaces:
    """_remap_namespaces is a pure function — recursively replaces namespace fields."""

    def test_top_level_namespace_remapped(self):
        svc = ImportService(
            def_store_url="http://def",
            template_store_url="http://tpl",
            document_store_url="http://doc",
            api_key="k",
        )
        item = {"namespace": "src", "value": "X"}
        out = svc._remap_namespaces(item, {"src": "dst"})
        assert out == {"namespace": "dst", "value": "X"}

    def test_nested_dict_namespace_remapped(self):
        svc = ImportService(
            def_store_url="http://def",
            template_store_url="http://tpl",
            document_store_url="http://doc",
            api_key="k",
        )
        item = {"meta": {"namespace": "src", "other": 1}, "namespace": "src"}
        out = svc._remap_namespaces(item, {"src": "dst"})
        assert out == {"meta": {"namespace": "dst", "other": 1}, "namespace": "dst"}

    def test_list_of_dicts_remapped(self):
        svc = ImportService(
            def_store_url="http://def",
            template_store_url="http://tpl",
            document_store_url="http://doc",
            api_key="k",
        )
        item = {
            "refs": [
                {"namespace": "src", "id": "1"},
                {"namespace": "src", "id": "2"},
            ],
        }
        out = svc._remap_namespaces(item, {"src": "dst"})
        assert out["refs"][0]["namespace"] == "dst"
        assert out["refs"][1]["namespace"] == "dst"

    def test_namespace_not_in_map_unchanged(self):
        svc = ImportService(
            def_store_url="http://def",
            template_store_url="http://tpl",
            document_store_url="http://doc",
            api_key="k",
        )
        item = {"namespace": "other", "value": "X"}
        out = svc._remap_namespaces(item, {"src": "dst"})
        assert out == {"namespace": "other", "value": "X"}

    def test_non_dict_list_values_passed_through(self):
        svc = ImportService(
            def_store_url="http://def",
            template_store_url="http://tpl",
            document_store_url="http://doc",
            api_key="k",
        )
        # Lists with non-dict entries (e.g., strings) are kept as-is per item.
        item = {"tags": ["a", "b", "c"]}
        out = svc._remap_namespaces(item, {"src": "dst"})
        assert out == {"tags": ["a", "b", "c"]}


class TestImportNamespaceManifestValidation:
    """Import requires a valid manifest.json inside the archive."""

    @pytest.mark.asyncio
    async def test_missing_manifest_raises(self, client, tmp_path):
        zip_path = tmp_path / "broken.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("manifest-NOPE.json", "{}")

        svc = ImportService(
            def_store_url="http://def",
            template_store_url="http://tpl",
            document_store_url="http://doc",
            api_key="k",
        )
        with pytest.raises(ValueError, match=r"missing manifest\.json"):
            await svc.import_namespace(str(zip_path))


class TestImportNamespaceCreateMode:
    """mode='create' fails when target prefix already exists."""

    @pytest.mark.asyncio
    async def test_create_into_fresh_prefix_succeeds(self, client, tmp_path):
        archive = _build_archive(
            tmp_path,
            manifest={
                "version": "2.0",
                "prefix": "src-ns",
                "description": "test ns",
                "isolation_mode": "open",
                "allowed_external_refs": [],
                "id_config": {},
            },
            jsonl_files={},
        )
        svc = ImportService(
            def_store_url="http://def",
            template_store_url="http://tpl",
            document_store_url="http://doc",
            api_key="k",
        )
        namespace, _stats = await svc.import_namespace(
            archive, target_prefix="dst-ns", mode="create", imported_by="tester",
        )
        assert namespace.prefix == "dst-ns"
        assert namespace.description == "test ns"
        # Verify it landed in the DB
        in_db = await Namespace.find_one({"prefix": "dst-ns"})
        assert in_db is not None

    @pytest.mark.asyncio
    async def test_create_into_existing_prefix_raises(self, client, tmp_path):
        # Pre-create the target namespace
        existing = Namespace(prefix="taken", description="already here", created_by="test")
        await existing.create()

        archive = _build_archive(
            tmp_path,
            manifest={
                "version": "2.0",
                "prefix": "src-ns",
                "description": "imported",
                "isolation_mode": "open",
                "allowed_external_refs": [],
                "id_config": {},
            },
            jsonl_files={},
        )
        svc = ImportService(
            def_store_url="http://def",
            template_store_url="http://tpl",
            document_store_url="http://doc",
            api_key="k",
        )
        with pytest.raises(ValueError, match="already exists"):
            await svc.import_namespace(archive, target_prefix="taken", mode="create")


class TestImportNamespaceReplaceMode:
    """mode='replace' updates the existing record with imported values
    (CASE-344). The pre-fix behavior of archiving + creating a new row
    raised DuplicateKeyError on the unique-prefix index. Post-fix, the
    existing record is mutated in place — its status flips to 'active'
    after the import, fields overwritten with the manifest's values.
    """

    @pytest.mark.asyncio
    async def test_replace_updates_existing_record(self, client, tmp_path):
        existing = Namespace(
            prefix="replace-target",
            description="old desc",
            isolation_mode="open",
            allowed_external_refs=[],
            created_by="test",
        )
        await existing.create()
        original_id = existing.id

        archive = _build_archive(
            tmp_path,
            manifest={
                "version": "2.0",
                "prefix": "replace-src",
                "description": "fresh desc",
                "isolation_mode": "strict",
                "allowed_external_refs": ["wip"],
                "id_config": {},
            },
            jsonl_files={},
        )
        svc = ImportService(
            def_store_url="http://def",
            template_store_url="http://tpl",
            document_store_url="http://doc",
            api_key="k",
        )
        ns, _stats = await svc.import_namespace(
            archive, target_prefix="replace-target", mode="replace",
        )
        # The returned namespace is the same row, mutated in place
        assert ns.id == original_id
        assert ns.prefix == "replace-target"
        assert ns.description == "fresh desc"
        assert ns.isolation_mode == "strict"
        assert ns.allowed_external_refs == ["wip"]
        assert ns.status == "active"

        # Verify in DB
        in_db = await Namespace.find_one({"prefix": "replace-target"})
        assert in_db is not None
        assert in_db.description == "fresh desc"
        assert in_db.isolation_mode == "strict"


class TestImportJsonl:
    """_import_jsonl posts each line to the endpoint, applies ns_map, counts."""

    @pytest.mark.asyncio
    async def test_posts_each_jsonl_line(self, client, tmp_path):
        svc = ImportService(
            def_store_url="http://def",
            template_store_url="http://tpl",
            document_store_url="http://doc",
            api_key="k",
        )
        # Write a tiny jsonl with 3 items
        jsonl_path = tmp_path / "items.jsonl"
        items = [{"value": "A"}, {"value": "B"}, {"value": "C"}]
        jsonl_path.write_text("\n".join(json.dumps(i) for i in items))

        ok_resp = _make_post_response(status_code=201)
        mock_client = _patch_async_client(
            responses_by_method={"post": [ok_resp, ok_resp, ok_resp]},
        )

        with patch("httpx.AsyncClient", return_value=mock_client):
            count = await svc._import_jsonl(
                str(jsonl_path), "http://x/items", {}, mode="create",
            )
        assert count == 3
        assert mock_client.post.await_count == 3

    @pytest.mark.asyncio
    async def test_strips_created_at_and_updated_at(self, client, tmp_path):
        svc = ImportService(
            def_store_url="http://def",
            template_store_url="http://tpl",
            document_store_url="http://doc",
            api_key="k",
        )
        jsonl_path = tmp_path / "items.jsonl"
        jsonl_path.write_text(json.dumps({
            "value": "X",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-02T00:00:00Z",
        }))

        captured_payloads: list[dict] = []
        async def capture_post(url, json=None, headers=None, **kw):
            captured_payloads.append(json)
            return _make_post_response(status_code=201)

        mock_client = MagicMock()
        mock_client.post = AsyncMock(side_effect=capture_post)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("httpx.AsyncClient", return_value=mock_client):
            await svc._import_jsonl(str(jsonl_path), "http://x/items", {}, mode="create")

        assert len(captured_payloads) == 1
        sent = captured_payloads[0]
        assert "created_at" not in sent
        assert "updated_at" not in sent
        assert sent["value"] == "X"

    @pytest.mark.asyncio
    async def test_applies_namespace_remapping(self, client, tmp_path):
        svc = ImportService(
            def_store_url="http://def",
            template_store_url="http://tpl",
            document_store_url="http://doc",
            api_key="k",
        )
        jsonl_path = tmp_path / "items.jsonl"
        jsonl_path.write_text(json.dumps({"namespace": "src", "value": "X"}))

        captured: list[dict] = []
        async def capture_post(url, json=None, headers=None, **kw):
            captured.append(json)
            return _make_post_response(status_code=201)

        mock_client = MagicMock()
        mock_client.post = AsyncMock(side_effect=capture_post)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("httpx.AsyncClient", return_value=mock_client):
            await svc._import_jsonl(
                str(jsonl_path), "http://x/items", {"src": "dst"}, mode="create",
            )

        assert captured[0]["namespace"] == "dst"

    @pytest.mark.asyncio
    async def test_skips_blank_lines(self, client, tmp_path):
        svc = ImportService(
            def_store_url="http://def",
            template_store_url="http://tpl",
            document_store_url="http://doc",
            api_key="k",
        )
        jsonl_path = tmp_path / "items.jsonl"
        jsonl_path.write_text("\n\n" + json.dumps({"value": "A"}) + "\n\n")

        mock_client = _patch_async_client(
            responses_by_method={"post": [_make_post_response(status_code=201)]},
        )

        with patch("httpx.AsyncClient", return_value=mock_client):
            count = await svc._import_jsonl(
                str(jsonl_path), "http://x/items", {}, mode="create",
            )
        assert count == 1
        assert mock_client.post.await_count == 1


class TestImportTerms:
    """_import_terms groups by terminology_id and posts a bulk per terminology."""

    @pytest.mark.asyncio
    async def test_groups_by_terminology_and_posts_bulk(self, client, tmp_path):
        svc = ImportService(
            def_store_url="http://def",
            template_store_url="http://tpl",
            document_store_url="http://doc",
            api_key="k",
        )
        jsonl_path = tmp_path / "terms.jsonl"
        terms = [
            {"terminology_id": "T1", "value": "a", "label": "A"},
            {"terminology_id": "T1", "value": "b", "label": "B"},
            {"terminology_id": "T2", "value": "x", "label": "X"},
        ]
        jsonl_path.write_text("\n".join(json.dumps(t) for t in terms))

        captured_calls: list[tuple[str, list]] = []
        async def capture_post(url, json=None, headers=None, **kw):
            captured_calls.append((url, json))
            return _make_post_response(
                status_code=200,
                body={"succeeded": len(json), "failed": 0},
            )

        mock_client = MagicMock()
        mock_client.post = AsyncMock(side_effect=capture_post)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("httpx.AsyncClient", return_value=mock_client):
            count = await svc._import_terms(str(jsonl_path), {}, mode="create")

        assert count == 3  # 2 + 1 succeeded
        assert len(captured_calls) == 2  # one bulk per terminology
        urls_posted = {url for url, _ in captured_calls}
        assert "http://def/api/def-store/terminologies/T1/terms" in urls_posted
        assert "http://def/api/def-store/terminologies/T2/terms" in urls_posted

    @pytest.mark.asyncio
    async def test_handles_bulk_failure(self, client, tmp_path):
        svc = ImportService(
            def_store_url="http://def",
            template_store_url="http://tpl",
            document_store_url="http://doc",
            api_key="k",
        )
        jsonl_path = tmp_path / "terms.jsonl"
        jsonl_path.write_text(json.dumps(
            {"terminology_id": "T1", "value": "a"}
        ))
        bad_resp = MagicMock()
        bad_resp.status_code = 500
        mock_client = _patch_async_client(
            responses_by_method={"post": [bad_resp]},
        )
        with patch("httpx.AsyncClient", return_value=mock_client):
            count = await svc._import_terms(str(jsonl_path), {}, mode="create")
        # Bulk failed → no count increment, no raise
        assert count == 0


class TestImportNamespaceEndToEnd:
    """import_namespace orchestrates manifest read + per-entity-type import."""

    @pytest.mark.asyncio
    async def test_full_pipeline_calls_each_endpoint(self, client, tmp_path):
        archive = _build_archive(
            tmp_path,
            manifest={
                "version": "2.0",
                "prefix": "src",
                "description": "test",
                "isolation_mode": "open",
                "allowed_external_refs": [],
                "id_config": {},
            },
            jsonl_files={
                "terminologies.jsonl": [{"value": "T1", "label": "T1", "namespace": "src"}],
                "terms.jsonl": [{"terminology_id": "T-XYZ", "value": "a", "label": "A"}],
                "templates.jsonl": [{"value": "TPL", "namespace": "src"}],
                "documents.jsonl": [{"data": {}, "namespace": "src"}],
                "files.jsonl": [{"file_id": "f1", "namespace": "src"}],
            },
        )

        svc = ImportService(
            def_store_url="http://def",
            template_store_url="http://tpl",
            document_store_url="http://doc",
            api_key="k",
        )

        # 1 terminology + 1 terms-bulk + 1 template + 1 document = 4 POSTs; all succeed.
        ok = _make_post_response(status_code=201)
        # Terms bulk returns the succeeded count shape
        terms_ok = _make_post_response(status_code=200, body={"succeeded": 1, "failed": 0})
        mock_client = _patch_async_client(
            responses_by_method={"post": [ok, terms_ok, ok, ok]},
        )

        with patch("httpx.AsyncClient", return_value=mock_client):
            ns, stats = await svc.import_namespace(archive, mode="create", imported_by="tester")

        # Namespace created with source prefix (no target_prefix override)
        assert ns.prefix == "src"
        assert stats["terminologies"] == 1
        assert stats["terms"] == 1
        assert stats["templates"] == 1
        assert stats["documents"] == 1
        assert stats["files"] == 1  # files: count from jsonl lines (no upload)

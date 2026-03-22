"""HTTP client for WIP service APIs.

Wraps httpx to provide a unified interface to all WIP services.
Handles the bulk response envelope so callers get clean results.
"""

import os
from typing import Any

import httpx


class BulkError(Exception):
    """Raised when a single-item bulk operation fails."""

    def __init__(self, error: str, index: int = 0):
        self.error = error
        self.index = index
        super().__init__(error)


def _resolve_api_key() -> str:
    """Resolve the API key from env var or file.

    Priority: WIP_API_KEY env var > WIP_API_KEY_FILE contents > default dev key.
    WIP_API_KEY_FILE allows key rotation without updating each app's .mcp.json.
    """
    key = os.getenv("WIP_API_KEY")
    if key:
        return key
    key_file = os.getenv("WIP_API_KEY_FILE")
    if key_file:
        try:
            return open(key_file).read().strip()
        except OSError as e:
            import logging
            logging.getLogger("wip_mcp").warning("Cannot read WIP_API_KEY_FILE %s: %s", key_file, e)
    return "dev_master_key_for_testing"


class WipClient:
    """Client for all WIP service APIs."""

    def __init__(
        self,
        registry_url: str | None = None,
        def_store_url: str | None = None,
        template_store_url: str | None = None,
        document_store_url: str | None = None,
        reporting_sync_url: str | None = None,
        api_key: str | None = None,
        timeout: float = 30.0,
    ):
        self.registry_url = registry_url or os.getenv(
            "REGISTRY_URL", "http://localhost:8001"
        )
        self.def_store_url = def_store_url or os.getenv(
            "DEF_STORE_URL", "http://localhost:8002"
        )
        self.template_store_url = template_store_url or os.getenv(
            "TEMPLATE_STORE_URL", "http://localhost:8003"
        )
        self.document_store_url = document_store_url or os.getenv(
            "DOCUMENT_STORE_URL", "http://localhost:8004"
        )
        self.reporting_sync_url = reporting_sync_url or os.getenv(
            "REPORTING_SYNC_URL", "http://localhost:8005"
        )
        self.api_key = api_key or _resolve_api_key()
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "X-API-Key": self.api_key,
            "Content-Type": "application/json",
        }

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                headers=self._headers, timeout=self.timeout
            )
        return self._client

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.close()

    # -- Low-level helpers --

    async def _get(self, base_url: str, path: str, **params) -> dict[str, Any]:
        """GET request, return parsed JSON."""
        client = await self._get_client()
        # Strip None params
        params = {k: v for k, v in params.items() if v is not None}
        resp = await client.get(f"{base_url}{path}", params=params)
        resp.raise_for_status()
        return resp.json()

    async def _post(
        self, base_url: str, path: str, json: Any = None, **params
    ) -> dict[str, Any]:
        """POST request, return parsed JSON."""
        client = await self._get_client()
        params = {k: v for k, v in params.items() if v is not None}
        resp = await client.post(f"{base_url}{path}", json=json, params=params)
        resp.raise_for_status()
        return resp.json()

    async def _put(
        self, base_url: str, path: str, json: Any = None
    ) -> dict[str, Any]:
        """PUT request, return parsed JSON."""
        client = await self._get_client()
        resp = await client.put(f"{base_url}{path}", json=json)
        resp.raise_for_status()
        return resp.json()

    async def _delete(
        self, base_url: str, path: str, json: Any = None
    ) -> dict[str, Any]:
        """DELETE request with body, return parsed JSON."""
        client = await self._get_client()
        resp = await client.request("DELETE", f"{base_url}{path}", json=json)
        resp.raise_for_status()
        return resp.json()

    def _unwrap_single(self, bulk_response: dict[str, Any]) -> dict[str, Any]:
        """Unwrap a single-item BulkResponse. Raise on error."""
        result = bulk_response["results"][0]
        if result.get("status") == "error":
            raise BulkError(result.get("error", "Unknown error"))
        return result

    def _unwrap_bulk(self, bulk_response: dict[str, Any]) -> dict[str, Any]:
        """Return full bulk response with summary."""
        return {
            "total": bulk_response.get("total", 0),
            "succeeded": bulk_response.get("succeeded", 0),
            "failed": bulk_response.get("failed", 0),
            "results": bulk_response.get("results", []),
        }

    # ========================================================
    # Registry
    # ========================================================

    async def list_namespaces(self, include_archived: bool = False) -> list[dict]:
        data = await self._get(
            self.registry_url,
            "/api/registry/namespaces",
            include_archived=include_archived,
        )
        # Registry returns a list directly
        return data if isinstance(data, list) else data.get("items", data)

    async def get_namespace(self, prefix: str) -> dict:
        return await self._get(
            self.registry_url, f"/api/registry/namespaces/{prefix}"
        )

    async def get_namespace_stats(self, prefix: str) -> dict:
        return await self._get(
            self.registry_url, f"/api/registry/namespaces/{prefix}/stats"
        )

    async def search_registry(
        self,
        query: str,
        namespace: str | None = None,
        entity_type: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> dict:
        return await self._get(
            self.registry_url,
            "/api/registry/search",
            q=query,
            namespace=namespace,
            entity_type=entity_type,
            page=page,
            page_size=page_size,
        )

    # ========================================================
    # Registry: Entries & Synonyms
    # ========================================================

    async def get_entry(self, entry_id: str) -> dict:
        return await self._get(
            self.registry_url, f"/api/registry/entries/{entry_id}"
        )

    async def register_entry(
        self,
        namespace: str,
        entity_type: str,
        composite_key: dict | None = None,
        identity_values: dict | None = None,
        created_by: str | None = None,
    ) -> dict:
        item: dict[str, Any] = {
            "namespace": namespace,
            "entity_type": entity_type,
            "composite_key": composite_key or {},
        }
        if identity_values:
            item["identity_values"] = identity_values
        if created_by:
            item["created_by"] = created_by
        resp = await self._post(
            self.registry_url, "/api/registry/entries/register", json=[item]
        )
        return resp["results"][0] if resp.get("results") else resp

    async def lookup_by_id(self, entry_id: str, fetch_source_data: bool = False) -> dict:
        resp = await self._post(
            self.registry_url,
            "/api/registry/entries/lookup/by-id",
            json=[{"entry_id": entry_id, "fetch_source_data": fetch_source_data}],
        )
        return resp["results"][0] if resp.get("results") else resp

    async def lookup_by_key(
        self,
        namespace: str,
        entity_type: str,
        composite_key: dict,
        search_synonyms: bool = True,
    ) -> dict:
        resp = await self._post(
            self.registry_url,
            "/api/registry/entries/lookup/by-key",
            json=[{
                "namespace": namespace,
                "entity_type": entity_type,
                "composite_key": composite_key,
                "search_synonyms": search_synonyms,
            }],
        )
        return resp["results"][0] if resp.get("results") else resp

    async def add_synonym(
        self,
        target_id: str,
        synonym_namespace: str,
        synonym_entity_type: str,
        synonym_composite_key: dict,
    ) -> dict:
        resp = await self._post(
            self.registry_url,
            "/api/registry/synonyms/add",
            json=[{
                "target_id": target_id,
                "synonym_namespace": synonym_namespace,
                "synonym_entity_type": synonym_entity_type,
                "synonym_composite_key": synonym_composite_key,
            }],
        )
        return resp["results"][0] if resp.get("results") else resp

    async def remove_synonym(
        self,
        target_id: str,
        synonym_namespace: str,
        synonym_entity_type: str,
        synonym_composite_key: dict,
    ) -> dict:
        resp = await self._post(
            self.registry_url,
            "/api/registry/synonyms/remove",
            json=[{
                "target_id": target_id,
                "synonym_namespace": synonym_namespace,
                "synonym_entity_type": synonym_entity_type,
                "synonym_composite_key": synonym_composite_key,
            }],
        )
        return resp["results"][0] if resp.get("results") else resp

    async def merge_entries(
        self, preferred_id: str, deprecated_id: str
    ) -> dict:
        resp = await self._post(
            self.registry_url,
            "/api/registry/synonyms/merge",
            json=[{
                "preferred_id": preferred_id,
                "deprecated_id": deprecated_id,
            }],
        )
        return resp["results"][0] if resp.get("results") else resp

    # ========================================================
    # Def-Store: Terminologies
    # ========================================================

    async def list_terminologies(
        self,
        namespace: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> dict:
        return await self._get(
            self.def_store_url,
            "/api/def-store/terminologies",
            namespace=namespace,
            page=page,
            page_size=page_size,
        )

    async def get_terminology(self, terminology_id: str) -> dict:
        return await self._get(
            self.def_store_url,
            f"/api/def-store/terminologies/{terminology_id}",
        )

    async def get_terminology_by_value(self, value: str) -> dict:
        return await self._get(
            self.def_store_url,
            f"/api/def-store/terminologies/by-value/{value}",
        )

    async def create_terminology(
        self, value: str, label: str, namespace: str = "wip", **kwargs
    ) -> dict:
        payload = {"value": value, "label": label, "namespace": namespace, **kwargs}
        resp = await self._post(
            self.def_store_url, "/api/def-store/terminologies", json=[payload]
        )
        return self._unwrap_single(resp)

    async def create_terminologies(self, items: list[dict]) -> dict:
        resp = await self._post(
            self.def_store_url, "/api/def-store/terminologies", json=items
        )
        return self._unwrap_bulk(resp)

    async def update_terminology(self, terminology_id: str, updates: dict) -> dict:
        item = {"terminology_id": terminology_id, **updates}
        resp = await self._put(
            self.def_store_url, "/api/def-store/terminologies", json=[item]
        )
        return self._unwrap_single(resp)

    async def delete_terminology(self, terminology_id: str, force: bool = False) -> dict:
        item: dict[str, Any] = {"id": terminology_id}
        if force:
            item["force"] = True
        resp = await self._delete(
            self.def_store_url, "/api/def-store/terminologies", json=[item]
        )
        return self._unwrap_single(resp)

    async def restore_terminology(
        self, terminology_id: str, restore_terms: bool = True
    ) -> dict:
        return await self._post(
            self.def_store_url,
            f"/api/def-store/terminologies/{terminology_id}/restore",
            restore_terms=restore_terms,
        )

    # ========================================================
    # Def-Store: Terms
    # ========================================================

    async def list_terms(
        self,
        terminology_id: str,
        search: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> dict:
        return await self._get(
            self.def_store_url,
            f"/api/def-store/terminologies/{terminology_id}/terms",
            search=search,
            page=page,
            page_size=page_size,
        )

    async def get_term(self, term_id: str) -> dict:
        return await self._get(
            self.def_store_url, f"/api/def-store/terms/{term_id}"
        )

    async def create_terms(
        self, terminology_id: str, terms: list[dict], batch_size: int | None = None
    ) -> dict:
        resp = await self._post(
            self.def_store_url,
            f"/api/def-store/terminologies/{terminology_id}/terms",
            json=terms,
            batch_size=batch_size,
        )
        return self._unwrap_bulk(resp)

    async def update_term(self, term_id: str, updates: dict) -> dict:
        item = {"term_id": term_id, **updates}
        resp = await self._put(
            self.def_store_url, "/api/def-store/terms", json=[item]
        )
        return self._unwrap_single(resp)

    async def delete_term(self, term_id: str) -> dict:
        resp = await self._delete(
            self.def_store_url, "/api/def-store/terms", json=[{"id": term_id}]
        )
        return self._unwrap_single(resp)

    async def deprecate_term(
        self, term_id: str, reason: str, replaced_by_term_id: str | None = None
    ) -> dict:
        item: dict[str, Any] = {"term_id": term_id, "reason": reason}
        if replaced_by_term_id:
            item["replaced_by_term_id"] = replaced_by_term_id
        resp = await self._post(
            self.def_store_url, "/api/def-store/terms/deprecate", json=[item]
        )
        return self._unwrap_single(resp)

    async def validate_term(self, terminology_id: str, value: str) -> dict:
        return await self._post(
            self.def_store_url,
            "/api/def-store/validate",
            json={"terminology_id": terminology_id, "value": value},
        )

    # ========================================================
    # Def-Store: Ontology
    # ========================================================

    async def get_term_children(self, term_id: str) -> list[dict]:
        return await self._get(
            self.def_store_url,
            f"/api/def-store/ontology/terms/{term_id}/children",
        )

    async def get_term_parents(self, term_id: str) -> list[dict]:
        return await self._get(
            self.def_store_url,
            f"/api/def-store/ontology/terms/{term_id}/parents",
        )

    async def get_term_ancestors(
        self, term_id: str, relationship_type: str | None = None, max_depth: int = 10
    ) -> list[dict]:
        return await self._get(
            self.def_store_url,
            f"/api/def-store/ontology/terms/{term_id}/ancestors",
            relationship_type=relationship_type,
            max_depth=max_depth,
        )

    async def get_term_descendants(
        self, term_id: str, relationship_type: str | None = None, max_depth: int = 10
    ) -> list[dict]:
        return await self._get(
            self.def_store_url,
            f"/api/def-store/ontology/terms/{term_id}/descendants",
            relationship_type=relationship_type,
            max_depth=max_depth,
        )

    async def create_relationships(self, relationships: list[dict]) -> dict:
        resp = await self._post(
            self.def_store_url,
            "/api/def-store/ontology/relationships",
            json=relationships,
        )
        return self._unwrap_bulk(resp)

    async def delete_relationships(self, relationships: list[dict], namespace: str | None = None) -> dict:
        resp = await self._delete(
            self.def_store_url,
            "/api/def-store/ontology/relationships",
            json=relationships,
        )
        return self._unwrap_bulk(resp)

    async def list_relationships(
        self,
        term_id: str,
        direction: str = "outgoing",
        relationship_type: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> dict:
        return await self._get(
            self.def_store_url,
            "/api/def-store/ontology/relationships",
            term_id=term_id,
            direction=direction,
            relationship_type=relationship_type,
            page=page,
            page_size=page_size,
        )

    # ========================================================
    # Def-Store: Import/Export
    # ========================================================

    async def export_terminology(
        self,
        terminology_id: str,
        format: str = "json",
        include_relationships: bool = True,
    ) -> dict:
        return await self._get(
            self.def_store_url,
            f"/api/def-store/import-export/export/{terminology_id}",
            format=format,
            include_relationships=include_relationships,
        )

    async def import_terminology(
        self,
        data: dict | list,
        format: str = "json",
        skip_duplicates: bool = True,
        update_existing: bool = False,
    ) -> dict:
        return await self._post(
            self.def_store_url,
            "/api/def-store/import-export/import",
            json=data,
            format=format,
            skip_duplicates=skip_duplicates,
            update_existing=update_existing,
        )

    # ========================================================
    # Template-Store
    # ========================================================

    async def list_templates(
        self,
        namespace: str | None = None,
        status: str | None = None,
        latest_only: bool = True,
        page: int = 1,
        page_size: int = 50,
    ) -> dict:
        return await self._get(
            self.template_store_url,
            "/api/template-store/templates",
            namespace=namespace,
            status=status,
            latest_only=latest_only,
            page=page,
            page_size=page_size,
        )

    async def get_template(
        self, template_id: str, version: int | None = None
    ) -> dict:
        return await self._get(
            self.template_store_url,
            f"/api/template-store/templates/{template_id}",
            version=version,
        )

    async def get_template_by_value(self, value: str, namespace: str | None = None) -> dict:
        return await self._get(
            self.template_store_url,
            f"/api/template-store/templates/by-value/{value}",
            namespace=namespace,
        )

    async def get_template_raw(self, template_id: str) -> dict:
        return await self._get(
            self.template_store_url,
            f"/api/template-store/templates/{template_id}/raw",
        )

    async def create_template(self, template: dict) -> dict:
        resp = await self._post(
            self.template_store_url,
            "/api/template-store/templates",
            json=[template],
        )
        return self._unwrap_single(resp)

    async def create_templates(self, templates: list[dict]) -> dict:
        resp = await self._post(
            self.template_store_url,
            "/api/template-store/templates",
            json=templates,
        )
        return self._unwrap_bulk(resp)

    async def activate_template(
        self, template_id: str, namespace: str | None = None, dry_run: bool = False
    ) -> dict:
        return await self._post(
            self.template_store_url,
            f"/api/template-store/templates/{template_id}/activate",
            namespace=namespace,
            dry_run=dry_run,
        )

    async def deactivate_template(
        self, template_id: str, version: int | None = None, force: bool = False
    ) -> dict:
        item: dict[str, Any] = {"id": template_id}
        if version is not None:
            item["version"] = version
        if force:
            item["force"] = True
        resp = await self._delete(
            self.template_store_url,
            "/api/template-store/templates",
            json=[item],
        )
        return self._unwrap_single(resp)

    async def get_template_versions(self, template_id: str) -> dict:
        return await self._get(
            self.template_store_url,
            f"/api/template-store/templates/{template_id}/versions",
        )

    async def get_template_versions_by_value(self, value: str) -> dict:
        return await self._get(
            self.template_store_url,
            f"/api/template-store/templates/by-value/{value}/versions",
        )

    async def validate_template(self, template_id: str) -> dict:
        return await self._post(
            self.template_store_url,
            f"/api/template-store/templates/{template_id}/validate",
            json={},
        )

    async def get_template_dependencies(self, template_id: str) -> dict:
        return await self._get(
            self.template_store_url,
            f"/api/template-store/templates/{template_id}/dependencies",
        )

    # ========================================================
    # Document-Store: Documents
    # ========================================================

    async def list_documents(
        self,
        namespace: str | None = None,
        template_id: str | None = None,
        template_value: str | None = None,
        status: str | None = None,
        latest_only: bool = True,
        page: int = 1,
        page_size: int = 50,
    ) -> dict:
        return await self._get(
            self.document_store_url,
            "/api/document-store/documents",
            namespace=namespace,
            template_id=template_id,
            template_value=template_value,
            status=status,
            latest_only=latest_only,
            page=page,
            page_size=page_size,
        )

    async def get_document(
        self, document_id: str, version: int | None = None
    ) -> dict:
        return await self._get(
            self.document_store_url,
            f"/api/document-store/documents/{document_id}",
            version=version,
        )

    async def create_document(self, document: dict) -> dict:
        resp = await self._post(
            self.document_store_url,
            "/api/document-store/documents",
            json=[document],
        )
        return self._unwrap_single(resp)

    async def create_documents(self, documents: list[dict]) -> dict:
        resp = await self._post(
            self.document_store_url,
            "/api/document-store/documents",
            json=documents,
        )
        return self._unwrap_bulk(resp)

    async def get_document_versions(self, document_id: str) -> dict:
        return await self._get(
            self.document_store_url,
            f"/api/document-store/documents/{document_id}/versions",
        )

    async def archive_document(self, document_id: str, archived_by: str | None = None) -> dict:
        item: dict[str, Any] = {"id": document_id}
        if archived_by:
            item["archived_by"] = archived_by
        resp = await self._post(
            self.document_store_url,
            "/api/document-store/documents/archive",
            json=[item],
        )
        return self._unwrap_single(resp)

    async def query_documents(self, filters: dict) -> dict:
        return await self._post(
            self.document_store_url,
            "/api/document-store/documents/query",
            json=filters,
        )

    async def get_table_view(
        self,
        template_id: str,
        status: str | None = None,
        page: int = 1,
        page_size: int = 100,
    ) -> dict:
        return await self._get(
            self.document_store_url,
            f"/api/document-store/table/{template_id}",
            status=status,
            page=page,
            page_size=page_size,
        )

    async def export_table_csv(
        self,
        template_id: str,
        status: str | None = None,
        include_metadata: bool = True,
    ) -> str:
        """Returns raw CSV content as a string."""
        client = await self._get_client()
        params = {"include_metadata": include_metadata}
        if status:
            params["status"] = status
        resp = await client.get(
            f"{self.document_store_url}/api/document-store/table/{template_id}/csv",
            params=params,
        )
        resp.raise_for_status()
        return resp.text

    # ========================================================
    # Document-Store: Files
    # ========================================================

    async def list_files(
        self,
        namespace: str | None = None,
        status: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> dict:
        return await self._get(
            self.document_store_url,
            "/api/document-store/files",
            namespace=namespace,
            status=status,
            page=page,
            page_size=page_size,
        )

    async def get_file(self, file_id: str) -> dict:
        return await self._get(
            self.document_store_url,
            f"/api/document-store/files/{file_id}",
        )

    async def upload_file(
        self,
        file_content: bytes,
        filename: str,
        content_type: str,
        namespace: str = "wip",
        description: str | None = None,
        tags: list[str] | None = None,
        category: str | None = None,
    ) -> dict:
        """Upload a file via multipart form. Returns single FileResponse (not bulk)."""
        client = await self._get_client()
        files = {"file": (filename, file_content, content_type)}
        data: dict[str, str] = {"namespace": namespace}
        if description:
            data["description"] = description
        if tags:
            data["tags"] = ",".join(tags)
        if category:
            data["category"] = category

        resp = await client.post(
            f"{self.document_store_url}/api/document-store/files",
            files=files,
            data=data,
            headers={"X-API-Key": self.api_key},  # Override default JSON headers
        )
        resp.raise_for_status()
        return resp.json()

    async def delete_file(self, file_id: str, force: bool = False) -> dict:
        item: dict[str, Any] = {"id": file_id}
        if force:
            item["force"] = True
        resp = await self._delete(
            self.document_store_url,
            "/api/document-store/files",
            json=[item],
        )
        return self._unwrap_single(resp)

    async def hard_delete_file(self, file_id: str) -> dict:
        return await self._delete(
            self.document_store_url,
            f"/api/document-store/files/{file_id}/hard",
        )

    async def get_file_documents(
        self, file_id: str, page: int = 1, page_size: int = 50
    ) -> dict:
        return await self._get(
            self.document_store_url,
            f"/api/document-store/files/{file_id}/documents",
            page=page,
            page_size=page_size,
        )

    # ========================================================
    # Document-Store: Import
    # ========================================================

    async def preview_import(
        self,
        file_content: bytes,
        filename: str,
    ) -> dict:
        """Preview a CSV/XLSX file for import."""
        client = await self._get_client()
        files = {"file": (filename, file_content, "application/octet-stream")}
        resp = await client.post(
            f"{self.document_store_url}/api/document-store/import/preview",
            files=files,
            headers={"X-API-Key": self.api_key},
        )
        resp.raise_for_status()
        return resp.json()

    async def import_documents(
        self,
        file_content: bytes,
        filename: str,
        template_id: str,
        column_mapping: dict,
        namespace: str = "wip",
        skip_errors: bool = False,
    ) -> dict:
        """Import documents from CSV/XLSX."""
        import json as _json
        client = await self._get_client()
        files = {"file": (filename, file_content, "application/octet-stream")}
        data = {
            "template_id": template_id,
            "column_mapping": _json.dumps(column_mapping),
            "namespace": namespace,
            "skip_errors": str(skip_errors).lower(),
        }
        resp = await client.post(
            f"{self.document_store_url}/api/document-store/import",
            files=files,
            data=data,
            headers={"X-API-Key": self.api_key},
        )
        resp.raise_for_status()
        return resp.json()

    # ========================================================
    # Document-Store: Replay
    # ========================================================

    async def start_replay(
        self,
        filter_config: dict | None = None,
        throttle_ms: int = 10,
        batch_size: int = 100,
    ) -> dict:
        """Start a document replay session."""
        body = {
            "filter": filter_config or {},
            "throttle_ms": throttle_ms,
            "batch_size": batch_size,
        }
        return await self._post(
            self.document_store_url,
            "/api/document-store/replay/start",
            json=body,
        )

    async def get_replay_session(self, session_id: str) -> dict:
        """Get replay session status."""
        return await self._get(
            self.document_store_url,
            f"/api/document-store/replay/{session_id}",
        )

    async def pause_replay(self, session_id: str) -> dict:
        return await self._post(
            self.document_store_url,
            f"/api/document-store/replay/{session_id}/pause",
        )

    async def resume_replay(self, session_id: str) -> dict:
        return await self._post(
            self.document_store_url,
            f"/api/document-store/replay/{session_id}/resume",
        )

    async def cancel_replay(self, session_id: str) -> dict:
        return await self._delete(
            self.document_store_url,
            f"/api/document-store/replay/{session_id}",
        )

    # ========================================================
    # Reporting-Sync
    # ========================================================

    async def get_sync_status(self) -> dict:
        return await self._get(
            self.reporting_sync_url, "/api/reporting-sync/status"
        )

    async def list_report_tables(self) -> dict:
        """List available reporting tables (doc_* + terminologies/terms)."""
        return await self._get(
            self.reporting_sync_url, "/api/reporting-sync/tables"
        )

    async def run_report_query(
        self,
        sql: str,
        params: list | None = None,
        timeout_seconds: int = 30,
        max_rows: int = 1000,
    ) -> dict:
        """Execute a read-only SQL query against the reporting database."""
        body = {
            "sql": sql,
            "params": params or [],
            "timeout_seconds": timeout_seconds,
            "max_rows": max_rows,
        }
        return await self._post(
            self.reporting_sync_url, "/api/reporting-sync/query", json=body
        )

    async def unified_search(
        self,
        query: str,
        types: list[str] | None = None,
        limit: int = 20,
    ) -> dict:
        body: dict[str, Any] = {"query": query, "limit": limit}
        if types:
            body["types"] = types
        return await self._post(
            self.reporting_sync_url, "/api/reporting-sync/search", json=body
        )

    # ========================================================
    # Health (all services)
    # ========================================================

    async def check_health(self) -> dict[str, Any]:
        """Check health of all WIP services."""
        services = {
            "registry": self.registry_url,
            "def_store": self.def_store_url,
            "template_store": self.template_store_url,
            "document_store": self.document_store_url,
            "reporting_sync": self.reporting_sync_url,
        }
        results = {}
        client = await self._get_client()
        for name, url in services.items():
            try:
                resp = await client.get(f"{url}/health", timeout=5.0)
                results[name] = {
                    "healthy": resp.status_code == 200,
                    "details": resp.json() if resp.status_code == 200 else None,
                }
            except Exception as e:
                results[name] = {"healthy": False, "error": str(e)}
        return results

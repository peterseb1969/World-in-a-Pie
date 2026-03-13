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
        self.api_key = api_key or os.getenv(
            "WIP_API_KEY", "dev_master_key_for_testing"
        )
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

    async def query_documents(self, filters: dict) -> dict:
        return await self._post(
            self.document_store_url,
            "/api/document-store/documents/query",
            json=filters,
        )

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

    # ========================================================
    # Reporting-Sync
    # ========================================================

    async def get_sync_status(self) -> dict:
        return await self._get(
            self.reporting_sync_url, "/api/reporting-sync/status"
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

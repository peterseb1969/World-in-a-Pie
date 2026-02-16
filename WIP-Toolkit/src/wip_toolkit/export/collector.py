"""Paginated entity fetching from WIP services."""

from __future__ import annotations

from typing import Any

from rich.console import Console

from ..client import WIPClient

console = Console(stderr=True)


class EntityCollector:
    """Fetches entities from WIP services with pagination."""

    def __init__(self, client: WIPClient, namespace: str, include_inactive: bool = False) -> None:
        self.client = client
        self.namespace = namespace
        self.include_inactive = include_inactive

    # --- Def-Store ---

    def fetch_terminologies(self) -> list[dict[str, Any]]:
        """Fetch all terminologies in the namespace."""
        params: dict[str, Any] = {"namespace": self.namespace}
        if not self.include_inactive:
            params["status"] = "active"
        items = self.client.fetch_all_paginated(
            "def-store", "/terminologies", params=params, page_size=100,
        )
        console.print(f"  Fetched {len(items)} terminologies")
        return items

    def fetch_terms(self, terminology_id: str) -> list[dict[str, Any]]:
        """Fetch all terms for a terminology."""
        params: dict[str, Any] = {}
        if not self.include_inactive:
            params["status"] = "active"
        return self.client.fetch_all_paginated(
            "def-store", f"/terminologies/{terminology_id}/terms",
            params=params, page_size=500,
        )

    def fetch_all_terms(self, terminologies: list[dict]) -> list[dict[str, Any]]:
        """Fetch terms for all terminologies."""
        all_terms: list[dict] = []
        for term_def in terminologies:
            tid = term_def["terminology_id"]
            terms = self.fetch_terms(tid)
            all_terms.extend(terms)
        console.print(f"  Fetched {len(all_terms)} terms across {len(terminologies)} terminologies")
        return all_terms

    # --- Template-Store ---

    def fetch_templates(self) -> list[dict[str, Any]]:
        """Fetch all templates (all versions) in the namespace."""
        params: dict[str, Any] = {
            "namespace": self.namespace,
            "latest_only": "false",
        }
        if not self.include_inactive:
            params["status"] = "active"
        items = self.client.fetch_all_paginated(
            "template-store", "/templates", params=params, page_size=100,
        )
        console.print(f"  Fetched {len(items)} template versions")
        return items

    def fetch_template_raw(self, template_id: str, version: int) -> dict[str, Any]:
        """Fetch a single template version in raw (unresolved) form."""
        return self.client.get(
            "template-store",
            f"/templates/{template_id}/raw",
            params={"version": version},
        )

    # --- Document-Store ---

    def fetch_documents(self) -> list[dict[str, Any]]:
        """Fetch all documents (latest versions) in the namespace."""
        params: dict[str, Any] = {"namespace": self.namespace}
        if not self.include_inactive:
            params["status"] = "active"
        items = self.client.fetch_all_paginated(
            "document-store", "/documents", params=params, page_size=100,
        )
        # Deduplicate — Document-Store pagination can return duplicates across
        # page boundaries (WIP bug: documents inserted during pagination shift
        # offsets). Use (document_id, version) as the unique key.
        seen: set[tuple[str, int]] = set()
        deduped: list[dict[str, Any]] = []
        for doc in items:
            key = (doc["document_id"], doc.get("version", 1))
            if key not in seen:
                seen.add(key)
                deduped.append(doc)
        if len(deduped) < len(items):
            console.print(f"  Fetched {len(items)} documents ({len(items) - len(deduped)} duplicates removed)")
        else:
            console.print(f"  Fetched {len(deduped)} documents")
        return deduped

    def fetch_document_versions(self, document_id: str) -> list[dict[str, Any]]:
        """Fetch all versions of a specific document."""
        data = self.client.get(
            "document-store", f"/documents/{document_id}/versions",
        )
        return data.get("versions", [])

    def fetch_document_version(self, document_id: str, version: int) -> dict[str, Any]:
        """Fetch a specific document version."""
        return self.client.get(
            "document-store", f"/documents/{document_id}/versions/{version}",
        )

    def fetch_files(self) -> list[dict[str, Any]]:
        """Fetch all file metadata in the namespace."""
        params: dict[str, Any] = {"namespace": self.namespace}
        items = self.client.fetch_all_paginated(
            "document-store", "/files", params=params, page_size=100,
        )
        console.print(f"  Fetched {len(items)} files")
        return items

    def fetch_file_content(self, file_id: str) -> bytes:
        """Download binary file content."""
        resp = self.client.get_stream("document-store", f"/files/{file_id}/content")
        return resp.content

    # --- Single entity lookups (for closure) ---

    def fetch_terminology_by_id(self, terminology_id: str) -> dict[str, Any] | None:
        """Fetch a single terminology by ID. Returns None if not found."""
        try:
            return self.client.get("def-store", f"/terminologies/{terminology_id}")
        except Exception:
            return None

    def fetch_template_by_id(self, template_id: str) -> list[dict[str, Any]]:
        """Fetch all versions of a template by ID. Returns empty list if not found."""
        try:
            params: dict[str, Any] = {"latest_only": "false"}
            items = self.client.fetch_all_paginated(
                "template-store", "/templates",
                params={**params, "value": ""},  # Can't filter by ID directly in list
                page_size=100,
            )
            # Filter to the specific template_id
            return [t for t in items if t.get("template_id") == template_id]
        except Exception:
            return []

    def fetch_template_versions_by_id(self, template_id: str) -> list[dict[str, Any]]:
        """Fetch all versions of a specific template by its ID."""
        try:
            # Use the list endpoint filtering, since there's no direct get-all-versions-by-id
            all_templates = self.client.fetch_all_paginated(
                "template-store", "/templates",
                params={"latest_only": "false"},
                page_size=100,
            )
            return [t for t in all_templates if t.get("template_id") == template_id]
        except Exception:
            return []

    # --- Registry ---

    def fetch_namespace_config(self, prefix: str) -> dict[str, Any] | None:
        """Fetch namespace configuration."""
        try:
            return self.client.get("registry", f"/namespaces/{prefix}")
        except Exception:
            return None

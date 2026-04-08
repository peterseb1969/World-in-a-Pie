"""Paginated entity fetching from WIP services."""

from __future__ import annotations

from typing import Any, BinaryIO, Iterator

from rich.console import Console

from ..client import WIPClient

console = Console(stderr=True)


class EntityCollector:
    """Fetches entities from WIP services with pagination."""

    def __init__(self, client: WIPClient, namespace: str, include_inactive: bool = False) -> None:
        self.client = client
        self.namespace = namespace
        self.include_inactive = include_inactive
        self._template_cache: list[dict[str, Any]] | None = None

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
            params=params, page_size=100,
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

    def fetch_relationships(self, terminology_id: str) -> list[dict[str, Any]]:
        """Fetch all relationships for a terminology (paginated)."""
        items: list[dict[str, Any]] = []
        page = 1
        while True:
            data = self.client.get(
                "def-store", "/ontology/relationships/all",
                params={
                    "namespace": self.namespace,
                    "source_terminology_id": terminology_id,
                    "status": "active" if not self.include_inactive else "",
                    "page": page,
                    "page_size": 100,
                },
            )
            page_items = data.get("items", [])
            items.extend(page_items)
            if len(page_items) < 100:
                break
            page += 1
        return items

    def fetch_all_relationships(self, terminologies: list[dict]) -> list[dict[str, Any]]:
        """Fetch relationships for all terminologies."""
        all_rels: list[dict[str, Any]] = []
        for t in terminologies:
            tid = t["terminology_id"]
            rels = self.fetch_relationships(tid)
            all_rels.extend(rels)
        if all_rels:
            console.print(f"  Fetched {len(all_rels)} relationships across {len(terminologies)} terminologies")
        return all_rels

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
            params={"version": version, "namespace": self.namespace},
        )

    # --- Document-Store ---

    def fetch_documents(
        self, latest_only: bool = True, template_ids: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch documents in the namespace.

        Always uses the fast indexed query path (no server-side aggregation).
        When latest_only=True, deduplicates client-side keeping only the
        highest version per document_id.

        Args:
            latest_only: If True, return only the latest version of each
                document_id. If False, return all versions.
            template_ids: If provided, only return documents matching these
                template IDs (client-side filter).
        """
        params: dict[str, Any] = {"namespace": self.namespace}
        if not self.include_inactive:
            params["status"] = "active"
        # Always fetch all versions via the fast path — client-side dedup
        # is much faster than server-side aggregation for large datasets.
        items = self.client.fetch_all_paginated(
            "document-store", "/documents", params=params, page_size=1000,
        )

        # Apply template filter before dedup (reduces work)
        if template_ids is not None:
            items = [d for d in items if d.get("template_id") in template_ids]

        # Deduplicate by (document_id, version) — pagination can return
        # duplicates across page boundaries.
        seen: set[tuple[str, int]] = set()
        deduped: list[dict[str, Any]] = []
        for doc in items:
            key = (doc["document_id"], doc.get("version", 1))
            if key not in seen:
                seen.add(key)
                deduped.append(doc)

        if latest_only:
            # Keep only the highest version per document_id
            latest: dict[str, dict[str, Any]] = {}
            for doc in deduped:
                did = doc["document_id"]
                if did not in latest or doc.get("version", 1) > latest[did].get("version", 1):
                    latest[did] = doc
            result = list(latest.values())
            console.print(
                f"  Fetched {len(result)} documents"
                + (f" (filtered by {len(template_ids)} templates)" if template_ids else "")
            )
            return result

        if len(deduped) < len(items):
            console.print(f"  Fetched {len(items)} documents ({len(items) - len(deduped)} duplicates removed)")
        else:
            console.print(f"  Fetched {len(deduped)} documents")
        return deduped

    def stream_documents(
        self,
        latest_only: bool = True,
        page_size: int = 1000,
    ) -> Iterator[list[dict[str, Any]]]:
        """Stream documents page-by-page using offset pagination.

        Yields one page (list of dicts) at a time for O(page_size) memory.
        When latest_only=True, deduplicates within each page keeping only
        the highest version per document_id.
        """
        params: dict[str, Any] = {"namespace": self.namespace, "page_size": page_size}
        if not self.include_inactive:
            params["status"] = "active"

        total_yielded = 0
        page = 1

        while True:
            params["page"] = page
            data = self.client.get("document-store", "/documents", params=params)
            page_items = data.get("items", [])
            if not page_items:
                break

            if latest_only:
                # Deduplicate within page: keep highest version per document_id
                latest: dict[str, dict[str, Any]] = {}
                for doc in page_items:
                    did = doc["document_id"]
                    if did not in latest or doc.get("version", 1) > latest[did].get("version", 1):
                        latest[did] = doc
                page_items = list(latest.values())

            total_yielded += len(page_items)
            yield page_items

            if len(data.get("items", [])) < page_size:
                break
            page += 1

        console.print(f"  Streamed {total_yielded} documents")

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

    def download_file_content(self, file_id: str, dest: BinaryIO) -> None:
        """Stream binary file content directly into ``dest``.

        No full body is held in Python — the underlying
        :meth:`WIPClient.stream_to_file` writes chunks straight from the
        socket to the destination handle (CASE-28).
        """
        self.client.stream_to_file(
            "document-store", f"/files/{file_id}/content", dest,
        )

    # --- Single entity lookups (for closure) ---

    def fetch_terminology_by_id(self, terminology_id: str) -> dict[str, Any] | None:
        """Fetch a single terminology by ID. Returns None if not found."""
        try:
            return self.client.get("def-store", f"/terminologies/{terminology_id}")
        except Exception:
            return None

    def _ensure_template_cache(self) -> list[dict[str, Any]]:
        """Fetch all templates once and cache for repeated lookups."""
        if self._template_cache is None:
            try:
                self._template_cache = self.client.fetch_all_paginated(
                    "template-store", "/templates",
                    params={"latest_only": "false"},
                    page_size=100,
                )
            except Exception:
                self._template_cache = []
        return self._template_cache

    def fetch_template_by_id(self, template_id: str) -> list[dict[str, Any]]:
        """Fetch all versions of a template by ID. Returns empty list if not found."""
        all_templates = self._ensure_template_cache()
        return [t for t in all_templates if t.get("template_id") == template_id]

    def fetch_template_versions_by_id(self, template_id: str) -> list[dict[str, Any]]:
        """Fetch all versions of a specific template by its ID."""
        all_templates = self._ensure_template_cache()
        return [t for t in all_templates if t.get("template_id") == template_id]

    # --- Document version history ---

    def fetch_all_document_versions(self, documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Expand latest-only documents to include all versions.

        For each unique document_id, fetches the version list and then
        each individual version. Deduplicates by (document_id, version).
        """
        seen_ids: set[str] = set()
        unique_doc_ids: list[str] = []
        for doc in documents:
            did = doc["document_id"]
            if did not in seen_ids:
                seen_ids.add(did)
                unique_doc_ids.append(did)

        all_versions: list[dict[str, Any]] = []
        seen_versions: set[tuple[str, int]] = set()

        for did in unique_doc_ids:
            try:
                version_list = self.fetch_document_versions(did)
                for v_info in version_list:
                    version = v_info.get("version", 1)
                    key = (did, version)
                    if key in seen_versions:
                        continue
                    seen_versions.add(key)
                    try:
                        full_doc = self.fetch_document_version(did, version)
                        all_versions.append(full_doc)
                    except Exception:
                        # Fall back: if we already have this version from the
                        # original fetch, it's already covered
                        pass
            except Exception:
                # If version listing fails, keep whatever we have from the
                # original documents list for this doc_id
                pass

        # Merge: ensure we haven't lost any documents from the original list
        for doc in documents:
            key = (doc["document_id"], doc.get("version", 1))
            if key not in seen_versions:
                seen_versions.add(key)
                all_versions.append(doc)

        console.print(
            f"  Expanded to {len(all_versions)} document versions "
            f"(from {len(unique_doc_ids)} unique documents)"
        )
        return all_versions

    # --- Registry bulk lookup ---

    def fetch_registry_entries(self, entity_ids: list[str]) -> dict[str, dict[str, Any]]:
        """Bulk-fetch Registry entries by ID, returning {entry_id: registry_data}.

        Batches IDs in groups of 100. For each found entry, extracts:
        - entry_id, namespace, entity_type
        - primary_composite_key (from matched_composite_key)
        - synonyms (list of {namespace, entity_type, composite_key})
        - source_info
        """
        result: dict[str, dict[str, Any]] = {}
        batch_size = 100

        for i in range(0, len(entity_ids), batch_size):
            batch = entity_ids[i:i + batch_size]
            try:
                resp = self.client.post(
                    "registry", "/entries/lookup/by-id",
                    json=[{"entry_id": eid} for eid in batch],
                )
                for entry in resp.get("results", []):
                    if entry.get("status") != "found":
                        continue
                    eid = entry.get("entry_id")
                    if not eid:
                        continue

                    # Extract synonyms from the entry
                    synonyms: list[dict[str, Any]] = []
                    for syn in entry.get("synonyms", []):
                        synonyms.append({
                            "namespace": syn.get("namespace", ""),
                            "entity_type": syn.get("entity_type", ""),
                            "composite_key": syn.get("composite_key", {}),
                        })

                    result[eid] = {
                        "entry_id": eid,
                        "namespace": entry.get("namespace", ""),
                        "entity_type": entry.get("entity_type", ""),
                        "primary_composite_key": entry.get("matched_composite_key", {}),
                        "synonyms": synonyms,
                        "source_info": entry.get("source_info"),
                    }
            except Exception as e:
                console.print(f"  [yellow]Warning: Registry lookup failed for batch at {i}: {e}[/yellow]")

        console.print(f"  Fetched {len(result)} Registry entries for {len(entity_ids)} IDs")
        return result

    # --- Registry ---

    def fetch_namespace_config(self, prefix: str) -> dict[str, Any] | None:
        """Fetch namespace configuration."""
        try:
            return self.client.get("registry", f"/namespaces/{prefix}")
        except Exception:
            return None

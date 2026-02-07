"""
Export Service - Export namespace group data to portable format.

Exports all data from a namespace group into JSONL files that can be
imported into another WIP instance or used for backup/restore.
"""

import json
import logging
import os
import tempfile
import zipfile
from datetime import datetime, timezone
from typing import Any

import httpx

from ..models.namespace_group import NamespaceGroup
from ..models.entry import RegistryEntry

logger = logging.getLogger(__name__)


class ExportService:
    """Service for exporting namespace group data."""

    def __init__(
        self,
        def_store_url: str,
        template_store_url: str,
        document_store_url: str,
        api_key: str,
    ):
        self.def_store_url = def_store_url
        self.template_store_url = template_store_url
        self.document_store_url = document_store_url
        self.api_key = api_key

    async def export_namespace_group(
        self,
        group: NamespaceGroup,
        include_files: bool = False,
    ) -> tuple[str, dict[str, int]]:
        """
        Export a namespace group to a zip file.

        Args:
            group: The namespace group to export
            include_files: Whether to include binary file content

        Returns:
            Tuple of (zip_file_path, stats_dict)
        """
        stats = {
            "terminologies": 0,
            "terms": 0,
            "templates": 0,
            "documents": 0,
            "files": 0,
            "registry_entries": 0,
        }

        # Create temp directory for export
        export_dir = tempfile.mkdtemp(prefix=f"wip-export-{group.prefix}-")

        try:
            # Export manifest
            manifest = {
                "version": "1.0",
                "exported_at": datetime.now(timezone.utc).isoformat(),
                "prefix": group.prefix,
                "description": group.description,
                "isolation_mode": group.isolation_mode,
                "allowed_external_refs": group.allowed_external_refs,
                "namespaces": group.get_all_namespaces(),
            }
            with open(os.path.join(export_dir, "manifest.json"), "w") as f:
                json.dump(manifest, f, indent=2)

            # Export registry entries for all namespaces in the group
            registry_path = os.path.join(export_dir, "registry-entries.jsonl")
            async for entry in RegistryEntry.find(
                {"primary_namespace": {"$in": group.get_all_namespaces()}}
            ):
                with open(registry_path, "a") as f:
                    f.write(entry.model_dump_json() + "\n")
                stats["registry_entries"] += 1

            # Export terminologies
            terminologies = await self._fetch_all_paginated(
                f"{self.def_store_url}/api/def-store/terminologies",
                {"namespace": group.terminologies_ns},
            )
            terms_path = os.path.join(export_dir, "terminologies.jsonl")
            for term in terminologies:
                with open(terms_path, "a") as f:
                    f.write(json.dumps(term) + "\n")
                stats["terminologies"] += 1

            # Export terms
            all_terms = []
            for terminology in terminologies:
                terms = await self._fetch_all_paginated(
                    f"{self.def_store_url}/api/def-store/terminologies/{terminology['terminology_id']}/terms",
                    {"namespace": group.terms_ns},
                )
                all_terms.extend(terms)

            terms_path = os.path.join(export_dir, "terms.jsonl")
            for term in all_terms:
                with open(terms_path, "a") as f:
                    f.write(json.dumps(term) + "\n")
                stats["terms"] += 1

            # Export templates
            templates = await self._fetch_all_paginated(
                f"{self.template_store_url}/api/template-store/templates",
                {"namespace": group.templates_ns},
            )
            templates_path = os.path.join(export_dir, "templates.jsonl")
            for template in templates:
                with open(templates_path, "a") as f:
                    f.write(json.dumps(template) + "\n")
                stats["templates"] += 1

            # Export documents
            documents = await self._fetch_all_paginated(
                f"{self.document_store_url}/api/document-store/documents",
                {"namespace": group.documents_ns},
            )
            documents_path = os.path.join(export_dir, "documents.jsonl")
            for doc in documents:
                with open(documents_path, "a") as f:
                    f.write(json.dumps(doc) + "\n")
                stats["documents"] += 1

            # Export file metadata (and optionally content)
            files = await self._fetch_all_paginated(
                f"{self.document_store_url}/api/document-store/files",
                {"namespace": group.files_ns},
            )
            files_path = os.path.join(export_dir, "files.jsonl")
            for file_meta in files:
                with open(files_path, "a") as f:
                    f.write(json.dumps(file_meta) + "\n")
                stats["files"] += 1

            if include_files and files:
                files_dir = os.path.join(export_dir, "files")
                os.makedirs(files_dir, exist_ok=True)
                for file_meta in files:
                    await self._download_file(
                        file_meta["file_id"],
                        os.path.join(files_dir, file_meta["file_id"]),
                    )

            # Update manifest with stats
            manifest["stats"] = stats
            with open(os.path.join(export_dir, "manifest.json"), "w") as f:
                json.dump(manifest, f, indent=2)

            # Create zip file
            zip_filename = f"namespace-export-{group.prefix}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.zip"
            zip_path = os.path.join(tempfile.gettempdir(), zip_filename)

            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for root, _, filenames in os.walk(export_dir):
                    for filename in filenames:
                        file_path = os.path.join(root, filename)
                        arcname = os.path.relpath(file_path, export_dir)
                        zf.write(file_path, arcname)

            return zip_path, stats

        finally:
            # Cleanup temp directory
            import shutil
            shutil.rmtree(export_dir, ignore_errors=True)

    async def _fetch_all_paginated(
        self,
        url: str,
        params: dict[str, Any],
        page_size: int = 100,
    ) -> list[dict[str, Any]]:
        """Fetch all items from a paginated endpoint."""
        items = []
        page = 1

        async with httpx.AsyncClient(timeout=60.0) as client:
            while True:
                response = await client.get(
                    url,
                    params={**params, "page": page, "page_size": page_size},
                    headers={"X-API-Key": self.api_key},
                )

                if response.status_code != 200:
                    logger.warning(f"Failed to fetch from {url}: {response.status_code}")
                    break

                data = response.json()
                page_items = data.get("items", [])
                items.extend(page_items)

                if len(page_items) < page_size:
                    break
                page += 1

        return items

    async def _download_file(self, file_id: str, dest_path: str) -> bool:
        """Download file content to destination path."""
        try:
            async with httpx.AsyncClient(timeout=300.0) as client:
                response = await client.get(
                    f"{self.document_store_url}/api/document-store/files/{file_id}/content",
                    headers={"X-API-Key": self.api_key},
                )
                if response.status_code == 200:
                    with open(dest_path, "wb") as f:
                        f.write(response.content)
                    return True
        except Exception as e:
            logger.error(f"Failed to download file {file_id}: {e}")
        return False

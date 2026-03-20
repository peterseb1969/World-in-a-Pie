"""
Import Service - Import namespace data from exported archive.

Imports data from a namespace export archive into a WIP instance,
optionally remapping to a different namespace prefix.
"""

import json
import logging
import os
import tempfile
import zipfile
from datetime import UTC, datetime
from typing import Any, Literal

import httpx

from ..models.namespace import Namespace

logger = logging.getLogger(__name__)


class ImportService:
    """Service for importing namespace data."""

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

    async def import_namespace(
        self,
        zip_path: str,
        target_prefix: str | None = None,
        mode: Literal["create", "merge", "replace"] = "create",
        imported_by: str | None = None,
    ) -> tuple[Namespace, dict[str, int]]:
        """
        Import a namespace from a zip archive.

        Args:
            zip_path: Path to the export zip file
            target_prefix: Optional new prefix (renames namespace on import)
            mode: Import mode - create (fail if exists), merge (add new), replace (overwrite)
            imported_by: User performing the import

        Returns:
            Tuple of (created/updated Namespace, stats_dict)
        """
        stats = {
            "terminologies": 0,
            "terms": 0,
            "templates": 0,
            "documents": 0,
            "files": 0,
            "skipped": 0,
            "errors": 0,
        }

        # Extract zip to temp directory
        extract_dir = tempfile.mkdtemp(prefix="wip-import-")

        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(extract_dir)

            # Read manifest
            manifest_path = os.path.join(extract_dir, "manifest.json")
            if not os.path.exists(manifest_path):
                raise ValueError("Invalid export archive: missing manifest.json")

            with open(manifest_path) as f:
                manifest = json.load(f)

            source_prefix = manifest["prefix"]
            prefix = target_prefix or source_prefix

            # Check if namespace exists
            existing_ns = await Namespace.find_one({"prefix": prefix})

            if mode == "create" and existing_ns:
                raise ValueError(f"Namespace '{prefix}' already exists. Use mode='merge' or 'replace'.")

            if mode == "replace" and existing_ns:
                # Archive existing data (soft delete)
                existing_ns.status = "archived"
                existing_ns.updated_at = datetime.now(UTC)
                await existing_ns.save()

            # Create or get namespace
            if not existing_ns or mode == "replace":
                namespace = Namespace(
                    prefix=prefix,
                    description=manifest.get("description", f"Imported from {source_prefix}"),
                    isolation_mode=manifest.get("isolation_mode", "open"),
                    allowed_external_refs=manifest.get("allowed_external_refs", []),
                    id_config=manifest.get("id_config", {}),
                    created_by=imported_by,
                )
                await namespace.create()
            else:
                namespace = existing_ns

            # Build namespace mapping (for prefix remapping)
            ns_map = {}
            if target_prefix and target_prefix != source_prefix:
                ns_map[source_prefix] = target_prefix

            # Import terminologies
            terminologies_path = os.path.join(extract_dir, "terminologies.jsonl")
            if os.path.exists(terminologies_path):
                stats["terminologies"] = await self._import_jsonl(
                    terminologies_path,
                    f"{self.def_store_url}/api/def-store/terminologies",
                    ns_map,
                    mode,
                )

            # Import terms (need to be imported per terminology)
            terms_path = os.path.join(extract_dir, "terms.jsonl")
            if os.path.exists(terms_path):
                stats["terms"] = await self._import_terms(
                    terms_path,
                    ns_map,
                    mode,
                )

            # Import templates
            templates_path = os.path.join(extract_dir, "templates.jsonl")
            if os.path.exists(templates_path):
                stats["templates"] = await self._import_jsonl(
                    templates_path,
                    f"{self.template_store_url}/api/template-store/templates",
                    ns_map,
                    mode,
                )

            # Import documents
            documents_path = os.path.join(extract_dir, "documents.jsonl")
            if os.path.exists(documents_path):
                stats["documents"] = await self._import_jsonl(
                    documents_path,
                    f"{self.document_store_url}/api/document-store/documents",
                    ns_map,
                    mode,
                )

            # Import file metadata (content import is more complex, skip for now)
            files_path = os.path.join(extract_dir, "files.jsonl")
            if os.path.exists(files_path):
                with open(files_path) as f:
                    for line in f:
                        if line.strip():
                            stats["files"] += 1

            return namespace, stats

        finally:
            # Cleanup
            import shutil
            shutil.rmtree(extract_dir, ignore_errors=True)

    async def _import_jsonl(
        self,
        file_path: str,
        endpoint: str,
        ns_map: dict[str, str],
        mode: str,
    ) -> int:
        """Import items from a JSONL file to an API endpoint."""
        count = 0

        async with httpx.AsyncClient(timeout=60.0) as client:
            with open(file_path) as f:
                for line in f:
                    if not line.strip():
                        continue

                    try:
                        item = json.loads(line)

                        # Remap namespace if needed
                        if ns_map:
                            item = self._remap_namespaces(item, ns_map)

                        # Remove read-only fields
                        item.pop("created_at", None)
                        item.pop("updated_at", None)

                        response = await client.post(
                            endpoint,
                            json=item,
                            headers={"X-API-Key": self.api_key},
                        )

                        if response.status_code in (200, 201):
                            count += 1
                        elif response.status_code == 409 and mode == "merge":
                            pass
                        else:
                            logger.warning(
                                f"Failed to import item: {response.status_code} - {response.text[:200]}"
                            )

                    except Exception as e:
                        logger.error(f"Error importing item: {e}")

        return count

    async def _import_terms(
        self,
        file_path: str,
        ns_map: dict[str, str],
        mode: str,
    ) -> int:
        """Import terms, grouping by terminology."""
        count = 0
        terms_by_terminology: dict[str, list[dict]] = {}

        # Group terms by terminology_id
        with open(file_path) as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    term = json.loads(line)
                    terminology_id = term.get("terminology_id")
                    if terminology_id:
                        if terminology_id not in terms_by_terminology:
                            terms_by_terminology[terminology_id] = []
                        terms_by_terminology[terminology_id].append(term)
                except Exception as e:
                    logger.error(f"Error parsing term: {e}")

        # Import terms in bulk per terminology
        async with httpx.AsyncClient(timeout=120.0) as client:
            for terminology_id, terms in terms_by_terminology.items():
                # Remap namespaces if needed
                if ns_map:
                    terms = [self._remap_namespaces(t, ns_map) for t in terms]

                # Prepare bulk request
                bulk_items = []
                for term in terms:
                    bulk_items.append({
                        "value": term.get("value"),
                        "label": term.get("label"),
                        "description": term.get("description"),
                        "aliases": term.get("aliases", []),
                        "metadata": term.get("metadata", {}),
                        "created_by": term.get("created_by"),
                    })

                try:
                    response = await client.post(
                        f"{self.def_store_url}/api/def-store/terminologies/{terminology_id}/terms",
                        json=bulk_items,
                        headers={"X-API-Key": self.api_key},
                    )

                    if response.status_code == 200:
                        result = response.json()
                        count += result.get("succeeded", 0)
                    else:
                        logger.warning(
                            f"Failed to import terms for {terminology_id}: {response.status_code}"
                        )
                except Exception as e:
                    logger.error(f"Error importing terms for {terminology_id}: {e}")

        return count

    def _remap_namespaces(self, item: dict[str, Any], ns_map: dict[str, str]) -> dict[str, Any]:
        """Remap namespace fields in an item."""
        result = {}
        for key, value in item.items():
            if key == "namespace" and isinstance(value, str) and value in ns_map:
                result[key] = ns_map[value]
            elif isinstance(value, dict):
                result[key] = self._remap_namespaces(value, ns_map)
            elif isinstance(value, list):
                result[key] = [
                    self._remap_namespaces(v, ns_map) if isinstance(v, dict)
                    else v
                    for v in value
                ]
            else:
                result[key] = value
        return result

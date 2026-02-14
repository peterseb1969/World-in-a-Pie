#!/usr/bin/env python3
"""
Comprehensive seed script for World In a Pie (WIP).

Populates all services (Def-Store, Template Store, Document Store) with
test data for functional testing and performance benchmarking.

Usage:
    python scripts/seed_comprehensive.py [options]

Options:
    --host HOSTNAME       WIP host (default: localhost). Sets all service URLs.
    --via-proxy          Route through Caddy proxy (https://<host>:8443) instead of direct ports
    --profile PROFILE     Data profile: minimal, standard, full, performance (default: standard)
    --services SERVICES   Comma-separated services: all, def-store, template-store, document-store
    --clean              Clean existing data before seeding (USE WITH CAUTION)
    --benchmark          Run performance benchmarks after seeding
    --output FILE        Write benchmark results to JSON file
    --skip-terminologies Skip terminology seeding (use existing)
    --skip-templates     Skip template seeding (use existing)
    --dry-run            Show what would be created without making changes

Examples:
    # Seed everything with standard profile (localhost)
    python scripts/seed_comprehensive.py

    # Seed a remote WIP instance via direct ports (requires port access)
    python scripts/seed_comprehensive.py --host wip-pi.local

    # Seed a remote WIP instance via Caddy proxy (only needs port 8443)
    python scripts/seed_comprehensive.py --host wip-pi.local --via-proxy

    # Seed only def-store with minimal data
    python scripts/seed_comprehensive.py --profile minimal --services def-store

    # Full performance test with benchmarks
    python scripts/seed_comprehensive.py --profile performance --benchmark --output benchmark.json

    # Seed documents only (using existing terminologies and templates)
    python scripts/seed_comprehensive.py --skip-terminologies --skip-templates --services document-store

Environment Variables:
    WIP_HOST              Default host if --host not specified
    WIP_API_KEY           API key for authentication
"""
from __future__ import annotations

import argparse
import os
import sys
import json
import time
from pathlib import Path
from typing import Any
from datetime import datetime

import requests
import urllib3

# Suppress InsecureRequestWarning when using --via-proxy with self-signed certs
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Add seed-data module to path
sys.path.insert(0, str(Path(__file__).parent.parent / "components"))

from seed_data import terminologies, templates, documents, generators, performance


def field_name_to_label(name: str) -> str:
    """Convert field_name to Field Name label."""
    # Replace underscores with spaces and title case
    return name.replace("_", " ").title()


def process_template_fields(fields: list[dict]) -> list[dict]:
    """Process template fields to add labels and ensure validation is not None.

    The Template Store stores validation as null when not specified, which causes
    the Document Store validation service to fail. We ensure validation is always
    an empty dict if not specified.
    """
    processed = []
    for field in fields:
        field_copy = field.copy()
        # Add label if missing
        if "label" not in field_copy:
            field_copy["label"] = field_name_to_label(field_copy["name"])
        # Ensure validation is never None (use empty dict as default)
        if field_copy.get("validation") is None:
            field_copy["validation"] = {}
        processed.append(field_copy)
    return processed


# Default host and API key (can be overridden via environment or arguments)
DEFAULT_HOST = os.environ.get("WIP_HOST", "localhost")


def _resolve_api_key() -> str:
    """Resolve API key from environment or .env file."""
    # 1. Explicit env var takes priority
    key = os.environ.get("WIP_API_KEY")
    if key:
        return key
    # 2. Try reading from .env in project root
    env_file = Path(__file__).parent.parent / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line.startswith("WIP_AUTH_LEGACY_API_KEY=") and not line.startswith("#"):
                val = line.split("=", 1)[1].strip().strip('"').strip("'")
                if val:
                    return val
    # 3. Fallback for dev
    return "dev_master_key_for_testing"


DEFAULT_API_KEY = _resolve_api_key()


def get_service_urls(host: str = DEFAULT_HOST, via_proxy: bool = False) -> dict[str, str]:
    """Build service URLs for the given host.

    Args:
        host: The WIP host (e.g., localhost, wip-pi.local)
        via_proxy: If True, route through Caddy proxy (https://<host>:8443)
                   If False, connect directly to service ports (http://<host>:800x)
    """
    if via_proxy:
        base = f"https://{host}:8443"
        return {
            "registry": base,
            "def-store": base,
            "template-store": base,
            "document-store": base,
        }
    else:
        return {
            "registry": f"http://{host}:8001",
            "def-store": f"http://{host}:8002",
            "template-store": f"http://{host}:8003",
            "document-store": f"http://{host}:8004",
        }


class ServiceClient:
    """Simple HTTP client for WIP services."""

    def __init__(self, base_url: str, api_key: str, verify_ssl: bool = True):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.session = requests.Session()
        self.session.headers.update({"X-API-Key": api_key})
        self.session.verify = verify_ssl

    def health_check(self) -> tuple[bool, str]:
        """Check if service is healthy. Returns (ok, status_message)."""
        try:
            resp = self.session.get(f"{self.base_url}/health", timeout=5)
            if resp.status_code == 200:
                return True, "healthy"
            elif resp.status_code == 401:
                return False, "AUTH REJECTED (wrong API key?)"
            else:
                return False, f"HTTP {resp.status_code}"
        except requests.exceptions.ConnectionError:
            return False, "NOT RESPONDING (connection refused)"
        except Exception as e:
            return False, f"ERROR ({e})"

    def get(self, path: str, params: dict = None) -> dict:
        """HTTP GET request."""
        resp = self.session.get(f"{self.base_url}{path}", params=params, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def post(self, path: str, data: dict | list) -> dict:
        """HTTP POST request."""
        resp = self.session.post(f"{self.base_url}{path}", json=data, timeout=60)
        resp.raise_for_status()
        return resp.json()

    def put(self, path: str, data: dict) -> dict:
        """HTTP PUT request."""
        resp = self.session.put(f"{self.base_url}{path}", json=data, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def delete(self, path: str) -> dict | None:
        """HTTP DELETE request."""
        resp = self.session.delete(f"{self.base_url}{path}", timeout=30)
        resp.raise_for_status()
        if resp.content:
            return resp.json()
        return None


class WIPSeeder:
    """Main seeding orchestrator."""

    def __init__(
        self,
        profile: str = "standard",
        api_key: str = DEFAULT_API_KEY,
        host: str = DEFAULT_HOST,
        via_proxy: bool = False,
        urls: dict[str, str] = None,
        dry_run: bool = False
    ):
        self.profile = profile
        self.api_key = api_key
        self.host = host
        self.via_proxy = via_proxy
        self.urls = urls or get_service_urls(host, via_proxy)
        self.dry_run = dry_run

        # Disable SSL verification for self-signed certs when using proxy
        verify_ssl = not via_proxy

        # Initialize clients
        self.registry = ServiceClient(self.urls["registry"], api_key, verify_ssl)
        self.def_store = ServiceClient(self.urls["def-store"], api_key, verify_ssl)
        self.template_store = ServiceClient(self.urls["template-store"], api_key, verify_ssl)
        self.document_store = ServiceClient(self.urls["document-store"], api_key, verify_ssl)

        # Track created resources
        self.created_terminologies: dict[str, str] = {}  # code -> id
        self.created_templates: dict[str, str] = {}  # code -> id
        self.created_term_ids: dict[str, dict[str, str]] = {}  # terminology_code -> {term_value -> term_id}
        self.created_documents: list[str] = []

    def check_services(self, services: list[str]) -> bool:
        """Check that required services are healthy."""
        service_map = {
            "def-store": self.def_store,
            "template-store": self.template_store,
            "document-store": self.document_store,
        }

        all_healthy = True
        for service in services:
            if service in service_map:
                client = service_map[service]
                ok, status = client.health_check()
                print(f"  {service}: {status}")
                if not ok:
                    all_healthy = False

        return all_healthy

    def seed_terminologies(self) -> dict[str, int]:
        """Seed all terminologies and terms."""
        stats = {"terminologies": 0, "terms": 0, "errors": 0}

        print("\nSeeding terminologies...")
        terminology_defs = terminologies.get_terminology_definitions()

        for term_def in terminology_defs:
            code = term_def["code"]

            if self.dry_run:
                term_count = len(term_def.get("terms", []))
                print(f"  [DRY-RUN] Would create terminology: {code} ({term_count} terms)")
                stats["terminologies"] += 1
                stats["terms"] += term_count
                continue

            try:
                # Check if terminology already exists
                try:
                    existing = self.def_store.get(f"/api/def-store/terminologies/by-code/{code}")
                    terminology_id = existing["terminology_id"]
                    print(f"  {code}: already exists ({terminology_id})")
                    self.created_terminologies[code] = terminology_id
                    stats["terminologies"] += 1

                    # Still need to track term IDs for document generation
                    terms_resp = self.def_store.get(
                        f"/api/def-store/terminologies/{terminology_id}/terms",
                        params={"limit": 500}
                    )
                    self.created_term_ids[code] = {
                        t["value"]: t["term_id"] for t in terms_resp.get("items", [])
                    }
                    stats["terms"] += len(self.created_term_ids[code])
                    continue
                except requests.HTTPError as e:
                    if e.response.status_code != 404:
                        raise

                # Create terminology
                create_data = {
                    "code": code,
                    "name": term_def["name"],
                    "description": term_def.get("description", ""),
                    "case_sensitive": term_def.get("case_sensitive", False),
                    "allow_multiple": term_def.get("allow_multiple", False),
                    "extensible": term_def.get("extensible", False),
                    "metadata": term_def.get("metadata", {}),
                    "created_by": "seed_script"
                }

                result = self.def_store.post("/api/def-store/terminologies", create_data)
                terminology_id = result["terminology_id"]
                self.created_terminologies[code] = terminology_id
                stats["terminologies"] += 1
                print(f"  {code}: created ({terminology_id})")

                # Create terms
                terms = term_def.get("terms", [])
                if terms:
                    self.created_term_ids[code] = {}

                    # Need to handle parent_value references
                    # First pass: create terms without parents
                    terms_without_parents = [t for t in terms if "parent_value" not in t]
                    terms_with_parents = [t for t in terms if "parent_value" in t]

                    # Create non-parent terms in bulk
                    if terms_without_parents:
                        bulk_terms = []
                        for t in terms_without_parents:
                            term_data = {
                                "value": t["value"],
                                "label": t.get("label"),
                                "aliases": t.get("aliases", []),
                                "sort_order": t.get("sort_order"),
                                "metadata": t.get("metadata", {}),
                                "translations": t.get("translations", []),
                                "created_by": "seed_script"
                            }
                            bulk_terms.append(term_data)

                        bulk_result = self.def_store.post(
                            f"/api/def-store/terminologies/{terminology_id}/terms/bulk",
                            {"terms": bulk_terms}
                        )

                        for r in bulk_result.get("results", []):
                            if r.get("term_id"):
                                # Find the term value from the index
                                idx = r.get("index", 0)
                                if idx < len(bulk_terms):
                                    term_value = bulk_terms[idx]["value"]
                                    self.created_term_ids[code][term_value] = r["term_id"]
                                    stats["terms"] += 1

                    # Create terms with parents (need parent term_id)
                    for t in terms_with_parents:
                        parent_value = t["parent_value"]
                        parent_term_id = self.created_term_ids[code].get(parent_value)

                        term_data = {
                            "value": t["value"],
                            "label": t.get("label"),
                            "aliases": t.get("aliases", []),
                            "sort_order": t.get("sort_order"),
                            "parent_term_id": parent_term_id,
                            "metadata": t.get("metadata", {}),
                            "translations": t.get("translations", []),
                            "created_by": "seed_script"
                        }

                        try:
                            result = self.def_store.post(
                                f"/api/def-store/terminologies/{terminology_id}/terms",
                                term_data
                            )
                            self.created_term_ids[code][t["value"]] = result["term_id"]
                            stats["terms"] += 1
                        except Exception as e:
                            print(f"    Error creating term {t['value']}: {e}")
                            stats["errors"] += 1

            except Exception as e:
                print(f"  {code}: ERROR - {e}")
                stats["errors"] += 1

        return stats

    def seed_templates(self) -> dict[str, int]:
        """Seed all templates."""
        stats = {"templates": 0, "errors": 0}

        print("\nSeeding templates...")
        template_defs = templates.get_template_definitions()

        # Templates need to be created in dependency order (base templates first)
        # The get_template_definitions() already returns them in correct order

        for template_def in template_defs:
            code = template_def["code"]

            if self.dry_run:
                print(f"  [DRY-RUN] Would create template: {code}")
                stats["templates"] += 1
                continue

            try:
                # Check if template already exists
                try:
                    existing = self.template_store.get(f"/api/template-store/templates/by-code/{code}")
                    template_id = existing["template_id"]
                    print(f"  {code}: already exists ({template_id})")
                    self.created_templates[code] = template_id
                    stats["templates"] += 1
                    continue
                except requests.HTTPError as e:
                    if e.response.status_code != 404:
                        raise

                # Resolve extends to template_id
                extends_code = template_def.get("extends")
                extends_id = None
                if extends_code:
                    extends_id = self.created_templates.get(extends_code)
                    if not extends_id:
                        # Try to look it up
                        try:
                            parent = self.template_store.get(
                                f"/api/template-store/templates/by-code/{extends_code}"
                            )
                            extends_id = parent["template_id"]
                            self.created_templates[extends_code] = extends_id
                        except Exception:
                            print(f"    Warning: Parent template {extends_code} not found")

                # Create template
                # Process fields to add labels if missing
                fields = process_template_fields(template_def.get("fields", []))

                create_data = {
                    "code": code,
                    "name": template_def["name"],
                    "description": template_def.get("description", ""),
                    "identity_fields": template_def.get("identity_fields", []),
                    "fields": fields,
                    "rules": template_def.get("rules", []),
                    "created_by": "seed_script"
                }

                if extends_id:
                    create_data["extends"] = extends_id

                result = self.template_store.post("/api/template-store/templates", create_data)
                template_id = result["template_id"]
                self.created_templates[code] = template_id
                stats["templates"] += 1
                print(f"  {code}: created ({template_id})")

            except Exception as e:
                print(f"  {code}: ERROR - {e}")
                stats["errors"] += 1

        return stats

    def seed_documents(self) -> dict[str, int]:
        """Seed documents for all templates."""
        stats = {"documents": 0, "errors": 0, "by_template": {}}

        print("\nSeeding documents...")
        doc_counts = documents.get_document_counts(self.profile)

        progress = performance.ProgressReporter(
            total=documents.get_total_documents(self.profile),
            operation="Creating documents"
        )

        for template_code, count in doc_counts.items():
            template_id = self.created_templates.get(template_code)
            if not template_id:
                # Try to look it up
                try:
                    template = self.template_store.get(
                        f"/api/template-store/templates/by-code/{template_code}"
                    )
                    template_id = template["template_id"]
                    self.created_templates[template_code] = template_id
                except Exception:
                    print(f"  {template_code}: SKIPPED (template not found)")
                    continue

            stats["by_template"][template_code] = {"created": 0, "errors": 0}

            if self.dry_run:
                print(f"  [DRY-RUN] Would create {count} {template_code} documents")
                stats["documents"] += count
                progress.update(count)
                continue

            # Generate and create documents in batches
            batch_size = 50  # Smaller batches for reliability
            docs = documents.generate_documents_for_template(template_code, count)

            for i in range(0, len(docs), batch_size):
                batch = docs[i:i + batch_size]
                batch_data = [
                    {"template_id": template_id, "data": doc, "created_by": "seed_script"}
                    for doc in batch
                ]

                try:
                    result = self.document_store.post(
                        "/api/document-store/documents/bulk",
                        {"items": batch_data, "continue_on_error": True}
                    )

                    for r in result.get("results", []):
                        if r.get("document_id"):
                            self.created_documents.append(r["document_id"])
                            stats["documents"] += 1
                            stats["by_template"][template_code]["created"] += 1
                        else:
                            stats["errors"] += 1
                            stats["by_template"][template_code]["errors"] += 1

                    progress.update(len(batch))

                except requests.HTTPError as e:
                    # Try to get detailed error info
                    try:
                        error_detail = e.response.json()
                    except Exception:
                        error_detail = e.response.text

                    print(f"\n  {template_code}: batch error - {e}")
                    if isinstance(error_detail, dict) and "detail" in error_detail:
                        print(f"    Detail: {error_detail['detail'][:200]}...")
                    stats["errors"] += len(batch)
                    stats["by_template"][template_code]["errors"] += len(batch)
                    progress.update(len(batch))
                except Exception as e:
                    print(f"\n  {template_code}: batch error - {e}")
                    stats["errors"] += len(batch)
                    stats["by_template"][template_code]["errors"] += len(batch)
                    progress.update(len(batch))

        progress.complete()

        # Print summary by template
        print("\nDocuments by template:")
        for code, s in stats["by_template"].items():
            print(f"  {code}: {s['created']} created, {s['errors']} errors")

        return stats

    def run_benchmarks(self) -> performance.BenchmarkReport:
        """Run performance benchmarks."""
        print("\nRunning performance benchmarks...")

        # Get counts for report
        try:
            term_resp = self.def_store.get("/api/def-store/terminologies", params={"limit": 1})
            terms_count = term_resp.get("total", 0)
        except Exception:
            terms_count = 0

        report = performance.create_benchmark_report(
            profile=self.profile,
            documents_count=len(self.created_documents),
            templates_count=len(self.created_templates),
            terms_count=terms_count
        )

        # Benchmark document operations
        print("  Benchmarking document creation...")
        doc_count = min(100, len(documents.generate_documents_for_template("PERSON", 100)))

        # Get a template_id for testing
        template_id = self.created_templates.get("PERSON")
        if template_id:
            test_docs = documents.generate_documents_for_template("MINIMAL", 100)

            # Use MINIMAL template for faster creation
            min_template_id = self.created_templates.get("MINIMAL")
            if min_template_id:
                # Measure single creates (sample of 20)
                create_result = performance.BenchmarkResult(
                    operation="create_document",
                    count=20,
                    target_ms=performance.PERFORMANCE_TARGETS["create_document"]
                )

                for i, doc in enumerate(test_docs[:20]):
                    # Make unique ID
                    doc["id"] = f"BENCH-{int(time.time() * 1000)}-{i}"
                    start = time.perf_counter()
                    try:
                        self.document_store.post(
                            "/api/document-store/documents",
                            {"template_id": min_template_id, "data": doc, "created_by": "benchmark"}
                        )
                        elapsed = (time.perf_counter() - start) * 1000
                        create_result.times_ms.append(elapsed)
                    except Exception:
                        create_result.errors += 1

                report.add_result(create_result)

        # Benchmark document reads
        if self.created_documents:
            print("  Benchmarking document reads...")
            read_result = performance.BenchmarkResult(
                operation="get_document",
                count=min(50, len(self.created_documents)),
                target_ms=performance.PERFORMANCE_TARGETS["get_document"]
            )

            for doc_id in self.created_documents[:50]:
                start = time.perf_counter()
                try:
                    self.document_store.get(f"/api/document-store/documents/{doc_id}")
                    elapsed = (time.perf_counter() - start) * 1000
                    read_result.times_ms.append(elapsed)
                except Exception:
                    read_result.errors += 1

            report.add_result(read_result)

        # Benchmark document listing
        print("  Benchmarking document listing...")
        list_result = performance.BenchmarkResult(
            operation="list_documents",
            count=20,
            target_ms=performance.PERFORMANCE_TARGETS["list_documents"]
        )

        for _ in range(20):
            start = time.perf_counter()
            try:
                self.document_store.get("/api/document-store/documents", params={"limit": 50})
                elapsed = (time.perf_counter() - start) * 1000
                list_result.times_ms.append(elapsed)
            except Exception:
                list_result.errors += 1

        report.add_result(list_result)

        # Benchmark term validation
        if self.created_terminologies:
            print("  Benchmarking term validation...")
            validation_result = performance.BenchmarkResult(
                operation="term_validation",
                count=50,
                target_ms=performance.PERFORMANCE_TARGETS["term_validation"]
            )

            # Test with various term values
            test_values = [
                ("SALUTATION", "Mr"),
                ("SALUTATION", "Mr."),  # Alias
                ("SALUTATION", "MR"),  # Code
                ("GENDER", "Male"),
                ("GENDER", "M"),  # Code
                ("COUNTRY", "United States"),
                ("COUNTRY", "USA"),  # Code
            ] * 7  # Repeat to get 49 tests

            for term_code, value in test_values[:50]:
                terminology_id = self.created_terminologies.get(term_code)
                if not terminology_id:
                    continue

                start = time.perf_counter()
                try:
                    self.def_store.post(
                        "/api/def-store/validate",
                        {"terminology_id": terminology_id, "value": value}
                    )
                    elapsed = (time.perf_counter() - start) * 1000
                    validation_result.times_ms.append(elapsed)
                except Exception:
                    validation_result.errors += 1

            report.add_result(validation_result)

        # Benchmark template resolution
        if self.created_templates:
            print("  Benchmarking template resolution...")
            resolution_result = performance.BenchmarkResult(
                operation="template_resolution",
                count=30,
                target_ms=performance.PERFORMANCE_TARGETS["template_resolution"]
            )

            # Test with inheritance templates
            test_templates = ["MANAGER", "EMPLOYEE", "PERSON", "PRODUCT", "ORDER"] * 6

            for template_code in test_templates[:30]:
                template_id = self.created_templates.get(template_code)
                if not template_id:
                    continue

                start = time.perf_counter()
                try:
                    self.template_store.get(f"/api/template-store/templates/{template_id}")
                    elapsed = (time.perf_counter() - start) * 1000
                    resolution_result.times_ms.append(elapsed)
                except Exception:
                    resolution_result.errors += 1

            report.add_result(resolution_result)

        return report


def main():
    parser = argparse.ArgumentParser(
        description="Comprehensive seed script for World In a Pie",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )

    parser.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help="WIP host for remote seeding (default: localhost or WIP_HOST env var)"
    )

    parser.add_argument(
        "--via-proxy",
        action="store_true",
        help="Route requests through Caddy proxy (https://<host>:8443) instead of direct ports"
    )

    parser.add_argument(
        "--profile",
        choices=["minimal", "standard", "full", "performance"],
        default="standard",
        help="Data profile to use (default: standard)"
    )

    parser.add_argument(
        "--services",
        default="all",
        help="Comma-separated services to seed: all, def-store, template-store, document-store"
    )

    parser.add_argument(
        "--clean",
        action="store_true",
        help="Clean existing data before seeding (USE WITH CAUTION)"
    )

    parser.add_argument(
        "--benchmark",
        action="store_true",
        help="Run performance benchmarks after seeding"
    )

    parser.add_argument(
        "--output",
        help="Write benchmark results to JSON file"
    )

    parser.add_argument(
        "--skip-terminologies",
        action="store_true",
        help="Skip terminology seeding (use existing)"
    )

    parser.add_argument(
        "--skip-templates",
        action="store_true",
        help="Skip template seeding (use existing)"
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be created without making changes"
    )

    parser.add_argument(
        "--api-key",
        default=DEFAULT_API_KEY,
        help="API key for authentication"
    )

    args = parser.parse_args()

    # Parse services
    if args.services == "all":
        services = ["def-store", "template-store", "document-store"]
    else:
        services = [s.strip() for s in args.services.split(",")]

    print("=" * 70)
    print("WIP Comprehensive Seed Script")
    print("=" * 70)
    print(f"Host: {args.host}" + (" (via Caddy proxy)" if args.via_proxy else " (direct ports)"))
    print(f"Profile: {args.profile}")
    print(f"Services: {', '.join(services)}")
    print(f"Dry run: {args.dry_run}")

    if args.via_proxy:
        print("\nNote: Using Caddy proxy - SSL verification disabled for self-signed certs")

    if args.clean:
        print("\nWARNING: Clean mode is enabled. This will delete existing data!")
        confirm = input("Type 'yes' to confirm: ")
        if confirm.lower() != "yes":
            print("Aborted.")
            return

    # Initialize seeder
    seeder = WIPSeeder(
        profile=args.profile,
        api_key=args.api_key,
        host=args.host,
        via_proxy=args.via_proxy,
        dry_run=args.dry_run
    )

    # Check services
    print("\nChecking service health...")
    if not seeder.check_services(services):
        print("\nSome services are not responding. Please ensure all services are running:")
        print("  podman-compose -f docker-compose.infra.yml up -d")
        print("  cd components/def-store && podman-compose -f docker-compose.yml up -d --build")
        print("  cd components/template-store && podman-compose -f docker-compose.yml up -d --build")
        print("  cd components/document-store && podman-compose -f docker-compose.yml up -d --build")
        return

    start_time = time.time()
    total_stats = {}

    # Seed terminologies
    if "def-store" in services and not args.skip_terminologies:
        stats = seeder.seed_terminologies()
        total_stats["terminologies"] = stats
    else:
        # Still need to load existing terminology IDs for document generation
        print("\nLoading existing terminologies...")
        try:
            resp = seeder.def_store.get("/api/def-store/terminologies", params={"limit": 100})
            for t in resp.get("items", []):
                seeder.created_terminologies[t["code"]] = t["terminology_id"]
            print(f"  Loaded {len(seeder.created_terminologies)} terminologies")
        except Exception as e:
            print(f"  Warning: Could not load terminologies: {e}")

    # Seed templates
    if "template-store" in services and not args.skip_templates:
        stats = seeder.seed_templates()
        total_stats["templates"] = stats
    else:
        # Still need to load existing template IDs for document generation
        print("\nLoading existing templates...")
        try:
            resp = seeder.template_store.get("/api/template-store/templates", params={"limit": 100})
            for t in resp.get("items", []):
                seeder.created_templates[t["code"]] = t["template_id"]
            print(f"  Loaded {len(seeder.created_templates)} templates")
        except Exception as e:
            print(f"  Warning: Could not load templates: {e}")

    # Seed documents
    if "document-store" in services:
        stats = seeder.seed_documents()
        total_stats["documents"] = stats

    elapsed = time.time() - start_time

    # Summary
    print("\n" + "=" * 70)
    print("SEEDING COMPLETE")
    print("=" * 70)
    print(f"Time: {elapsed:.1f} seconds")

    if "terminologies" in total_stats:
        s = total_stats["terminologies"]
        print(f"Terminologies: {s['terminologies']} created, {s['terms']} terms, {s['errors']} errors")

    if "templates" in total_stats:
        s = total_stats["templates"]
        print(f"Templates: {s['templates']} created, {s['errors']} errors")

    if "documents" in total_stats:
        s = total_stats["documents"]
        print(f"Documents: {s['documents']} created, {s['errors']} errors")

    # Run benchmarks if requested
    if args.benchmark and not args.dry_run:
        report = seeder.run_benchmarks()
        report.print_report()

        if args.output:
            with open(args.output, "w") as f:
                f.write(report.to_json())
            print(f"\nBenchmark results saved to: {args.output}")

    print("\n" + "=" * 70)
    print("You can now explore the data at:")
    print(f"  Def-Store API:      http://{args.host}:8002/docs")
    print(f"  Template Store API: http://{args.host}:8003/docs")
    print(f"  Document Store API: http://{args.host}:8004/docs")
    print(f"  MongoDB Express:    http://{args.host}:8081")
    print(f"  WIP Console:        https://{args.host}:8443")
    print("=" * 70)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Comprehensive seed script for World In a Pie (WIP).  v1.3

Populates all services (Def-Store, Template Store, Document Store) with
test data for functional testing and performance benchmarking.

v1.3 changes:
- Always routes through the Caddy proxy at https://<host>:8443 (or
  --port). The wip-deploy v2 deployment does not publish service ports
  to the host, so the previous "direct ports" mode no longer works.
- API key is resolved (in order) from --api-key, --api-key-file,
  WIP_API_KEY, or ~/.wip-deploy/<deployment>/secrets/api-key.
  No fallback to repo-root .env, no hardcoded dev key.

Usage:
    python scripts/seed_comprehensive.py [options]

Options:
    --host HOSTNAME       WIP host (default: localhost or WIP_HOST env)
    --port PORT           Proxy port (default: 8443; use 443 for K8s Ingress)
    --api-key KEY         API key (overrides all auto-discovery)
    --api-key-file PATH   Read the key from this file (single line)
    --deployment NAME     Pick a wip-deploy deployment under ~/.wip-deploy/
                          when more than one exists
    --profile PROFILE     Data profile: minimal, standard, full, performance
    --services SERVICES   Comma-separated: all, def-store, template-store, document-store
    --clean               Clean existing data before seeding (USE WITH CAUTION)
    --benchmark           Run performance benchmarks after seeding
    --output FILE         Write benchmark results to JSON file
    --namespace PREFIX    Namespace prefix for data isolation (default: seed)
    --time-limit SECS     Stop document seeding after SECS seconds
    --skip-terminologies  Skip terminology seeding (use existing)
    --skip-templates      Skip template seeding (use existing)
    --dry-run             Show what would be created without making changes

API key resolution (first hit wins):
  1. --api-key KEY
  2. --api-key-file PATH
  3. WIP_API_KEY env var
  4. ~/.wip-deploy/<deployment>/secrets/api-key
     - If --deployment not given and exactly one deployment dir exists,
       it is auto-selected.
     - If --host is non-localhost, auto-discovery is skipped (the local
       ~/.wip-deploy/ does not describe the remote host).

Examples:
    # Local seed against the wip-dev-local deployment (most common)
    python scripts/seed_comprehensive.py

    # Quick 30-second performance test
    python scripts/seed_comprehensive.py --profile performance --time-limit 30

    # Remote WIP instance — must supply the key explicitly
    python scripts/seed_comprehensive.py --host wip-pi.local \\
        --api-key-file /secure/path/wip-pi.api-key

    # Pick a specific local deployment when several exist
    python scripts/seed_comprehensive.py --deployment wip-staging-local

    # Seed only def-store with minimal data
    python scripts/seed_comprehensive.py --profile minimal --services def-store

    # K8s Ingress on port 443
    python scripts/seed_comprehensive.py --host wip.example.com --port 443

Environment Variables:
    WIP_HOST              Default host if --host not specified
    WIP_API_KEY           API key (used if --api-key/--api-key-file omitted)
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Any

import requests
import urllib3

# Suppress InsecureRequestWarning when using --via-proxy with self-signed certs
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Add seed-data module to path
sys.path.insert(0, str(Path(__file__).parent.parent / "components"))

from seed_data import documents, performance, templates, terminologies  # noqa: E402  (sys.path mutated above)


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


# Default host (can be overridden via WIP_HOST env or --host arg)
DEFAULT_HOST = os.environ.get("WIP_HOST", "localhost")

# Root for wip-deploy generated deployments
WIP_DEPLOY_ROOT = Path.home() / ".wip-deploy"


class ApiKeyResolutionError(RuntimeError):
    """Raised when no API key can be resolved from any source."""


def resolve_api_key(
    *,
    cli_key: str | None,
    cli_key_file: str | None,
    deployment: str | None,
    host: str,
) -> tuple[str, str]:
    """Resolve the API key. Returns (key, source-description).

    Resolution order:
      1. --api-key
      2. --api-key-file
      3. WIP_API_KEY env var
      4. ~/.wip-deploy/<deployment>/secrets/api-key
         - Auto-pick the deployment if exactly one dir exists.
         - Skipped when host != localhost (local layout cannot describe
           a remote host).

    Raises ApiKeyResolutionError with a multi-line, actionable message
    when no source yields a key.
    """
    if cli_key:
        return cli_key, "--api-key"

    if cli_key_file:
        p = Path(cli_key_file).expanduser()
        if not p.is_file():
            raise ApiKeyResolutionError(f"--api-key-file: not a file: {p}")
        key = p.read_text().strip()
        if not key:
            raise ApiKeyResolutionError(f"--api-key-file: file is empty: {p}")
        return key, f"--api-key-file ({p})"

    env_key = os.environ.get("WIP_API_KEY")
    if env_key:
        return env_key, "WIP_API_KEY env var"

    is_local = host in ("localhost", "127.0.0.1", "::1")
    if is_local and WIP_DEPLOY_ROOT.is_dir():
        deployments = sorted(
            d.name for d in WIP_DEPLOY_ROOT.iterdir()
            if d.is_dir() and (d / "secrets" / "api-key").is_file()
        )
        if deployment:
            if deployment not in deployments:
                raise ApiKeyResolutionError(
                    f"--deployment '{deployment}' not found under {WIP_DEPLOY_ROOT}.\n"
                    f"Available: {', '.join(deployments) if deployments else '(none)'}"
                )
            picked = deployment
        elif len(deployments) == 1:
            picked = deployments[0]
        elif len(deployments) > 1:
            raise ApiKeyResolutionError(
                f"Multiple deployments under {WIP_DEPLOY_ROOT}: {', '.join(deployments)}.\n"
                f"Pass --deployment <name> or --api-key / --api-key-file."
            )
        else:
            picked = None

        if picked:
            key_file = WIP_DEPLOY_ROOT / picked / "secrets" / "api-key"
            key = key_file.read_text().strip()
            if not key:
                raise ApiKeyResolutionError(f"{key_file} is empty")
            return key, f"~/.wip-deploy/{picked}/secrets/api-key"

    # Nothing matched.
    if not is_local:
        hint = (
            f"Host '{host}' is not localhost — auto-discovery skipped.\n"
            f"Pass --api-key <KEY> or --api-key-file <PATH> for remote hosts."
        )
    else:
        hint = (
            f"No deployment found under {WIP_DEPLOY_ROOT} (or no secrets/api-key file in any).\n"
            f"Pass --api-key, --api-key-file, or set WIP_API_KEY."
        )
    raise ApiKeyResolutionError(
        "Could not resolve a WIP API key.\n"
        "Tried (in order): --api-key, --api-key-file, WIP_API_KEY env, "
        "~/.wip-deploy/<deployment>/secrets/api-key.\n" + hint
    )


def get_service_urls(host: str = DEFAULT_HOST, proxy_port: int = 8443) -> dict[str, str]:
    """Build service URLs for the given host.

    All requests go through the Caddy proxy at https://<host>:<port>/api/<svc>/...
    The wip-deploy v2 deployment does not publish service ports to the
    host, so direct-port access is no longer supported.

    Args:
        host: The WIP host (e.g., localhost, wip-pi.local)
        proxy_port: Caddy port (default 8443, use 443 for K8s Ingress)
    """
    port_suffix = f":{proxy_port}" if proxy_port != 443 else ""
    base = f"https://{host}{port_suffix}"
    return {
        "registry": base,
        "def-store": base,
        "template-store": base,
        "document-store": base,
    }


class ServiceClient:
    """Simple HTTP client for WIP services."""

    def __init__(self, base_url: str, api_key: str, verify_ssl: bool = True):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.session = requests.Session()
        self.session.headers.update({"X-API-Key": api_key})
        self.session.verify = verify_ssl

    def health_check(self, health_path: str = "/health") -> tuple[bool, str]:
        """Check if service is healthy. Returns (ok, status_message)."""
        try:
            resp = self.session.get(f"{self.base_url}{health_path}", timeout=5)
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

    def get(self, path: str, params: dict | None = None) -> dict:
        """HTTP GET request."""
        start = time.perf_counter()
        resp = self.session.get(f"{self.base_url}{path}", params=params, timeout=30)
        elapsed = (time.perf_counter() - start) * 1000
        self._record_call("GET", path, elapsed, resp.status_code)
        resp.raise_for_status()
        return resp.json()

    def post(self, path: str, data: dict | list, params: dict | None = None) -> dict:
        """HTTP POST request."""
        start = time.perf_counter()
        resp = self.session.post(f"{self.base_url}{path}", json=data, params=params, timeout=60)
        elapsed = (time.perf_counter() - start) * 1000
        self._record_call("POST", path, elapsed, resp.status_code)
        resp.raise_for_status()
        return resp.json()

    def put(self, path: str, data: dict | list, params: dict | None = None) -> dict:
        """HTTP PUT request."""
        start = time.perf_counter()
        resp = self.session.put(f"{self.base_url}{path}", json=data, params=params, timeout=30)
        elapsed = (time.perf_counter() - start) * 1000
        self._record_call("PUT", path, elapsed, resp.status_code)
        resp.raise_for_status()
        return resp.json()

    def delete(self, path: str) -> dict | None:
        """HTTP DELETE request."""
        start = time.perf_counter()
        resp = self.session.delete(f"{self.base_url}{path}", timeout=30)
        elapsed = (time.perf_counter() - start) * 1000
        self._record_call("DELETE", path, elapsed, resp.status_code)
        resp.raise_for_status()
        if resp.content:
            return resp.json()
        return None

    def _record_call(self, method: str, path: str, elapsed_ms: float, status: int):
        """Record an HTTP call for timing analysis."""
        if not hasattr(self, '_call_log'):
            self._call_log = []
            self._call_stats = {}
        # Normalize path (strip IDs for grouping)
        import re
        normalized = re.sub(
            r'/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}',
            '/{id}', path
        )
        key = f"{method} {normalized}"
        if key not in self._call_stats:
            self._call_stats[key] = {"count": 0, "total_ms": 0, "max_ms": 0}
        self._call_stats[key]["count"] += 1
        self._call_stats[key]["total_ms"] += elapsed_ms
        self._call_stats[key]["max_ms"] = max(self._call_stats[key]["max_ms"], elapsed_ms)

    def print_timing_report(self, label: str):
        """Print HTTP call timing summary."""
        stats = getattr(self, '_call_stats', {})
        if not stats:
            return
        total_calls = sum(s["count"] for s in stats.values())
        total_ms = sum(s["total_ms"] for s in stats.values())
        print(f"\n  {label} HTTP Timing ({total_calls} calls, {total_ms:.0f}ms total):")
        for key in sorted(stats, key=lambda k: stats[k]["total_ms"], reverse=True):
            s = stats[key]
            avg = s["total_ms"] / s["count"]
            print(f"    {key:60s}  n={s['count']:4d}  total={s['total_ms']:8.0f}ms  avg={avg:6.1f}ms  max={s['max_ms']:6.0f}ms")


class WIPSeeder:
    """Main seeding orchestrator."""

    def __init__(
        self,
        profile: str = "standard",
        api_key: str = "",
        host: str = DEFAULT_HOST,
        proxy_port: int = 8443,
        urls: dict[str, str] | None = None,
        dry_run: bool = False,
        namespace: str = "wip",
        time_limit: float | None = None,
    ):
        if not api_key:
            raise ValueError("WIPSeeder: api_key is required")
        self.profile = profile
        self.api_key = api_key
        self.host = host
        self.urls = urls or get_service_urls(host, proxy_port)
        self.dry_run = dry_run
        self.namespace = namespace
        self._custom_ns = namespace != "wip"
        self.time_limit = time_limit

        # All traffic goes through Caddy on HTTPS with a self-signed cert
        # in dev — disable SSL verification.
        verify_ssl = False

        # Initialize clients
        self.registry = ServiceClient(self.urls["registry"], api_key, verify_ssl)
        self.def_store = ServiceClient(self.urls["def-store"], api_key, verify_ssl)
        self.template_store = ServiceClient(self.urls["template-store"], api_key, verify_ssl)
        self.document_store = ServiceClient(self.urls["document-store"], api_key, verify_ssl)

        # Track created resources
        self.created_terminologies: dict[str, str] = {}  # value -> id
        self.created_templates: dict[str, str] = {}  # value -> id
        # Per-template document_id list, populated during seed_documents.
        # Used by seed_relationship_documents to pick endpoint refs.
        self._docs_by_template: dict[str, list[str]] = {}
        self.created_term_ids: dict[str, dict[str, str]] = {}  # terminology_value -> {term_value -> term_id}
        self.created_documents: list[str] = []

    def check_services(self, services: list[str]) -> bool:
        """Check that required services are healthy.

        Uses the api-prefixed /api/<svc>/health endpoint, which is the
        only health path Caddy routes through.
        """
        service_map = {
            "def-store": self.def_store,
            "template-store": self.template_store,
            "document-store": self.document_store,
        }
        health_paths = {
            "def-store": "/api/def-store/health",
            "template-store": "/api/template-store/health",
            "document-store": "/api/document-store/health",
        }

        all_healthy = True
        for service in services:
            if service in service_map:
                client = service_map[service]
                ok, status = client.health_check(health_paths[service])
                print(f"  {service}: {status}")
                if not ok:
                    all_healthy = False

        return all_healthy

    def _ns_params(self, **extra: Any) -> dict:
        """Return params dict with namespace."""
        params = {"namespace": self.namespace}
        params.update(extra)
        return params

    def initialize_namespace(self) -> None:
        """Ensure namespace and its ID pools exist in the registry."""
        if self.namespace == "wip":
            # Default namespace uses the dedicated init endpoint
            try:
                self.registry.post("/api/registry/namespaces/initialize-wip", {})
                print(f"  Namespace '{self.namespace}' initialized")
            except requests.HTTPError as e:
                if e.response.status_code == 409:
                    print(f"  Namespace '{self.namespace}' already exists")
                else:
                    raise
        else:
            # Custom namespace — create via generic endpoint
            try:
                self.registry.post("/api/registry/namespaces", {
                    "prefix": self.namespace,
                    "description": f"Seed data namespace ({self.namespace})",
                    "isolation_mode": "open",
                    "deletion_mode": "full",
                    "created_by": "seed_script",
                })
                print(f"  Namespace '{self.namespace}' created")
            except requests.HTTPError as e:
                if e.response.status_code == 409:
                    print(f"  Namespace '{self.namespace}' already exists")
                else:
                    raise

    def seed_terminologies(self) -> dict[str, int]:
        """Seed all terminologies and terms."""
        stats = {"terminologies": 0, "terms": 0, "errors": 0}

        print("\nSeeding terminologies...")
        terminology_defs = terminologies.get_terminology_definitions()

        for term_def in terminology_defs:
            value = term_def["value"]

            if self.dry_run:
                term_count = len(term_def.get("terms", []))
                print(f"  [DRY-RUN] Would create terminology: {value} ({term_count} terms)")
                stats["terminologies"] += 1
                stats["terms"] += term_count
                continue

            try:
                # Check if terminology already exists
                try:
                    existing = self.def_store.get(
                        f"/api/def-store/terminologies/by-value/{value}",
                        params=self._ns_params(),
                    )
                    terminology_id = existing["terminology_id"]
                    print(f"  {value}: already exists ({terminology_id})")
                    self.created_terminologies[value] = terminology_id
                    stats["terminologies"] += 1

                    # Still need to track term IDs for document generation
                    terms_resp = self.def_store.get(
                        f"/api/def-store/terminologies/{terminology_id}/terms",
                        params=self._ns_params(page_size=100),
                    )
                    self.created_term_ids[value] = {
                        t["value"]: t["term_id"] for t in terms_resp.get("items", [])
                    }
                    stats["terms"] += len(self.created_term_ids[value])
                    continue
                except requests.HTTPError as e:
                    if e.response.status_code != 404:
                        raise

                # Create terminology
                create_data = {
                    "value": value,
                    "label": term_def["label"],
                    "description": term_def.get("description", ""),
                    "namespace": self.namespace,
                    "case_sensitive": term_def.get("case_sensitive", False),
                    "allow_multiple": term_def.get("allow_multiple", False),
                    "extensible": term_def.get("extensible", False),
                    "metadata": term_def.get("metadata", {}),
                    "created_by": "seed_script"
                }

                result = self.def_store.post(
                    "/api/def-store/terminologies",
                    [create_data],
                )
                terminology_id = result["results"][0]["id"]
                self.created_terminologies[value] = terminology_id
                stats["terminologies"] += 1
                print(f"  {value}: created ({terminology_id})")

                # Create terms
                terms = term_def.get("terms", [])
                if terms:
                    self.created_term_ids[value] = {}

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
                            f"/api/def-store/terminologies/{terminology_id}/terms",
                            bulk_terms,
                            params={"namespace": self.namespace},
                        )

                        for r in bulk_result.get("results", []):
                            if r.get("id"):
                                # Find the term value from the index
                                idx = r.get("index", 0)
                                if idx < len(bulk_terms):
                                    term_value = bulk_terms[idx]["value"]
                                    self.created_term_ids[value][term_value] = r["id"]
                                    stats["terms"] += 1

                    # Create terms with parents (need parent term_id)
                    for t in terms_with_parents:
                        parent_value = t["parent_value"]
                        parent_term_id = self.created_term_ids[value].get(parent_value)

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
                                [term_data],
                                params={"namespace": self.namespace},
                            )
                            self.created_term_ids[value][t["value"]] = result["results"][0]["id"]
                            stats["terms"] += 1
                        except Exception as e:
                            print(f"    Error creating term {t['value']}: {e}")
                            stats["errors"] += 1

            except Exception as e:
                print(f"  {value}: ERROR - {e}")
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
            value = template_def["value"]

            if self.dry_run:
                print(f"  [DRY-RUN] Would create template: {value}")
                stats["templates"] += 1
                continue

            try:
                # Check if template already exists
                try:
                    existing = self.template_store.get(
                        f"/api/template-store/templates/by-value/{value}",
                        params=self._ns_params(),
                    )
                    template_id = existing["template_id"]
                    print(f"  {value}: already exists ({template_id})")
                    self.created_templates[value] = template_id
                    stats["templates"] += 1
                    continue
                except requests.HTTPError as e:
                    if e.response.status_code != 404:
                        raise

                # Resolve extends to template_id
                extends_value = template_def.get("extends")
                extends_id = None
                if extends_value:
                    extends_id = self.created_templates.get(extends_value)
                    if not extends_id:
                        # Try to look it up
                        try:
                            parent = self.template_store.get(
                                f"/api/template-store/templates/by-value/{extends_value}",
                                params=self._ns_params(),
                            )
                            extends_id = parent["template_id"]
                            self.created_templates[extends_value] = extends_id
                        except Exception:
                            print(f"    Warning: Parent template {extends_value} not found")

                # Create template
                # Process fields to add labels if missing
                fields = process_template_fields(template_def.get("fields", []))

                create_data = {
                    "value": value,
                    "label": template_def["label"],
                    "description": template_def.get("description", ""),
                    "namespace": self.namespace,
                    "identity_fields": template_def.get("identity_fields", []),
                    "fields": fields,
                    "rules": template_def.get("rules", []),
                    "created_by": "seed_script"
                }

                # Forward relationship-template metadata when present.
                # Immutable after create; safe to omit for entity templates.
                if template_def.get("usage") and template_def["usage"] != "entity":
                    create_data["usage"] = template_def["usage"]
                if template_def.get("source_templates"):
                    create_data["source_templates"] = template_def["source_templates"]
                if template_def.get("target_templates"):
                    create_data["target_templates"] = template_def["target_templates"]
                if "versioned" in template_def and template_def["versioned"] is not True:
                    create_data["versioned"] = template_def["versioned"]

                if extends_id:
                    create_data["extends"] = extends_id

                result = self.template_store.post(
                    "/api/template-store/templates",
                    [create_data],
                )
                template_id = result["results"][0]["id"]
                self.created_templates[value] = template_id
                stats["templates"] += 1
                print(f"  {value}: created ({template_id})")

            except Exception as e:
                print(f"  {value}: ERROR - {e}")
                stats["errors"] += 1

        return stats

    def seed_documents(self) -> dict[str, int]:
        """Seed documents for all templates."""
        stats = {"documents": 0, "errors": 0, "by_template": {}, "time_limited": False}

        print("\nSeeding documents...")
        doc_counts = documents.get_document_counts(self.profile)

        total_planned = documents.get_total_documents(self.profile)
        if self.time_limit:
            print(f"  Time limit: {self.time_limit:.0f}s (will stop after limit is reached)")

        progress = performance.ProgressReporter(
            total=total_planned,
            operation="Creating documents"
        )

        doc_start_time = time.perf_counter()
        time_exceeded = False

        for template_value, count in doc_counts.items():
            if time_exceeded:
                break

            template_id = self.created_templates.get(template_value)
            if not template_id:
                # Try to look it up
                try:
                    template = self.template_store.get(
                        f"/api/template-store/templates/by-value/{template_value}",
                        params=self._ns_params(),
                    )
                    template_id = template["template_id"]
                    self.created_templates[template_value] = template_id
                except Exception:
                    print(f"  {template_value}: SKIPPED (template not found)")
                    continue

            stats["by_template"][template_value] = {"created": 0, "errors": 0}

            if self.dry_run:
                print(f"  [DRY-RUN] Would create {count} {template_value} documents")
                stats["documents"] += count
                progress.update(count)
                continue

            # Generate and create documents in batches
            batch_size = 50  # Smaller batches for reliability
            docs = documents.generate_documents_for_template(template_value, count)

            for i in range(0, len(docs), batch_size):
                # Check time limit before starting next batch
                if self.time_limit and (time.perf_counter() - doc_start_time) >= self.time_limit:
                    time_exceeded = True
                    break

                batch = docs[i:i + batch_size]
                batch_data = [
                    {"template_id": template_id, "namespace": self.namespace, "data": doc, "created_by": "seed_script"}
                    for doc in batch
                ]

                try:
                    result = self.document_store.post(
                        "/api/document-store/documents",
                        batch_data,
                    )

                    # Log server-side timing if available
                    server_timing = result.get("timing")
                    if server_timing:
                        if not hasattr(self, '_bulk_timings'):
                            self._bulk_timings = []
                        self._bulk_timings.append({
                            "template": template_value,
                            "batch_size": len(batch),
                            **server_timing,
                        })

                    for r in result.get("results", []):
                        if r.get("document_id"):
                            self.created_documents.append(r["document_id"])
                            self._docs_by_template.setdefault(template_value, []).append(r["document_id"])
                            stats["documents"] += 1
                            stats["by_template"][template_value]["created"] += 1
                        else:
                            stats["errors"] += 1
                            stats["by_template"][template_value]["errors"] += 1

                    progress.update(len(batch))

                except requests.HTTPError as e:
                    # Try to get detailed error info
                    try:
                        error_detail = e.response.json()
                    except Exception:
                        error_detail = e.response.text

                    print(f"\n  {template_value}: batch error - {e}")
                    if isinstance(error_detail, dict) and "detail" in error_detail:
                        print(f"    Detail: {error_detail['detail'][:200]}...")
                    stats["errors"] += len(batch)
                    stats["by_template"][template_value]["errors"] += len(batch)
                    progress.update(len(batch))
                except Exception as e:
                    print(f"\n  {template_value}: batch error - {e}")
                    stats["errors"] += len(batch)
                    stats["by_template"][template_value]["errors"] += len(batch)
                    progress.update(len(batch))

        if time_exceeded:
            stats["time_limited"] = True
            elapsed_docs = time.perf_counter() - doc_start_time
            rate = stats["documents"] / elapsed_docs if elapsed_docs > 0 else 0
            progress.complete_time_limited(stats["documents"], elapsed_docs)
            print(f"\n  Time limit reached: {stats['documents']} docs in {elapsed_docs:.1f}s ({rate:.1f} docs/sec)")
        else:
            progress.complete()

        # Print summary by template
        print("\nDocuments by template:")
        for code, s in stats["by_template"].items():
            print(f"  {code}: {s['created']} created, {s['errors']} errors")

        return stats

    def seed_template_versions(self) -> dict[str, int]:
        """Create v2 of selected templates to exercise multi-version operation."""
        stats = {"versions_created": 0, "errors": 0}

        print("\nSeeding template versions...")

        # Templates to version and the field to add in v2
        version_targets = {
            "PERSON": {
                "name": "nickname",
                "type": "string",
                "mandatory": False,
                "label": "Nickname",
                "validation": {"max_length": 100},
            },
            "PRODUCT": {
                "name": "warranty_months",
                "type": "integer",
                "mandatory": False,
                "label": "Warranty (months)",
                "validation": {"minimum": 0, "maximum": 120},
            },
        }

        for template_value, new_field in version_targets.items():
            template_id = self.created_templates.get(template_value)
            if not template_id:
                print(f"  {template_value}: SKIPPED (template not found)")
                continue

            if self.dry_run:
                print(f"  [DRY-RUN] Would create v2 of {template_value}")
                stats["versions_created"] += 1
                continue

            try:
                # Fetch current template to get its fields
                current = self.template_store.get(
                    f"/api/template-store/templates/{template_id}",
                )

                # Build updated field list (current fields + new field)
                updated_fields = list(current.get("fields", []))
                # Strip resolved/computed metadata that the API doesn't accept back
                strip_keys = [
                    "terminology_label", "template_ref_label",
                    "inherited", "inherited_from",
                ]
                for f in updated_fields:
                    for k in strip_keys:
                        f.pop(k, None)
                    # Strip null values — the API doesn't need them
                    for k in list(f.keys()):
                        if f[k] is None:
                            del f[k]
                    # Ensure validation is not all-None (simplify to empty or omit)
                    v = f.get("validation")
                    if isinstance(v, dict) and all(val is None for val in v.values()):
                        del f["validation"]
                updated_fields.append(new_field)

                result = self.template_store.put(
                    "/api/template-store/templates",
                    [{
                        "template_id": template_id,
                        "fields": updated_fields,
                        "updated_by": "seed_script",
                    }],
                )

                r = result["results"][0]
                new_version = r.get("version", "?")
                is_new = r.get("is_new_version", False)
                if is_new:
                    stats["versions_created"] += 1
                    print(f"  {template_value}: created v{new_version} (+{new_field['name']})")
                else:
                    print(f"  {template_value}: no changes detected (v{new_version})")

            except Exception as e:
                print(f"  {template_value}: ERROR - {e}")
                stats["errors"] += 1

        return stats

    def seed_versioning_tests(self) -> dict[str, int]:
        """Submit documents with same identity multiple times to exercise upsert versioning."""
        stats = {"versions_created": 0, "errors": 0}

        print("\nSeeding versioning test documents...")

        # Only run if PERSON template exists (it has identity_fields: ["email"])
        person_template_id = self.created_templates.get("PERSON")
        if not person_template_id:
            print("  SKIPPED (PERSON template not found)")
            return stats

        if self.dry_run:
            print("  [DRY-RUN] Would create 3 versions of a PERSON document")
            print("  [DRY-RUN] Would create 2 versions of an EMPLOYEE document")
            stats["versions_created"] = 5
            return stats

        # Submit same PERSON (same email) 3 times with different data
        # This should create document_id v1, then v2, then v3
        versions = [
            {"first_name": "Version", "last_name": "Test", "email": "version.test@example.com",
             "birth_date": "1990-01-15", "notes": "Version 1 - Initial creation"},
            {"first_name": "Version", "last_name": "Test-Updated", "email": "version.test@example.com",
             "birth_date": "1990-01-15", "notes": "Version 2 - Name updated"},
            {"first_name": "Version", "last_name": "Test-Final", "email": "version.test@example.com",
             "birth_date": "1990-01-15", "notes": "Version 3 - Final update", "active": False},
        ]

        prev_doc_id = None
        for i, data in enumerate(versions, 1):
            try:
                result = self.document_store.post(
                    "/api/document-store/documents",
                    [{
                        "template_id": person_template_id,
                        "namespace": self.namespace,
                        "data": data,
                        "created_by": "seed_script",
                    }],
                )
                r = result["results"][0]
                doc_id = r.get("document_id", "?")
                version = r.get("version", "?")

                if prev_doc_id and doc_id == prev_doc_id:
                    print(f"  PERSON v{version}: upsert OK (same document_id {doc_id})")
                elif prev_doc_id:
                    print(f"  PERSON v{version}: WARNING - new document_id {doc_id} (expected {prev_doc_id})")
                else:
                    print(f"  PERSON v{version}: created {doc_id}")

                prev_doc_id = doc_id
                stats["versions_created"] += 1

            except Exception as e:
                print(f"  PERSON version {i}: ERROR - {e}")
                stats["errors"] += 1

        # Also test EMPLOYEE if available
        employee_template_id = self.created_templates.get("EMPLOYEE")
        if employee_template_id:
            emp_versions = [
                {"employee_id": "EMP-999999", "first_name": "Emp", "last_name": "Version",
                 "email": "emp.version@example.com", "birth_date": "1985-06-15",
                 "hire_date": "2020-01-15", "department": "Human Resources",
                 "job_title": "Analyst", "employment_type": "Full-time",
                 "notes": "Employee v1"},
                {"employee_id": "EMP-999999", "first_name": "Emp", "last_name": "Version",
                 "email": "emp.version@example.com", "birth_date": "1985-06-15",
                 "hire_date": "2020-01-15", "department": "Human Resources",
                 "job_title": "Senior Analyst", "employment_type": "Full-time",
                 "notes": "Employee v2 - Promoted"},
            ]

            prev_doc_id = None
            for i, data in enumerate(emp_versions, 1):
                try:
                    result = self.document_store.post(
                        "/api/document-store/documents",
                        [{
                            "template_id": employee_template_id,
                            "namespace": self.namespace,
                            "data": data,
                            "created_by": "seed_script",
                        }],
                    )
                    r = result["results"][0]
                    doc_id = r.get("document_id", "?")
                    version = r.get("version", "?")

                    if prev_doc_id and doc_id == prev_doc_id:
                        print(f"  EMPLOYEE v{version}: upsert OK (same document_id {doc_id})")
                    elif prev_doc_id:
                        print(f"  EMPLOYEE v{version}: WARNING - new document_id {doc_id} (expected {prev_doc_id})")
                    else:
                        print(f"  EMPLOYEE v{version}: created {doc_id}")

                    prev_doc_id = doc_id
                    stats["versions_created"] += 1

                except Exception as e:
                    print(f"  EMPLOYEE version {i}: ERROR - {e}")
                    stats["errors"] += 1

        return stats

    def seed_relationship_documents(self) -> dict[str, int]:
        """Seed relationship documents (usage='relationship' templates).

        Picks endpoint document_ids from self._docs_by_template (populated
        by seed_documents) and posts edges for each profile-defined count.
        Skipped if no entity documents have been created yet.
        """
        stats = {"documents": 0, "errors": 0, "by_template": {}, "skipped": []}

        print("\nSeeding relationship documents...")
        rel_counts = documents.get_relationship_counts(self.profile)

        # Edge generators — produce a list of (data) dicts given lists of
        # source/target document_ids and a count. Each edge picks a
        # random source/target pair plus type-appropriate edge properties.
        def _gen_employee_manages(sources: list[str], targets: list[str], n: int) -> list[dict]:
            import random
            from datetime import date, timedelta
            edges = []
            seen = set()
            attempts = 0
            # Distinct (manager, report) pairs; allow self-loops to exercise
            # the design doc's open question on relationship-to-self.
            while len(edges) < n and attempts < n * 10:
                attempts += 1
                src = random.choice(sources)
                tgt = random.choice(targets)
                if (src, tgt) in seen:
                    continue
                seen.add((src, tgt))
                edges.append({
                    "source_ref": src,
                    "target_ref": tgt,
                    "since": (date.today() - timedelta(days=random.randint(30, 1825))).isoformat(),
                    "reporting_type": random.choice(["direct", "dotted_line"]),
                })
            return edges

        def _gen_order_contains(sources: list[str], targets: list[str], n: int) -> list[dict]:
            import random
            edges = []
            seen = set()
            attempts = 0
            while len(edges) < n and attempts < n * 10:
                attempts += 1
                src = random.choice(sources)
                tgt = random.choice(targets)
                if (src, tgt) in seen:
                    continue
                seen.add((src, tgt))
                edges.append({
                    "source_ref": src,
                    "target_ref": tgt,
                    "quantity": random.randint(1, 25),
                    "unit_price": round(random.uniform(5.0, 500.0), 2),
                })
            return edges

        edge_generators = {
            "EMPLOYEE_MANAGES": ("EMPLOYEE", "EMPLOYEE", _gen_employee_manages),
            "ORDER_CONTAINS": ("ORDER", "PRODUCT", _gen_order_contains),
        }

        for rel_value, count in rel_counts.items():
            template_id = self.created_templates.get(rel_value)
            if not template_id:
                print(f"  {rel_value}: SKIPPED (template not found — was the template phase run?)")
                stats["skipped"].append(rel_value)
                continue

            spec = edge_generators.get(rel_value)
            if not spec:
                print(f"  {rel_value}: SKIPPED (no edge generator defined)")
                stats["skipped"].append(rel_value)
                continue

            src_template, tgt_template, gen_fn = spec
            sources = self._docs_by_template.get(src_template, [])
            targets = self._docs_by_template.get(tgt_template, [])
            if not sources or not targets:
                print(f"  {rel_value}: SKIPPED (need {src_template} + {tgt_template} entity docs first; have {len(sources)}/{len(targets)})")
                stats["skipped"].append(rel_value)
                continue

            stats["by_template"][rel_value] = {"created": 0, "errors": 0}

            if self.dry_run:
                print(f"  [DRY-RUN] Would create {count} {rel_value} edges from {len(sources)} sources / {len(targets)} targets")
                stats["documents"] += count
                continue

            edges = gen_fn(sources, targets, count)
            if len(edges) < count:
                print(f"  {rel_value}: only {len(edges)}/{count} unique pairs available")

            batch_size = 50
            for i in range(0, len(edges), batch_size):
                batch = edges[i:i + batch_size]
                batch_data = [
                    {"template_id": template_id, "namespace": self.namespace, "data": data, "created_by": "seed_script"}
                    for data in batch
                ]
                try:
                    result = self.document_store.post(
                        "/api/document-store/documents",
                        batch_data,
                    )
                    for r in result.get("results", []):
                        if r.get("document_id"):
                            self.created_documents.append(r["document_id"])
                            self._docs_by_template.setdefault(rel_value, []).append(r["document_id"])
                            stats["documents"] += 1
                            stats["by_template"][rel_value]["created"] += 1
                        else:
                            stats["errors"] += 1
                            stats["by_template"][rel_value]["errors"] += 1
                            err = (r.get("error") or "")[:120]
                            if err:
                                print(f"  {rel_value}: per-item error — {err}")
                except requests.HTTPError as e:
                    detail = ""
                    try:
                        body = e.response.json()
                        detail = str(body.get("detail", ""))[:200]
                    except Exception:
                        pass
                    print(f"  {rel_value}: batch error — {e} {detail}")
                    stats["errors"] += len(batch)
                    stats["by_template"][rel_value]["errors"] += len(batch)
                except Exception as e:
                    print(f"  {rel_value}: batch error — {e}")
                    stats["errors"] += len(batch)
                    stats["by_template"][rel_value]["errors"] += len(batch)

            s = stats["by_template"][rel_value]
            print(f"  {rel_value}: {s['created']} created, {s['errors']} errors")

        return stats

    def seed_term_relations(self) -> dict[str, int]:
        """Seed ontology term-relations on the DEPARTMENT terminology.

        Mirrors DEPARTMENT.parent_value chain as is_a edges. Exercises the
        Phase-0-renamed /api/def-store/ontology/term-relations surface
        (CASE-61). Idempotent — skipped per-edge if it already exists.
        """
        stats = {"relations_created": 0, "errors": 0, "skipped": 0}

        print("\nSeeding ontology term-relations (DEPARTMENT is_a chain)...")

        dept_id = self.created_terminologies.get("DEPARTMENT")
        if not dept_id:
            print("  SKIPPED (DEPARTMENT terminology not found)")
            return stats

        if self.dry_run:
            print("  [DRY-RUN] Would create is_a edges for DEPARTMENT children")
            stats["relations_created"] = 8  # rough count from terminologies.py
            return stats

        # Fetch terms in DEPARTMENT to map value -> term_id
        try:
            resp = self.def_store.get(
                f"/api/def-store/terminologies/{dept_id}/terms",
                params=self._ns_params(page_size=200),
            )
            terms_by_value = {t["value"]: t["term_id"] for t in resp.get("items", [])}
        except Exception as e:
            print(f"  ERROR fetching DEPARTMENT terms: {e}")
            stats["errors"] += 1
            return stats

        # Build is_a edges: child -> parent based on parent_value in seed defs.
        from seed_data import terminologies as term_module
        dept_def = term_module.get_terminology_by_value("DEPARTMENT")
        edges: list[dict[str, str]] = []
        for term_def in dept_def.get("terms", []):
            parent_value = term_def.get("parent_value")
            if not parent_value:
                continue
            child_id = terms_by_value.get(term_def["value"])
            parent_id = terms_by_value.get(parent_value)
            if child_id and parent_id:
                edges.append({
                    "source_term_id": child_id,
                    "target_term_id": parent_id,
                    "relation_type": "is_a",
                })

        if not edges:
            print("  SKIPPED (no DEPARTMENT parent chain — terms may not be seeded yet)")
            return stats

        try:
            result = self.def_store.post(
                "/api/def-store/ontology/term-relations",
                edges,
                params=self._ns_params(),
            )
            for r in result.get("results", []):
                status_str = r.get("status", "")
                if status_str == "created":
                    stats["relations_created"] += 1
                elif status_str in ("unchanged", "skipped"):
                    stats["skipped"] += 1
                else:
                    stats["errors"] += 1
                    err = (r.get("error") or "")[:120]
                    if err:
                        print(f"  per-item error: {err}")
            print(f"  Term-relations: {stats['relations_created']} created, {stats['skipped']} skipped, {stats['errors']} errors")
        except Exception as e:
            print(f"  ERROR: {e}")
            stats["errors"] = len(edges)

        return stats

    def verify_relationships(self) -> dict[str, Any]:
        """Smoke-test the new relationship query paths against seeded data.

        Touches each Phase-4..7 code path with at least one assertion so a
        regression in any of them surfaces in routine seeding output.
        """
        results: dict[str, Any] = {"checks": [], "errors": 0}

        print("\nVerifying relationship APIs...")

        if self.dry_run:
            print("  [DRY-RUN] Would call /relationships, /traverse, get_document_versions, run_report_query")
            return results

        def _check(name: str, ok: bool, detail: str = "") -> None:
            mark = "OK" if ok else "FAIL"
            print(f"  [{mark}] {name}{(' — ' + detail) if detail else ''}")
            results["checks"].append({"name": name, "ok": ok, "detail": detail})
            if not ok:
                results["errors"] += 1

        # 1. /relationships on a manager (any EMPLOYEE that appears as source_ref)
        managers = self._docs_by_template.get("EMPLOYEE", [])
        if managers:
            sample_mgr = managers[0]
            try:
                resp = self.document_store.get(
                    f"/api/document-store/documents/{sample_mgr}/relationships",
                    params={"namespace": self.namespace, "direction": "outgoing", "page_size": 10},
                )
                count = len(resp.get("items", []))
                _check("get /relationships?direction=outgoing", True, f"{count} edges from sample employee")
            except Exception as e:
                _check("get /relationships?direction=outgoing", False, str(e)[:120])

        # 2. /traverse with depth=2 on the same manager
        if managers:
            try:
                resp = self.document_store.get(
                    f"/api/document-store/documents/{managers[0]}/traverse",
                    params={"namespace": self.namespace, "depth": 2, "direction": "outgoing"},
                )
                node_count = len(resp.get("nodes", []))
                truncated = resp.get("truncated", False)
                _check("get /traverse?depth=2", True, f"{node_count} nodes (truncated={truncated})")
            except Exception as e:
                _check("get /traverse?depth=2", False, str(e)[:120])

        # 3. versioned=false: update an ORDER_CONTAINS doc twice and assert version stays at 1.
        order_contains_docs = self._docs_by_template.get("ORDER_CONTAINS", [])
        oc_template_id = self.created_templates.get("ORDER_CONTAINS")
        if order_contains_docs and oc_template_id:
            sample_oc = order_contains_docs[0]
            try:
                # Fetch current document to get source/target refs (re-post replaces in place).
                current = self.document_store.get(
                    f"/api/document-store/documents/{sample_oc}",
                    params={"namespace": self.namespace},
                )
                cur_data = current.get("data", {})
                # Re-post the same identity with a different unit_price twice.
                for new_price in (99.99, 199.99):
                    self.document_store.post(
                        "/api/document-store/documents",
                        [{
                            "template_id": oc_template_id,
                            "namespace": self.namespace,
                            "data": {**cur_data, "unit_price": new_price},
                            "created_by": "seed_script_versioned_false_check",
                        }],
                    )
                # Now verify only one version exists.
                versions = self.document_store.get(
                    f"/api/document-store/documents/{sample_oc}/versions",
                    params={"namespace": self.namespace},
                )
                vlist = versions.get("versions") or []
                cur_version = versions.get("current_version", -1)
                _check(
                    "versioned=false (ORDER_CONTAINS): 1 version after 2 updates",
                    len(vlist) == 1 and cur_version == 1,
                    f"got versions={len(vlist)} current_version={cur_version}",
                )
            except Exception as e:
                _check("versioned=false (ORDER_CONTAINS): 1 version after 2 updates", False, str(e)[:120])

        # 4. Reporting: source_ref_id / target_ref_id columns populated on
        # the relationship template's table. All services share the same
        # Caddy URL — reuse the document-store client's base URL.
        try:
            # Give reporting-sync a moment to consume the NATS events from
            # the relationship-document phase.
            time.sleep(2.0)
            rs_client = ServiceClient(self.urls["document-store"], self.api_key, verify_ssl=False)
            rq = rs_client.post(
                "/api/reporting-sync/query",
                {"sql": "SELECT source_ref_id, target_ref_id FROM doc_order_contains LIMIT 5"},
            )
            rows = rq.get("rows", [])
            non_null = sum(1 for r in rows if r.get("source_ref_id") and r.get("target_ref_id"))
            _check(
                "reporting: doc_order_contains source/target_ref_id populated",
                non_null > 0,
                f"{non_null}/{len(rows)} rows populated",
            )
        except Exception as e:
            _check("reporting: doc_order_contains source/target_ref_id populated", False, str(e)[:120])

        # 5. Negative case: archived endpoint should reject with archived_relationship_endpoint.
        em_template_id = self.created_templates.get("EMPLOYEE_MANAGES")
        employees = self._docs_by_template.get("EMPLOYEE", [])
        if em_template_id and len(employees) >= 2:
            archive_target = employees[-1]
            try:
                # Archive one employee via the bulk archive endpoint.
                self.document_store.post(
                    "/api/document-store/documents/archive",
                    [{"id": archive_target, "archived_by": "seed_script_negative_check"}],
                )
                # Try to create an EMPLOYEE_MANAGES edge that points at that
                # archived employee. Expected: per-item error with
                # `archived_relationship_endpoint` prefix.
                manager_id = employees[0]
                result = self.document_store.post(
                    "/api/document-store/documents",
                    [{
                        "template_id": em_template_id,
                        "namespace": self.namespace,
                        "data": {
                            "source_ref": manager_id,
                            "target_ref": archive_target,
                            "since": "2025-01-01",
                            "reporting_type": "direct",
                        },
                        "created_by": "seed_script_negative_check",
                    }],
                )
                r = result.get("results", [{}])[0]
                err_str = r.get("error", "") or ""
                rejected = r.get("status") == "error" and "archived_relationship_endpoint" in err_str
                _check(
                    "negative: archived_relationship_endpoint enforced",
                    rejected,
                    f"got status={r.get('status')} error={err_str[:120]}",
                )
            except Exception as e:
                _check("negative: archived_relationship_endpoint enforced", False, str(e)[:120])

        return results

    def run_benchmarks(self) -> performance.BenchmarkReport:
        """Run performance benchmarks."""
        print("\nRunning performance benchmarks...")

        # Get counts for report
        try:
            term_resp = self.def_store.get(
                "/api/def-store/terminologies",
                params=self._ns_params(page_size=1),
            )
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
                            [{"template_id": min_template_id, "namespace": self.namespace, "data": doc, "created_by": "benchmark"}],
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
                self.document_store.get(
                    "/api/document-store/documents",
                    params=self._ns_params(page_size=50),
                )
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

            for template_value in test_templates[:30]:
                template_id = self.created_templates.get(template_value)
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
        help="WIP host (default: localhost or WIP_HOST env var)"
    )

    parser.add_argument(
        "--port",
        type=int,
        default=8443,
        help="Caddy proxy port (default: 8443, use 443 for K8s Ingress)"
    )

    parser.add_argument(
        "--api-key",
        default=None,
        help="API key (overrides --api-key-file, WIP_API_KEY, and auto-discovery)"
    )

    parser.add_argument(
        "--api-key-file",
        default=None,
        metavar="PATH",
        help="Read the API key from this file (single line)"
    )

    parser.add_argument(
        "--deployment",
        default=None,
        metavar="NAME",
        help="When several deployments exist under ~/.wip-deploy/, pick this one (e.g. wip-dev-local)"
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
        "--time-limit",
        type=float,
        default=None,
        metavar="SECS",
        help="Stop document seeding after SECS seconds (for quick perf tests, e.g. --time-limit 30)"
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
        "--namespace",
        default="seed",
        help="Namespace prefix for data isolation (default: seed). Use 'wip' to seed into the default namespace."
    )

    args = parser.parse_args()

    # Parse services
    if args.services == "all":
        services = ["def-store", "template-store", "document-store"]
    else:
        services = [s.strip() for s in args.services.split(",")]

    # Resolve the API key before printing the banner so its source can
    # appear there.
    try:
        api_key, key_source = resolve_api_key(
            cli_key=args.api_key,
            cli_key_file=args.api_key_file,
            deployment=args.deployment,
            host=args.host,
        )
    except ApiKeyResolutionError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return

    print("=" * 70)
    print("WIP Comprehensive Seed Script  v1.3")
    print("=" * 70)
    print(f"Host: https://{args.host}:{args.port} (Caddy proxy; only mode supported)")
    print(f"API key source: {key_source}")
    print(f"Profile: {args.profile}")
    print(f"Namespace: {args.namespace}")
    print(f"Services: {', '.join(services)}")
    if args.time_limit:
        print(f"Time limit: {args.time_limit:.0f}s (document seeding)")
    print(f"Dry run: {args.dry_run}")
    print("\nNote: SSL verification disabled for self-signed Caddy cert.")

    if args.clean:
        print("\nWARNING: Clean mode is enabled. This will delete existing data!")
        confirm = input("Type 'yes' to confirm: ")
        if confirm.lower() != "yes":
            print("Aborted.")
            return

    # Initialize seeder
    seeder = WIPSeeder(
        profile=args.profile,
        api_key=api_key,
        host=args.host,
        proxy_port=args.port,
        dry_run=args.dry_run,
        namespace=args.namespace,
        time_limit=args.time_limit,
    )

    # Check services
    print("\nChecking service health...")
    if not seeder.check_services(services):
        print("\nSome services are not responding.")
        print("  Verify the deployment is up:  podman ps | grep wip-")
        print("  Caddy must be reachable on https://"
              f"{args.host}:{args.port}/api/<service>/health")
        return

    # Initialize custom namespace in registry
    # Default 'wip' namespace is initialized by setup.sh, not the seed script
    if not args.dry_run and seeder._custom_ns:
        print("\nInitializing namespace...")
        seeder.initialize_namespace()

    start_time = time.time()
    total_stats = {}

    # Seed terminologies
    if "def-store" in services and not args.skip_terminologies:
        phase_start = time.perf_counter()
        stats = seeder.seed_terminologies()
        total_stats["terminologies"] = stats
        print(f"  Phase time: {(time.perf_counter() - phase_start) * 1000:.0f}ms")
    else:
        # Still need to load existing terminology IDs for document generation
        print("\nLoading existing terminologies...")
        try:
            resp = seeder.def_store.get(
                "/api/def-store/terminologies",
                params=seeder._ns_params(page_size=100),
            )
            for t in resp.get("items", []):
                seeder.created_terminologies[t["value"]] = t["terminology_id"]
            print(f"  Loaded {len(seeder.created_terminologies)} terminologies")
        except Exception as e:
            print(f"  Warning: Could not load terminologies: {e}")

    # Seed templates
    if "template-store" in services and not args.skip_templates:
        phase_start = time.perf_counter()
        stats = seeder.seed_templates()
        total_stats["templates"] = stats
        print(f"  Phase time: {(time.perf_counter() - phase_start) * 1000:.0f}ms")
    else:
        # Still need to load existing template IDs for document generation
        print("\nLoading existing templates...")
        try:
            resp = seeder.template_store.get(
                "/api/template-store/templates",
                params=seeder._ns_params(page_size=100),
            )
            for t in resp.get("items", []):
                seeder.created_templates[t["value"]] = t["template_id"]
            print(f"  Loaded {len(seeder.created_templates)} templates")
        except Exception as e:
            print(f"  Warning: Could not load templates: {e}")

    # Seed documents
    if "document-store" in services:
        phase_start = time.perf_counter()
        stats = seeder.seed_documents()
        total_stats["documents"] = stats
        print(f"  Phase time: {(time.perf_counter() - phase_start) * 1000:.0f}ms")

    # Seed relationship documents — needs entity documents to exist first.
    # Runs even under time-limit since this is the only path that exercises
    # the document-relationship feature.
    if "document-store" in services:
        phase_start = time.perf_counter()
        stats = seeder.seed_relationship_documents()
        total_stats["relationship_documents"] = stats
        print(f"  Phase time: {(time.perf_counter() - phase_start) * 1000:.0f}ms")

    # Seed term-relations (CASE-61 — Phase-0 rename surface coverage).
    if "def-store" in services and not args.skip_terminologies:
        phase_start = time.perf_counter()
        stats = seeder.seed_term_relations()
        total_stats["term_relations"] = stats
        print(f"  Phase time: {(time.perf_counter() - phase_start) * 1000:.0f}ms")

    # Seed template versions and versioning tests (skip when time-limited — not useful for perf tests)
    if not args.time_limit:
        if "template-store" in services and not args.skip_templates:
            phase_start = time.perf_counter()
            stats = seeder.seed_template_versions()
            total_stats["template_versions"] = stats
            print(f"  Phase time: {(time.perf_counter() - phase_start) * 1000:.0f}ms")

        if "document-store" in services:
            phase_start = time.perf_counter()
            stats = seeder.seed_versioning_tests()
            total_stats["versioning_tests"] = stats
            print(f"  Phase time: {(time.perf_counter() - phase_start) * 1000:.0f}ms")

        # Verify the relationship query/reporting paths against seeded data.
        # Skip under --time-limit (not useful for perf benchmarks).
        if "document-store" in services:
            phase_start = time.perf_counter()
            stats = seeder.verify_relationships()
            total_stats["relationship_verification"] = stats
            print(f"  Phase time: {(time.perf_counter() - phase_start) * 1000:.0f}ms")

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

    if "template_versions" in total_stats:
        s = total_stats["template_versions"]
        print(f"Template versions: {s['versions_created']} created, {s['errors']} errors")

    if "documents" in total_stats:
        s = total_stats["documents"]
        time_note = " (time limit reached)" if s.get("time_limited") else ""
        print(f"Documents: {s['documents']} created, {s['errors']} errors{time_note}")

    if "relationship_documents" in total_stats:
        s = total_stats["relationship_documents"]
        skipped_note = f", {len(s['skipped'])} skipped" if s.get("skipped") else ""
        print(f"Relationship documents: {s['documents']} created, {s['errors']} errors{skipped_note}")

    if "term_relations" in total_stats:
        s = total_stats["term_relations"]
        print(f"Term relations: {s['relations_created']} created, {s['skipped']} skipped, {s['errors']} errors")

    if "versioning_tests" in total_stats:
        s = total_stats["versioning_tests"]
        print(f"Versioning tests: {s['versions_created']} document versions created, {s['errors']} errors")

    if "relationship_verification" in total_stats:
        s = total_stats["relationship_verification"]
        passed = sum(1 for c in s.get("checks", []) if c["ok"])
        total_checks = len(s.get("checks", []))
        print(f"Relationship verification: {passed}/{total_checks} checks passed, {s['errors']} errors")

    # Per-service HTTP timing reports
    seeder.def_store.print_timing_report("Def-Store")
    seeder.template_store.print_timing_report("Template-Store")
    seeder.document_store.print_timing_report("Document-Store")
    seeder.registry.print_timing_report("Registry")

    # Server-side bulk timing breakdown
    bulk_timings = getattr(seeder, '_bulk_timings', [])
    if bulk_timings:
        print(f"\n  Document-Store Server-Side Timing ({len(bulk_timings)} batches):")
        # Aggregate by stage
        stages = {}
        for bt in bulk_timings:
            for k, v in bt.items():
                if k in ("template", "batch_size"):
                    continue
                if k not in stages:
                    stages[k] = []
                stages[k].append(v)
        for stage in sorted(stages):
            vals = stages[stage]
            print(f"    {stage:30s}  avg={sum(vals)/len(vals):7.1f}ms  max={max(vals):7.0f}ms  total={sum(vals):8.0f}ms")
        # Show first batch separately (cold cache)
        first = bulk_timings[0]
        print(f"    --- First batch ({first.get('template', '?')}, n={first.get('batch_size', '?')}) ---")
        for k, v in sorted(first.items()):
            if k not in ("template", "batch_size"):
                print(f"    {k:30s}  {v:7.1f}ms")

    # Run benchmarks if requested
    if args.benchmark and not args.dry_run:
        report = seeder.run_benchmarks()
        report.print_report()

        if args.output:
            with open(args.output, "w") as f:
                f.write(report.to_json())
            print(f"\nBenchmark results saved to: {args.output}")

    base = f"https://{args.host}:{args.port}"
    print("\n" + "=" * 70)
    print("You can now explore the data at:")
    print(f"  Registry API:       {base}/api/registry/docs")
    print(f"  Def-Store API:      {base}/api/def-store/docs")
    print(f"  Template Store API: {base}/api/template-store/docs")
    print(f"  Document Store API: {base}/api/document-store/docs")
    print(f"  WIP Console:        {base}")
    print("=" * 70)


if __name__ == "__main__":
    main()

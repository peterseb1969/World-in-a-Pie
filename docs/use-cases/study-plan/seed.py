#!/usr/bin/env python3
"""
Study Plan Use Case - Seed Script

Seeds terminologies, templates, and demo study plan data into WIP.

Usage:
    cd docs/use-cases/study-plan
    pip install -r requirements.txt

    # Seed everything
    python seed.py --base-url http://localhost

    # Seed specific parts
    python seed.py --terminologies
    python seed.py --templates
    python seed.py --data

    # Dry run (show what would be created)
    python seed.py --dry-run
"""

import argparse
import json
import sys
from typing import Any

import requests

# Import local modules
from terminologies import ALL_TERMINOLOGIES
from templates import TEMPLATES
from demo_data import DEMO_DATA


class WIPSeeder:
    """Seeds WIP with study plan data."""

    def __init__(
        self,
        base_url: str = "http://localhost",
        api_key: str = "dev_master_key_for_testing",
        dry_run: bool = False,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.dry_run = dry_run
        self.headers = {"X-API-Key": api_key, "Content-Type": "application/json"}

        # Service URLs
        self.def_store_url = f"{self.base_url}:8002/api/def-store"
        self.template_store_url = f"{self.base_url}:8003/api/template-store"
        self.document_store_url = f"{self.base_url}:8004/api/document-store"

        # Statistics
        self.stats = {
            "terminologies_created": 0,
            "terminologies_skipped": 0,
            "terms_created": 0,
            "templates_created": 0,
            "templates_skipped": 0,
            "documents_created": 0,
            "documents_updated": 0,
            "errors": [],
        }

    def log(self, message: str, level: str = "info"):
        """Print a log message."""
        prefix = {"info": "  ", "success": "✓ ", "error": "✗ ", "warn": "⚠ "}
        print(f"{prefix.get(level, '  ')}{message}")

    def _request(
        self, method: str, url: str, data: dict | None = None
    ) -> dict | None:
        """Make an HTTP request."""
        if self.dry_run:
            self.log(f"[DRY RUN] {method} {url}")
            if data:
                self.log(f"  Data: {json.dumps(data, indent=2)[:200]}...")
            return {"dry_run": True}

        try:
            response = requests.request(
                method, url, headers=self.headers, json=data, timeout=30
            )
            if response.status_code in (200, 201):
                return response.json()
            elif response.status_code == 409:
                # Conflict - already exists
                return {"conflict": True, "detail": response.json().get("detail", "")}
            else:
                self.log(
                    f"HTTP {response.status_code}: {response.text[:200]}", "error"
                )
                return None
        except requests.RequestException as e:
            self.log(f"Request failed: {e}", "error")
            return None

    # -------------------------------------------------------------------------
    # TERMINOLOGIES
    # -------------------------------------------------------------------------

    def seed_terminologies(self):
        """Seed all terminologies and their terms."""
        print("\n=== Seeding Terminologies ===\n")

        for terminology in ALL_TERMINOLOGIES:
            self._seed_terminology(terminology)

        print(f"\nTerminologies: {self.stats['terminologies_created']} created, "
              f"{self.stats['terminologies_skipped']} skipped")
        print(f"Terms: {self.stats['terms_created']} created")

    def _seed_terminology(self, terminology: dict):
        """Seed a single terminology with its terms."""
        code = terminology["code"]
        terms = terminology.pop("terms", [])

        # Create terminology
        result = self._request("POST", f"{self.def_store_url}/terminologies", terminology)

        if result is None:
            self.stats["errors"].append(f"Failed to create terminology: {code}")
            return
        elif result.get("conflict"):
            self.log(f"Terminology {code} already exists, skipping", "warn")
            self.stats["terminologies_skipped"] += 1
            terminology["terms"] = terms  # Restore for later use
            return

        self.log(f"Created terminology: {code}", "success")
        self.stats["terminologies_created"] += 1

        # Get the terminology ID for adding terms
        if not self.dry_run:
            terminology_id = result.get("terminology_id")
            if terminology_id and terms:
                self._seed_terms(terminology_id, terms)

        terminology["terms"] = terms  # Restore

    def _seed_terms(self, terminology_id: str, terms: list[dict]):
        """Seed terms for a terminology."""
        for term in terms:
            result = self._request(
                "POST",
                f"{self.def_store_url}/terminologies/{terminology_id}/terms",
                term,
            )
            if result and not result.get("conflict"):
                self.stats["terms_created"] += 1

    # -------------------------------------------------------------------------
    # TEMPLATES
    # -------------------------------------------------------------------------

    def seed_templates(self):
        """Seed all templates."""
        print("\n=== Seeding Templates ===\n")

        # Sort templates so base templates come before extended ones
        sorted_templates = self._sort_templates_by_dependency(TEMPLATES)

        for template in sorted_templates:
            self._seed_template(template)

        print(f"\nTemplates: {self.stats['templates_created']} created, "
              f"{self.stats['templates_skipped']} skipped")

    def _sort_templates_by_dependency(self, templates: list[dict]) -> list[dict]:
        """Sort templates so dependencies come first."""
        # Build dependency graph
        by_code = {t["code"]: t for t in templates}
        sorted_list = []
        visited = set()

        def visit(template):
            code = template["code"]
            if code in visited:
                return
            visited.add(code)

            # Visit parent first if exists
            extends = template.get("extends")
            if extends and extends in by_code:
                visit(by_code[extends])

            sorted_list.append(template)

        for template in templates:
            visit(template)

        return sorted_list

    def _seed_template(self, template: dict):
        """Seed a single template."""
        code = template["code"]

        result = self._request("POST", f"{self.template_store_url}/templates", template)

        if result is None:
            self.stats["errors"].append(f"Failed to create template: {code}")
        elif result.get("conflict"):
            self.log(f"Template {code} already exists, skipping", "warn")
            self.stats["templates_skipped"] += 1
        else:
            self.log(f"Created template: {code}", "success")
            self.stats["templates_created"] += 1

    # -------------------------------------------------------------------------
    # DEMO DATA
    # -------------------------------------------------------------------------

    def seed_data(self):
        """Seed demo study plan data."""
        print("\n=== Seeding Demo Data (DEMO-001) ===\n")

        # 1. Study Definition
        self._seed_document("STUDY_DEFINITION", DEMO_DATA["study_definition"])

        # 2. Study Arms
        for arm in DEMO_DATA["study_arms"]:
            self._seed_document("STUDY_ARM", arm)

        # 3. Study Timepoints
        for timepoint in DEMO_DATA["study_timepoints"]:
            self._seed_document("STUDY_TIMEPOINT", timepoint)

        # 4. Planned Events (various templates)
        for event in DEMO_DATA["planned_events"]:
            template_code = event["template_code"]
            data = event["data"]
            self._seed_document(template_code, data)

        print(f"\nDocuments: {self.stats['documents_created']} created, "
              f"{self.stats['documents_updated']} updated")

    def _seed_document(self, template_code: str, data: dict):
        """Seed a single document."""
        payload = {"template_code": template_code, "data": data}

        result = self._request("POST", f"{self.document_store_url}/documents", payload)

        if result is None:
            self.stats["errors"].append(
                f"Failed to create {template_code} document"
            )
        elif result.get("dry_run"):
            self.stats["documents_created"] += 1
        else:
            # Check if this was a new document or update
            version = result.get("version", 1)
            if version == 1:
                self.log(f"Created {template_code}: {self._doc_identity(data)}", "success")
                self.stats["documents_created"] += 1
            else:
                self.log(f"Updated {template_code}: {self._doc_identity(data)} (v{version})", "success")
                self.stats["documents_updated"] += 1

    def _doc_identity(self, data: dict) -> str:
        """Get a brief identity string for a document."""
        # Try common identity fields
        for field in ["study_id", "arm_code", "timepoint", "event_type", "name"]:
            if field in data:
                return str(data[field])
        return str(list(data.values())[0])[:30]

    # -------------------------------------------------------------------------
    # MAIN
    # -------------------------------------------------------------------------

    def seed_all(self):
        """Seed everything."""
        self.seed_terminologies()
        self.seed_templates()
        self.seed_data()
        self._print_summary()

    def _print_summary(self):
        """Print final summary."""
        print("\n" + "=" * 50)
        print("SEED SUMMARY")
        print("=" * 50)
        print(f"Terminologies: {self.stats['terminologies_created']} created, "
              f"{self.stats['terminologies_skipped']} skipped")
        print(f"Terms: {self.stats['terms_created']} created")
        print(f"Templates: {self.stats['templates_created']} created, "
              f"{self.stats['templates_skipped']} skipped")
        print(f"Documents: {self.stats['documents_created']} created, "
              f"{self.stats['documents_updated']} updated")

        if self.stats["errors"]:
            print(f"\nErrors ({len(self.stats['errors'])}):")
            for error in self.stats["errors"]:
                print(f"  - {error}")

        if self.dry_run:
            print("\n[DRY RUN - no changes made]")


def main():
    parser = argparse.ArgumentParser(
        description="Seed WIP with study plan use case data"
    )
    parser.add_argument(
        "--base-url",
        default="http://localhost",
        help="Base URL for WIP services (default: http://localhost)",
    )
    parser.add_argument(
        "--api-key",
        default="dev_master_key_for_testing",
        help="API key for authentication",
    )
    parser.add_argument(
        "--terminologies",
        action="store_true",
        help="Seed only terminologies",
    )
    parser.add_argument(
        "--templates",
        action="store_true",
        help="Seed only templates",
    )
    parser.add_argument(
        "--data",
        action="store_true",
        help="Seed only demo data (requires terminologies and templates)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be created without making changes",
    )

    args = parser.parse_args()

    seeder = WIPSeeder(
        base_url=args.base_url,
        api_key=args.api_key,
        dry_run=args.dry_run,
    )

    # Determine what to seed
    seed_specific = args.terminologies or args.templates or args.data

    if not seed_specific:
        # Seed everything
        seeder.seed_all()
    else:
        if args.terminologies:
            seeder.seed_terminologies()
        if args.templates:
            seeder.seed_templates()
        if args.data:
            seeder.seed_data()
        seeder._print_summary()


if __name__ == "__main__":
    main()

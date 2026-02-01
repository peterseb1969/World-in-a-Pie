#!/usr/bin/env python3
"""
Network Inventory Use Case - Seed Script

Seeds WIP with terminologies, templates, and demo data for network hardware inventory.

Usage:
    python docs/use-cases/network-inventory/seed.py [OPTIONS]

Options:
    --api-key KEY      API key (default: dev_master_key_for_testing)
    --base-url URL     Base URL (default: http://localhost)
    --terminologies    Seed only terminologies
    --templates        Seed only templates
    --data             Seed only demo data
    --dry-run          Show what would be created without creating
"""

import argparse
import json
import sys
from typing import Any

import requests

# Import definitions from local modules
from terminologies import TERMINOLOGIES
from templates import TEMPLATES
from demo_data import (
    LOCATIONS,
    VLANS,
    DEVICES,
    SWITCH_PORTS,
    PATCH_PANELS,
    PATCH_PORTS,
    CABLES,
    IP_ASSIGNMENTS,
)


class WIPClient:
    """Simple client for WIP APIs."""

    def __init__(self, base_url: str, api_key: str, dry_run: bool = False):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.dry_run = dry_run
        self.headers = {"X-API-Key": api_key, "Content-Type": "application/json"}

    def _request(self, method: str, url: str, data: dict = None) -> dict:
        """Make API request."""
        if self.dry_run:
            print(f"  [DRY-RUN] {method} {url}")
            if data:
                print(f"    {json.dumps(data, indent=2)[:200]}...")
            return {"dry_run": True}

        try:
            response = requests.request(
                method,
                url,
                headers=self.headers,
                json=data,
                timeout=30,
            )
            if response.status_code >= 400:
                print(f"  ERROR: {response.status_code} - {response.text[:200]}")
                return None
            return response.json() if response.text else {}
        except Exception as e:
            print(f"  ERROR: {e}")
            return None

    # Def-Store API
    def create_terminology(self, data: dict) -> dict:
        url = f"{self.base_url}:8002/api/def-store/terminologies"
        return self._request("POST", url, data)

    def get_terminology(self, code: str) -> dict:
        url = f"{self.base_url}:8002/api/def-store/terminologies/by-code/{code}"
        try:
            response = requests.get(url, headers=self.headers, timeout=10)
            if response.status_code == 200:
                return response.json()
        except Exception:
            pass
        return None

    def create_terms_bulk(self, terminology_id: str, terms: list) -> dict:
        url = f"{self.base_url}:8002/api/def-store/terminologies/{terminology_id}/terms/bulk"
        return self._request("POST", url, {"terms": terms})

    # Template-Store API
    def create_template(self, data: dict) -> dict:
        url = f"{self.base_url}:8003/api/template-store/templates"
        return self._request("POST", url, data)

    def get_template(self, code: str) -> dict:
        url = f"{self.base_url}:8003/api/template-store/templates/by-code/{code}"
        try:
            response = requests.get(url, headers=self.headers, timeout=10)
            if response.status_code == 200:
                return response.json()
        except Exception:
            pass
        return None

    # Document-Store API
    def create_document(self, template_code: str, data: dict) -> dict:
        url = f"{self.base_url}:8004/api/document-store/documents"
        payload = {"template_code": template_code, "data": data}
        return self._request("POST", url, payload)


def seed_terminologies(client: WIPClient) -> dict:
    """Seed all network inventory terminologies."""
    print("\n=== Seeding Terminologies ===\n")

    results = {"created": 0, "skipped": 0, "failed": 0}

    for term_def in TERMINOLOGIES:
        code = term_def["code"]
        print(f"Terminology: {code}")

        # Check if exists
        existing = client.get_terminology(code)
        if existing:
            print(f"  Already exists, skipping")
            results["skipped"] += 1
            continue

        # Create terminology
        terminology_data = {
            "code": code,
            "name": term_def["name"],
            "description": term_def.get("description", ""),
        }
        result = client.create_terminology(terminology_data)
        if not result:
            results["failed"] += 1
            continue

        terminology_id = result.get("terminology_id")
        print(f"  Created: {terminology_id}")

        # Add terms
        terms = term_def.get("terms", [])
        if terms and terminology_id:
            term_data = [
                {
                    "code": t["code"],
                    "value": t["value"],
                    "label": t["label"],
                    "aliases": t.get("aliases", []),
                    "sort_order": t.get("sort_order", 0),
                    "metadata": t.get("metadata", {}),
                }
                for t in terms
            ]
            bulk_result = client.create_terms_bulk(terminology_id, term_data)
            if bulk_result:
                print(f"  Added {len(terms)} terms")

        results["created"] += 1

    print(f"\nTerminologies: {results['created']} created, {results['skipped']} skipped, {results['failed']} failed")
    return results


def seed_templates(client: WIPClient) -> dict:
    """Seed all network inventory templates."""
    print("\n=== Seeding Templates ===\n")

    results = {"created": 0, "skipped": 0, "failed": 0}

    for template_def in TEMPLATES:
        code = template_def["code"]
        print(f"Template: {code}")

        # Check if exists
        existing = client.get_template(code)
        if existing:
            print(f"  Already exists, skipping")
            results["skipped"] += 1
            continue

        # Create template
        template_data = {
            "code": code,
            "name": template_def["name"],
            "description": template_def.get("description", ""),
            "identity_fields": template_def.get("identity_fields", []),
            "fields": template_def["fields"],
        }

        # Handle inheritance
        if "extends" in template_def:
            template_data["extends"] = template_def["extends"]

        result = client.create_template(template_data)
        if result:
            print(f"  Created: {result.get('template_id')}")
            results["created"] += 1
        else:
            results["failed"] += 1

    print(f"\nTemplates: {results['created']} created, {results['skipped']} skipped, {results['failed']} failed")
    return results


def seed_demo_data(client: WIPClient) -> dict:
    """Seed demo network inventory data."""
    print("\n=== Seeding Demo Data ===\n")

    results = {"created": 0, "failed": 0}

    # Helper function
    def seed_documents(template_code: str, documents: list, name: str):
        print(f"\n{name} ({template_code}):")
        for doc in documents:
            # Remove 'template' key if present (used for routing in DEVICES)
            doc_data = {k: v for k, v in doc.items() if k != "template"}
            result = client.create_document(template_code, doc_data)
            if result:
                doc_id = result.get("document_id", "")[:20]
                identifier = doc_data.get("hostname") or doc_data.get("name") or doc_data.get("location_code") or doc_data.get("cable_id") or str(doc_data.get("vlan_id", ""))
                print(f"  {identifier}: {doc_id}...")
                results["created"] += 1
            else:
                results["failed"] += 1

    # Seed in dependency order
    seed_documents("NET_LOCATION", LOCATIONS, "Locations")
    seed_documents("NET_VLAN", VLANS, "VLANs")

    # Devices - separate by template type
    base_devices = [d for d in DEVICES if d.get("template") == "NET_DEVICE"]
    switch_devices = [d for d in DEVICES if d.get("template") == "NET_SWITCH"]

    seed_documents("NET_DEVICE", base_devices, "Devices")
    seed_documents("NET_SWITCH", switch_devices, "Switches")

    seed_documents("NET_SWITCH_PORT", SWITCH_PORTS, "Switch Ports")
    seed_documents("NET_PATCH_PANEL", PATCH_PANELS, "Patch Panels")
    seed_documents("NET_PATCH_PORT", PATCH_PORTS, "Patch Ports")
    seed_documents("NET_CABLE", CABLES, "Cables")
    seed_documents("NET_IP_ASSIGNMENT", IP_ASSIGNMENTS, "IP Assignments")

    print(f"\nDocuments: {results['created']} created, {results['failed']} failed")
    return results


def main():
    parser = argparse.ArgumentParser(
        description="Seed WIP with network inventory use case data"
    )
    parser.add_argument(
        "--api-key",
        default="dev_master_key_for_testing",
        help="API key for authentication",
    )
    parser.add_argument(
        "--base-url",
        default="http://localhost",
        help="Base URL for WIP services",
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
        help="Seed only demo data",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be created without creating",
    )
    args = parser.parse_args()

    # If no specific flags, seed everything
    seed_all = not (args.terminologies or args.templates or args.data)

    print("=" * 60)
    print("  Network Inventory Use Case - Seed Script")
    print("=" * 60)
    print(f"\nBase URL: {args.base_url}")
    print(f"Dry run: {args.dry_run}")

    client = WIPClient(args.base_url, args.api_key, args.dry_run)

    if seed_all or args.terminologies:
        seed_terminologies(client)

    if seed_all or args.templates:
        seed_templates(client)

    if seed_all or args.data:
        seed_demo_data(client)

    print("\n" + "=" * 60)
    print("  Seeding complete!")
    print("=" * 60)


if __name__ == "__main__":
    main()

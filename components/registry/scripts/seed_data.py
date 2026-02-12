#!/usr/bin/env python3
"""
Seed script to populate the Registry with dummy data for testing.

Usage:
    python scripts/seed_data.py [--base-url URL] [--api-key KEY]

Examples:
    # Using defaults (localhost:8001, dev key)
    python scripts/seed_data.py

    # Custom URL and key
    python scripts/seed_data.py --base-url http://localhost:8001 --api-key your_key
"""

import argparse
import requests
from typing import Optional


def create_namespaces(base_url: str, api_key: str) -> None:
    """Create dummy namespaces with different ID generation strategies."""

    namespaces = [
        {
            "namespace_id": "products",
            "name": "Product Catalog",
            "description": "Product identifiers from various sources",
            "id_generator": {"type": "prefixed", "prefix": "PROD-"}
        },
        {
            "namespace_id": "customers",
            "name": "Customer Registry",
            "description": "Customer identifiers",
            "id_generator": {"type": "uuid4"}
        },
        {
            "namespace_id": "vendor-acme",
            "name": "ACME Vendor",
            "description": "External vendor ACME's product codes",
            "id_generator": {"type": "external"}
        },
        {
            "namespace_id": "vendor-globex",
            "name": "Globex Corporation",
            "description": "External vendor Globex's SKUs",
            "id_generator": {"type": "external"}
        },
        {
            "namespace_id": "orders",
            "name": "Order Registry",
            "description": "Time-sortable order IDs",
            "id_generator": {"type": "uuid7"}
        },
    ]

    print("Creating namespaces...")
    response = requests.post(
        f"{base_url}/api/registry/namespaces",
        json=namespaces,
        headers={"X-API-Key": api_key}
    )

    if response.status_code == 200:
        results = response.json()
        for r in results:
            status = r.get("status", "unknown")
            ns_id = r.get("namespace_id", "?")
            print(f"  {ns_id}: {status}")
    else:
        print(f"  Error: {response.status_code} - {response.text}")


def create_product_entries(base_url: str, api_key: str) -> list[str]:
    """Create product entries and return their registry IDs."""

    products = [
        {"pool_id": "products", "composite_key": {"name": "Widget Pro", "category": "electronics", "sku": "WP-001"}},
        {"pool_id": "products", "composite_key": {"name": "Gadget Plus", "category": "electronics", "sku": "GP-002"}},
        {"pool_id": "products", "composite_key": {"name": "Super Sprocket", "category": "hardware", "sku": "SS-003"}},
        {"pool_id": "products", "composite_key": {"name": "Mega Bolt", "category": "hardware", "sku": "MB-004"}},
        {"pool_id": "products", "composite_key": {"name": "Ultra Cable", "category": "accessories", "sku": "UC-005"}},
    ]

    print("\nRegistering products...")
    response = requests.post(
        f"{base_url}/api/registry/entries/register",
        json=products,
        headers={"X-API-Key": api_key}
    )

    registry_ids = []
    if response.status_code == 200:
        data = response.json()
        print(f"  Created: {data['created']}, Already exists: {data['already_exists']}")
        for r in data["results"]:
            if r.get("registry_id"):
                registry_ids.append(r["registry_id"])
                print(f"    {r['registry_id']}: {r['status']}")
    else:
        print(f"  Error: {response.status_code} - {response.text}")

    return registry_ids


def create_customer_entries(base_url: str, api_key: str) -> list[str]:
    """Create customer entries and return their registry IDs."""

    customers = [
        {"pool_id": "customers", "composite_key": {"email": "john.doe@example.com", "region": "US"}},
        {"pool_id": "customers", "composite_key": {"email": "jane.smith@example.com", "region": "EU"}},
        {"pool_id": "customers", "composite_key": {"email": "bob.wilson@example.com", "region": "US"}},
        {"pool_id": "customers", "composite_key": {"email": "alice.chen@example.com", "region": "APAC"}},
    ]

    print("\nRegistering customers...")
    response = requests.post(
        f"{base_url}/api/registry/entries/register",
        json=customers,
        headers={"X-API-Key": api_key}
    )

    registry_ids = []
    if response.status_code == 200:
        data = response.json()
        print(f"  Created: {data['created']}, Already exists: {data['already_exists']}")
        for r in data["results"]:
            if r.get("registry_id"):
                registry_ids.append(r["registry_id"])
                print(f"    {r['registry_id']}: {r['status']}")
    else:
        print(f"  Error: {response.status_code} - {response.text}")

    return registry_ids


def add_vendor_synonyms(base_url: str, api_key: str, product_ids: list[str]) -> None:
    """Add vendor-specific synonyms to products."""

    if len(product_ids) < 3:
        print("\nSkipping synonyms (not enough products)")
        return

    synonyms = [
        # Widget Pro has codes in both ACME and Globex
        {
            "target_pool_id": "products",
            "target_id": product_ids[0],
            "synonym_pool_id": "vendor-acme",
            "synonym_composite_key": {"acme_code": "ACM-WGT-001", "acme_category": "ELEC"}
        },
        {
            "target_pool_id": "products",
            "target_id": product_ids[0],
            "synonym_pool_id": "vendor-globex",
            "synonym_composite_key": {"globex_sku": "GLX-1001", "globex_dept": "Electronics"}
        },
        # Gadget Plus has ACME code
        {
            "target_pool_id": "products",
            "target_id": product_ids[1],
            "synonym_pool_id": "vendor-acme",
            "synonym_composite_key": {"acme_code": "ACM-GDG-002", "acme_category": "ELEC"}
        },
        # Super Sprocket has Globex code
        {
            "target_pool_id": "products",
            "target_id": product_ids[2],
            "synonym_pool_id": "vendor-globex",
            "synonym_composite_key": {"globex_sku": "GLX-2001", "globex_dept": "Hardware"}
        },
    ]

    print("\nAdding vendor synonyms...")
    response = requests.post(
        f"{base_url}/api/registry/synonyms/add",
        json=synonyms,
        headers={"X-API-Key": api_key}
    )

    if response.status_code == 200:
        results = response.json()
        for r in results:
            print(f"  Synonym to {r.get('target_id', '?')[:12]}...: {r.get('status', 'unknown')}")
    else:
        print(f"  Error: {response.status_code} - {response.text}")


def create_order_entries(base_url: str, api_key: str, customer_ids: list[str], product_ids: list[str]) -> None:
    """Create some order entries linking customers and products."""

    if not customer_ids or not product_ids:
        print("\nSkipping orders (missing customer or product IDs)")
        return

    orders = [
        {
            "pool_id": "orders",
            "composite_key": {
                "customer_ref": customer_ids[0] if customer_ids else "unknown",
                "product_ref": product_ids[0] if product_ids else "unknown",
                "order_date": "2024-01-15",
                "quantity": 2
            }
        },
        {
            "pool_id": "orders",
            "composite_key": {
                "customer_ref": customer_ids[1] if len(customer_ids) > 1 else customer_ids[0],
                "product_ref": product_ids[1] if len(product_ids) > 1 else product_ids[0],
                "order_date": "2024-01-16",
                "quantity": 1
            }
        },
        {
            "pool_id": "orders",
            "composite_key": {
                "customer_ref": customer_ids[0] if customer_ids else "unknown",
                "product_ref": product_ids[2] if len(product_ids) > 2 else product_ids[0],
                "order_date": "2024-01-17",
                "quantity": 5
            }
        },
    ]

    print("\nRegistering orders...")
    response = requests.post(
        f"{base_url}/api/registry/entries/register",
        json=orders,
        headers={"X-API-Key": api_key}
    )

    if response.status_code == 200:
        data = response.json()
        print(f"  Created: {data['created']}, Already exists: {data['already_exists']}")
        for r in data["results"]:
            if r.get("registry_id"):
                print(f"    {r['registry_id']}: {r['status']}")
    else:
        print(f"  Error: {response.status_code} - {response.text}")


def demo_searches(base_url: str, api_key: str) -> None:
    """Demonstrate some search capabilities."""

    print("\n" + "="*60)
    print("DEMO SEARCHES")
    print("="*60)

    # Search by field
    print("\n1. Search for 'electronics' category products:")
    response = requests.post(
        f"{base_url}/api/registry/search/by-fields",
        json=[{"field_criteria": {"category": "electronics"}}],
        headers={"X-API-Key": api_key}
    )
    if response.status_code == 200:
        results = response.json()["results"][0]["results"]
        for r in results:
            print(f"   - {r['registry_id']}: matched in {r['matched_pool_id']}")

    # Search across namespaces for vendor code
    print("\n2. Search across all namespaces for ACME code 'ACM-WGT-001':")
    response = requests.post(
        f"{base_url}/api/registry/search/across-namespaces",
        json=[{"field_criteria": {"acme_code": "ACM-WGT-001"}}],
        headers={"X-API-Key": api_key}
    )
    if response.status_code == 200:
        results = response.json()["results"][0]["results"]
        for r in results:
            print(f"   - {r['registry_id']}: found via {r['matched_in']} in {r['matched_pool_id']}")

    # Free text search
    print("\n3. Free text search for 'widget':")
    response = requests.post(
        f"{base_url}/api/registry/search/by-term",
        json=[{"term": "widget"}],
        headers={"X-API-Key": api_key}
    )
    if response.status_code == 200:
        results = response.json()["results"][0]["results"]
        for r in results:
            print(f"   - {r['registry_id']}: {r.get('matched_composite_key', {})}")


def main():
    parser = argparse.ArgumentParser(description="Seed the Registry with dummy data")
    parser.add_argument("--base-url", default="http://localhost:8001", help="Registry API base URL")
    parser.add_argument("--api-key", default="dev_master_key_for_testing", help="API key")
    args = parser.parse_args()

    print(f"Seeding Registry at {args.base_url}")
    print("="*60)

    # Check health first
    try:
        health = requests.get(f"{args.base_url}/health", timeout=5)
        if health.status_code != 200:
            print(f"Registry not healthy: {health.status_code}")
            return
        print("Registry is healthy\n")
    except requests.exceptions.ConnectionError:
        print(f"Cannot connect to {args.base_url}")
        print("Make sure the registry is running:")
        print("  podman-compose -f docker-compose.yml up -d --build")
        return

    # Create data
    create_namespaces(args.base_url, args.api_key)
    product_ids = create_product_entries(args.base_url, args.api_key)
    customer_ids = create_customer_entries(args.base_url, args.api_key)
    add_vendor_synonyms(args.base_url, args.api_key, product_ids)
    create_order_entries(args.base_url, args.api_key, customer_ids, product_ids)

    # Demo searches
    demo_searches(args.base_url, args.api_key)

    print("\n" + "="*60)
    print("DONE! You can now explore the data at:")
    print(f"  Swagger UI: {args.base_url}/docs")
    print(f"  ReDoc:      {args.base_url}/redoc")
    print(f"  MongoDB:    http://localhost:8081 (mongo-express)")
    print("="*60)


if __name__ == "__main__":
    main()

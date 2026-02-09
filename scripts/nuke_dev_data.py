#!/usr/bin/env python3
"""
Nuke script for World In a Pie (WIP) development databases.

Drops MongoDB collections for a complete clean slate.
Use with seed_comprehensive.py to repopulate.

WARNING: This is destructive! Only use on development instances.

Usage:
    python scripts/nuke_dev_data.py [options]

Options:
    --services SERVICES   Comma-separated services: all, def-store, template-store, document-store
    --force              Skip confirmation prompt (USE WITH CAUTION)
    --dry-run            Show what would be deleted without making changes
    --mongo-uri URI      MongoDB connection URI (default: mongodb://localhost:27017)

Examples:
    # Nuke everything (with confirmation)
    python scripts/nuke_dev_data.py

    # Nuke only document-store
    python scripts/nuke_dev_data.py --services document-store

    # Nuke without confirmation (for CI/scripts)
    python scripts/nuke_dev_data.py --force
"""

import argparse
import os

from pymongo import MongoClient


# Default MongoDB URI
DEFAULT_MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")

# Database names
DATABASES = {
    "document-store": "wip_document_store",
    "template-store": "wip_template_store",
    "def-store": "wip_def_store",
    "registry": "wip_registry",
}

# Collections to drop per service
COLLECTIONS = {
    "document-store": ["documents"],
    "template-store": ["templates"],
    "def-store": ["terminologies", "terms", "term_audit_log"],
    "registry": ["entries", "namespaces"],
}


def get_collection_stats(client: MongoClient, db_name: str, collections: list[str]) -> dict:
    """Get document counts for collections."""
    db = client[db_name]
    stats = {}
    for coll in collections:
        try:
            stats[coll] = db[coll].count_documents({})
        except Exception:
            stats[coll] = 0
    return stats


def drop_collections(client: MongoClient, db_name: str, collections: list[str], dry_run: bool = False) -> dict:
    """Drop collections from a database."""
    db = client[db_name]
    stats = {"dropped": 0, "documents": 0, "errors": 0}

    for coll in collections:
        try:
            count = db[coll].count_documents({})
            stats["documents"] += count

            if dry_run:
                print(f"    [DRY-RUN] Would drop {coll}: {count} documents")
            else:
                db[coll].drop()
                print(f"    Dropped {coll}: {count} documents")

            stats["dropped"] += 1
        except Exception as e:
            print(f"    Error dropping {coll}: {e}")
            stats["errors"] += 1

    return stats


def main():
    parser = argparse.ArgumentParser(
        description="Nuke WIP development databases for a clean slate",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )

    parser.add_argument(
        "--services",
        default="all",
        help="Comma-separated services: all, def-store, template-store, document-store, registry"
    )

    parser.add_argument(
        "--force",
        action="store_true",
        help="Skip confirmation prompt"
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be deleted without making changes"
    )

    parser.add_argument(
        "--mongo-uri",
        default=DEFAULT_MONGO_URI,
        help="MongoDB connection URI"
    )

    args = parser.parse_args()

    # Parse services
    if args.services == "all":
        services = ["document-store", "template-store", "def-store"]
    else:
        services = [s.strip() for s in args.services.split(",")]

    # Validate services
    valid_services = list(DATABASES.keys())
    for s in services:
        if s not in valid_services:
            print(f"Unknown service: {s}")
            print(f"Valid services: {', '.join(valid_services)}")
            return

    print("=" * 70)
    print("WIP NUKE SCRIPT - DROP MONGODB COLLECTIONS")
    print("=" * 70)
    print(f"Services: {', '.join(services)}")
    print(f"MongoDB: {args.mongo_uri}")
    print(f"Dry run: {args.dry_run}")

    # Connect to MongoDB
    try:
        client = MongoClient(args.mongo_uri, serverSelectionTimeoutMS=5000)
        # Test connection
        client.admin.command('ping')
        print("\nMongoDB connection: OK")
    except Exception as e:
        print(f"\nFailed to connect to MongoDB: {e}")
        return

    # Show what will be deleted
    print("\nCollections to be dropped:")
    total_docs = 0
    for service in services:
        db_name = DATABASES[service]
        collections = COLLECTIONS[service]
        stats = get_collection_stats(client, db_name, collections)
        for coll, count in stats.items():
            print(f"  {db_name}.{coll}: {count} documents")
            total_docs += count

    print(f"\nTotal documents to delete: {total_docs}")

    if not args.dry_run and not args.force:
        print("\n" + "!" * 70)
        print("WARNING: This will permanently delete ALL data!")
        print("!" * 70)
        confirm = input("\nType 'NUKE' to confirm: ")
        if confirm != "NUKE":
            print("Aborted.")
            return

    # Drop collections
    print("\nDropping collections...")
    total_stats = {"dropped": 0, "documents": 0, "errors": 0}

    for service in services:
        db_name = DATABASES[service]
        collections = COLLECTIONS[service]
        print(f"\n  {service} ({db_name}):")
        stats = drop_collections(client, db_name, collections, args.dry_run)
        total_stats["dropped"] += stats["dropped"]
        total_stats["documents"] += stats["documents"]
        total_stats["errors"] += stats["errors"]

    # Summary
    print("\n" + "=" * 70)
    if args.dry_run:
        print("DRY RUN COMPLETE - No data was actually deleted")
    else:
        print("NUKE COMPLETE")
        print(f"  Collections dropped: {total_stats['dropped']}")
        print(f"  Documents deleted: {total_stats['documents']}")
        if total_stats["errors"]:
            print(f"  Errors: {total_stats['errors']}")
    print("=" * 70)

    if not args.dry_run:
        print("\nTo repopulate with seed data:")
        print("  python scripts/seed_comprehensive.py --profile standard")

    client.close()


if __name__ == "__main__":
    main()

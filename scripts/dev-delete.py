#!/usr/bin/env python3
"""
WIP Dev Delete — Hard-delete entities from MongoDB, MinIO, and PostgreSQL by WIP ID.

DEVELOPMENT ONLY. Bypasses soft-delete, removes all versions, and cleans
up Registry entries, MinIO blobs, and PostgreSQL rows so IDs can be re-used.

Usage:
    # Dry run (default) — shows what would be deleted
    python scripts/dev-delete.py T-0001a2b3 D-0004c5d6

    # Actually delete
    python scripts/dev-delete.py --force T-0001a2b3

    # Delete with cascade (e.g., terminology + all its terms)
    python scripts/dev-delete.py --cascade --force TERM-0001a2b3

    # Delete by type when ID format is ambiguous
    python scripts/dev-delete.py --type template --force TPL-0001a2b3

    # Custom MongoDB URI
    python scripts/dev-delete.py --mongo-uri mongodb://localhost:27017/ --force T-0001a2b3

    # List all entities in a collection
    python scripts/dev-delete.py --list templates
    python scripts/dev-delete.py --list documents --limit 20

    # Skip MinIO or PostgreSQL cleanup
    python scripts/dev-delete.py --no-minio --no-postgres --force FILE-0001a2b3

MinIO and PostgreSQL connection details are read from environment variables
(same as WIP services) or can be passed via CLI flags. If a backend is not
reachable, cleanup for that backend is skipped with a warning.
"""

import argparse
import os
import sys
from pymongo import MongoClient

# ── Entity type detection and DB mapping ─────────────────────────────────

# database → collection → id field
ENTITY_MAP = {
    "terminology": {
        "db": "wip_def_store",
        "collection": "terminologies",
        "id_field": "terminology_id",
    },
    "term": {
        "db": "wip_def_store",
        "collection": "terms",
        "id_field": "term_id",
    },
    "relationship": {
        "db": "wip_def_store",
        "collection": "term_relationships",
        "id_field": "relationship_id",
    },
    "template": {
        "db": "wip_template_store",
        "collection": "templates",
        "id_field": "template_id",
    },
    "document": {
        "db": "wip_document_store",
        "collection": "documents",
        "id_field": "document_id",
    },
    "file": {
        "db": "wip_document_store",
        "collection": "files",
        "id_field": "file_id",
    },
    "registry": {
        "db": "wip_registry",
        "collection": "registry_entries",
        "id_field": "entry_id",
    },
}

# For --list shorthand
LIST_ALIASES = {
    "terminologies": "terminology",
    "terms": "term",
    "relationships": "relationship",
    "templates": "template",
    "documents": "document",
    "files": "file",
    "registry": "registry",
}

# Cascade rules: deleting parent also deletes children
CASCADE_RULES = {
    "terminology": [
        {
            "db": "wip_def_store",
            "collection": "terms",
            "foreign_key": "terminology_id",
            "match_field": "terminology_id",
            "label": "terms",
        },
        {
            "db": "wip_def_store",
            "collection": "term_relationships",
            "foreign_key": "source_terminology_id",
            "match_field": "terminology_id",
            "label": "relationships (as source)",
        },
        {
            "db": "wip_def_store",
            "collection": "term_relationships",
            "foreign_key": "target_terminology_id",
            "match_field": "terminology_id",
            "label": "relationships (as target)",
        },
    ],
    "term": [
        {
            "db": "wip_def_store",
            "collection": "term_relationships",
            "foreign_key": "source_term_id",
            "match_field": "term_id",
            "label": "relationships (as source)",
        },
        {
            "db": "wip_def_store",
            "collection": "term_relationships",
            "foreign_key": "target_term_id",
            "match_field": "term_id",
            "label": "relationships (as target)",
        },
    ],
    "template": [
        {
            "db": "wip_document_store",
            "collection": "documents",
            "foreign_key": "template_id",
            "match_field": "template_id",
            "label": "documents using this template",
        },
    ],
}

# PostgreSQL tables that mirror MongoDB entities
PG_TABLE_MAP = {
    "terminology": {"table": "_wip_terminologies", "id_field": "terminology_id"},
    "term": {"table": "_wip_terms", "id_field": "term_id"},
    "relationship": {"table": "_wip_term_relationships", "id_field": "relationship_id"},
    "document": None,  # Documents go into doc_{template_value} tables — handled specially
}


# ── MinIO helper ─────────────────────────────────────────────────────────

def connect_minio(args):
    """Connect to MinIO/S3. Returns (client, bucket) or (None, None)."""
    if args.no_minio:
        return None, None

    try:
        import boto3
        from botocore.config import Config
    except ImportError:
        print("  [WARN] boto3 not installed — skipping MinIO cleanup")
        return None, None

    endpoint = args.minio_endpoint or os.getenv(
        "WIP_FILE_STORAGE_ENDPOINT", "http://localhost:9000"
    )
    access_key = args.minio_access_key or os.getenv(
        "WIP_FILE_STORAGE_ACCESS_KEY", "wip-minio-root"
    )
    secret_key = args.minio_secret_key or os.getenv(
        "WIP_FILE_STORAGE_SECRET_KEY", "wip-minio-password"
    )
    bucket = args.minio_bucket or os.getenv(
        "WIP_FILE_STORAGE_BUCKET", "wip-attachments"
    )

    try:
        s3 = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            config=Config(signature_version="s3v4"),
        )
        s3.list_buckets()  # connectivity check
        return s3, bucket
    except Exception as e:
        print(f"  [WARN] Cannot connect to MinIO at {endpoint}: {e}")
        return None, None


def delete_minio_objects(s3, bucket, storage_keys, force):
    """Delete objects from MinIO by storage key."""
    if not s3 or not storage_keys:
        return
    print(f"  MinIO: {len(storage_keys)} object(s) in bucket '{bucket}'")
    if not force:
        return
    for key in storage_keys:
        try:
            s3.delete_object(Bucket=bucket, Key=key)
        except Exception as e:
            print(f"  [WARN] Failed to delete MinIO object {key}: {e}")
    print(f"  Deleted {len(storage_keys)} MinIO object(s)")


# ── PostgreSQL helper ────────────────────────────────────────────────────

def connect_postgres(args):
    """Connect to PostgreSQL. Returns connection or None."""
    if args.no_postgres:
        return None

    try:
        import psycopg2
    except ImportError:
        print("  [WARN] psycopg2 not installed — skipping PostgreSQL cleanup")
        return None

    host = args.pg_host or os.getenv("POSTGRES_HOST", "localhost")
    port = args.pg_port or int(os.getenv("POSTGRES_PORT", "5432"))
    dbname = args.pg_db or os.getenv("POSTGRES_DB", "wip_reporting")
    user = args.pg_user or os.getenv("POSTGRES_USER", "wip")
    password = args.pg_password or os.getenv("POSTGRES_PASSWORD", "wip_dev_password")

    try:
        conn = psycopg2.connect(
            host=host, port=port, dbname=dbname, user=user, password=password
        )
        conn.autocommit = True
        return conn
    except Exception as e:
        print(f"  [WARN] Cannot connect to PostgreSQL at {host}:{port}: {e}")
        return None


def delete_pg_rows(pg_conn, table, id_field, id_value, force):
    """Delete rows from a PostgreSQL table by ID."""
    if not pg_conn:
        return
    try:
        cur = pg_conn.cursor()
        cur.execute(
            f'SELECT COUNT(*) FROM "{table}" WHERE "{id_field}" = %s',
            (id_value,),
        )
        count = cur.fetchone()[0]
        if count == 0:
            return
        print(f"  PostgreSQL: {count} row(s) in {table}")
        if force:
            cur.execute(
                f'DELETE FROM "{table}" WHERE "{id_field}" = %s',
                (id_value,),
            )
            print(f"  Deleted {count} row(s) from {table}")
        cur.close()
    except Exception as e:
        print(f"  [WARN] PostgreSQL cleanup failed for {table}: {e}")


def delete_pg_document_rows(pg_conn, mongo_client, doc_ids, force):
    """Delete document rows from doc_* tables in PostgreSQL."""
    if not pg_conn or not doc_ids:
        return

    try:
        cur = pg_conn.cursor()
        # Find all doc_* tables
        cur.execute("""
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name LIKE 'doc\\_%'
        """)
        doc_tables = [row[0] for row in cur.fetchall()]

        for table in doc_tables:
            cur.execute(
                f'SELECT COUNT(*) FROM "{table}" WHERE document_id = ANY(%s)',
                (doc_ids,),
            )
            count = cur.fetchone()[0]
            if count == 0:
                continue
            print(f"  PostgreSQL: {count} row(s) in {table}")
            if force:
                cur.execute(
                    f'DELETE FROM "{table}" WHERE document_id = ANY(%s)',
                    (doc_ids,),
                )
                print(f"  Deleted {count} row(s) from {table}")
        cur.close()
    except Exception as e:
        print(f"  [WARN] PostgreSQL document cleanup failed: {e}")


# ── Core logic ───────────────────────────────────────────────────────────

def find_entity(client: MongoClient, wip_id: str, type_hint: str | None = None):
    """Find which collection(s) contain this ID."""
    found = []

    search_types = [type_hint] if type_hint else ENTITY_MAP.keys()

    for etype in search_types:
        if etype not in ENTITY_MAP:
            continue
        info = ENTITY_MAP[etype]
        db = client[info["db"]]
        coll = db[info["collection"]]
        count = coll.count_documents({info["id_field"]: wip_id})
        if count > 0:
            found.append((etype, info, count))

    return found


def delete_entity(
    client: MongoClient,
    wip_id: str,
    etype: str,
    info: dict,
    cascade: bool,
    force: bool,
    s3=None,
    s3_bucket=None,
    pg_conn=None,
):
    """Delete an entity and optionally cascade."""
    db = client[info["db"]]
    coll = db[info["collection"]]

    # Show what we found
    docs = list(coll.find({info["id_field"]: wip_id}))
    print(f"  Found {len(docs)} version(s) in {info['db']}.{info['collection']}")
    for doc in docs:
        version = doc.get("version", "-")
        status = doc.get("status", "-")
        value = doc.get("value", doc.get("data", {}).get("title", ""))
        ns = doc.get("namespace", "-")
        label = f"v{version} " if version != "-" else ""
        print(f"    {label}[{status}] ns={ns} value={_truncate(str(value), 60)}")

    # Collect MinIO storage keys for file entities
    minio_keys = []
    if etype == "file":
        minio_keys = [doc.get("storage_key") for doc in docs if doc.get("storage_key")]

    # Collect document IDs for PostgreSQL cleanup
    pg_doc_ids = []
    if etype == "document":
        pg_doc_ids = [doc.get("document_id") for doc in docs if doc.get("document_id")]

    # Cascade
    cascade_deletes = []
    if cascade and etype in CASCADE_RULES:
        for rule in CASCADE_RULES[etype]:
            match_val = docs[0].get(rule["match_field"]) if docs else wip_id
            child_db = client[rule["db"]]
            child_coll = child_db[rule["collection"]]
            child_count = child_coll.count_documents({rule["foreign_key"]: match_val})
            if child_count > 0:
                cascade_deletes.append((rule, match_val, child_count))
                print(f"  Cascade: {child_count} {rule['label']} in {rule['collection']}")

                # Collect MinIO keys from cascaded file deletions
                if rule["collection"] == "documents":
                    # Documents may have attached files
                    cascade_doc_ids = child_coll.distinct(
                        "document_id", {rule["foreign_key"]: match_val}
                    )
                    pg_doc_ids.extend(cascade_doc_ids)
                    # Find files attached to these documents
                    file_coll = client["wip_document_store"]["files"]
                    for did in cascade_doc_ids:
                        for fdoc in file_coll.find({"document_id": did}):
                            if fdoc.get("storage_key"):
                                minio_keys.append(fdoc["storage_key"])
                    if minio_keys:
                        print(f"  Cascade: {len(minio_keys)} file(s) in MinIO")

    # Registry cleanup info
    reg_db = client["wip_registry"]
    reg_coll = reg_db["registry_entries"]
    reg_count = reg_coll.count_documents({"entry_id": wip_id})
    if reg_count > 0:
        print(f"  Registry: {reg_count} entry(ies) for {wip_id}")

    # MinIO info
    if minio_keys:
        delete_minio_objects(s3, s3_bucket, minio_keys, force)

    # PostgreSQL info
    if etype == "document" and pg_doc_ids:
        delete_pg_document_rows(pg_conn, client, pg_doc_ids, force)
    elif etype in PG_TABLE_MAP and PG_TABLE_MAP[etype]:
        pg_info = PG_TABLE_MAP[etype]
        delete_pg_rows(pg_conn, pg_info["table"], pg_info["id_field"], wip_id, force)

    if not force:
        return

    # Execute MongoDB deletes
    result = coll.delete_many({info["id_field"]: wip_id})
    print(f"  Deleted {result.deleted_count} from {info['collection']}")

    # Cascade deletes
    for rule, match_val, _ in cascade_deletes:
        child_db = client[rule["db"]]
        child_coll = child_db[rule["collection"]]

        # Collect child IDs for registry cleanup
        id_field_map = {
            "terms": "term_id",
            "term_relationships": "relationship_id",
            "documents": "document_id",
        }
        child_id_field = id_field_map.get(rule["collection"])
        if child_id_field:
            child_ids = child_coll.distinct(child_id_field, {rule["foreign_key"]: match_val})
            if child_ids:
                reg_result = reg_coll.delete_many({"entry_id": {"$in": child_ids}})
                if reg_result.deleted_count:
                    print(f"  Cleaned {reg_result.deleted_count} child registry entries")

            # PostgreSQL cleanup for cascaded children
            if rule["collection"] == "documents" and child_ids:
                delete_pg_document_rows(pg_conn, client, list(child_ids), force)
            elif rule["collection"] in ("terms", "term_relationships"):
                child_etype = "term" if rule["collection"] == "terms" else "relationship"
                if child_etype in PG_TABLE_MAP and PG_TABLE_MAP[child_etype]:
                    pg_info = PG_TABLE_MAP[child_etype]
                    for cid in child_ids:
                        delete_pg_rows(pg_conn, pg_info["table"], pg_info["id_field"], cid, force)

        child_result = child_coll.delete_many({rule["foreign_key"]: match_val})
        print(f"  Cascade deleted {child_result.deleted_count} from {rule['collection']}")

        # Delete cascaded files from MongoDB (file metadata)
        if rule["collection"] == "documents" and minio_keys:
            file_coll = client["wip_document_store"]["files"]
            file_result = file_coll.delete_many({"storage_key": {"$in": minio_keys}})
            if file_result.deleted_count:
                print(f"  Cascade deleted {file_result.deleted_count} file metadata entries")

    # Registry cleanup
    if reg_count > 0:
        reg_result = reg_coll.delete_many({"entry_id": wip_id})
        print(f"  Cleaned {reg_result.deleted_count} registry entry(ies)")

    # Audit log cleanup (best effort)
    if etype in ("term", "terminology"):
        audit_coll = client["wip_def_store"]["term_audit_log"]
        if etype == "term":
            audit_result = audit_coll.delete_many({"term_id": wip_id})
        else:
            audit_result = audit_coll.delete_many({"terminology_id": wip_id})
        if audit_result.deleted_count:
            print(f"  Cleaned {audit_result.deleted_count} audit log entries")


def list_entities(client: MongoClient, etype: str, limit: int):
    """List entities in a collection."""
    info = ENTITY_MAP[etype]
    db = client[info["db"]]
    coll = db[info["collection"]]

    total = coll.estimated_document_count()
    print(f"\n{info['db']}.{info['collection']} ({total} total):\n")

    cursor = coll.find().sort("_id", -1).limit(limit)
    for doc in cursor:
        wip_id = doc.get(info["id_field"], "?")
        version = doc.get("version", "")
        status = doc.get("status", "")
        ns = doc.get("namespace", "")
        value = doc.get("value", doc.get("label", ""))
        ver_str = f" v{version}" if version else ""
        status_str = f" [{status}]" if status else ""
        val_str = f" {_truncate(str(value), 50)}" if value else ""
        print(f"  {wip_id}{ver_str}{status_str} ns={ns}{val_str}")


def _truncate(s: str, maxlen: int) -> str:
    return s if len(s) <= maxlen else s[: maxlen - 3] + "..."


def main():
    parser = argparse.ArgumentParser(
        description="Hard-delete WIP entities from MongoDB, MinIO, and PostgreSQL (dev only)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("ids", nargs="*", help="WIP IDs to delete")
    parser.add_argument(
        "--force", action="store_true",
        help="Actually delete (default is dry run)",
    )
    parser.add_argument(
        "--cascade", action="store_true",
        help="Also delete child entities (e.g., terms of a terminology)",
    )
    parser.add_argument(
        "--type", choices=list(ENTITY_MAP.keys()),
        help="Entity type (auto-detected if omitted)",
    )
    parser.add_argument(
        "--list", dest="list_type", metavar="COLLECTION",
        help="List entities (terminologies, terms, templates, documents, files, registry)",
    )
    parser.add_argument(
        "--limit", type=int, default=50,
        help="Max entries for --list (default: 50)",
    )

    # MongoDB
    parser.add_argument(
        "--mongo-uri", default="mongodb://localhost:27017/",
        help="MongoDB URI (default: mongodb://localhost:27017/)",
    )

    # MinIO
    parser.add_argument("--no-minio", action="store_true", help="Skip MinIO cleanup")
    parser.add_argument("--minio-endpoint", default=None, help="MinIO endpoint URL")
    parser.add_argument("--minio-access-key", default=None, help="MinIO access key")
    parser.add_argument("--minio-secret-key", default=None, help="MinIO secret key")
    parser.add_argument("--minio-bucket", default=None, help="MinIO bucket name")

    # PostgreSQL
    parser.add_argument("--no-postgres", action="store_true", help="Skip PostgreSQL cleanup")
    parser.add_argument("--pg-host", default=None, help="PostgreSQL host")
    parser.add_argument("--pg-port", default=None, type=int, help="PostgreSQL port")
    parser.add_argument("--pg-db", default=None, help="PostgreSQL database name")
    parser.add_argument("--pg-user", default=None, help="PostgreSQL user")
    parser.add_argument("--pg-password", default=None, help="PostgreSQL password")

    args = parser.parse_args()

    if not args.ids and not args.list_type:
        parser.print_help()
        sys.exit(1)

    client = MongoClient(args.mongo_uri)

    # Test MongoDB connection
    try:
        client.admin.command("ping")
    except Exception as e:
        print(f"Cannot connect to MongoDB at {args.mongo_uri}: {e}", file=sys.stderr)
        sys.exit(1)

    # List mode
    if args.list_type:
        etype = LIST_ALIASES.get(args.list_type, args.list_type)
        if etype not in ENTITY_MAP:
            print(f"Unknown type: {args.list_type}", file=sys.stderr)
            print(f"Valid: {', '.join(LIST_ALIASES.keys())}", file=sys.stderr)
            sys.exit(1)
        list_entities(client, etype, args.limit)
        return

    # Connect to optional backends
    s3, s3_bucket = connect_minio(args)
    pg_conn = connect_postgres(args)

    # Delete mode
    if not args.force:
        print("DRY RUN — add --force to actually delete\n")

    backends = ["MongoDB"]
    if s3:
        backends.append("MinIO")
    if pg_conn:
        backends.append("PostgreSQL")
    print(f"Backends: {', '.join(backends)}\n")

    for wip_id in args.ids:
        print(f"\n{'='*60}")
        print(f"ID: {wip_id}")
        print(f"{'='*60}")

        matches = find_entity(client, wip_id, args.type)

        if not matches:
            print("  Not found in any collection")
            # Check registry anyway
            reg = client["wip_registry"]["registry_entries"]
            reg_count = reg.count_documents({"entry_id": wip_id})
            if reg_count:
                print(f"  But found {reg_count} orphan registry entry(ies)")
                if args.force:
                    reg.delete_many({"entry_id": wip_id})
                    print("  Cleaned orphan registry entries")
            continue

        for etype, info, count in matches:
            print(f"\n  Type: {etype}")
            delete_entity(
                client, wip_id, etype, info, args.cascade, args.force,
                s3=s3, s3_bucket=s3_bucket, pg_conn=pg_conn,
            )

    if not args.force:
        print(f"\n{'='*60}")
        print("DRY RUN complete. Re-run with --force to execute.")
        print(f"{'='*60}")

    # Cleanup
    if pg_conn:
        pg_conn.close()


if __name__ == "__main__":
    main()

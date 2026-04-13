#!/usr/bin/env python3
"""
WIP Dev Delete — Hard-delete entities from MongoDB, MinIO, and PostgreSQL.

DEVELOPMENT ONLY. Bypasses soft-delete, removes all versions, and cleans
up Registry entries, MinIO blobs, and PostgreSQL rows so IDs can be re-used.

Usage:
    # Dry run (default) — shows what would be deleted
    python scripts/dev-delete.py 019abc01-def3-7abc-8def-123456789abc

    # Actually delete (by UUID or value code)
    python scripts/dev-delete.py --force 019abc01-def3-7abc-8def-123456789abc

    # Delete with full cascade (terminology → terms → relationships,
    # template → child templates → documents → files, etc.)
    python scripts/dev-delete.py --cascade --force COUNTRY

    # Delete entire namespace (cascade is implied)
    python scripts/dev-delete.py --namespace dnd --force

    # Delete by value prefix
    python scripts/dev-delete.py --prefix DND_ --type terminology --force

    # Delete by type when ID format is ambiguous
    python scripts/dev-delete.py --type template --force PATIENT_RECORD

    # Custom MongoDB URI
    python scripts/dev-delete.py --mongo-uri mongodb://localhost:27017/ --force 019abc01-...

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
import re
import sys
from pathlib import Path

from pymongo import MongoClient

# Container hostnames that need translating to localhost for host-side access.
_CONTAINER_HOSTS = ("wip-minio", "wip-mongodb", "wip-postgres", "wip-nats")


def _load_dotenv():
    """Load .env file into os.environ as a convenience fallback.

    Discovery: $WIP_ENV_FILE → ./.env → <script-dir>/../.env.
    Never overwrites existing env vars (CLI flags and explicit env win).
    Translates container-internal hostnames to localhost.
    """
    candidates = [
        os.getenv("WIP_ENV_FILE"),
        Path.cwd() / ".env",
        Path(__file__).resolve().parent.parent / ".env",
    ]
    env_file = None
    for c in candidates:
        if c and Path(c).is_file():
            env_file = Path(c)
            break
    if not env_file:
        return

    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if not key or key in os.environ:
            continue
        # Translate container hostnames to localhost for host-side access
        for chost in _CONTAINER_HOSTS:
            value = value.replace(f"//{chost}:", "//localhost:")
        os.environ[key] = value


_load_dotenv()

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

# PostgreSQL tables that mirror MongoDB entities
PG_TABLE_MAP = {
    "terminology": {"table": "_wip_terminologies", "id_field": "terminology_id"},
    "term": {"table": "_wip_terms", "id_field": "term_id"},
    "relationship": {"table": "_wip_term_relationships", "id_field": "relationship_id"},
    "document": None,  # Documents go into doc_{template_value} tables — handled specially
}

# Deletion order for namespace mode: children before parents
NAMESPACE_DELETE_ORDER = [
    "relationship",
    "document",
    "file",
    "term",
    "template",
    "terminology",
    "registry",
]


# ── Backend availability tracking ────────────────────────────────────────

# Why a backend is unavailable: "skipped" (--no-*), "missing" (ImportError),
# "unreachable" (connection failed), or None (available)
_minio_unavailable_reason: str | None = None
_postgres_unavailable_reason: str | None = None


def check_backends_for_data(client, args, s3, pg_conn, namespace=None,
                            entity_ids=None, entity_type=None):
    """Abort if data exists in a backend whose Python module is missing.

    When boto3 or psycopg2 is not installed, the script cannot clean up
    MinIO or PostgreSQL. If there is data to clean up, we must abort
    rather than silently leaving orphaned data behind.
    """
    problems = []

    # Check MinIO: are there files that need cleaning?
    if _minio_unavailable_reason == "missing" and not s3:
        has_files = False
        if namespace:
            files_coll = client["wip_document_store"]["files"]
            has_files = files_coll.count_documents({"namespace": namespace}) > 0
        elif entity_ids and entity_type in ("file", "document"):
            files_coll = client["wip_document_store"]["files"]
            if entity_type == "file":
                has_files = files_coll.count_documents(
                    {"file_id": {"$in": list(entity_ids)}}
                ) > 0
            else:
                has_files = files_coll.count_documents(
                    {"document_id": {"$in": list(entity_ids)}}
                ) > 0
        if has_files:
            problems.append(
                "boto3 is not installed but there are files in MinIO to clean up.\n"
                "  Install it:  pip install boto3\n"
                "  Or skip:     --no-minio  (leaves MinIO objects orphaned)"
            )

    # Check PostgreSQL: are there reporting tables/rows?
    if _postgres_unavailable_reason == "missing" and not pg_conn:
        has_pg_data = False
        if namespace:
            tmpl_coll = client["wip_template_store"]["templates"]
            has_pg_data = tmpl_coll.count_documents({"namespace": namespace}) > 0
        elif entity_ids and entity_type in ("terminology", "term", "relationship",
                                             "template", "document"):
            has_pg_data = True  # any of these types may have PG rows
        if has_pg_data:
            problems.append(
                "psycopg2 is not installed but there may be PostgreSQL rows to clean up.\n"
                "  Install it:  pip install psycopg2-binary\n"
                "  Or skip:     --no-postgres  (leaves PostgreSQL rows orphaned)"
            )

    if problems:
        print("\n[ERROR] Cannot proceed — missing Python modules for required backends:\n",
              file=sys.stderr)
        for p in problems:
            print(f"  {p}\n", file=sys.stderr)
        print("Aborting to prevent incomplete cleanup.", file=sys.stderr)
        sys.exit(1)


# ── MinIO helper ─────────────────────────────────────────────────────────

def connect_minio(args):
    """Connect to MinIO/S3. Returns (client, bucket) or (None, None)."""
    global _minio_unavailable_reason

    if args.no_minio:
        _minio_unavailable_reason = "skipped"
        return None, None

    try:
        import boto3
        from botocore.config import Config
    except ImportError:
        _minio_unavailable_reason = "missing"
        print("  [INFO] boto3 not installed — MinIO cleanup not available")
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
        _minio_unavailable_reason = "unreachable"
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
    global _postgres_unavailable_reason

    if args.no_postgres:
        _postgres_unavailable_reason = "skipped"
        return None

    try:
        import psycopg2
    except ImportError:
        _postgres_unavailable_reason = "missing"
        print("  [INFO] psycopg2 not installed — PostgreSQL cleanup not available")
        return None

    host = args.pg_host or os.getenv("POSTGRES_HOST", "localhost")
    port = args.pg_port or int(os.getenv("POSTGRES_PORT", "5432"))
    dbname = args.pg_db or os.getenv("POSTGRES_DB", "wip_reporting")
    user = args.pg_user or os.getenv("WIP_POSTGRES_USER") or os.getenv("POSTGRES_USER", "wip")
    password = args.pg_password or os.getenv("WIP_POSTGRES_PASSWORD") or os.getenv("POSTGRES_PASSWORD", "wip_dev_password")

    try:
        conn = psycopg2.connect(
            host=host, port=port, dbname=dbname, user=user, password=password
        )
        conn.autocommit = True
        return conn
    except Exception as e:
        _postgres_unavailable_reason = "unreachable"
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


def delete_pg_rows_bulk(pg_conn, table, id_field, id_values, force):
    """Delete rows from a PostgreSQL table by a list of IDs."""
    if not pg_conn or not id_values:
        return
    try:
        cur = pg_conn.cursor()
        cur.execute(
            f'SELECT COUNT(*) FROM "{table}" WHERE "{id_field}" = ANY(%s)',
            (list(id_values),),
        )
        count = cur.fetchone()[0]
        if count == 0:
            return
        print(f"  PostgreSQL: {count} row(s) in {table}")
        if force:
            cur.execute(
                f'DELETE FROM "{table}" WHERE "{id_field}" = ANY(%s)',
                (list(id_values),),
            )
            print(f"  Deleted {count} row(s) from {table}")
        cur.close()
    except Exception as e:
        print(f"  [WARN] PostgreSQL bulk cleanup failed for {table}: {e}")


def delete_pg_document_rows(pg_conn, doc_ids, force):
    """Delete document rows from doc_* tables in PostgreSQL."""
    if not pg_conn or not doc_ids:
        return

    try:
        cur = pg_conn.cursor()
        cur.execute("""
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name LIKE 'doc\\_%'
        """)
        doc_tables = [row[0] for row in cur.fetchall()]

        for table in doc_tables:
            cur.execute(
                f'SELECT COUNT(*) FROM "{table}" WHERE document_id = ANY(%s)',
                (list(doc_ids),),
            )
            count = cur.fetchone()[0]
            if count == 0:
                continue
            print(f"  PostgreSQL: {count} row(s) in {table}")
            if force:
                cur.execute(
                    f'DELETE FROM "{table}" WHERE document_id = ANY(%s)',
                    (list(doc_ids),),
                )
                print(f"  Deleted {count} row(s) from {table}")
        cur.close()
    except Exception as e:
        print(f"  [WARN] PostgreSQL document cleanup failed: {e}")


def drop_pg_doc_table(pg_conn, template_value, force):
    """Drop a doc_* table from PostgreSQL when deleting a template."""
    if not pg_conn or not template_value:
        return
    table_name = f"doc_{template_value.lower()}"
    try:
        cur = pg_conn.cursor()
        cur.execute("""
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = %s
        """, (table_name,))
        if not cur.fetchone():
            return
        cur.execute(f'SELECT COUNT(*) FROM "{table_name}"')
        count = cur.fetchone()[0]
        print(f"  PostgreSQL: table {table_name} ({count} row(s))")
        if force:
            cur.execute(f'DROP TABLE IF EXISTS "{table_name}" CASCADE')
            print(f"  Dropped table {table_name}")
            # Also remove migration tracking
            cur.execute(
                'DELETE FROM "_wip_schema_migrations" WHERE template_value = %s',
                (template_value,),
            )
        cur.close()
    except Exception as e:
        print(f"  [WARN] PostgreSQL table drop failed for {table_name}: {e}")


# ── File collection helpers ──────────────────────────────────────────────

def collect_file_refs_from_documents(client, doc_query):
    """Collect file_ids and storage_keys from documents matching a query."""
    doc_coll = client["wip_document_store"]["documents"]
    file_coll = client["wip_document_store"]["files"]
    file_ids = set()
    storage_keys = []

    for doc in doc_coll.find(doc_query, {"file_references": 1}):
        for ref in (doc.get("file_references") or []):
            fid = ref.get("file_id")
            if fid:
                file_ids.add(fid)

    if file_ids:
        for fdoc in file_coll.find({"file_id": {"$in": list(file_ids)}}):
            sk = fdoc.get("storage_key")
            if sk:
                storage_keys.append(sk)

    return list(file_ids), storage_keys


def collect_files_by_namespace(client, namespace):
    """Collect all files in a namespace."""
    file_coll = client["wip_document_store"]["files"]
    file_ids = []
    storage_keys = []
    for fdoc in file_coll.find({"namespace": namespace}):
        file_ids.append(fdoc.get("file_id"))
        sk = fdoc.get("storage_key")
        if sk:
            storage_keys.append(sk)
    return file_ids, storage_keys


# ── Core delete logic ────────────────────────────────────────────────────

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
    """Delete an entity and optionally cascade to all dependents."""
    db = client[info["db"]]
    coll = db[info["collection"]]
    reg_coll = client["wip_registry"]["registry_entries"]

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

    # ── Collect MinIO keys ───────────────────────────────────────────
    minio_keys = []
    file_ids_to_delete = []

    if etype == "file":
        minio_keys = [doc.get("storage_key") for doc in docs if doc.get("storage_key")]
        file_ids_to_delete = [doc.get("file_id") for doc in docs if doc.get("file_id")]

    elif etype == "document":
        # Documents may reference files via file_references
        fids, skeys = collect_file_refs_from_documents(
            client, {info["id_field"]: wip_id}
        )
        file_ids_to_delete.extend(fids)
        minio_keys.extend(skeys)
        if fids:
            print(f"  Referenced files: {len(fids)} file(s)")

    # ── Collect document IDs for PostgreSQL cleanup ──────────────────
    pg_doc_ids = []
    if etype == "document":
        pg_doc_ids = [doc.get("document_id") for doc in docs if doc.get("document_id")]

    # ── Cascade ──────────────────────────────────────────────────────
    cascade_plan = []  # [(collection_info, query, label)]

    if cascade:
        if etype == "terminology":
            term_id = docs[0].get("terminology_id") if docs else wip_id
            _plan_terminology_cascade(
                client, term_id, cascade_plan, pg_doc_ids,
                file_ids_to_delete, minio_keys, pg_conn
            )

        elif etype == "template":
            template_id = docs[0].get("template_id") if docs else wip_id
            template_value = docs[0].get("value") if docs else None
            _plan_template_cascade(
                client, template_id, template_value, cascade_plan,
                pg_doc_ids, file_ids_to_delete, minio_keys, pg_conn
            )

        elif etype == "term":
            term_id = docs[0].get("term_id") if docs else wip_id
            _plan_term_cascade(client, term_id, cascade_plan)

        elif etype == "document":
            # Document → files cascade already handled above
            pass

    # Print cascade summary
    for label, count, _ in cascade_plan:
        print(f"  Cascade: {count} {label}")

    # ── Registry cleanup info ────────────────────────────────────────
    reg_count = reg_coll.count_documents({"entry_id": wip_id})
    if reg_count > 0:
        print(f"  Registry: {reg_count} entry(ies) for {wip_id}")

    # ── MinIO cleanup ────────────────────────────────────────────────
    if minio_keys:
        delete_minio_objects(s3, s3_bucket, minio_keys, force)

    # ── PostgreSQL cleanup ───────────────────────────────────────────
    if etype == "document" and pg_doc_ids:
        delete_pg_document_rows(pg_conn, pg_doc_ids, force)
    elif etype == "template" and cascade:
        # Drop the whole doc_* table when cascading template deletion
        template_value = docs[0].get("value") if docs else None
        if template_value:
            drop_pg_doc_table(pg_conn, template_value, force)
    elif PG_TABLE_MAP.get(etype):
        pg_info = PG_TABLE_MAP[etype]
        delete_pg_rows(pg_conn, pg_info["table"], pg_info["id_field"], wip_id, force)

    if not force:
        return

    # ── Execute cascade deletes (children first) ─────────────────────
    for _label, _count, delete_fn in cascade_plan:
        delete_fn()

    # Delete file metadata for cascaded files
    if file_ids_to_delete and etype != "file":
        file_coll = client["wip_document_store"]["files"]
        fr = file_coll.delete_many({"file_id": {"$in": file_ids_to_delete}})
        if fr.deleted_count:
            print(f"  Deleted {fr.deleted_count} file metadata entries")
        # Registry cleanup for files
        fr2 = reg_coll.delete_many({"entry_id": {"$in": file_ids_to_delete}})
        if fr2.deleted_count:
            print(f"  Cleaned {fr2.deleted_count} file registry entries")

    # Delete the entity itself
    result = coll.delete_many({info["id_field"]: wip_id})
    print(f"  Deleted {result.deleted_count} from {info['collection']}")

    # Registry cleanup for the entity
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


# ── Cascade planners ─────────────────────────────────────────────────────

def _plan_terminology_cascade(client, terminology_id, cascade_plan, pg_doc_ids,
                               file_ids_to_delete, minio_keys, pg_conn):
    """Plan cascade: terminology → terms → relationships.
    Also warn about templates that reference this terminology."""
    reg_coll = client["wip_registry"]["registry_entries"]
    term_coll = client["wip_def_store"]["terms"]
    rel_coll = client["wip_def_store"]["term_relationships"]

    # Terms in this terminology
    term_ids = term_coll.distinct("term_id", {"terminology_id": terminology_id})
    if term_ids:
        cascade_plan.append((
            "terms in terminology",
            len(term_ids),
            lambda ids=list(term_ids): _exec_cascade_delete(
                client, "wip_def_store", "terms",
                {"terminology_id": terminology_id}, "term_id", ids,
                reg_coll, pg_conn, PG_TABLE_MAP.get("term"),
            ),
        ))

        # Relationships involving these terms
        rel_query = {"$or": [
            {"source_term_id": {"$in": term_ids}},
            {"target_term_id": {"$in": term_ids}},
        ]}
        rel_count = rel_coll.count_documents(rel_query)
        if rel_count:
            rel_ids = rel_coll.distinct("relationship_id", rel_query)
            cascade_plan.append((
                "relationships involving terms",
                rel_count,
                lambda q=rel_query, ids=rel_ids: _exec_cascade_delete(
                    client, "wip_def_store", "term_relationships",
                    q, "relationship_id", ids,
                    reg_coll, pg_conn, PG_TABLE_MAP.get("relationship"),
                ),
            ))

    # Relationships referencing the terminology directly
    trel_query = {"$or": [
        {"source_terminology_id": terminology_id},
        {"target_terminology_id": terminology_id},
    ]}
    trel_count = rel_coll.count_documents(trel_query)
    if trel_count:
        trel_ids = rel_coll.distinct("relationship_id", trel_query)
        cascade_plan.append((
            "relationships referencing terminology",
            trel_count,
            lambda q=trel_query, ids=trel_ids: _exec_cascade_delete(
                client, "wip_def_store", "term_relationships",
                q, "relationship_id", ids,
                reg_coll, pg_conn, PG_TABLE_MAP.get("relationship"),
            ),
        ))

    # Audit log for terms
    if term_ids:
        audit_coll = client["wip_def_store"]["term_audit_log"]
        audit_count = audit_coll.count_documents({"terminology_id": terminology_id})
        if audit_count:
            cascade_plan.append((
                "audit log entries",
                audit_count,
                lambda: audit_coll.delete_many({"terminology_id": terminology_id}),
            ))

    # Warn about templates referencing this terminology (not auto-deleted)
    tmpl_coll = client["wip_template_store"]["templates"]
    referencing = tmpl_coll.count_documents({
        "$or": [
            {"fields.terminology_ref": terminology_id},
            {"fields.array_terminology_ref": terminology_id},
        ]
    })
    if referencing:
        print(f"  [WARN] {referencing} template(s) reference this terminology — not auto-deleted")


def _plan_template_cascade(client, template_id, template_value, cascade_plan,
                            pg_doc_ids, file_ids_to_delete, minio_keys, pg_conn):
    """Plan cascade: template → child templates (recursive) → documents → files."""
    reg_coll = client["wip_registry"]["registry_entries"]
    tmpl_coll = client["wip_template_store"]["templates"]
    doc_coll = client["wip_document_store"]["documents"]

    # Collect all template_ids to delete (self + descendants via extends)
    all_template_ids = _collect_template_tree(client, template_id)
    child_ids = [tid for tid in all_template_ids if tid != template_id]

    if child_ids:
        cascade_plan.append((
            "child template(s) (recursive inheritance)",
            len(child_ids),
            lambda ids=child_ids: _exec_cascade_delete(
                client, "wip_template_store", "templates",
                {"template_id": {"$in": ids}}, "template_id", ids,
                reg_coll, None, None,  # PG table drop handled separately
            ),
        ))
        # Drop PG tables for child templates
        for cid in child_ids:
            child_val = tmpl_coll.find_one({"template_id": cid}, {"value": 1})
            if child_val and child_val.get("value"):
                drop_pg_doc_table(pg_conn, child_val["value"], False)  # report only in plan

    # Documents using any of these templates
    doc_query = {"template_id": {"$in": all_template_ids}}
    doc_count = doc_coll.count_documents(doc_query)
    if doc_count:
        doc_ids = doc_coll.distinct("document_id", doc_query)
        pg_doc_ids.extend(doc_ids)

        # Files referenced by these documents
        fids, skeys = collect_file_refs_from_documents(client, doc_query)
        file_ids_to_delete.extend(fids)
        minio_keys.extend(skeys)

        cascade_plan.append((
            f"documents across {len(all_template_ids)} template(s)",
            doc_count,
            lambda q=doc_query, ids=list(doc_ids): _exec_cascade_delete(
                client, "wip_document_store", "documents",
                q, "document_id", ids,
                reg_coll, pg_conn, None,  # PG handled via doc_ids
            ),
        ))

        if pg_doc_ids:
            cascade_plan.append((
                "PostgreSQL document rows",
                len(pg_doc_ids),
                lambda ids=list(pg_doc_ids): delete_pg_document_rows(pg_conn, ids, True),
            ))

        if fids:
            cascade_plan.append((
                "files referenced by documents",
                len(fids),
                lambda: None,  # actual deletion handled in delete_entity
            ))

    # Drop PG table for the main template
    if template_value:
        cascade_plan.append((
            f"PostgreSQL table doc_{template_value.lower()}",
            1,
            lambda tv=template_value: drop_pg_doc_table(pg_conn, tv, True),
        ))


def _plan_term_cascade(client, term_id, cascade_plan):
    """Plan cascade: term → relationships."""
    reg_coll = client["wip_registry"]["registry_entries"]
    rel_coll = client["wip_def_store"]["term_relationships"]

    rel_query = {"$or": [
        {"source_term_id": term_id},
        {"target_term_id": term_id},
    ]}
    rel_count = rel_coll.count_documents(rel_query)
    if rel_count:
        rel_ids = rel_coll.distinct("relationship_id", rel_query)
        cascade_plan.append((
            "relationships involving this term",
            rel_count,
            lambda q=rel_query, ids=rel_ids: _exec_cascade_delete(
                client, "wip_def_store", "term_relationships",
                q, "relationship_id", ids,
                reg_coll, None, PG_TABLE_MAP.get("relationship"),
            ),
        ))


def _collect_template_tree(client, root_template_id):
    """Recursively collect template_id for a template and all its descendants."""
    tmpl_coll = client["wip_template_store"]["templates"]
    collected = set()
    to_process = [root_template_id]

    while to_process:
        tid = to_process.pop()
        if tid in collected:
            continue
        collected.add(tid)
        # Find templates that extend this one
        children = tmpl_coll.distinct("template_id", {"extends": tid})
        to_process.extend(children)

    return list(collected)


def _exec_cascade_delete(client, db_name, collection_name, query, id_field,
                          entity_ids, reg_coll, pg_conn, pg_map):
    """Execute a cascade delete: remove from MongoDB, Registry, and PostgreSQL."""
    coll = client[db_name][collection_name]
    result = coll.delete_many(query)
    print(f"  Cascade deleted {result.deleted_count} from {collection_name}")

    # Registry cleanup
    if entity_ids:
        reg_result = reg_coll.delete_many({"entry_id": {"$in": entity_ids}})
        if reg_result.deleted_count:
            print(f"  Cleaned {reg_result.deleted_count} registry entries for {collection_name}")

    # PostgreSQL cleanup
    if pg_conn and pg_map and entity_ids:
        delete_pg_rows_bulk(pg_conn, pg_map["table"], pg_map["id_field"], entity_ids, True)


# ── Namespace deletion ───────────────────────────────────────────────────

def delete_namespace(client, namespace, force, s3, s3_bucket, pg_conn):
    """Delete all entities in a namespace across all collections."""
    print(f"\n{'='*60}")
    print(f"NAMESPACE: {namespace}")
    print(f"{'='*60}")

    reg_coll = client["wip_registry"]["registry_entries"]

    # Inventory
    totals = {}
    for etype in NAMESPACE_DELETE_ORDER:
        if etype == "registry":
            continue
        info = ENTITY_MAP[etype]
        coll = client[info["db"]][info["collection"]]
        count = coll.count_documents({"namespace": namespace})
        if count > 0:
            totals[etype] = count

    # Registry entries for this namespace
    reg_count = reg_coll.count_documents({"namespace": namespace})

    # Check if namespace record exists
    ns_coll = client["wip_registry"]["namespaces"]
    ns_exists = ns_coll.find_one({"prefix": namespace}) is not None

    if not totals and reg_count == 0 and not ns_exists:
        print(f"  Namespace '{namespace}' does not exist — nothing to delete")
        return

    if not totals and reg_count == 0 and ns_exists:
        print(f"  Namespace '{namespace}' has no entities — only the namespace record remains")
        if force:
            ns_coll.delete_one({"prefix": namespace})
            print(f"  Removed namespace record '{namespace}'")
            counters_coll = client["wip_registry"]["counters"]
            cr = counters_coll.delete_many({"_id": {"$regex": f"^{namespace}:"}})
            if cr.deleted_count:
                print(f"  Cleaned {cr.deleted_count} ID counter(s)")
            print(f"\n  Namespace '{namespace}' deleted.")
        return

    print(f"\n  Impact report for namespace '{namespace}':")
    for etype, count in totals.items():
        print(f"    {etype:20s} {count:>6}")
    if reg_count:
        print(f"    {'registry':20s} {reg_count:>6}")
    total_entities = sum(totals.values()) + reg_count
    print(f"    {'─'*28}")
    print(f"    {'TOTAL':20s} {total_entities:>6}")

    # Collect files for MinIO cleanup
    _file_ids, minio_keys = collect_files_by_namespace(client, namespace)
    if minio_keys:
        print(f"\n  MinIO: {len(minio_keys)} file object(s) to delete")

    # Collect template values for PG table drops
    tmpl_coll = client["wip_template_store"]["templates"]
    template_values = tmpl_coll.distinct("value", {"namespace": namespace})
    if template_values and pg_conn:
        print(f"  PostgreSQL: {len(template_values)} doc_* table(s) to drop")

    if not force:
        return

    # Delete in dependency order
    for etype in NAMESPACE_DELETE_ORDER:
        if etype == "registry":
            continue
        if etype not in totals:
            continue
        info = ENTITY_MAP[etype]
        coll = client[info["db"]][info["collection"]]

        # Collect IDs for registry cleanup
        entity_ids = coll.distinct(info["id_field"], {"namespace": namespace})

        result = coll.delete_many({"namespace": namespace})
        print(f"  Deleted {result.deleted_count} from {info['collection']}")

        # Registry cleanup for these entities
        if entity_ids:
            rr = reg_coll.delete_many({"entry_id": {"$in": entity_ids}})
            if rr.deleted_count:
                print(f"    Cleaned {rr.deleted_count} registry entries")

        # PostgreSQL cleanup
        if etype in PG_TABLE_MAP and PG_TABLE_MAP[etype] and pg_conn:
            delete_pg_rows_bulk(
                pg_conn, PG_TABLE_MAP[etype]["table"],
                PG_TABLE_MAP[etype]["id_field"], entity_ids, True
            )

    # Drop PG doc_* tables
    for tv in template_values:
        drop_pg_doc_table(pg_conn, tv, True)

    # Delete PG document rows (in case any survived table drops)
    # MinIO cleanup
    if minio_keys:
        delete_minio_objects(s3, s3_bucket, minio_keys, True)

    # Remaining registry entries (namespace-level, not tied to entities)
    remaining_reg = reg_coll.count_documents({"namespace": namespace})
    if remaining_reg:
        reg_coll.delete_many({"namespace": namespace})
        print(f"  Cleaned {remaining_reg} remaining registry entries")

    # Audit log cleanup
    audit_coll = client["wip_def_store"]["term_audit_log"]
    audit_result = audit_coll.delete_many({"namespace": namespace})
    if audit_result.deleted_count:
        print(f"  Cleaned {audit_result.deleted_count} audit log entries")

    # Delete the namespace record itself from the registry
    ns_coll = client["wip_registry"]["namespaces"]
    ns_doc = ns_coll.find_one({"prefix": namespace})
    if ns_doc:
        ns_coll.delete_one({"prefix": namespace})
        print(f"  Removed namespace record '{namespace}'")
        # Also clean up the counters collection for this namespace
        counters_coll = client["wip_registry"]["counters"]
        cr = counters_coll.delete_many({"_id": {"$regex": f"^{namespace}:"}})
        if cr.deleted_count:
            print(f"  Cleaned {cr.deleted_count} ID counter(s)")

    print(f"\n  Namespace '{namespace}' deleted.")


def delete_namespace_by_type(client, namespace, type_filter, force, s3, s3_bucket, pg_conn):
    """Delete only entities of a specific type within a namespace."""
    print(f"\n{'='*60}")
    print(f"NAMESPACE: {namespace}  TYPE: {type_filter}")
    print(f"{'='*60}")

    info = ENTITY_MAP[type_filter]
    coll = client[info["db"]][info["collection"]]
    count = coll.count_documents({"namespace": namespace})

    if count == 0:
        print(f"  No {type_filter} entities in namespace '{namespace}'")
        return

    print("\n  Impact report:")
    print(f"    {type_filter:20s} {count:>6}")

    # Collect file info for MinIO cleanup if deleting files
    minio_keys = []
    if type_filter == "file" and s3:
        _file_ids, minio_keys = collect_files_by_namespace(client, namespace)
        if minio_keys:
            print(f"\n  MinIO: {len(minio_keys)} file object(s) to delete")

    # Collect template values for PG table drops if deleting documents
    template_values = []
    if type_filter == "document" and pg_conn:
        tmpl_coll = client["wip_template_store"]["templates"]
        template_values = tmpl_coll.distinct("value", {"namespace": namespace})
        if template_values:
            print(f"  PostgreSQL: {len(template_values)} doc_* table(s) to drop")

    if not force:
        return

    # Collect IDs for registry cleanup
    entity_ids = coll.distinct(info["id_field"], {"namespace": namespace})

    result = coll.delete_many({"namespace": namespace})
    print(f"  Deleted {result.deleted_count} from {info['collection']}")

    # Registry cleanup for these entities
    reg_coll = client["wip_registry"]["registry_entries"]
    if entity_ids:
        rr = reg_coll.delete_many({"entry_id": {"$in": entity_ids}})
        if rr.deleted_count:
            print(f"    Cleaned {rr.deleted_count} registry entries")

    # PostgreSQL cleanup
    if type_filter in PG_TABLE_MAP and PG_TABLE_MAP[type_filter] and pg_conn:
        delete_pg_rows_bulk(
            pg_conn, PG_TABLE_MAP[type_filter]["table"],
            PG_TABLE_MAP[type_filter]["id_field"], entity_ids, True
        )

    # Drop PG doc_* tables for document deletion
    if type_filter == "document":
        for tv in template_values:
            drop_pg_doc_table(pg_conn, tv, True)

    # MinIO cleanup for file deletion
    if type_filter == "file" and minio_keys:
        delete_minio_objects(s3, s3_bucket, minio_keys, True)

    print(f"\n  Deleted {count} {type_filter}(s) from namespace '{namespace}'.")


# ── Prefix deletion ──────────────────────────────────────────────────────

def delete_by_prefix(client, prefix, type_filter, cascade, force, s3, s3_bucket, pg_conn):
    """Delete entities whose value matches a prefix."""
    # Only terminologies and templates have a 'value' field
    search_types = [type_filter] if type_filter else ["terminology", "template"]

    print(f"\n{'='*60}")
    print(f"PREFIX: {prefix}*" + (f" (type: {type_filter})" if type_filter else ""))
    print(f"{'='*60}")

    found_ids = []

    for etype in search_types:
        info = ENTITY_MAP[etype]
        coll = client[info["db"]][info["collection"]]
        regex = re.compile(f"^{re.escape(prefix)}", re.IGNORECASE)
        matches = list(coll.find({"value": regex}, {info["id_field"]: 1, "value": 1}))

        if not matches:
            continue

        unique_ids = list(set(doc[info["id_field"]] for doc in matches))
        unique_values = list(set(doc.get("value", "?") for doc in matches))
        print(f"\n  {etype}: {len(unique_ids)} matching entity(ies)")
        for v in sorted(unique_values)[:20]:
            print(f"    {v}")
        if len(unique_values) > 20:
            print(f"    ... and {len(unique_values) - 20} more")

        for uid in unique_ids:
            found_ids.append((uid, etype))

    if not found_ids:
        print("  No entities found matching prefix")
        return

    if not force:
        return

    for uid, etype in found_ids:
        info = ENTITY_MAP[etype]
        print(f"\n  Deleting {etype} {uid}...")
        delete_entity(
            client, uid, etype, info, cascade, force,
            s3=s3, s3_bucket=s3_bucket, pg_conn=pg_conn,
        )


# ── List and utilities ───────────────────────────────────────────────────

def list_entities(client: MongoClient, etype: str, limit: int, namespace: str | None = None):
    """List entities in a collection."""
    info = ENTITY_MAP[etype]
    db = client[info["db"]]
    coll = db[info["collection"]]

    query = {"namespace": namespace} if namespace else {}
    total = coll.count_documents(query)
    ns_label = f" (namespace: {namespace})" if namespace else ""
    print(f"\n{info['db']}.{info['collection']}{ns_label} ({total} total):\n")

    cursor = coll.find(query).sort("_id", -1).limit(limit)
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
        help="Also delete child entities (terminology→terms→relationships, "
             "template→child templates→documents→files)",
    )
    parser.add_argument(
        "--type", choices=[k for k in ENTITY_MAP if k != "registry"],
        help="Entity type (auto-detected if omitted)",
    )
    parser.add_argument(
        "--namespace",
        help="Delete ALL entities in a namespace (cascade is implied)",
    )
    parser.add_argument(
        "--prefix",
        help="Delete entities whose value starts with this prefix",
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

    if not args.ids and not args.list_type and not args.namespace and not args.prefix:
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
        list_entities(client, etype, args.limit, args.namespace)
        return

    # Connect to optional backends
    s3, s3_bucket = connect_minio(args)
    pg_conn = connect_postgres(args)

    # Pre-flight: abort early if Python modules are missing but data exists
    if args.namespace:
        check_backends_for_data(client, args, s3, pg_conn,
                                namespace=args.namespace)
    elif args.prefix:
        check_backends_for_data(client, args, s3, pg_conn,
                                namespace=args.namespace,
                                entity_type=args.type)
    elif args.ids:
        all_entity_ids = set(args.ids)
        all_types = set()
        for wip_id in args.ids:
            matches = find_entity(client, wip_id, args.type)
            for etype, _info, _count in matches:
                all_types.add(etype)
        for etype in all_types:
            check_backends_for_data(client, args, s3, pg_conn,
                                    entity_ids=all_entity_ids,
                                    entity_type=etype)

    if not args.force:
        print("DRY RUN — add --force to actually delete\n")

    backends = ["MongoDB"]
    if s3:
        backends.append("MinIO")
    if pg_conn:
        backends.append("PostgreSQL")
    print(f"Backends: {', '.join(backends)}")

    # Namespace mode
    if args.namespace:
        if args.type:
            delete_namespace_by_type(client, args.namespace, args.type, args.force, s3, s3_bucket, pg_conn)
        else:
            delete_namespace(client, args.namespace, args.force, s3, s3_bucket, pg_conn)
        if not args.force:
            print(f"\n{'='*60}")
            print("DRY RUN complete. Re-run with --force to execute.")
            print(f"{'='*60}")
        if pg_conn:
            pg_conn.close()
        return

    # Prefix mode
    if args.prefix:
        delete_by_prefix(
            client, args.prefix, args.type,
            True,  # cascade implied for prefix
            args.force, s3, s3_bucket, pg_conn,
        )
        if not args.force:
            print(f"\n{'='*60}")
            print("DRY RUN complete. Re-run with --force to execute.")
            print(f"{'='*60}")
        if pg_conn:
            pg_conn.close()
        return

    # ID mode
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

        for etype, info, _count in matches:
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

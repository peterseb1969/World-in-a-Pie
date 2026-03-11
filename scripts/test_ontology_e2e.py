#!/usr/bin/env python3
"""
End-to-end ontology test for World In a Pie.

Tests the full lifecycle:
1. Create a hand-crafted mini medical ontology with polyhierarchy and typed relationships
2. Import ICD-10-GM from testdata/ as a real-world terminology with hierarchy
3. Create templates that reference ontology terms
4. Create documents that use ontology terms
5. Verify traversal queries work correctly

Usage:
    # Against localhost (direct ports)
    python scripts/test_ontology_e2e.py

    # Against a remote host via proxy
    python scripts/test_ontology_e2e.py --host wip-pi.local --via-proxy

    # Only run the mini ontology test (no ICD-10 import)
    python scripts/test_ontology_e2e.py --skip-icd10

    # Only import ICD-10 (skip mini ontology)
    python scripts/test_ontology_e2e.py --skip-mini
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

API_KEY = os.getenv("WIP_API_KEY", "dev_master_key_for_testing")
NAMESPACE = "wip"
HEADERS = {"X-API-Key": API_KEY, "Content-Type": "application/json"}

# Counters
passed = 0
failed = 0
errors: list[str] = []


def configure_urls(host: str, via_proxy: bool):
    global DEF_STORE, TEMPLATE_STORE, DOCUMENT_STORE
    if via_proxy:
        base = f"https://{host}:8443"
        DEF_STORE = f"{base}/api/def-store"
        TEMPLATE_STORE = f"{base}/api/template-store"
        DOCUMENT_STORE = f"{base}/api/document-store"
    else:
        DEF_STORE = f"http://{host}:8002/api/def-store"
        TEMPLATE_STORE = f"http://{host}:8003/api/template-store"
        DOCUMENT_STORE = f"http://{host}:8004/api/document-store"


def req(method: str, url: str, **kwargs) -> requests.Response:
    """Make an HTTP request with default headers and error handling."""
    kwargs.setdefault("headers", HEADERS)
    kwargs.setdefault("verify", False)
    kwargs.setdefault("timeout", 30)
    resp = getattr(requests, method)(url, **kwargs)
    return resp


def check(label: str, condition: bool, detail: str = ""):
    """Record a test result."""
    global passed, failed
    if condition:
        passed += 1
        print(f"  ✓ {label}")
    else:
        failed += 1
        msg = f"  ✗ {label}" + (f" — {detail}" if detail else "")
        print(msg)
        errors.append(msg)


def bulk_ok(resp: requests.Response, expected_count: int = 1) -> dict:
    """Assert a bulk response succeeded (status: created, updated, or skipped)."""
    data = resp.json()
    ok_count = sum(1 for r in data.get("results", [])
                   if r.get("status") in ("created", "updated", "skipped"))
    check(
        f"Bulk OK: {ok_count}/{data.get('total', 0)} succeeded",
        resp.status_code == 200 and ok_count >= expected_count,
        f"status={resp.status_code}, body={json.dumps(data)[:200]}"
    )
    return data


def create_or_get_template(value: str, body: dict) -> str | None:
    """Create a template or return existing template ID if it already exists."""
    resp = req("post", f"{TEMPLATE_STORE}/templates", json=[body])
    data = resp.json()
    result = data["results"][0]
    if result["status"] == "created":
        check(f"Created template {value}", True)
        return result["id"]
    elif "already exists" in result.get("error", ""):
        lookup = req("get", f"{TEMPLATE_STORE}/templates/by-value/{value}")
        tid = lookup.json()["template_id"]
        check(f"Template {value} exists (reusing {tid[:8]}...)", True)
        return tid
    else:
        check(f"Created template {value}", False, result.get("error"))
        return None


# ===========================================================================
# TEST 1: Hand-Crafted Mini Medical Ontology
# ===========================================================================

def test_mini_ontology():
    """
    Build a small medical ontology to exercise all features:

    Terminologies:
      - BODY_SYSTEMS: Circulatory, Respiratory, ...
      - ANATOMY: Heart, Lung, Aorta, Left Ventricle, ...
      - CONDITIONS: Pneumonia, Viral Pneumonia, Heart Failure, ...

    Relationships:
      - Viral Pneumonia is_a Pneumonia
      - Viral Pneumonia is_a Viral Respiratory Infection  (polyhierarchy!)
      - Heart part_of Circulatory System
      - Left Ventricle part_of Heart
      - Aorta part_of Circulatory System
      - Pneumonia finding_site Lung
      - Heart Failure finding_site Heart

    Templates:
      - PATIENT: name, date_of_birth
      - DIAGNOSIS: patient_ref, condition (term from CONDITIONS), site (term from ANATOMY)

    Documents:
      - A patient document
      - A diagnosis document referencing an ontology term
    """
    print("\n" + "=" * 60)
    print("TEST 1: Hand-Crafted Mini Medical Ontology")
    print("=" * 60)

    term_ids = {}  # value -> term_id

    # --- Create terminologies ---
    print("\n--- Creating terminologies ---")

    for term_def in [
        {"value": "BODY_SYSTEMS", "label": "Body Systems", "namespace": NAMESPACE},
        {"value": "ANATOMY", "label": "Anatomy", "namespace": NAMESPACE},
        {"value": "CONDITIONS", "label": "Medical Conditions", "namespace": NAMESPACE},
    ]:
        resp = req("post", f"{DEF_STORE}/terminologies",
                    json=[term_def])
        data = resp.json()
        result = data["results"][0]
        if result["status"] == "created":
            term_ids[term_def["value"]] = result["id"]
            check(f"Created {term_def['value']}", True)
        elif result["status"] == "error" and "already exists" in result.get("error", ""):
            # Look up existing terminology
            lookup = req("get", f"{DEF_STORE}/terminologies/by-value/{term_def['value']}")
            tid = lookup.json()["terminology_id"]
            term_ids[term_def["value"]] = tid
            check(f"{term_def['value']} exists (reusing {tid[:8]}...)", True)
        else:
            check(f"Created {term_def['value']}", False, result.get("error", "unknown error"))

    # --- Create terms ---
    print("\n--- Creating terms ---")

    terminology_terms = {
        "BODY_SYSTEMS": [
            "Circulatory System", "Respiratory System",
        ],
        "ANATOMY": [
            "Heart", "Left Ventricle", "Aorta", "Lung", "Bronchus",
        ],
        "CONDITIONS": [
            "Disease", "Respiratory Disease", "Viral Respiratory Infection",
            "Pneumonia", "Viral Pneumonia", "Bacterial Pneumonia",
            "Cardiac Disease", "Heart Failure",
        ],
    }

    for terminology_value, terms in terminology_terms.items():
        tid = term_ids[terminology_value]
        term_requests = [{"value": t} for t in terms]
        resp = req("post", f"{DEF_STORE}/terminologies/{tid}/terms",
                    json=term_requests)
        data = resp.json()

        created = 0
        existed = 0
        for r in data.get("results", []):
            if r["status"] == "created":
                term_ids[r["value"]] = r["id"]
                created += 1
            elif r["status"] in ("skipped", "error") and "lready exists" in r.get("error", ""):
                existed += 1
                # Look up term by searching (use larger page_size for partial match)
                search_resp = req("get", f"{DEF_STORE}/terminologies/{tid}/terms",
                                   params={"search": r.get("value", ""), "page_size": 100})
                search_data = search_resp.json()
                for item in search_data.get("items", []):
                    if item["value"] == r.get("value"):
                        term_ids[item["value"]] = item["term_id"]
                        break

        check(f"{terminology_value}: {created} created, {existed} existed",
              created + existed == len(terms),
              f"expected {len(terms)}, got {created + existed}")

    print(f"\n  Created {len(term_ids)} entities total")

    # --- Create relationships ---
    print("\n--- Creating relationships (including polyhierarchy) ---")

    relationships = [
        # CONDITIONS hierarchy — with polyhierarchy
        ("Respiratory Disease", "Disease", "is_a"),
        ("Cardiac Disease", "Disease", "is_a"),
        ("Pneumonia", "Respiratory Disease", "is_a"),
        ("Viral Respiratory Infection", "Respiratory Disease", "is_a"),
        # Polyhierarchy: Viral Pneumonia has TWO parents
        ("Viral Pneumonia", "Pneumonia", "is_a"),
        ("Viral Pneumonia", "Viral Respiratory Infection", "is_a"),
        ("Bacterial Pneumonia", "Pneumonia", "is_a"),
        ("Heart Failure", "Cardiac Disease", "is_a"),

        # ANATOMY part_of
        ("Left Ventricle", "Heart", "part_of"),
        ("Aorta", "Heart", "part_of"),
        ("Bronchus", "Lung", "part_of"),

        # Cross-terminology: finding_site
        ("Pneumonia", "Lung", "finding_site"),
        ("Heart Failure", "Heart", "finding_site"),
    ]

    rel_requests = [
        {
            "source_term_id": term_ids[src],
            "target_term_id": term_ids[tgt],
            "relationship_type": rel_type,
        }
        for src, tgt, rel_type in relationships
    ]

    resp = req("post", f"{DEF_STORE}/ontology/relationships",
                json=rel_requests, params={"namespace": NAMESPACE})
    data = resp.json()
    rel_ok = sum(1 for r in data.get("results", []) if r.get("status") in ("created", "skipped"))
    check(f"Relationships: {rel_ok}/{len(relationships)} OK",
          rel_ok == len(relationships),
          f"total={data.get('total')}, results={[(r.get('status'), (r.get('error') or '')[:50]) for r in data.get('results',[])]}")

    # --- Test traversal: ancestors ---
    print("\n--- Testing traversal: ancestors of 'Viral Pneumonia' ---")

    vp_id = term_ids["Viral Pneumonia"]
    resp = req("get", f"{DEF_STORE}/ontology/terms/{vp_id}/ancestors",
                params={"namespace": NAMESPACE, "max_depth": 10})
    data = resp.json()

    ancestor_values = {n["value"] for n in data["nodes"]}
    print(f"  Ancestors: {sorted(ancestor_values)}")

    check("Polyhierarchy: Pneumonia is ancestor",
          "Pneumonia" in ancestor_values)
    check("Polyhierarchy: Viral Respiratory Infection is ancestor",
          "Viral Respiratory Infection" in ancestor_values)
    check("Transitive: Respiratory Disease is ancestor",
          "Respiratory Disease" in ancestor_values)
    check("Transitive: Disease is ancestor (root)",
          "Disease" in ancestor_values)
    check("Total ancestors = 4 (Pneumonia, Viral Resp Inf, Resp Disease, Disease)",
          data["total"] == 4,
          f"got {data['total']}: {sorted(ancestor_values)}")

    # --- Test traversal: descendants ---
    print("\n--- Testing traversal: descendants of 'Disease' ---")

    disease_id = term_ids["Disease"]
    resp = req("get", f"{DEF_STORE}/ontology/terms/{disease_id}/descendants",
                params={"namespace": NAMESPACE, "max_depth": 10})
    data = resp.json()

    desc_values = {n["value"] for n in data["nodes"]}
    print(f"  Descendants: {sorted(desc_values)}")

    check("Descendants include Viral Pneumonia (depth 3)",
          "Viral Pneumonia" in desc_values)
    check("Descendants include Heart Failure",
          "Heart Failure" in desc_values)
    check("Total descendants = 7",
          data["total"] == 7,
          f"got {data['total']}: {sorted(desc_values)}")

    # --- Test traversal: part_of ---
    print("\n--- Testing traversal: 'Left Ventricle' part_of ancestors ---")

    lv_id = term_ids["Left Ventricle"]
    resp = req("get", f"{DEF_STORE}/ontology/terms/{lv_id}/ancestors",
                params={"namespace": NAMESPACE, "relationship_type": "part_of"})
    data = resp.json()

    check("Left Ventricle part_of Heart",
          any(n["value"] == "Heart" for n in data["nodes"]))

    # --- Test parents/children ---
    print("\n--- Testing direct parents/children ---")

    resp = req("get", f"{DEF_STORE}/ontology/terms/{vp_id}/parents",
                params={"namespace": NAMESPACE})
    parent_ids = {r["target_term_id"] for r in resp.json()}
    check("Viral Pneumonia has 2 parents",
          len(parent_ids) == 2,
          f"got {len(parent_ids)}")
    check("Parents are Pneumonia and Viral Respiratory Infection",
          parent_ids == {term_ids["Pneumonia"], term_ids["Viral Respiratory Infection"]})

    # --- Test listing with filters ---
    print("\n--- Testing relationship listing ---")

    resp = req("get", f"{DEF_STORE}/ontology/relationships",
                params={"term_id": term_ids["Pneumonia"], "direction": "outgoing",
                         "namespace": NAMESPACE})
    data = resp.json()
    rel_types = {r["relationship_type"] for r in data["items"]}
    check("Pneumonia has outgoing is_a and finding_site",
          rel_types == {"is_a", "finding_site"},
          f"got {rel_types}")

    # --- Create templates referencing ontology terms ---
    print("\n--- Creating templates ---")

    conditions_tid = term_ids["CONDITIONS"]
    anatomy_tid = term_ids["ANATOMY"]

    # PATIENT template
    patient_template_id = create_or_get_template("PATIENT", {
        "value": "PATIENT",
        "label": "Patient",
        "namespace": NAMESPACE,
        "fields": [
            {"name": "name", "label": "Name", "type": "string",
             "validation": {"required": True}},
            {"name": "date_of_birth", "label": "Date of Birth",
             "type": "string"},
        ],
        "identity_fields": ["name"],
        "status": "active",
    })

    # DIAGNOSIS template with term fields referencing ontology terminologies
    diag_template_id = create_or_get_template("DIAGNOSIS", {
        "value": "DIAGNOSIS",
        "label": "Diagnosis",
        "namespace": NAMESPACE,
        "fields": [
            {"name": "condition", "label": "Condition", "type": "term",
             "terminology_ref": conditions_tid,
             "validation": {"required": True}},
            {"name": "site", "label": "Anatomical Site", "type": "term",
             "terminology_ref": anatomy_tid},
            {"name": "severity", "label": "Severity", "type": "string"},
            {"name": "notes", "label": "Notes", "type": "string"},
        ],
        "status": "active",
    })

    # --- Create documents ---
    print("\n--- Creating documents ---")

    if not patient_template_id or not diag_template_id:
        print("  ! Skipping document creation — template IDs missing")
        return

    # Patient
    resp = req("post", f"{DOCUMENT_STORE}/documents",
                json=[{
                    "template_id": patient_template_id,
                    "namespace": NAMESPACE,
                    "data": {"name": "John Doe", "date_of_birth": "1985-03-15"},
                }])
    patient_doc = bulk_ok(resp)

    # Diagnosis referencing ontology terms
    resp = req("post", f"{DOCUMENT_STORE}/documents",
                json=[{
                    "template_id": diag_template_id,
                    "namespace": NAMESPACE,
                    "data": {
                        "condition": "Viral Pneumonia",
                        "site": "Lung",
                        "severity": "moderate",
                        "notes": "Confirmed via chest X-ray",
                    },
                }])
    diag_doc = bulk_ok(resp)

    # Verify term references were resolved
    if diag_doc["succeeded"] >= 1:
        doc_id = diag_doc["results"][0].get("id")
        if doc_id:
            resp = req("get", f"{DOCUMENT_STORE}/documents/{doc_id}",
                        params={"namespace": NAMESPACE})
            if resp.status_code == 200:
                doc = resp.json()
                term_refs = {r["field_path"]: r for r in doc.get("term_references", [])}
                check("condition term resolved to Viral Pneumonia term_id",
                      "condition" in term_refs and term_refs["condition"]["term_id"] == vp_id,
                      f"term_refs={json.dumps(term_refs.get('condition', {}))[:200]}")
                check("site term resolved to Lung term_id",
                      "site" in term_refs and term_refs["site"]["term_id"] == term_ids["Lung"],
                      f"term_refs={json.dumps(term_refs.get('site', {}))[:200]}")

    # --- Delete a relationship and verify ---
    print("\n--- Testing relationship deletion ---")

    # Ensure the relationship exists before deleting (may have been deleted in a previous run)
    req("post", f"{DEF_STORE}/ontology/relationships",
        json=[{
            "source_term_id": term_ids["Viral Pneumonia"],
            "target_term_id": term_ids["Viral Respiratory Infection"],
            "relationship_type": "is_a",
        }],
        params={"namespace": NAMESPACE})

    resp = req("delete", f"{DEF_STORE}/ontology/relationships",
                json=[{
                    "source_term_id": term_ids["Viral Pneumonia"],
                    "target_term_id": term_ids["Viral Respiratory Infection"],
                    "relationship_type": "is_a",
                }],
                params={"namespace": NAMESPACE})
    del_data = resp.json()
    check("Relationship deleted", del_data["results"][0]["status"] == "deleted")

    # Verify ancestors changed (no longer goes through Viral Respiratory Infection)
    resp = req("get", f"{DEF_STORE}/ontology/terms/{vp_id}/ancestors",
                params={"namespace": NAMESPACE})
    new_ancestors = {n["value"] for n in resp.json()["nodes"]}
    check("After deletion: Viral Respiratory Infection no longer ancestor",
          "Viral Respiratory Infection" not in new_ancestors,
          f"ancestors={sorted(new_ancestors)}")
    check("After deletion: Disease still reachable via Pneumonia path",
          "Disease" in new_ancestors)

    print(f"\n  Mini ontology test complete.")


# ===========================================================================
# TEST 2: ICD-10-GM Import
# ===========================================================================

def test_icd10_import():
    """
    Import ICD-10-GM 2025 from testdata/ as a real-world terminology.

    Structure:
      - 22 chapters (Kapitel)
      - ~250 groups (Gruppen): ranges like A00-A09
      - ~16,800 codes (Kodes): individual diagnoses

    Creates:
      - One terminology: ICD-10-GM
      - Terms for chapters, groups, and codes
      - is_a relationships: code → group → chapter
    """
    print("\n" + "=" * 60)
    print("TEST 2: ICD-10-GM Import")
    print("=" * 60)

    base_dir = Path(__file__).parent.parent / "testdata" / "icd10gm2025syst-meta" / "Klassifikationsdateien"
    if not base_dir.exists():
        print(f"  SKIP: ICD-10 test data not found at {base_dir}")
        return

    # --- Parse files ---
    print("\n--- Parsing ICD-10-GM files ---")

    # Chapters: "01;Bestimmte infektiöse..."
    chapters = {}
    with open(base_dir / "icd10gm2025syst_kapitel.txt", encoding="utf-8") as f:
        for line in f:
            parts = line.strip().split(";", 1)
            if len(parts) == 2:
                chapters[parts[0]] = parts[1]
    print(f"  Chapters: {len(chapters)}")

    # Groups: "A00;A09;01;Infektiöse Darmkrankheiten"
    groups = {}
    group_to_chapter = {}
    with open(base_dir / "icd10gm2025syst_gruppen.txt", encoding="utf-8") as f:
        for line in f:
            parts = line.strip().split(";", 3)
            if len(parts) == 4:
                group_key = f"{parts[0]}-{parts[1]}"
                groups[group_key] = parts[3]
                group_to_chapter[group_key] = parts[2]
    print(f"  Groups: {len(groups)}")

    # Codes: semicolon-separated, field 6 = code (e.g., "A00.0"), field 8 = label
    # Format: level;type;?;chapter;group_start;code_with_dot;code_alt;code_nodot;label;...
    codes = {}
    code_to_group = {}
    with open(base_dir / "icd10gm2025syst_kodes.txt", encoding="utf-8") as f:
        for line in f:
            parts = line.strip().split(";")
            if len(parts) >= 9:
                level = parts[0]
                code = parts[5]        # e.g., "A00.0" or "A00.-"
                label = parts[8]       # description
                chapter = parts[3]
                group_start = parts[4]

                # Skip category headers (level 3, codes ending with .-)
                if code.endswith(".-"):
                    continue

                codes[code] = label

                # Find which group this code belongs to
                for gk in groups:
                    g_start, g_end = gk.split("-")
                    code_prefix = code.split(".")[0]
                    if g_start <= code_prefix <= g_end:
                        code_to_group[code] = gk
                        break

    print(f"  Codes (leaf-level): {len(codes)}")

    # --- Create terminology ---
    print("\n--- Creating ICD-10-GM terminology ---")
    t0 = time.time()

    resp = req("post", f"{DEF_STORE}/terminologies",
                json=[{
                    "value": "ICD-10-GM-2025",
                    "label": "ICD-10-GM 2025",
                    "description": "International Classification of Diseases, 10th Revision, German Modification, 2025",
                    "namespace": NAMESPACE,
                    "metadata": {"source": "BfArM", "version": "2025", "language": "de"},
                }])
    data = resp.json()
    result = data["results"][0]
    if result["status"] == "created":
        icd_terminology_id = result["id"]
        check("Created ICD-10-GM terminology", True)
    elif "already exists" in result.get("error", ""):
        lookup = req("get", f"{DEF_STORE}/terminologies/by-value/ICD-10-GM-2025")
        icd_terminology_id = lookup.json()["terminology_id"]
        check(f"ICD-10-GM exists (reusing {icd_terminology_id[:8]}...)", True)
    else:
        check("Created ICD-10-GM terminology", False, result.get("error"))
        return

    # --- Create chapter terms ---
    print(f"\n--- Creating {len(chapters)} chapter terms ---")

    chapter_terms = [
        {"value": f"CH-{ch_num}", "label": ch_label,
         "metadata": {"icd10_chapter": ch_num}}
        for ch_num, ch_label in chapters.items()
    ]
    resp = req("post", f"{DEF_STORE}/terminologies/{icd_terminology_id}/terms",
                json=chapter_terms)
    ch_data = bulk_ok(resp, expected_count=len(chapters))
    chapter_ids = {r["value"]: r["id"] for r in ch_data["results"] if r.get("id")}
    print(f"  Created {len(chapter_ids)} chapter terms")

    # --- Create group terms ---
    print(f"\n--- Creating {len(groups)} group terms ---")

    group_terms = [
        {"value": gk, "label": gl,
         "metadata": {"icd10_group": gk, "chapter": group_to_chapter[gk]}}
        for gk, gl in groups.items()
    ]
    resp = req("post", f"{DEF_STORE}/terminologies/{icd_terminology_id}/terms",
                json=group_terms)
    gr_data = bulk_ok(resp, expected_count=len(groups))
    group_ids = {r["value"]: r["id"] for r in gr_data["results"] if r.get("id")}
    print(f"  Created {len(group_ids)} group terms")

    # --- Create code terms (in batches) ---
    print(f"\n--- Creating {len(codes)} code terms (batched) ---")

    BATCH_SIZE = 1000
    code_items = [{"value": code, "label": label} for code, label in codes.items()]
    code_ids = {}
    total_created = 0

    for i in range(0, len(code_items), BATCH_SIZE):
        batch = code_items[i:i + BATCH_SIZE]
        resp = req("post", f"{DEF_STORE}/terminologies/{icd_terminology_id}/terms",
                    json=batch,
                    params={"batch_size": 1000, "registry_batch_size": 50},
                    timeout=120)
        data = resp.json()
        for r in data["results"]:
            if r.get("id"):
                code_ids[r["value"]] = r["id"]
                total_created += 1
        print(f"  Batch {i // BATCH_SIZE + 1}: {data.get('succeeded', 0)} created")

    t_terms = time.time() - t0
    print(f"\n  Total terms created: {len(chapter_ids) + len(group_ids) + total_created} in {t_terms:.1f}s")

    # --- Create hierarchy relationships ---
    print("\n--- Creating hierarchy relationships ---")
    t1 = time.time()

    # Group is_a Chapter
    group_rels = []
    for gk, ch_num in group_to_chapter.items():
        ch_key = f"CH-{ch_num}"
        if gk in group_ids and ch_key in chapter_ids:
            group_rels.append({
                "source_term_id": group_ids[gk],
                "target_term_id": chapter_ids[ch_key],
                "relationship_type": "is_a",
            })

    if group_rels:
        for i in range(0, len(group_rels), 500):
            batch = group_rels[i:i + 500]
            resp = req("post", f"{DEF_STORE}/ontology/relationships",
                        json=batch, params={"namespace": NAMESPACE})
            data = resp.json()
            print(f"  Group→Chapter batch: {data.get('succeeded', 0)}/{data.get('total', 0)}")

    # Code is_a Group
    code_rels = []
    for code, gk in code_to_group.items():
        if code in code_ids and gk in group_ids:
            code_rels.append({
                "source_term_id": code_ids[code],
                "target_term_id": group_ids[gk],
                "relationship_type": "is_a",
            })

    if code_rels:
        for i in range(0, len(code_rels), 500):
            batch = code_rels[i:i + 500]
            resp = req("post", f"{DEF_STORE}/ontology/relationships",
                        json=batch, params={"namespace": NAMESPACE},
                        timeout=60)
            data = resp.json()
            print(f"  Code→Group batch {i // 500 + 1}: {data.get('succeeded', 0)}/{data.get('total', 0)}")

    t_rels = time.time() - t1
    total_rels = len(group_rels) + len(code_rels)
    print(f"\n  Total relationships: {total_rels} in {t_rels:.1f}s")

    # --- Verify traversal ---
    print("\n--- Verifying traversal ---")

    # Pick a code and check its ancestors go up to chapter
    test_code = "A00.0"  # Cholera durch Vibrio cholerae
    if test_code in code_ids:
        resp = req("get", f"{DEF_STORE}/ontology/terms/{code_ids[test_code]}/ancestors",
                    params={"namespace": NAMESPACE, "max_depth": 5})
        data = resp.json()
        ancestor_values = [n["value"] for n in data["nodes"]]
        print(f"  Ancestors of {test_code}: {ancestor_values}")

        check(f"{test_code} has ancestors (group + chapter)",
              data["total"] >= 2,
              f"total={data['total']}")

    # Check descendants of Chapter 01
    ch01_key = "CH-01"
    if ch01_key in chapter_ids:
        resp = req("get", f"{DEF_STORE}/ontology/terms/{chapter_ids[ch01_key]}/descendants",
                    params={"namespace": NAMESPACE, "max_depth": 2})
        data = resp.json()
        print(f"  Descendants of Chapter 01 (depth≤2): {data['total']}")
        check("Chapter 01 has descendants (groups + codes)",
              data["total"] > 10)

    # --- Create a template using ICD-10 ---
    print("\n--- Creating template referencing ICD-10-GM ---")

    clinical_note_tpl_id = create_or_get_template("CLINICAL_NOTE", {
        "value": "CLINICAL_NOTE",
        "label": "Clinical Note",
        "namespace": NAMESPACE,
        "fields": [
            {"name": "patient_name", "label": "Patient", "type": "string",
             "validation": {"required": True}},
            {"name": "diagnosis_code", "label": "ICD-10 Diagnosis", "type": "term",
             "terminology_ref": icd_terminology_id,
             "validation": {"required": True}},
            {"name": "notes", "label": "Clinical Notes", "type": "string"},
        ],
        "status": "active",
    })

    if not clinical_note_tpl_id:
        print("  ! Skipping document creation — template ID missing")
        return

    # --- Create a document with an ICD-10 code ---
    print("\n--- Creating document with ICD-10 term ---")

    resp = req("post", f"{DOCUMENT_STORE}/documents",
                json=[{
                    "template_id": clinical_note_tpl_id,
                    "namespace": NAMESPACE,
                    "data": {
                        "patient_name": "Max Mustermann",
                        "diagnosis_code": "A00.0",
                        "notes": "Cholera confirmed, treatment initiated",
                    },
                }])
    doc_data = bulk_ok(resp)

    doc_id = doc_data["results"][0].get("id") if doc_data.get("results") else None
    if doc_id:
        resp = req("get", f"{DOCUMENT_STORE}/documents/{doc_id}")
        if resp.status_code == 200:
            doc = resp.json()
            term_refs = {r["field_path"]: r for r in doc.get("term_references", [])}
            check("ICD-10 code resolved to term_id",
                  "diagnosis_code" in term_refs,
                  f"term_refs keys={list(term_refs.keys())}")

    total_time = time.time() - t0
    print(f"\n  ICD-10-GM import complete: {total_time:.1f}s total")
    print(f"  Terms: {len(chapter_ids) + len(group_ids) + total_created}")
    print(f"  Relationships: {total_rels}")


# ===========================================================================
# MAIN
# ===========================================================================

def main():
    global passed, failed

    parser = argparse.ArgumentParser(description="End-to-end ontology test for WIP")
    parser.add_argument("--host", default="localhost", help="WIP host")
    parser.add_argument("--via-proxy", action="store_true", help="Route through Caddy proxy")
    parser.add_argument("--skip-mini", action="store_true", help="Skip mini ontology test")
    parser.add_argument("--skip-icd10", action="store_true", help="Skip ICD-10 import test")
    args = parser.parse_args()

    configure_urls(args.host, args.via_proxy)

    print(f"WIP Ontology End-to-End Test")
    print(f"Def-Store:      {DEF_STORE}")
    print(f"Template-Store: {TEMPLATE_STORE}")
    print(f"Document-Store: {DOCUMENT_STORE}")

    # Verify connectivity
    try:
        # Health endpoint is at service root, not under /api/def-store
        health_url = DEF_STORE.replace("/api/def-store", "/health")
        resp = req("get", health_url, timeout=5)
        if resp.status_code != 200:
            print(f"\nERROR: Def-Store not reachable (HTTP {resp.status_code})")
            sys.exit(1)
    except Exception as e:
        print(f"\nERROR: Cannot connect to Def-Store: {e}")
        sys.exit(1)

    if not args.skip_mini:
        test_mini_ontology()

    if not args.skip_icd10:
        test_icd10_import()

    # Summary
    print("\n" + "=" * 60)
    print(f"RESULTS: {passed} passed, {failed} failed")
    print("=" * 60)

    if errors:
        print("\nFailed checks:")
        for e in errors:
            print(e)

    sys.exit(1 if failed > 0 else 0)


if __name__ == "__main__":
    main()

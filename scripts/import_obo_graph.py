#!/usr/bin/env python3
"""
Import OBO Graph JSON ontologies into World In a Pie.

Parses OBO Graph JSON files (HP, GO, CHEBI, etc.) and imports them as
WIP terminologies with terms and relations via the def-store API.

Usage:
    # Import Human Phenotype Ontology
    python scripts/import_obo_graph.py testdata/hp.json \
        --terminology-value HPO \
        --terminology-label "Human Phenotype Ontology"

    # Import Gene Ontology (basic)
    python scripts/import_obo_graph.py testdata/go-basic.json \
        --terminology-value GO \
        --terminology-label "Gene Ontology"

    # Dry run (parse and report stats without importing)
    python scripts/import_obo_graph.py testdata/hp.json --dry-run

    # Against remote host
    python scripts/import_obo_graph.py testdata/hp.json \
        --terminology-value HPO \
        --terminology-label "Human Phenotype Ontology" \
        --host wip-pi.local --via-proxy
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


# =============================================================================
# OBO PREDICATE MAPPING
# =============================================================================

OBO_PREDICATE_MAP: dict[str, str] = {
    "is_a": "is_a",
    "http://purl.obolibrary.org/obo/BFO_0000050": "part_of",
    "http://purl.obolibrary.org/obo/BFO_0000051": "has_part",
    "http://purl.obolibrary.org/obo/RO_0002211": "regulates",
    "http://purl.obolibrary.org/obo/RO_0002212": "negatively_regulates",
    "http://purl.obolibrary.org/obo/RO_0002213": "positively_regulates",
    "http://purl.obolibrary.org/obo/RO_0002215": "capable_of",
    "http://purl.obolibrary.org/obo/RO_0002216": "capable_of_part_of",
    "http://purl.obolibrary.org/obo/RO_0002233": "has_input",
    "http://purl.obolibrary.org/obo/RO_0002234": "has_output",
    "http://purl.obolibrary.org/obo/RO_0002331": "involved_in",
    "http://purl.obolibrary.org/obo/RO_0002332": "regulates_activity_of",
}

# Predicates to skip (property-level axioms, not concept relations)
OBO_SKIP_PREDICATES = {"subPropertyOf"}


# =============================================================================
# PARSING
# =============================================================================

def uri_to_value(uri: str) -> str:
    """Convert OBO URI to compact value: HP_0000001 → HP:0000001."""
    fragment = uri.rsplit("/", 1)[-1]
    return fragment.replace("_", ":", 1)


def detect_prefix(graph: dict) -> str | None:
    """Auto-detect OBO prefix from graph ID."""
    graph_id = graph.get("id", "")
    # "http://purl.obolibrary.org/obo/hp.json" → "HP"
    # "http://purl.obolibrary.org/obo/go/go-basic.json" → "GO"
    filename = graph_id.rsplit("/", 1)[-1]
    base = filename.split(".")[0].split("-")[0].upper()
    return base if base else None


def map_predicate(pred: str) -> str | None:
    """Map OBO predicate to WIP relation type. Returns None to skip."""
    if pred in OBO_SKIP_PREDICATES:
        return None
    if pred in OBO_PREDICATE_MAP:
        return OBO_PREDICATE_MAP[pred]
    # Unknown URI: extract fragment and convert to compact form
    if "/" in pred:
        fragment = pred.rsplit("/", 1)[-1]
        return fragment.replace("_", ":", 1)
    return pred


def parse_obo_graph(
    data: dict,
    prefix_filter: str | None = None,
    include_deprecated: bool = False,
    max_synonyms: int = 10,
) -> dict[str, Any]:
    """
    Parse OBO Graph JSON into WIP-compatible structures.

    Returns:
        {
            "ontology_meta": {...},
            "nodes": {uri: {value, label, description, aliases, metadata, deprecated}},
            "edges": [{source_uri, target_uri, relation_type, raw_pred}],
            "stats": {...}
        }
    """
    graph = data["graphs"][0]
    meta = graph.get("meta", {})

    # Auto-detect prefix if not given
    if not prefix_filter:
        prefix_filter = detect_prefix(graph)

    uri_prefix = f"http://purl.obolibrary.org/obo/{prefix_filter}_" if prefix_filter else None

    # Extract ontology metadata
    ontology_meta: dict[str, str] = {}
    for bpv in meta.get("basicPropertyValues", []):
        pred = bpv.get("pred", "")
        val = bpv.get("val", "")
        if "title" in pred:
            ontology_meta["title"] = val
        elif "description" in pred:
            ontology_meta["description"] = val
        elif "versionInfo" in pred:
            ontology_meta["version"] = val
        elif "license" in pred or "rights" in pred:
            ontology_meta["license"] = val

    # Parse nodes
    nodes: dict[str, dict] = {}
    stats = {
        "raw_nodes": 0,
        "filtered_non_class": 0,
        "filtered_prefix": 0,
        "filtered_deprecated": 0,
        "nodes_imported": 0,
        "nodes_with_synonyms": 0,
        "total_synonyms": 0,
    }

    for n in graph.get("nodes", []):
        stats["raw_nodes"] += 1
        if n.get("type") != "CLASS":
            stats["filtered_non_class"] += 1
            continue

        uri = n["id"]
        if uri_prefix and not uri.startswith(uri_prefix):
            stats["filtered_prefix"] += 1
            continue

        node_meta = n.get("meta", {})
        is_deprecated = node_meta.get("deprecated", False)

        if is_deprecated and not include_deprecated:
            stats["filtered_deprecated"] += 1
            continue

        value = uri_to_value(uri)
        label = n.get("lbl", value)

        # Description from definition
        definition = node_meta.get("definition", {})
        description = definition.get("val") if definition else None

        # Synonyms → aliases
        raw_synonyms = node_meta.get("synonyms", [])
        aliases = [s["val"] for s in raw_synonyms if s.get("val")][:max_synonyms]

        if aliases:
            stats["nodes_with_synonyms"] += 1
            stats["total_synonyms"] += len(aliases)

        # Cross-references → metadata
        xrefs = [x.get("val") for x in node_meta.get("xrefs", []) if x.get("val")]

        # Build metadata
        term_metadata: dict[str, Any] = {}
        if xrefs:
            term_metadata["xrefs"] = xrefs
        if is_deprecated:
            term_metadata["deprecated"] = True
            comments = node_meta.get("comments", [])
            if comments:
                term_metadata["deprecation_comment"] = comments[0]

        nodes[uri] = {
            "value": value,
            "label": label,
            "description": description,
            "aliases": aliases,
            "metadata": term_metadata,
            "deprecated": is_deprecated,
        }

    stats["nodes_imported"] = len(nodes)

    # Parse edges
    edges: list[dict] = []
    pred_counts: Counter = Counter()
    stats["raw_edges"] = 0
    stats["filtered_edges_missing_endpoint"] = 0
    stats["filtered_edges_skipped_pred"] = 0

    for e in graph.get("edges", []):
        stats["raw_edges"] += 1
        sub = e.get("sub")
        obj = e.get("obj")
        pred = e.get("pred")

        if sub not in nodes or obj not in nodes:
            stats["filtered_edges_missing_endpoint"] += 1
            continue

        rel_type = map_predicate(pred)
        if rel_type is None:
            stats["filtered_edges_skipped_pred"] += 1
            continue

        pred_counts[rel_type] += 1
        edges.append({
            "source_uri": sub,
            "target_uri": obj,
            "relation_type": rel_type,
            "raw_pred": pred,
        })

    stats["edges_imported"] = len(edges)
    stats["predicate_distribution"] = dict(pred_counts.most_common())

    return {
        "ontology_meta": ontology_meta,
        "prefix": prefix_filter,
        "nodes": nodes,
        "edges": edges,
        "stats": stats,
    }


# =============================================================================
# API CLIENT
# =============================================================================

class WipClient:
    """Simple HTTP client for WIP API."""

    def __init__(self, host: str, via_proxy: bool, api_key: str, namespace: str):
        self.namespace = namespace
        self.api_key = api_key
        self.headers = {
            "X-API-Key": api_key,
            "Content-Type": "application/json",
        }

        if via_proxy:
            self.base = f"https://{host}:8443"
        else:
            self.base = f"http://{host}"

        self.def_store = f"{self.base}:8002/api/def-store" if not via_proxy else f"{self.base}/api/def-store"
        self.verify = False

    def get(self, path: str, **kwargs) -> requests.Response:
        resp = requests.get(
            f"{self.def_store}{path}",
            headers=self.headers,
            verify=self.verify,
            **kwargs,
        )
        resp.raise_for_status()
        return resp

    def post(self, path: str, **kwargs) -> requests.Response:
        resp = requests.post(
            f"{self.def_store}{path}",
            headers=self.headers,
            verify=self.verify,
            **kwargs,
        )
        resp.raise_for_status()
        return resp

    def delete(self, path: str, **kwargs) -> requests.Response:
        resp = requests.delete(
            f"{self.def_store}{path}",
            headers=self.headers,
            verify=self.verify,
            **kwargs,
        )
        resp.raise_for_status()
        return resp


# =============================================================================
# IMPORT LOGIC
# =============================================================================

def create_or_get_terminology(
    client: WipClient,
    value: str,
    label: str,
    description: str | None,
    metadata: dict | None,
) -> str:
    """Create terminology or return existing ID."""
    resp = client.post(
        "/terminologies",
        json=[{
            "value": value,
            "label": label,
            "description": description,
            "namespace": client.namespace,
            "metadata": metadata or {},
        }],
    )
    data = resp.json()
    result = data["results"][0]

    if result["status"] == "created":
        print(f"  Created terminology '{value}' ({result['id'][:12]}...)")
        return result["id"]

    if "already exists" in result.get("error", ""):
        lookup = client.get(f"/terminologies/by-value/{value}")
        tid = lookup.json()["terminology_id"]
        print(f"  Terminology '{value}' already exists ({tid[:12]}...)")
        return tid

    raise RuntimeError(f"Failed to create terminology: {result.get('error')}")


def import_terms(
    client: WipClient,
    terminology_id: str,
    nodes: dict[str, dict],
    batch_size: int,
    registry_batch_size: int,
) -> dict[str, str]:
    """Import terms in batches. Returns value→term_id mapping."""
    term_items = []
    for info in nodes.values():
        item: dict[str, Any] = {
            "value": info["value"],
            "label": info["label"],
        }
        if info["description"]:
            item["description"] = info["description"]
        if info["aliases"]:
            item["aliases"] = info["aliases"]
        if info["metadata"]:
            item["metadata"] = info["metadata"]
        term_items.append(item)

    value_to_id: dict[str, str] = {}
    total_ok = 0
    total_batches = (len(term_items) + batch_size - 1) // batch_size

    for i in range(0, len(term_items), batch_size):
        batch = term_items[i:i + batch_size]
        resp = client.post(
            f"/terminologies/{terminology_id}/terms",
            json=batch,
            params={"batch_size": batch_size, "registry_batch_size": registry_batch_size},
            timeout=120,
        )
        data = resp.json()
        batch_ok = 0
        for r in data.get("results", []):
            if r.get("id"):
                value_to_id[r["value"]] = r["id"]
                batch_ok += 1
            elif r["status"] in ("skipped",) and "lready exists" in r.get("error", ""):
                batch_ok += 1
        total_ok += batch_ok
        batch_num = i // batch_size + 1
        print(f"  Terms batch {batch_num}/{total_batches}: {batch_ok}/{len(batch)} OK")

    # Resolve IDs for skipped (already existing) terms
    if len(value_to_id) < len(nodes):
        missing = len(nodes) - len(value_to_id)
        print(f"  Resolving {missing} existing term IDs...")
        page = 1
        page_size = 100
        while True:
            resp = client.get(
                f"/terminologies/{terminology_id}/terms",
                params={"page": page, "page_size": page_size},
            )
            data = resp.json()
            for item in data.get("items", []):
                value_to_id[item["value"]] = item["term_id"]
            if page >= data.get("pages", 1):
                break
            page += 1
        print(f"  Resolved {len(value_to_id)} total term IDs")

    return value_to_id


def import_relations(
    client: WipClient,
    nodes: dict[str, dict],
    edges: list[dict],
    value_to_id: dict[str, str],
    batch_size: int,
) -> int:
    """Import relations in batches. Returns count of successful imports."""
    # Build URI→term_id mapping
    uri_to_id: dict[str, str] = {}
    for uri, info in nodes.items():
        tid = value_to_id.get(info["value"])
        if tid:
            uri_to_id[uri] = tid

    rel_items: list[dict] = []
    skipped = 0
    for e in edges:
        src_id = uri_to_id.get(e["source_uri"])
        tgt_id = uri_to_id.get(e["target_uri"])
        if src_id and tgt_id:
            rel_items.append({
                "source_term_id": src_id,
                "target_term_id": tgt_id,
                "relation_type": e["relation_type"],
            })
        else:
            skipped += 1

    if skipped:
        print(f"  Skipped {skipped} edges (missing term IDs)")

    total_ok = 0
    total_batches = (len(rel_items) + batch_size - 1) // batch_size

    for i in range(0, len(rel_items), batch_size):
        batch = rel_items[i:i + batch_size]
        resp = client.post(
            "/ontology/term-relations",
            json=batch,
            params={"namespace": client.namespace},
            timeout=120,
        )
        data = resp.json()
        batch_ok = sum(
            1 for r in data.get("results", [])
            if r.get("status") in ("created", "skipped")
        )
        total_ok += batch_ok
        batch_num = i // batch_size + 1
        print(f"  Relations batch {batch_num}/{total_batches}: {batch_ok}/{len(batch)} OK")

    return total_ok


# =============================================================================
# MAIN
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Import OBO Graph JSON ontologies into WIP"
    )
    parser.add_argument("file", help="Path to OBO Graph JSON file")
    parser.add_argument("--terminology-value", help="WIP terminology value (e.g., HPO, GO). Auto-detected if not set.")
    parser.add_argument("--terminology-label", help="Display label. Auto-detected from ontology metadata if not set.")
    parser.add_argument("--host", default="localhost", help="WIP host (default: localhost)")
    parser.add_argument("--via-proxy", action="store_true", help="Route through Caddy proxy")
    parser.add_argument("--namespace", default="wip", help="Target namespace (default: wip)")
    parser.add_argument("--api-key", default=os.getenv("WIP_API_KEY", "dev_master_key_for_testing"))
    parser.add_argument("--prefix-filter", help="Only import nodes with this OBO prefix (auto-detect if not set)")
    parser.add_argument("--include-deprecated", action="store_true", help="Import deprecated/obsolete nodes")
    parser.add_argument("--max-synonyms", type=int, default=10, help="Max aliases per term (default: 10)")
    parser.add_argument("--term-batch-size", type=int, default=1000, help="Terms per batch (default: 1000)")
    parser.add_argument("--relation-batch-size", type=int, default=500, help="Relations per batch (default: 500)")
    parser.add_argument("--registry-batch-size", type=int, default=50, help="Registry calls per batch (default: 50)")
    parser.add_argument("--dry-run", action="store_true", help="Parse and report stats without importing")

    args = parser.parse_args()

    filepath = Path(args.file)
    if not filepath.exists():
        print(f"Error: File not found: {filepath}")
        sys.exit(1)

    # --- Parse ---
    print(f"\n{'=' * 60}")
    print(f"Parsing {filepath.name}...")
    print(f"{'=' * 60}")

    t0 = time.time()
    with open(filepath) as f:
        raw_data = json.load(f)
    t_load = time.time() - t0
    print(f"  JSON loaded in {t_load:.1f}s")

    t1 = time.time()
    parsed = parse_obo_graph(
        raw_data,
        prefix_filter=args.prefix_filter,
        include_deprecated=args.include_deprecated,
        max_synonyms=args.max_synonyms,
    )
    t_parse = time.time() - t1

    stats = parsed["stats"]
    meta = parsed["ontology_meta"]

    print(f"\n  Ontology: {meta.get('title', 'Unknown')}")
    print(f"  Version:  {meta.get('version', 'Unknown')}")
    print(f"  Prefix:   {parsed['prefix']}")
    print(f"  Parse time: {t_parse:.1f}s")
    print(f"\n  Nodes:")
    print(f"    Raw:              {stats['raw_nodes']}")
    print(f"    Non-CLASS:        {stats['filtered_non_class']} (skipped)")
    print(f"    Wrong prefix:     {stats['filtered_prefix']} (skipped)")
    print(f"    Deprecated:       {stats['filtered_deprecated']} (skipped)")
    print(f"    Imported:         {stats['nodes_imported']}")
    print(f"    With synonyms:    {stats['nodes_with_synonyms']}")
    print(f"    Total synonyms:   {stats['total_synonyms']}")
    print(f"\n  Edges:")
    print(f"    Raw:              {stats['raw_edges']}")
    print(f"    Missing endpoint: {stats['filtered_edges_missing_endpoint']} (skipped)")
    print(f"    Skipped pred:     {stats['filtered_edges_skipped_pred']} (skipped)")
    print(f"    Imported:         {stats['edges_imported']}")
    print(f"\n  Relation types:")
    for pred, count in stats["predicate_distribution"].items():
        print(f"    {pred}: {count}")

    if args.dry_run:
        print(f"\n  DRY RUN — no data imported")
        sys.exit(0)

    # --- Resolve terminology name ---
    term_value = args.terminology_value or parsed["prefix"]
    term_label = args.terminology_label or meta.get("title") or term_value
    if not term_value:
        print("Error: Could not auto-detect terminology value. Use --terminology-value.")
        sys.exit(1)

    print(f"\n{'=' * 60}")
    print(f"Importing as: {term_value} ({term_label})")
    print(f"{'=' * 60}")

    client = WipClient(args.host, args.via_proxy, args.api_key, args.namespace)

    # --- Create terminology ---
    print("\n--- Creating terminology ---")
    terminology_id = create_or_get_terminology(
        client,
        value=term_value,
        label=term_label,
        description=meta.get("description"),
        metadata={
            "source": meta.get("title", term_value),
            "version": meta.get("version"),
            "format": "OBO Graph JSON",
        },
    )

    # --- Import terms ---
    print(f"\n--- Importing {len(parsed['nodes'])} terms ---")
    t2 = time.time()
    value_to_id = import_terms(
        client,
        terminology_id,
        parsed["nodes"],
        batch_size=args.term_batch_size,
        registry_batch_size=args.registry_batch_size,
    )
    t_terms = time.time() - t2
    print(f"  {len(value_to_id)} terms in {t_terms:.1f}s")

    # --- Import relations ---
    if parsed["edges"]:
        print(f"\n--- Importing {len(parsed['edges'])} relations ---")
        t3 = time.time()
        rel_ok = import_relations(
            client,
            parsed["nodes"],
            parsed["edges"],
            value_to_id,
            batch_size=args.relation_batch_size,
        )
        t_rels = time.time() - t3
        print(f"  {rel_ok} relations in {t_rels:.1f}s")
    else:
        print("\n  No relations to import")

    # --- Summary ---
    total_time = time.time() - t0
    print(f"\n{'=' * 60}")
    print(f"Import complete: {total_time:.1f}s total")
    print(f"  Terminology: {term_value} ({terminology_id[:12]}...)")
    print(f"  Terms:         {len(value_to_id)}")
    print(f"  Relations: {stats['edges_imported']}")
    print(f"  Synonyms:      {stats['total_synonyms']}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()

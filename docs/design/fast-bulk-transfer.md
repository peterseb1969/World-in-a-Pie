# Fast Bulk Transfer

## Problem

The current WIP-Toolkit import/export uses service APIs for every operation: paginated GETs for export, batched POSTs for import. For ClinTrial (~244K documents, ~17K terms, ~180 templates), a fresh import takes significant time because every entity goes through the full API stack: HTTP → validation → Registry ID generation → MongoDB write → NATS event → reporting-sync.

Export is acceptably fast (streaming pagination). Import is the bottleneck.

## Key Insight

Every WIP entity carries its own IDs internally. A terminology document in MongoDB contains `terminology_id`, `namespace`, `value`, etc. — everything needed to reconstruct the entity without going through the API. MongoDB's `insertMany()` is orders of magnitude faster than batched HTTP POST with per-item validation.

The challenge: the Registry must know about every ID, and reporting-sync must populate PostgreSQL.

## Design Revision: Fresh vs Restore is (Mostly) a False Distinction

Original UUIDs are globally unique. Importing with the same IDs works on any target host — there's no collision risk. The only scenario requiring new IDs is importing the **same archive twice into different namespaces on the same WIP instance** (Registry enforces global `entry_id` uniqueness). This is a narrow edge case, not the default.

**Therefore: the default fast import preserves original IDs.** No fresh/restore split needed for the common case.

The real complication isn't own-namespace IDs — it's **cross-namespace references**. Templates store terminology references as resolved UUIDs (e.g., `terminology_ref: "uuid-of-TherapeuticArea"`). If the target host's `wip` namespace has different UUIDs for the same terminologies, those references break. This requires value-based re-resolution, not ID regeneration.

---

## Cross-Namespace Reference Resolution

### How References Work Today

Template-store resolves references at creation time via `_resolve_to_terminology_id()` and `_resolve_to_template_id()`. These accept either a UUID or a human-readable value, resolve to a canonical UUID, and store the UUID in the template document. After that, the value is gone — only the UUID remains.

Document-store validates `term_references` against the template's resolved `terminology_ref` / `target_terminologies` UUIDs.

### The Import Problem

On a different host, the `wip` namespace exists but has different UUIDs for the same entities. A template with `terminology_ref: "source-uuid-123"` breaks because that UUID doesn't exist on the target.

### Solution: Value-Based Re-Resolution at Import Time

The archive carries enough information to resolve by value:

1. **Export includes external dependencies** (see next section)
2. Each external terminology/template has a `value` field
3. On import, for each cross-namespace UUID reference in templates:
   - Look up the source UUID in the archive's external dependencies → get the `value`
   - Resolve that `value` on the target instance → get the target UUID
   - Replace source UUID with target UUID in the template document
4. Similarly for document `term_references`: remap `terminology_id` and `term_id` using value-based lookup

This remapping is small — only cross-namespace references need it (typically a few dozen terminologies, not 260K entities). The in-namespace IDs stay untouched.

---

## Self-Contained Archives: Packing External Dependencies

### What to Pack

The archive should include all externally-referenced entities as read-only reference data, tagged `_source: "external"`:

- **Terminologies** referenced by template fields (`terminology_ref`, `target_terminologies`, `array_terminology_ref`)
- **All terms** belonging to those terminologies (needed for document validation and value-based resolution)
- **Templates** referenced by template fields (`template_ref`, `target_templates`, `extends`)
- **Relationship types** from `_ONTOLOGY_RELATIONSHIP_TYPES` used in ontology relationships

The closure logic already identifies these — `manifest.closure.external_terminologies` and `manifest.closure.external_templates` list them. The change is: **include the actual data, not just the IDs**.

### Import Behavior for External Dependencies

```
For each external dependency in archive:
  1. Resolve by value on target instance (namespace-aware lookup)
  2. If found:
     - Build UUID mapping: source_uuid → target_uuid
     - Validate term coverage: warn if target is missing terms
       that documents reference
  3. If NOT found:
     - STOP with actionable error:
       "Missing dependency: terminology 'TherapeuticArea' (namespace 'wip').
        Create it first, or use --create-dependencies to auto-create."
     - --create-dependencies flag: creates the terminology + terms in the
       referenced namespace (requires that namespace to exist)
```

### What `--create-dependencies` Does NOT Do

- Does not create namespaces (too dangerous as a side effect — user must create explicitly)
- Does not overwrite existing terminologies/templates (target takes precedence)
- Does not modify shared data owned by other namespaces

### Edge Cases

| Scenario | Behavior |
|----------|----------|
| External terminology exists, all terms match | Use target UUIDs, proceed |
| External terminology exists, target has MORE terms | Fine — superset is compatible |
| External terminology exists, target MISSING terms that docs reference | Warn with list of missing terms; import proceeds but those docs may fail validation |
| External terminology doesn't exist at all | Error unless `--create-dependencies` |
| External template exists, different version | Use target's latest active version UUID |
| External template doesn't exist | Error unless `--create-dependencies` |
| Cascading: external terminology has relationships to another external | Include transitively (closure already handles depth) |

---

## Synonym Handling

### Where Synonyms Live

Synonyms are stored **only in the Registry** (`registry_entries` collection), not in entity documents. A direct MongoDB insert into def-store/template-store/document-store gives you all the data but **zero synonym resolution** — nobody can look up `"TherapeuticArea"` → `"uuid-xyz"`.

Auto-synonyms (created by services during normal API operations) won't exist either. These enable human-readable lookups via MCP tools, Console UI, and any API call using values instead of UUIDs.

### Solution

After bulk-inserting entity documents and activating IDs in the Registry, register synonyms:

1. **Auto-synonyms**: Construct from entity data (the composite key pattern is deterministic):
   - Terminologies: `{ns, type: "terminology", value}` → `terminology_id`
   - Terms: `{ns, type: "term", terminology_id, value}` → `term_id`
   - Templates: `{ns, type: "template", value}` → `template_id`
   - Documents: `{ns, type: "document", identity_hash}` → `document_id`

2. **Custom synonyms**: Restore from `synonyms.jsonl` in the archive (if present)

3. **Bulk registration**: `POST /registry/synonyms/add` in batches of 1000

This is the one part that can't be shortcut — the Registry needs explicit synonym registration. But it's fast (bulk API, ~260 requests for 260K entities).

---

## Import Flow (Revised)

```
1. Read manifest, identify external dependencies
2. Resolve external dependencies on target (value-based)
   → Build cross-namespace UUID mapping
   → Abort if missing deps and no --create-dependencies
3. Create namespace (if needed)
4. Reserve all in-namespace IDs in Registry (bulk /reserve)
5. Transform documents:
   - Strip export metadata (_source, _namespace)
   - Remap cross-namespace UUIDs using mapping from step 2
   - Add Beanie fields if needed (_class_id, revision_id)
6. Bulk-insert into MongoDB (insert_many, ordered=False)
   - Terminologies → wip_def_store.terminologies
   - Terms → wip_def_store.terms
   - Relationships → wip_def_store.term_relationships
   - Templates → wip_template_store.templates
   - Documents → wip_document_store.documents
   - Files (metadata) → wip_document_store.files
7. Activate all IDs in Registry (bulk /activate)
8. Register auto-synonyms + custom synonyms (bulk /synonyms/add)
9. Upload file blobs to MinIO (parallel, ThreadPoolExecutor)
10. Trigger reporting-sync batch resync
```

Steps 4, 8, 9 are Registry/MinIO operations (HTTP).
Steps 5, 6 are direct MongoDB operations (PyMongo).
Step 10 is a single HTTP call that triggers async background work.

---

## Export Changes

### Include External Dependencies

Current behavior: closure identifies external terminologies/templates, records their IDs in `manifest.closure`.

New behavior: also **write the actual entities** to the archive JSONL files, tagged with `_source: "external"`:

```jsonl
{"terminology_id": "uuid", "value": "TherapeuticArea", "namespace": "wip", "_source": "external", ...}
```

Terms for external terminologies are included in `terms.jsonl` with the same `_source: "external"` tag.

The manifest gains a new field:

```json
{
  "closure": {
    "external_terminologies": ["uuid-1", "uuid-2"],
    "external_templates": ["uuid-3"],
    "external_data_included": true
  }
}
```

### Optional: `--format mongo` Export Mode

Add a `--format mongo` flag that dumps raw MongoDB documents instead of API representations:

```bash
wip-toolkit export clintrial ./backup.zip --format mongo
```

Requires `--mongo-uri` connection string. The archive contains documents that are directly insertable — no Beanie field translation needed on import.

When `--format mongo` is not used, the importer must add Beanie-specific fields during step 5 (transform). The only required addition is letting MongoDB generate `_id` — all other fields map 1:1 between API representation and Beanie document model.

---

## Registry Considerations

### Composite Key Construction

The `/reserve` endpoint requires composite keys. These must be constructed from entity fields:

```python
# Terminology composite key
{"ns": namespace, "type": "terminology", "value": terminology["value"]}

# Term composite key
{"ns": namespace, "type": "term", "terminology_id": term["terminology_id"], "value": term["value"]}

# Template composite key
{"ns": namespace, "type": "template", "value": template["value"]}

# Document composite key
{"ns": namespace, "type": "document", "identity_hash": doc["identity_hash"]}
```

These are the same patterns used by the services during normal creation. The archive data contains all the fields needed.

### What About the Registry's Own MongoDB?

The Registry stores `RegistryEntry` documents in `wip_registry.registry_entries`. Fast import could also bulk-insert these directly — but this is riskier than using the `/reserve` + `/activate` + `/synonyms/add` APIs, because:

- RegistryEntry has complex embedded structures (synonyms array, search_values)
- Hash computation must be identical to the service's implementation
- Index uniqueness constraints must be satisfied

**Recommendation**: Use the Registry's APIs for ID management. They're already bulk-capable and handle edge cases. Only bypass APIs for the domain data (def-store, template-store, document-store).

---

## Validation and Safety

### What Validation Is Bypassed

Direct MongoDB insert skips:
- Term value validation against terminology constraints
- Document data validation against template field definitions
- Template field reference validation
- Status transition checks (draft → active)

**Mitigation**: The source data was validated on export. The archive represents a consistent snapshot. An optional `--validate` flag could run post-import validation via service APIs (slower but catches issues).

### No NATS Events

Bypassing service APIs means no NATS events are published. Consequences:
- Reporting-sync won't see the data → **handled by batch resync (step 10)**
- Service-level caches won't be populated → services use lazy-load caching, so first API access populates the cache
- No event consumers beyond reporting-sync are affected today

### Crash Recovery

If import crashes mid-insert:
- Reserved but unactivated IDs → harmless (can re-reserve or clean up)
- Partially inserted MongoDB documents → re-run with `ordered=False` skips duplicates
- Registry activate on already-active IDs → idempotent

The entire flow is designed to be re-runnable.

---

## Performance Estimates

| Operation | Current (API) | Fast (MongoDB) | Speedup |
|-----------|--------------|----------------|---------|
| Export 244K docs | ~5 min (streaming) | ~2 min (--format mongo) | 2.5× |
| Resolve external deps | N/A | ~2 sec (value lookup) | — |
| Reserve 260K IDs | N/A | ~30 sec (bulk /reserve) | — |
| Insert terminologies + terms | ~10 min | ~5 sec (insertMany) | 120× |
| Insert templates | ~3 min | ~1 sec (insertMany) | 180× |
| Insert documents | ~60+ min | ~30 sec (insertMany) | 120× |
| Activate IDs | N/A | ~30 sec (bulk /activate) | — |
| Register synonyms | ~5 min | ~30 sec (bulk /synonyms/add) | 10× |
| File blob upload | ~10 min | ~3 min (parallel) | 3× |
| Reporting-sync catchup | Automatic (events) | ~10 min (batch sync) | Similar |
| **Total** | **~90 min** | **~15 min** | **~6×** |

The bottleneck shifts to reporting-sync batch resync and file blob uploads.

---

## Implementation Phases

**Phase 1: Self-Contained Archives** (no new import mode needed)
- Export includes external dependency data (tagged `_source: "external"`)
- Benefits current API-based import too (can resolve by value instead of assuming UUIDs match)

**Phase 2: Fast Import** (restore with original IDs)
- Add `--fast` flag to `wip-toolkit import`
- Requires `--mongo-uri` connection string
- Steps: resolve deps → reserve → insertMany → activate → synonyms → batch resync
- No changes to service code

**Phase 3: `--format mongo` Export**
- Direct MongoDB dump (requires connection string)
- Eliminates Beanie field translation on import

**Phase 4: Parallel File Uploads**
- ThreadPoolExecutor for MinIO blob uploads
- Independent of other phases

**Phase 5: Same-Host Namespace Cloning** (if needed)
- The one case requiring new IDs: same archive → different namespace on same instance
- Local UUID7 generation + in-memory remapping
- Reuses IDRemapper from current fresh.py

---

## Open Questions

1. **Should `--fast` become the default?** Once stable, API-based import is only needed when MongoDB is inaccessible (managed cloud, strict network policies).

2. **Registry export/import services**: The Registry has its own `export_service.py` and `import_service.py`. Should fast transfer build on these for the ID reservation step, or operate independently?

3. **Direct MongoDB → PostgreSQL for reporting-sync**: batch_sync currently fetches via service APIs. A `--direct` mode reading from MongoDB would eliminate one HTTP hop for the resync step.

4. **mongodump/mongorestore**: Native MongoDB tools handle BSON perfectly but are namespace-agnostic (dump entire collections). Our JSONL approach gives more control (namespace filtering, cross-namespace remapping, external deps). Could offer as an expert-mode alternative for same-host backup/restore.

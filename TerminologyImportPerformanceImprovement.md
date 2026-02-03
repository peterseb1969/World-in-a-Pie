# Terminology Import Performance Improvement

## Problem

Importing terminologies via the WIP Console UI is extremely slow. A terminology with 100 terms takes an unacceptable amount of time due to sequential per-term processing with multiple network and database round-trips.

## Analysis

### Two Import Flows Exist

The UI has two ways to import terms, both with performance issues:

| Flow | UI Component | API Endpoint | Backend Method |
|------|-------------|--------------|----------------|
| **Import Terminology** (full) | `ImportView.vue` | `POST /import-export/import` | `ImportExportService.import_terminology()` |
| **Bulk Add Terms** (to existing) | `BulkTermImport.vue` | `POST /terminologies/{id}/terms/bulk` | `TerminologyService.create_terms_bulk()` |

### Flow 1: Import Terminology (the primary bottleneck)

**File:** `components/def-store/src/def_store/services/import_export.py:282-348`

The import service loops through each term and calls `TerminologyService.create_term()` individually:

```python
for i, term_data in enumerate(terms_data):
    existing_term = await Term.find_one({...})       # MongoDB query per term
    term_response = await TerminologyService.create_term(...)  # Full create per term
```

`create_term()` (`terminology_service.py:258-339`) performs **6 operations per term**:

| # | Operation | Type | Latency |
|---|-----------|------|---------|
| 1 | `Terminology.find_one()` - verify terminology exists | MongoDB query | ~1-2ms |
| 2 | `Term.find_one()` - check code uniqueness | MongoDB query | ~1-2ms |
| 3 | `client.register_term()` - HTTP call to Registry service | HTTP round-trip | ~5-20ms |
| 4 | `term.insert()` - insert term document | MongoDB write | ~1-2ms |
| 5 | `audit_entry.insert()` - insert audit log | MongoDB write | ~1-2ms |
| 6 | `terminology.save()` - update term_count | MongoDB write | ~1-2ms |

Additionally, the import service itself adds one more MongoDB query per term (`Term.find_one` to check for duplicates at line 285), making it **7 operations per term**.

**Total for 100 terms:** ~700 sequential operations, including **100 separate HTTP calls** to the Registry service. At ~15ms average per operation, that's ~10 seconds minimum, often much worse under load.

The redundancy is also significant: the terminology existence check (`Terminology.find_one`) runs **for every single term** even though the terminology doesn't change.

### Flow 2: Bulk Add Terms (better, but still suboptimal)

**File:** `components/def-store/src/def_store/services/terminology_service.py:342-448`

This flow is better because it uses `register_terms_bulk()` to register all terms with the Registry in a **single HTTP call**. However, it still performs sequential operations per term:

| # | Operation | Type | Per-term? |
|---|-----------|------|-----------|
| 1 | `client.register_terms_bulk()` | Single HTTP call | No (batched) |
| 2 | `Term.find_one()` - check if term exists in DB | MongoDB query | Yes |
| 3 | `term.insert()` - insert term document | MongoDB write | Yes |
| 4 | `audit_entry.insert()` - insert audit log | MongoDB write | Yes |

**Total for 100 terms:** 1 HTTP call + 300 sequential MongoDB operations.

### Summary of Bottlenecks

| Bottleneck | Flow 1 (Import) | Flow 2 (Bulk) |
|------------|-----------------|---------------|
| Registry HTTP calls per term | 1 per term (N calls) | 1 total (batched) |
| Terminology existence check per term | Yes (redundant) | Once at start |
| Duplicate check per term | Yes (sequential) | Yes (sequential) |
| MongoDB insert per term | Yes (sequential) | Yes (sequential) |
| Audit log insert per term | Yes (sequential) | Yes (sequential) |
| Term count update per term | Yes (N updates!) | Once at end |

## Proposed Solution

### Change 1: Import endpoint should delegate to bulk method

**File:** `components/def-store/src/def_store/services/import_export.py`

The `import_terminology()` method (line 282 onward) should stop calling `create_term()` in a loop and instead delegate to `create_terms_bulk()`. This immediately eliminates the N individual Registry HTTP calls.

**Before (current):**
```python
for i, term_data in enumerate(terms_data):
    existing_term = await Term.find_one({...})
    term_response = await TerminologyService.create_term(
        terminology_id=terminology_id,
        request=create_term_req
    )
```

**After (proposed):**
```python
# Build list of CreateTermRequest objects
create_requests = []
for i, term_data in enumerate(terms_data):
    create_requests.append(CreateTermRequest(
        code=term_data["code"],
        value=term_data["value"],
        label=term_data.get("label", term_data["value"]),
        ...
    ))

# Delegate to bulk method
results = await TerminologyService.create_terms_bulk(
    terminology_id=terminology_id,
    terms=create_requests
)
```

The duplicate-check and skip/update logic currently in `import_terminology()` needs to be folded into `create_terms_bulk()` or handled before the call (e.g., pre-filtering by querying existing codes in one batch query).

### Change 2: Use MongoDB `insert_many` in bulk method

**File:** `components/def-store/src/def_store/services/terminology_service.py`

The `create_terms_bulk()` method should batch MongoDB writes instead of inserting one document at a time.

**Before (current):**
```python
for i, (term_req, reg_result) in enumerate(zip(terms, registry_results)):
    term = Term(...)
    await term.insert()          # Individual insert

    await TerminologyService._create_audit_log(...)  # Individual insert
```

**After (proposed):**
```python
# Collect all term documents and audit logs
term_documents = []
audit_entries = []

for i, (term_req, reg_result) in enumerate(zip(terms, registry_results)):
    term = Term(...)
    term_documents.append(term)

    audit_entry = TermAuditLog(...)
    audit_entries.append(audit_entry)

# Batch insert all at once
if term_documents:
    await Term.insert_many(term_documents)
if audit_entries:
    await TermAuditLog.insert_many(audit_entries)
```

### Change 3: Batch the duplicate check

Instead of checking each term individually with `Term.find_one()`, query all existing codes in one call:

**Before (current):**
```python
for term_req in terms:
    existing = await Term.find_one({
        "terminology_id": terminology_id,
        "code": term_req.code
    })
```

**After (proposed):**
```python
# Get all existing codes in one query
all_codes = [t.code for t in terms]
existing_terms = await Term.find({
    "terminology_id": terminology_id,
    "code": {"$in": all_codes}
}).to_list()
existing_codes = {t.code for t in existing_terms}

# Then filter without per-term queries
for term_req in terms:
    if term_req.code in existing_codes:
        # skip or update
        continue
```

### Expected Improvement

| Metric | Before (100 terms) | After (100 terms) |
|--------|--------------------|--------------------|
| Registry HTTP calls | 100 | 1 |
| MongoDB queries (duplicate check) | 100+ | 1 |
| MongoDB inserts (terms) | 100 | 1 (`insert_many`) |
| MongoDB inserts (audit logs) | 100 | 1 (`insert_many`) |
| MongoDB updates (term count) | 100 | 1 |
| **Total operations** | **~500+** | **~5** |
| **Estimated time** | **~10-30s** | **<1s** |

### Files to Modify

| File | Change |
|------|--------|
| `components/def-store/src/def_store/services/import_export.py` | Replace per-term `create_term()` loop with delegation to `create_terms_bulk()` |
| `components/def-store/src/def_store/services/terminology_service.py` | Add batch duplicate check, use `insert_many` for terms and audit logs |

### No UI Changes Required

Both UI flows (`ImportView.vue` and `BulkTermImport.vue`) already send all terms in a single request. The bottleneck is entirely server-side.

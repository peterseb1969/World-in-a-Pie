# Universal Semantic Types Implementation Plan

## Summary

Add a fixed set of **universal semantic types** to WIP that provide meaning and validation beyond base types. These types (duration, latitude, longitude, geo_point, percentage, email, url) are commonly needed across domains and reduce complexity for solutions built on WIP.

## Design Decision

**Use `semantic_type` property on existing base types** (not new FieldType enum values).

Rationale:
- Base types (string, number, object) define storage; semantic types add meaning/constraints
- Follows existing patterns (`file_config`, `validation`, `terminology_ref`)
- Backward compatible - `semantic_type` is optional
- Clean separation of concerns

## Semantic Types to Implement

| Semantic Type | Base Type | Validation | PostgreSQL |
|--------------|-----------|------------|------------|
| `email` | string | RFC 5322 pattern | TEXT |
| `url` | string | Valid HTTP(S) URL | TEXT |
| `latitude` | number | -90 to 90 | NUMERIC(9,6) |
| `longitude` | number | -180 to 180 | NUMERIC(10,6) |
| `percentage` | number | 0 to 100 | NUMERIC(6,3) |
| `duration` | object | {value: number, unit: term} | JSONB + normalized seconds column |
| `geo_point` | object | {latitude, longitude} structure | JSONB + separate lat/lon columns |

## Duration: Terminology-Based Units

Duration uses a **system-provided terminology** `_TIME_UNITS` for unit validation. This leverages WIP's existing term validation with alias support.

### Document Structure
```json
{
  "delay": {
    "value": -7,
    "unit": "days"
  }
}
```

- `value`: Number (positive or negative for relative offsets like "7 days before")
- `unit`: Validated against `_TIME_UNITS` terminology

### System Terminology: `_TIME_UNITS`

Auto-created by WIP during initialization (like wip-terminologies namespace):

```json
{
  "terminology_id": "TERM-TIME-UNITS",
  "code": "_TIME_UNITS",
  "name": "Time Units",
  "description": "System terminology for duration semantic type",
  "terms": [
    {"code": "SECONDS", "value": "seconds", "aliases": ["sec", "s", "second"], "metadata": {"factor": 1}},
    {"code": "MINUTES", "value": "minutes", "aliases": ["min", "m", "minute"], "metadata": {"factor": 60}},
    {"code": "HOURS", "value": "hours", "aliases": ["hr", "h", "hour"], "metadata": {"factor": 3600}},
    {"code": "DAYS", "value": "days", "aliases": ["d", "day"], "metadata": {"factor": 86400}},
    {"code": "WEEKS", "value": "weeks", "aliases": ["wk", "w", "week"], "metadata": {"factor": 604800}}
  ]
}
```

The `_` prefix indicates a system terminology managed by WIP. Users can add terms but shouldn't delete built-in ones.

### PostgreSQL Columns for Duration

```sql
delay JSONB,                    -- {"value": -7, "unit": "days"}
delay_seconds NUMERIC,          -- -604800 (computed: value * factor)
delay_unit_term_id TEXT         -- T-XXXXX (reference to "days" term)
```

The `delay_seconds` column enables queries like `WHERE delay_seconds > 3600`.

### Benefits of Terminology Approach

1. **Aliases for free**: "5 mins" → validated → stored as "minutes"
2. **Extensible**: Add months, years, milliseconds by adding terms (no code change)
3. **Self-documenting**: The terminology IS the documentation
4. **Consistent**: Uses existing WIP term validation

## File Changes

### 0. System Terminology Bootstrapping
**File:** `components/def-store/src/def_store/services/system_terminologies.py` (new)

Create system terminology initialization that runs on Def-Store startup:

```python
SYSTEM_TERMINOLOGIES = [
    {
        "code": "_TIME_UNITS",
        "name": "Time Units",
        "description": "System terminology for duration semantic type",
        "terms": [
            {"code": "SECONDS", "value": "seconds", "aliases": ["sec", "s", "second"], "metadata": {"factor": 1}},
            {"code": "MINUTES", "value": "minutes", "aliases": ["min", "m", "minute"], "metadata": {"factor": 60}},
            {"code": "HOURS", "value": "hours", "aliases": ["hr", "h", "hour"], "metadata": {"factor": 3600}},
            {"code": "DAYS", "value": "days", "aliases": ["d", "day"], "metadata": {"factor": 86400}},
            {"code": "WEEKS", "value": "weeks", "aliases": ["wk", "w", "week"], "metadata": {"factor": 604800}},
        ]
    }
]

async def ensure_system_terminologies():
    """Create system terminologies if they don't exist."""
    for term_def in SYSTEM_TERMINOLOGIES:
        existing = await Terminology.find_one({"code": term_def["code"]})
        if not existing:
            # Create terminology and terms
            ...
```

**File:** `components/def-store/src/def_store/main.py`

Call `ensure_system_terminologies()` on startup (after MongoDB connection).

### 1. Template Store - Field Model
**File:** `components/template-store/src/template_store/models/field.py`

Add:
```python
class SemanticType(str, Enum):
    EMAIL = "email"
    URL = "url"
    LATITUDE = "latitude"
    LONGITUDE = "longitude"
    PERCENTAGE = "percentage"
    DURATION = "duration"
    GEO_POINT = "geo_point"
```

Add to `FieldDefinition`:
- `semantic_type: Optional[SemanticType] = None`

Note: Duration uses terminology-based units, no DurationConfig needed.

### 2. Document Store - Validation Service (Authoritative)
**File:** `components/document-store/src/document_store/services/validation_service.py`

This is the **single source of truth** for all validation. Both UI and direct API submissions go through this service via `POST /api/document-store/documents`.

Add semantic validators (called after base type validation):
- `_validate_semantic_email` - RFC 5322 regex pattern
- `_validate_semantic_url` - urlparse validation
- `_validate_semantic_latitude` - range check -90 to 90
- `_validate_semantic_longitude` - range check -180 to 180
- `_validate_semantic_percentage` - range check 0 to 100
- `_validate_semantic_duration` - validates {value, unit} structure:
  - `value` must be a number (positive or negative allowed)
  - `unit` validated against `_TIME_UNITS` terminology (uses existing term validation)
  - Stores unit's term_id in term_references
- `_validate_semantic_geo_point` - dict with lat/lon, both validated

Integration: In `_validate_field_value`, after base type validation passes, call semantic validator if `semantic_type` is present.

### 3. Reporting Sync - Schema Manager
**File:** `components/reporting-sync/src/reporting_sync/schema_manager.py`

Add `SEMANTIC_TYPE_MAPPING` dict and update `_generate_column_ddl`:
- `geo_point` generates 3 columns: `{name}` (JSONB), `{name}_latitude` (NUMERIC), `{name}_longitude` (NUMERIC)
- `duration` generates 3 columns: `{name}` (JSONB), `{name}_seconds` (NUMERIC), `{name}_unit_term_id` (TEXT)
- Other semantic types use their mapped PostgreSQL type

### 4. Reporting Sync - Models
**File:** `components/reporting-sync/src/reporting_sync/models.py`

Add `SemanticType` enum (mirror of template-store) and update `TemplateField` to include `semantic_type` and `duration_config`.

### 5. Reporting Sync - Transformer
**File:** `components/reporting-sync/src/reporting_sync/transformer.py`

Handle semantic type extraction:
- `geo_point`: Populate separate lat/lon columns alongside JSONB column
- `duration`: Compute normalized seconds (`value * factor`) and populate `_seconds` column
  - Factor is retrieved from `_TIME_UNITS` term metadata
  - Also populate `_unit_term_id` from term_references

### 6. WIP Console - Types
**File:** `ui/wip-console/src/types/template.ts`

Add TypeScript types for `SemanticType`, `DurationUnit`, `DurationConfig`, and `SEMANTIC_TYPES` array with metadata.

### 7. WIP Console - FieldInput Component (UI + Optional Pre-validation)
**File:** `ui/wip-console/src/components/documents/FieldInput.vue`

Add semantic-type-aware rendering:
- `email`: email input with envelope icon
- `url`: URL input with link icon
- `latitude`/`longitude`: number input with ° suffix and range constraints
- `percentage`: number input with % suffix
- `duration`: value input with unit dropdown (populated from `_TIME_UNITS` terminology)
- `geo_point`: composite lat/lon inputs with map icon

**Optional client-side pre-validation** for better UX (not a substitute for backend):
- Show warning icon immediately if email format looks wrong
- Highlight out-of-range values for latitude/longitude/percentage
- Check duration structure (value + unit present) before submit
- These are UX hints only - backend validation is authoritative

### 8. Documentation
**File:** `docs/semantic-types.md` (new)

Document:
- Built-in semantic types and their behavior
- How to use them in templates
- **Future extensibility**: How custom semantic types could be added via Def-Store terminology (not implemented, but documented for developers)

## Example Usage

### Geo Point
Template:
```json
{
  "name": "office_location",
  "type": "object",
  "semantic_type": "geo_point"
}
```

Document:
```json
{
  "office_location": {
    "latitude": 52.52,
    "longitude": 13.405
  }
}
```

PostgreSQL:
```sql
office_location JSONB
office_location_latitude NUMERIC(9,6)
office_location_longitude NUMERIC(10,6)
```

### Duration
Template:
```json
{
  "name": "reminder_offset",
  "type": "object",
  "semantic_type": "duration"
}
```

Document (user can enter "7 d" or "days" - alias support):
```json
{
  "reminder_offset": {
    "value": -7,
    "unit": "days"
  }
}
```

PostgreSQL:
```sql
reminder_offset JSONB                -- {"value": -7, "unit": "days"}
reminder_offset_seconds NUMERIC      -- -604800 (normalized for queries)
reminder_offset_unit_term_id TEXT    -- T-XXXXX
```

Query example: Find all reminders more than 1 hour before:
```sql
WHERE reminder_offset_seconds < -3600
```

## Backward Compatibility

- `semantic_type` is optional - all existing templates/documents work unchanged
- Validation only runs if `semantic_type` is present
- No database migrations required

## Verification

1. **Template Store**: Create template with each semantic type, verify field model accepts them
2. **Document Store**: Submit documents with valid/invalid values, verify validation errors
3. **Reporting Sync**: Check PostgreSQL table has correct column types
4. **WIP Console**: Verify appropriate input components render for each semantic type
5. **Run existing tests**: Ensure no regressions

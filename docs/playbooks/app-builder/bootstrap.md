# Bootstrap Playbook

Full procedure for the `/bootstrap` slash command. The slash command stub at `.claude/commands/bootstrap.md` performs the directory pre-flight (`test -d data-model`) and the prerequisite check. By the time you are reading this, the seed file directory exists and WIP is reachable.

## What this does

Reads declarative seed files from `data-model/` in the constellation repo and creates all terminologies, terms, and templates in WIP via MCP tools. This is the executable equivalent of the data model documentation — if the seed files are in git, the data model is reproducible.

## API gotchas (read before bootstrapping)

1. **`create_terms` requires terminology UUID**, not value. After creating a terminology, use the returned UUID (or list terminologies to get it) for `create_terms(terminology_id, terms)`.
2. **`create_relationships` requires `TERMINOLOGY:TERM_VALUE` format** (e.g., `CT_DRUG_CLASS:CHECKPOINT_INHIBITOR`). Bare term values fail with "not found in namespace". The seed files already use this format in their `ontology.relationships` arrays — pass `source`/`target` directly as `source_term_id`/`target_term_id`.
3. **Cross-namespace terminology refs in templates** need the terminology UUID, not the value. COUNTRY lives in the `wip` namespace; when creating templates in your app's namespace, look up COUNTRY's UUID first and use it for `terminology_ref`.
4. **Custom relationship types** (like `targets`) must exist in `_ONTOLOGY_RELATIONSHIP_TYPES` before creating relationships that use them. Process `_ONTOLOGY_RELATIONSHIP_TYPES_EXT.json` first.
5. **Namespace must exist** before creating terminologies in it. Create with `create_namespace`.
6. **Pass namespace explicitly** on `create_terminology` calls — do not rely on implicit derivation during bootstrap, as the namespace may not be the API key's default.

## Seed file structure

```
data-model/
├── terminologies/
│   ├── _ONTOLOGY_RELATIONSHIP_TYPES_EXT.json  # System terminology extensions (underscore prefix)
│   ├── FIN_CURRENCY.json
│   ├── FIN_ACCOUNT_TYPE.json
│   └── ...
├── templates/
│   ├── 01_FIN_ACCOUNT.json          # Numbered for creation order
│   ├── 02_FIN_TRANSACTION.json
│   └── ...
└── seed-data/                        # Optional: test/sample documents
    ├── accounts.json
    └── ...
```

## Terminology file format

```json
{
  "value": "FIN_CURRENCY",
  "label": "Currencies",
  "description": "ISO 4217 currency codes used across the financial constellation",
  "terms": [
    { "value": "CHF", "label": "Swiss Franc", "aliases": [] },
    { "value": "EUR", "label": "Euro", "aliases": [] },
    { "value": "USD", "label": "US Dollar", "aliases": ["Dollar"] },
    { "value": "GBP", "label": "British Pound", "aliases": ["Sterling"] }
  ]
}
```

Terminologies with ontology relationships include an `ontology` block:

```json
{
  "value": "CT_DRUG_CLASS",
  "label": "Drug Classes",
  "terms": [ ... ],
  "ontology": {
    "relationships": [
      {
        "source": "CT_DRUG_CLASS:CHECKPOINT_INHIBITOR",
        "target": "CT_MOLECULE:PEMBROLIZUMAB",
        "type": "targets"
      }
    ]
  }
}
```

## Template file format

```json
{
  "value": "FIN_ACCOUNT",
  "label": "Financial Account",
  "description": "Bank account, credit card, share depot, or employer",
  "identity_fields": ["iban"],
  "fields": [
    { "name": "iban", "label": "IBAN", "type": "string", "mandatory": true },
    { "name": "institution", "label": "Institution Name", "type": "string", "mandatory": true },
    { "name": "account_type", "label": "Account Type", "type": "term", "mandatory": true, "terminology_ref": "FIN_ACCOUNT_TYPE" },
    { "name": "primary_currency", "label": "Primary Currency", "type": "term", "mandatory": true, "terminology_ref": "FIN_CURRENCY" },
    { "name": "holder_name", "label": "Account Holder", "type": "string", "mandatory": false },
    { "name": "account_number", "label": "Account Number", "type": "string", "mandatory": false },
    { "name": "swift_bic", "label": "SWIFT/BIC", "type": "string", "mandatory": false },
    { "name": "description", "label": "Description / Nickname", "type": "string", "mandatory": false }
  ]
}
```

Note: seed files use `mandatory` (not `required`), `terminology_ref` (not `terminology_id`), and `value` (not `code`) — the correct WIP field names.

For fields with `"mutable": true` or `"extensible": true`, pass those flags to `create_terminology`.

## Steps

### 1. Check current WIP state
Call `get_wip_status` and `list_terminologies` and `list_templates`.

If terminologies or templates from the seed files already exist:
- **Identical:** skip (idempotent — safe to re-run bootstrap)
- **Different version:** warn the user and ask whether to update or skip
- **Conflict (same value, different structure):** stop and ask the user

### 1.5. Create namespace (if needed)
If the app's namespace (e.g., `clintrial`, `finance`) doesn't exist, create it with `create_namespace`. This MUST happen before any terminology or template creation.

### 2. Extend system terminologies (if `_*_EXT.json` files exist)
For files matching `data-model/terminologies/_*_EXT.json`:
- These extend system terminologies (e.g., adding `targets`/`targeted_by` to `_ONTOLOGY_RELATIONSHIP_TYPES`)
- Look up the system terminology UUID, then `create_terms` with the extension terms
- **Must run before step 4** (relationship creation depends on these types)

### 3. Create terminologies and terms
For each file in `data-model/terminologies/` (excluding `_*_EXT.json` files):
- Read the JSON file
- Check if the terminology already exists in WIP (by value): `get_terminology_by_value(value)`
- If not, create it: `create_terminology(value, label, description, namespace)` — pass namespace explicitly
- Create all terms: `create_terms(terminology_uuid, terms)` — use the UUID from the create response, not the value
- For terminologies with `"mutable": true`, pass `mutable=true` to `create_terminology`

### 4. Create ontology relationships
For each terminology file that has an `ontology.relationships` array:
- Pass each relationship to `create_relationships` using the `source`/`target` fields directly as `source_term_id`/`target_term_id` (they're already in `TERMINOLOGY:TERM_VALUE` format)
- Pass the app's namespace
- Process terminologies that are only targets (e.g., CT_DRUG_CLASS) before terminologies that reference them (e.g., CT_MOLECULE)

### 5. Create templates (in numbered order)
For each file in `data-model/templates/` sorted by filename prefix:
- Read the JSON file
- Check if the template already exists in WIP (by value): `get_template_by_value(value)`
- If not, create it: `create_template({value, label, fields, identity_fields, ...})`
- **Important:** For fields with `terminology_ref` pointing to a cross-namespace terminology (e.g., COUNTRY in `wip`), replace the value with the terminology's UUID by looking it up first
- Verify: `get_template_fields(template_value)` — confirm fields match

### 6. Create seed data (optional)
If `data-model/seed-data/` exists and the user confirms:
- Create documents from each file
- These are sample/test documents, not production data

### 7. Summary
Report:
- Terminologies: created / skipped / failed
- Terms: total created
- Ontology relationships: created / failed
- Templates: created / skipped / failed
- Seed documents: created / skipped (if applicable)
- Any warnings or conflicts

## When to use this

- **Fresh WIP instance:** run `/bootstrap` to set up the full data model before any app development
- **Replication:** someone cloning the constellation repo runs `/bootstrap` on their own WIP instance
- **Recovery:** after a database loss, run `/bootstrap` to recreate the data model (documents are lost, but the structure is restored)
- **CI/testing:** automated tests can bootstrap a test namespace, run tests, then clean up

## When to update seed files

After any Phase 2/3 cycle that changes the data model:
- New terminology -> add a new file to `data-model/terminologies/`
- New template -> add a new file to `data-model/templates/`
- Modified template (new field, changed identity) -> update the existing file
- New terms added to a terminology -> update the existing file

**The seed files ARE the version-controlled data model.** If it's not in a seed file, it doesn't exist in git, and it can't be reproduced.

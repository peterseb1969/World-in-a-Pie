Execute Phase 3 (Implementation) of the AI-Assisted Development process.

### Prerequisites
Phase 2 must be complete with **explicit user approval** of the data model.
The WIP MCP server must be connected.

The model is **approved and frozen** for this command. If you discover mid-implementation that you need a shape the approved model doesn't have, that is a Phase-2 event — Step 0 Q1 — not an inline addition.

### Before Any MCP Calls
Read `docs/WIP_PoNIFs.md` — the 8 PoNIFs describe non-intuitive WIP behaviours that will cause silent failures if you rely on conventional assumptions. Pay special attention to:
- PoNIF #2: Template update does NOT replace the old version — both stay active
- PoNIF #3: Identity fields control create-vs-update via hash — get them wrong and versioning breaks silently
- PoNIF #4: Bulk API returns 200 OK even when items fail — check per-item results
- PoNIF #7: Edge types are templates with `usage: "relationship"` — different validation, different query endpoints; reference fields must be named exactly `source_ref` and `target_ref`
- PoNIF #8: `versioned: false` edge types overwrite in place — no version history

### Steps — Strict Order, Using MCP Tools

#### Step 0: Modelling gate (STOP if this is a new design event)
`/wip-implement` executes an **already-approved** model (see Prerequisites). Before any MCP write, answer both questions **explicitly in your output** — not silently in your head. This check is mechanical; running it is part of the command.

**Q1 — Is every terminology, term, template, field, identity-field, and edge type you are about to create or change part of the Phase-2 model the user approved?**
- **Yes** → proceed to Step 1.
- **No — you are adding to or extending the model** → **STOP. This is a new design event, not implementation.** A new template, a new `data` field, an identity-field change, a new terminology, or a new edge type is a Phase-2 decision. Return to `/wip-design-model`, get explicit user approval, then re-enter here. Do **not** add it inline because you're already in the command.

**Q2 — Are you about to persist app state — rules, config, per-doc-type behaviour, anything your code will later read back — anywhere other than a `data` field of a declared template?**
- **`metadata.*` is a throwaway scratchpad, never a data model.** It is caller-attached context (source tags, loader hints, audit traces) the platform makes no commitments about. The moment your code branches on it, sorts by it, queries it as identity, or treats its shape as a schema, you are building a **sidecar model** — the exact failure this gate exists to stop.
- **Config that matters is a document of a config template.** Need per-doc-type rules? Create the config *template* first (Phase 2), then store the rules as a config *document*. Need a controlled vocabulary? Those are **terms** — create the terminology. WIP's primitives — namespaces, terminologies, terms, templates, documents, files, relationships — are your toolkit; if a value needs structure or meaning, it has a home among them.
- **If you're unsure where something belongs, that's a discussion, not a workaround.** Ask the user before inventing a shape.

#### Step 1: Create terminologies
For each terminology in the approved data model:
- Check if it already exists: `list_terminologies` — if found, skip creation
- Create the terminology: `create_terminology(value, label, description)`
- Create all terms: `create_terms(terminology_id, [{value, label, aliases, description}, ...])`
- Verify: `list_terms(terminology_id)` — confirm all terms are present
- **If the data model specifies ontology relations** (hierarchical terminologies):
  - Create relations: `create_relations([{source_term_id, target_term_id, relation_type}, ...])`
  - Relation types: is_a, part_of, has_part, regulates, positively_regulates, negatively_regulates
  - Verify: `get_term_hierarchy(term_id, direction="children")` — confirm the hierarchy is correct
  - For bulk ontology loading, use `import_terminology` with OBO Graph JSON format
- Log: record the terminology ID and value

#### Step 2: Create templates (referenced entities first)
For each template, in dependency order (referenced before referencing):
- Create the template: `create_template({value, label, fields, identity_fields, ...})`
  - Use draft mode (`status: "draft"`) if there are circular dependencies
  - After all drafts are created: `activate_template(template_id)` — cascading validation
- Verify: `get_template_fields(template_value)` — confirm all fields, identity fields, and references are correct
- Log: record the template ID, value, and version

#### Step 3: Test with a single document
For each template:
- Create ONE test document: `create_document(document)` — pass `template_version` explicitly
- Verify: confirm the document was created with correct document_id and identity hash
- Test versioning: call `create_document` with the SAME identity fields but a changed non-identity field. Verify version increments to 2.
- Test validation: call `create_document` with invalid data (bad term value, missing mandatory field). Verify a clear validation error is returned.
- Test references: create a referencing document pointing to the first. Verify the reference resolves.

#### Step 4: Test file operations (if applicable)
- Upload a test file: `upload_file(file_path)`
- Create a document linking to the file ID
- Verify: `get_file_metadata(file_id)` — confirm file is referenced

#### Step 5: Clean up or keep test data
Ask the user whether to keep or archive the test documents.

#### Step 6: Summary
Present a summary:
- Terminologies created (with IDs and values) — distinguish new vs. reused
- Templates created (with IDs, values, and versions)
- Test results: all pass / any failures with details
- Ready for Phase 4 (Application Layer)

### Error Handling
If any MCP tool call fails:
- Do NOT continue to the next step
- Report the exact error returned by the MCP tool
- Diagnose: is it a data model issue (go back to Phase 2) or a WIP issue (investigate)?
- Ask the user how to proceed

### Transition to Phase 4
Once Phase 3 is complete and verified, the development-time work with MCP tools is done. Phase 4 shifts to writing application code that uses `@wip/client` and `@wip/react` at runtime. Read `docs/WIP_DevGuardrails.md` before proceeding.

Execute Phase 1 (Exploratory) of the AI-Assisted Development process.

### Steps

1. Read the MCP resources to internalize WIP's core concepts:
   - Read `wip://conventions` — bulk-first API, versioning, namespaces
   - Read `wip://data-model` — terminologies, terms, templates, documents, registry
   - Read `wip://development-guide` — the 4-phase process
   - Read `wip://ponifs` — powerful non-intuitive features that will trip you up
2. Verify the WIP MCP server is connected and responding:
   - Call `get_wip_status` — confirms all WIP services are reachable
   - If the MCP server is not available, ask the user to configure it
3. **Inventory existing data using MCP tools:**
   - Call `list_namespaces` — understand the namespace structure
   - Call `list_terminologies` — list ALL terminologies (value, label, status, term count). Output as a table.
   - Call `list_templates` — list ALL templates (value, label, status, version, field count). Output as a table.
   - Call `query_by_template(template_value)` for each active template to get document counts
   - Note any terminologies or templates relevant to the constellation being built
4. If the user provides a use case document, read it for domain context.
5. Summarize findings:
   - WIP instance health status
   - Existing terminologies (highlight any reusable ones like COUNTRY, CURRENCY, GENDER)
   - Existing templates (highlight any relevant to the planned work)
   - Document counts per template
   - Readiness assessment for Phase 2

### If seed files exist in `data-model/`

If the `data-model/` directory already contains terminology and template seed files, the data model has been designed and implemented in a previous session. In this case:

- Verify the seed files match what's in WIP (list terminologies and templates, compare)
- If they match: Phase 2 (design) and Phase 3 (implement) are already done. Proceed directly to Phase 4 (`/build-app`) after the user confirms.
- If they don't match: flag the discrepancy and ask the user whether to re-bootstrap from seed files or update the seed files from WIP's current state.

### Gate
Do NOT proceed to Phase 2 until:
- The WIP MCP server is connected and all services are healthy
- All existing terminologies and templates have been inventoried
- You can explain WIP's core concepts (terminologies, templates, documents, references, identity hashing, synonyms, bulk-first APIs)
- You have read the MCP resources and understand the PoNIFs

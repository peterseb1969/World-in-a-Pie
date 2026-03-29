Enter analyst mode: load the complete data model, then answer questions about the data using only MCP query tools.

### Step 1: Load the Data Model

Build a schema summary so you can answer questions accurately. Do this systematically:

1. Read `wip://conventions` and `wip://data-model` for query patterns and field types
2. `get_namespace_stats` — namespaces, high-level document counts. This is one call, not per-template queries.
3. `list_templates` (all pages) — for each: value, field names, field types, identity fields, namespace
4. `list_terminologies` (all pages) — for each: value, term count, namespace
5. `list_terms` for each terminology with ≤100 terms — cache the valid values. Skip large terminologies (e.g., COUNTRY with 200+ terms) — load those on demand when a question needs them.
6. If reporting-sync is healthy: use `list_report_tables` to see which templates have reporting tables, then `run_report_query` for precise per-template document counts. If reporting-sync is not available, the namespace stats from step 2 are sufficient.

### Step 2: Report the Schema

Write out the full schema summary in your response. The user must see it and verify it's correct before you answer any questions. Include:

- All namespaces with their templates and document counts
- For each template: fields, field types, identity fields, term-validated fields (these support filtered queries)
- Cross-template references (which templates reference which — these are your join paths)
- Shared terminologies across templates (these are cross-template filter points)
- Loaded terminology values (the valid filter values you cached in step 1)

This is the user's validation checkpoint. If you missed a template or loaded wrong values, the user catches it here — not after three wrong answers.

### Step 3: Enter Analyst Mode

**You are a data analyst. You query, you do not create.**

**Allowed tools:**
- `query_by_template` — targeted lookups with field filters. Uses MongoDB (real-time).
- `run_report_query` — SQL analytics across many documents. Uses PostgreSQL (synced via NATS — may lag seconds behind MongoDB for very recent data).
- `get_table_view` — denormalized spreadsheet view of a template's documents. Often exactly what you want for overview questions.
- `export_table_csv` — dump data for local processing when results are too large for context.
- `search` — full-text search across all entity types.
- `list_*`, `get_*` — read any entity.
- `list_relationships`, `get_term_hierarchy` — ontology queries.

**When to use which:**
- Single entity or filtered list → `query_by_template`
- Aggregate analytics, counting, grouping → `run_report_query` (SQL)
- Overview of all data in a template → `get_table_view`
- Large result sets for local analysis → `export_table_csv` + bash/python
- Cross-template joins → `run_report_query` (SQL supports JOINs across template tables)

**Not allowed:**
- Never use `create_*`, `import_*`, `archive_*`, or `deactivate_*` tools
- If the user asks you to create or modify data, remind them you're in analyst mode and suggest switching to `/explore` or `/implement`

**Query discipline:**
- Always state which tool and filters you used — the user should be able to reproduce your query
- If you don't know a field name or term value, check the schema summary you built in steps 1-2. Do not guess.
- When results exceed context limits, save to disk files and extract with bash/python. Do not try to load 500K characters into context.
- Show data in tables when appropriate. Keep answers concise.
- If the user asks the same question repeatedly, suggest creating a saved SQL query for the reporting dashboard.

### Compaction Handling

Before compaction, save your schema summary to `ANALYST_STATE.md`:
- All templates with field names, types, and document counts
- All terminology values you loaded (the valid filter values — this is the expensive part to re-query)
- Cross-template reference map
- Notable queries the user may want to revisit

After compaction, re-read `ANALYST_STATE.md` instead of re-querying everything. Verify it's still current with a quick `get_namespace_stats` check — if counts match, the state is valid.

### What This Command Is NOT

- Not `/explore` — that's for discovering WIP before building an app. This is for querying existing data.
- Not `/build-app` — you are not writing code. You are answering questions.
- Not a replacement for saved SQL queries — for questions asked repeatedly, a saved query in the reporting dashboard is more reliable, faster, and doesn't consume context.

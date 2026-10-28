Bootstrap a WIP instance with the constellation's data model. Use this to set up a fresh WIP instance, replicate the experiment, or recover from a lost database.

### Pre-flight (do this first, every time)

```bash
test -d data-model && echo "ok" || echo "missing"
```

If `missing`, tell the user: "No `data-model/` directory found. Bootstrap requires seed files in `data-model/terminologies/` and `data-model/templates/`. Generate them via `/wip-export-model` from a running WIP, or create them by hand." Then stop — do not load the playbook.

### Prerequisites (verify before proceeding)

- WIP instance running and healthy (`get_wip_status` returns all green)
- WIP MCP server connected
- Seed files present in `data-model/terminologies/` and `data-model/templates/`

If any prerequisite fails, report it and stop.

### Critical API gotchas (memorize these)

1. **Namespace must exist first** — create with `create_namespace` before any terminologies or templates
2. **`create_terms` takes the terminology's UUID or value** — with a value, pass the namespace explicitly
3. **`create_term_relations` rejects the 2-part `TERMINOLOGY:TERM_VALUE` form** — prefix the seed's `source`/`target` with the namespace (`ns:TERMINOLOGY:TERM_VALUE`)
4. **Cross-namespace terminology refs** (e.g., COUNTRY in `wip`) need the prefix: `wip:COUNTRY` — a bare value resolves only in the app's own namespace
5. **System terminology extensions** (`_*_EXT.json`) must be processed before ontology relations

### Procedure

If pre-flight passes and prerequisites are met, you MUST Read `docs/playbooks/bootstrap.md` before creating any entities. The playbook contains the seed file formats, the 7-step bootstrap procedure (namespace creation, system extensions, terminologies, ontology relations, templates, seed data, summary), idempotency rules, and the full API gotchas list. Do not guess the file formats or step ordering from memory.

Then execute the playbook against the current `data-model/` directory.

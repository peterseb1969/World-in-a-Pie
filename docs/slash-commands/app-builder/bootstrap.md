Bootstrap a WIP instance with the constellation's data model. Use this to set up a fresh WIP instance, replicate the experiment, or recover from a lost database.

### Pre-flight (do this first, every time)

```bash
test -d data-model && echo "ok" || echo "missing"
```

If `missing`, tell the user: "No `data-model/` directory found. Bootstrap requires seed files in `data-model/terminologies/` and `data-model/templates/`. Generate them via `/export-model` from a running WIP, or create them by hand." Then stop — do not load the playbook.

### Prerequisites (verify before proceeding)

- WIP instance running and healthy (`get_wip_status` returns all green)
- WIP MCP server connected
- Seed files present in `data-model/terminologies/` and `data-model/templates/`

If any prerequisite fails, report it and stop.

### Procedure

If pre-flight passes and prerequisites are met, you MUST Read `docs/playbooks/bootstrap.md` before creating any entities. The playbook contains the seed file directory structure, the terminology and template JSON formats, the 5-step bootstrap procedure (with idempotency rules), and the "when to update seed files" guidance. Do not guess the file formats from memory.

Then execute the playbook against the current `data-model/` directory.

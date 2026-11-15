---
section: fixes
cases: [1219]
---

### MCP: `export_terminology` takes every option the export endpoint takes

The `export_terminology` tool passed only `format`, `include_relations` and
`namespace`, so every export an agent made left out deprecated and inactive
terms, and the lifecycle an export carries (a deprecated term's reason and
`replaced_by_term_value`) could not be produced through MCP at all. The tool
now takes `include_inactive` (default false), `include_metadata` (default
true) and `languages`, with the endpoint's defaults. With
`include_inactive=true`, an export imported elsewhere through
`import_terminology` recreates its deprecated and inactive terms.

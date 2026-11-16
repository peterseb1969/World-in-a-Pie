---
section: fixes
cases: [1372]
---

### MCP tools tell a request that never reached WIP from one that may still be running

- **mcp-server:** a connect or pool timeout now answers that WIP was not reached and nothing was sent, instead of "the request may still be running; do not retry it before checking what it did". A read or write timeout keeps that answer.
- **`import_documents_csv`:** only a failure submitting the import job points at the namespace's job list; a timeout in the template lookup or the file preview, which write nothing and submit no job, no longer does. That check also no longer says the template's document count tells what landed: on a template with identity fields, a row that matched an existing document adds a version, not a document.

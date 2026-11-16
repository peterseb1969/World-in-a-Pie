---
section: fixes
cases: [1327]
---

### A term whose value contains '/' can be read by that value

A term value may contain `/` (unit codes like `mg/dL`, path-like codes), but the def-store routes that take a term identifier in the URL path — `GET /terms/{term_id}`, the four ontology traversals (`/ontology/terms/{term_id}/ancestors`, `/descendants`, `/parents`, `/children`) and `GET /audit/terms/{term_id}` — matched a single path segment, so such a term answered 404 by its value (raw or with the `/` sent as `%2F`), and a value ending in `/` was redirected to a different term. These routes now take the whole remaining path as the identifier, so every value form (the field form with `terminology=`, or `ns:terminology:value`) reaches the term; send the value percent-encoded, as `@wip/client` already does. The MCP server's term and ontology tools now encode the value too, so a value carrying `?`, `#` or `%` is no longer cut off. Nothing is refused: existing terms with `/` in their value become readable by it.

reporting-sync's term doors take the same rule: `GET /references/term/{term_id}/documents`, `GET /entity/{entity_type}/{entity_id}/references` and `GET /entity/{entity_type}/{entity_id}/referenced-by` answered 404 for an identifier containing `/` and now reach the term (or entity) it names.

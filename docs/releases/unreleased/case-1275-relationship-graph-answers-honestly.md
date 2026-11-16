---
section: fixes
cases: [1275]
packages: [wip-client]
---

### `GET /documents/{id}/relationships` and `/traverse`: honest answers

- **A template-store outage is reported as an outage.** Both endpoints ask the
  template store which of the templates involved are edge types; when it could
  not answer, the request ended in a bare 500. It now answers
  `dependency_unavailable` (503, retry) naming the dependency, like every other
  write path does.
- **Only edge types are edges — also when you name the type.** A `template` /
  `types` filter was applied as given, so documents of an entity template that
  happens to declare a `source_ref` or `target_ref` field could be listed as
  relationships. The filter is now cut against the edge types among the documents
  the query matches: naming something that carries no edges answers an empty
  page, which is the truth.
- **`max_nodes` counts nodes.** Endpoints in a namespace the caller cannot read
  were dropped *after* consuming a slot in the node cap, so a walk stopped short
  of `max_nodes` by exactly that many and still reported `truncated`. The cap
  now counts the nodes actually returned; a walk that filled it reports
  truncation, and one that did not says so.

The traversal node fields are also documented as they behave: every returned
node has a `via_relationship` and `depth >= 1` — the seed document is not a node
(previously the client types said `0 = seed itself`).

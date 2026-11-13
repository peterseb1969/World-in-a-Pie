---
section: fixes
cases: [1263]
packages: [wip-client]
---

### Namespace validation reports documents that lack a mandatory field of their pinned version

A document is validated when it is written, and never again. One that sits on
a template version it does not satisfy stayed invisible until its next PATCH
was refused. Two causes are known: a restore, which validates
nothing, and a merge with `align_templates=true` run before default values
became informational. That merge re-pinned documents onto a version whose new
mandatory field declared a `default_value`, although nothing fills the field.

The namespace validation job
(`POST /api/document-store/backup/namespaces/{namespace}/validate`, MCP
`validate_namespace`) and document-store's `GET /health/integrity` now report
every mandatory field of the document's pinned template version that is absent
or null in its current version, in nested objects and object array items too
(each read at its own pinned version). Superseded versions are history and are
not checked, so a document repaired by PATCH is healthy again. New issue types:

- `mandatory_field_missing`, one per field, with the field's path and the
  template version that declares it. Counted in
  `summary.mandatory_fields_missing`.
- `nested_template_not_found`: a nested template version the template store
  does not have, so its fields went unchecked. Counted in
  `summary.nested_templates_not_found`.

A template store that does not answer adds `mandatory_fields:template-store` to
`checks_not_run`. A document whose own pinned version is missing is now
reported as `template_version_not_found` with `check_identity=false` too;
before, only the identity check reported it. `@wip/client`'s
`NamespaceIntegritySummary` carries both new counters.

To repair a document found this way, PATCH it with the missing values, or
migrate it to a version it satisfies.

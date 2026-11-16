---
section: fixes
cases: [1251]
packages: [wip-client]
---

### A single-namespace key may omit the namespace on reporting queries; an import names its own faults

- **reporting-sync `POST /query` and `POST /export/csv`** now derive the namespace for a key that can read exactly one namespace, or whose scope implies one (an app key beside a read-only `wip`), as every other route does for such a key. Its schema goes on the search_path, so unqualified table names resolve. They used to resolve only when the body named the namespace. A key that can read several namespaces, and an admin key, still name their schemas, and nothing they get changes. Every namespace's schema has tables of the same names (`terms`, `templates`, ...), so for an app key an unqualified name now resolves in its own namespace, as a bare value does everywhere in WIP: a query that read `terms` and failed with "relation does not exist" now reads the app's own terms. Read the platform's shared vocabularies schema-qualified, as `wip.terms`.
- **document-store import and preview** answer 422 only for what a bad upload raises: a parser refusal, an undecodable CSV, a corrupt or non-XLSX archive. Any other exception while reading the file is a fault of the service: it is logged with its traceback and answers 500. It used to be answered 422 "Failed to parse file" and logged at INFO, as if the caller's file were bad.
- **`@wip/client`** sends an optional `namespace` on the by-id methods whose routes take one: `documents.getVersions`, `getVersion` and `getLatestDocument`; `templates.getTemplate`, `getTemplateRaw`, `updateTemplate`, `deleteTemplate`, `validateTemplate` and `cascadeTemplate`; `defStore.updateTerminology`, `deleteTerminology` and `getTerminologyAuditLog`. It scopes a value-form or synonym id for a key that can read several namespaces. A new trailing argument, or an `options` field where the method has options; existing calls are unchanged.

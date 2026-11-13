---
section: fixes
cases: [1257]
packages: [wip-client, wip-react]
---

### @wip/client and @wip/react: values reach the services whole, and the hooks refresh what a write changes

- **Path segments are encoded.** Every value the client puts into a request
  path (template values, term, document, file and job ids, key and view
  names, namespace prefixes) is percent-encoded. Before, a template value like
  `FOO#2` was sent as `FOO` (`#` starts a fragment) and `A?B` as `A` with a
  query string. A value containing `/` is sent encoded too, but the services
  still read `%2F` as a path separator, so such a value cannot be read by
  value yet.
- **`uploadFile` honours `filename` for a `File`.** It was used only for a
  bare `Blob`; a `File` was always stored under its own name.
- **`useQueryDocuments` runs any query the server takes.** A query filtered
  only on status, sort or page size never ran and stayed pending. A query that
  names `template_id` without a value yet (a template still loading) still
  waits.
- **`useFormSchema` takes `version`.** Without it the schema is the latest
  version, while an update is validated against the version the document is
  pinned to. A form editing a document passes its `template_version`. The
  version is part of the cache key.
- **Document writes refresh file lists and details**, whose reference count
  and status they move (download URLs are left alone). **Entity creates
  (terminology, term, template, document, file upload) refresh Registry
  searches on screen.**
- **`useBulkImport`** cancels the 2-second progress clear when a new run
  starts (it wiped that run's progress) and on unmount, and `reset` is stable
  across renders.
- **`<WipFooter>`**'s mark has the canonical softened look (opacity 0.7), as an
  intrinsic attribute so it applies without Tailwind compiling the class.
- Docs: the README's download-URL lifetime (a URL is cached 1 minute, valid
  for the server's `expires_in`, 1 hour by default), `useUpdateDocument`'s
  `metadataPatch`, `FormField.children` (reserved, never set: a group's
  subfields are in its referenced template), the `useReportQuery` and
  `useBatchJobs` examples (now runnable), `useTemplateByValue`'s unscoped
  result (the highest version across readable namespaces), and the session
  renewal comment (no `/apps/<name>` serving).

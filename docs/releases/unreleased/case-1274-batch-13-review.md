---
section: fixes
cases: [1274]
packages: [wip-archive, wip-toolkit]
---

### Failed writes repair under an outage, imports stop on one, and restore archives are read by what they state

- **The repair after a failed document write cannot itself fail the request.** A failed write leaves its document with no active version, and a repair gives that version back. The repair, and the check of which bulk rows a failed insert stored, both ran unguarded. With the store still unreachable, a bulk write answered 500 with no results, and the rows it had retired stayed retired. Now:
  - When the store cannot say which rows of a chunk landed, every row of that chunk is refused, saying it may have been stored.
  - The repair runs even when something else raised. A repair that fails is logged and never replaces the write's own error.
  - A row another write stored at the same version is no longer counted as this write's.
  - A row a duplicate-key retry retired gets its own `updated_at` / `updated_by` back.
- **`POST /import` stops on an outage.** If the Registry, def-store, template-store or the document store does not answer for a row, the import stops there, whatever `skip_errors` says. The answer is **503** with `error_code: dependency_unavailable`, and its `detail` is the import's report so far: the rows already written, then the row that met the outage, with the rows after it counted as `skipped`. Previously the outage became one failed row per row of the file, with a 200. Every entry of `errors` now carries the row's `error_code`. The MCP server's client now sends `skip_errors=true` by default, as the endpoint and the `import_documents_csv` tool do.
- **Bulk file writes report an outage per item.** `PATCH /files`, `DELETE /files` and `POST /files/reactivate` now answer the item an outage hit with `dependency_unavailable`, or with `registry_error` and `details.rule: registry_unavailable` for the Registry, and go on with the rest of the batch. An outage here is the Registry not answering while a PATCH resolves `allowed_templates`, or an unreachable object store. Previously the request failed with the earlier items already written and no per-item report.
- **`DELETE` and `PATCH /documents` check write permission on every namespace holding a `document_id`.** A document id is unique only within its namespace. Previously one namespace per id was asked, and a namespace never asked was read as allowed. An item is now refused when any namespace holding its id refuses.
- **`GET /documents/{id}/versions` is 404 when the document disappears between its two reads** (a hard delete), instead of returning the versions without the read check.
- **`GET /files?content_type=` follows the upload allowlist's rule:** case is ignored, and a stored type's parameters (`; charset=utf-8`) are not part of the type.
- **The MCP `upload_file` tool declares `.docx`, `.xlsx`, `.pptx` and their templates under their own types.** Without a system `mime.types` (the server's image has none) they were sent as `application/octet-stream`, and a file field allowing only Office types refused them.
- **Restore archives are judged by what the manifest states.**
  - The empty-archive waiver (a manifest-only zip) now needs the counts stated, for the archive and for each namespace. Previously absent counts read as zero.
  - The v3 gate needs a stated `format_version` of `3` or `3.N`. A manifest without one, or with `30.0` or `3-`, is refused. `wip_archive.Manifest.is_v3()` is the rule, and `wip convert-archive` uses it too.
- **A restore's namespace prefixes must each be one path segment.** A prefix such as `../x`, or an empty one, is refused with 400. Previously a prefix became part of the Registry URLs the restore calls.
- **A restore upload corrupt below the JSON layer is a 400, not a 500.** This covers bytes that are not UTF-8, a bad CRC and a broken deflate stream. `wip_archive` now raises `ManifestParseError` / `EntityParseError` for them. A 500 for an archive the service could not read now gives a fixed message, and the cause goes to the log only.
- **wip-archive:**
  - `ArchiveWriter.discard_blob` is a no-op for an id that is not a safe path segment.
  - A backup now leaves out a file whose id cannot name an archive member, and names it in a warning. Previously the whole backup failed.
  - `check_member_name` applies the full segment rule (no drive letter, no control character) to every member name.
- **Corrected claims.**
  - Macro-enabled Office files are accepted: only their own content types are outside the default list, so they still upload as `application/octet-stream` or with no type, and `.docm` / `.xlsm` / `.pptm` are not blocked extensions.
  - A revival upload's field sent empty keeps the stored value, because an empty form field reads as not sent. `PATCH /files` clears a field.

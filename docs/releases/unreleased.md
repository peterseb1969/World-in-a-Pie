# WIP — unreleased

Changes on `develop` since v2.1.0 that the next release note must carry. Move
these sections into `vX.Y.Z.md` when the release is cut, then empty this file.

---

## Action required / behavior changes (read before upgrading)

### Template cascade reaches only the parent's own children, as the caller may change them, and writes each child whole

`POST /api/template-store/templates/{id}/cascade` used to:
- find the parent's children by the parent's bare value, in every namespace. Another namespace's template with the same value counted as the parent, and its children were re-pointed;
- keep one child per bare value, so same-named children elsewhere were skipped;
- number new versions across namespaces;
- write into children's namespaces without checking write there;
- rebuild each child without its `usage`, endpoints, `versioned`, `header_fields` and `renames`. A cascaded edge-type child became an entity template.

Now:
- **Which children.** The children are the templates whose `extends` is the parent's id, each cascaded once and numbered within itself.
- **Permission.** A child in a namespace the caller can read but not write is reported per child with `forbidden`. A child in a namespace it cannot see is neither changed nor reported.
- **Whole copy.** The new version is the child unchanged except for its version, its `extends_version` pin and its stamps.
- **Unchanged.** A child already pinned to the parent's version is reported `unchanged`.

### Upload filenames: the blocklist judges the stored name; any name uploads and downloads

- **A file is refused when the name it would be stored under ends in a
  blocked extension.** The check judged the raw name, but the stored name is
  sanitised (trailing dots, spaces and control characters stripped), so
  `setup.exe.`, `setup.exe `, `setup.exe<TAB>` or `x.EXE..` passed and were
  stored as `.exe` files. Only the last extension counts: `invoice.exe.pdf`
  and `deploy.sh.zip` are accepted, deliberately — they do not run by name.
  The check inspects names and declared types only, never the bytes
  (`application/octet-stream` stays allowed).
- **A filename in any script uploads.** The name went raw into the object
  store's metadata headers, which cannot carry characters outside latin-1, so
  such an upload failed with a 500. Metadata values are now percent-encoded.
- **Downloads carry the name in RFC 5987 form**: `Content-Disposition` is
  `attachment; filename="<ASCII fallback>"; filename*=UTF-8''<encoded>` on
  `GET /files/{id}/content`, presigned download URLs, the table-view CSV
  export and backup downloads. A non-latin-1 name made the content download
  fail with a 500; a latin-1 one (`café.pdf`) was shown as mojibake.

### Template reactivate and endpoint widening need write on the template's own namespace

`POST /api/template-store/templates/{id}/reactivate` and `…/{id}/endpoints`
checked write on the `namespace` query parameter, then changed whichever
template the id named. A key with write on one namespace could reactivate a
frozen version of another namespace's template, or widen its edge type, by
naming the template's id. Both routes now require write on the namespace the
template lives in. A caller who can read that namespace but not write it gets
403. A caller who cannot see it gets the 404 of an id that names nothing.
`namespace` is still required, but it now only resolves a value or synonym.
### Concurrent writes to one Registry entry keep each other's changes

Synonym add and remove read an entry, changed its synonym list in memory and
wrote the whole list back; update, soft delete, merge, synonym retire/revive
and identity rekey saved a full copy of the entry they had read. Two requests
changing one entry at the same time silently lost one's change — a synonym
reported `added` was missing, a removed one came back with no claim behind it
(so a second entry could take the same key), and an update could undo a
concurrent soft delete. Now:

- **Every write to an entry increments its `revision`**, and a write that read
  the entry first lands only if the entry still has the revision it read.
  Writes set only the fields they change.
- **A write that lost re-reads the entry and applies its change again**, so
  the outcome is decided against the entry as it now is: two parallel adds of
  the same synonym answer `added` once and `already_exists` once; a remove
  that another request beat answers `not_found`.
- After sustained contention on one entry (10 lost attempts) the item reports
  `error_code: concurrency_conflict`, and the claims it took are released.
  Retry it.

Entries written before this have no `revision` and read as 0. Nothing to migrate.

### `POST /import` goes on past a failing row by default; `@wip/client` 0.97.0

- **Every row is attempted and reported by default.** `skip_errors` now
  defaults to `true`, as the MCP tool's already did; send `skip_errors=false`
  to stop at the first failing row and report the rest skipped, as before.
  **A caller that relied on the old default now gets the rest of its file
  written.**
- **Cut lists say so.** The response lists at most 100 created rows and 50
  errors, and now carries `results_truncated` and `errors_truncated`. The
  `succeeded` and `failed` counts always cover every row.
- **`@wip/client` 0.97.0** sends `skip_errors` whenever the caller sets it.
  It used to send it only when `true`, so under the new default an explicit
  `false` would have been dropped. `ImportDocumentsResponse` carries the two
  truncation flags.

### A failed document write no longer leaves the document without an active version

A new version is written by retiring the active version first and then
inserting its successor. If the insert failed, the document was left with no
active version, which reads as a deleted document. This applied to `POST
/documents` (single, sequential and bulk) and to `PATCH /documents`.
- **The document keeps an active version.** After a failed insert, its
  highest stored version is active again, with that version's own
  `updated_at` and `updated_by`.
- **A failed bulk insert reports per item.** When a bulk insert itself
  fails (connection lost, step-down, timeout), each row that did not land
  reports an error; previously the request answered 500 with no results.

The single-item `POST` still answers 500 on such an error.
### Word, Excel and PowerPoint files can be uploaded

The default upload allowlist refused every Office document except legacy
`.xls`. Its OOXML entry, `application/vnd.openxmlformats-officedocument.*`,
was read as one exact type because only patterns ending in `/*` were treated
as wildcards; and its Word entry, `application/vnd.ms-word`, is not a
registered type (`.doc` is `application/msword`). Now:

- **A pattern ending in `*` matches every type that starts with the text
  before it** — in the upload allowlist (`WIP_ALLOWED_MIME_TYPES`), in a file
  field's `allowed_types`, and in the `content_type` filter of
  `GET /api/document-store/files`. The three had separate copies of the
  matcher; they now share one.
- The default list admits `.docx`, `.xlsx`, `.pptx` (and their templates),
  `application/msword` (`.doc`) and `application/vnd.ms-powerpoint` (`.ppt`),
  alongside `application/vnd.ms-excel`. The unregistered
  `application/vnd.ms-word` is gone from it.
- **Macro-enabled Office documents stay refused** by default
  (`application/vnd.ms-word.document.macroEnabled.12` and kin): their types
  share neither prefix. Add them to `WIP_ALLOWED_MIME_TYPES` to accept them.

### Document bulk writes report every item and stop where asked; validation and integrity bounds hold

- **`DELETE /documents` and `PATCH /documents` report a namespace refusal
  per item.** One document in a namespace the caller could not write turned
  `DELETE` into a 403/404 after earlier documents had been deleted, with
  none of them reported, and refused `PATCH` whole with a 403. Both now
  answer 200, with the refused item reported as an error: `forbidden` when
  the caller can read the namespace, and `not_found` with the text of a
  missing document when the caller cannot see it. The rest of the batch
  proceeds. An authorization outage (503) refuses the request before any
  document changes.
- **`POST /documents?continue_on_error=false` stops at the first refusal by
  the reference gates.** These are the relationship constraints and
  namespace isolation. The items after the refused one were still written;
  they are now reported `skipped`, as for a validation failure.
- **`POST /validation/validate-candidate` accepts at most 1000 `documents`**
  (422 beyond), like every other bulk surface.
- **An integrity check's `limit` bounds what is read.** A limit under 500 was
  ignored, and the first 500 documents were checked.
- **Restore no longer counts a namespace as reporting-synced when its batch
  sync started no job** (no template syncs, or every job failed to start).
### A soft-deleted file can come back (@wip/client 0.96.0)

Soft-deleting a file kept its record and its bytes, but nothing could return
it to service: uploading the same bytes again answered HTTP 200 with the
inactive record (the checksum identity is never released), and no route
changed a file's status out of `inactive`. The only way to use those bytes
again was to change one, forking the identity. Now:

- **An upload of a soft-deleted file's bytes revives it** under the same
  `file_id`. The bytes are written to the object store again (a soft-deleted
  file's object is not watched by the integrity scan), the metadata fields the
  upload sends replace the stored ones (omitted fields keep their values), and
  `file.updated` is published. An upload of a live file's bytes is unchanged:
  the existing record, its metadata untouched.
- **`POST /api/document-store/files/reactivate`** (bulk, `[{id}]`) revives
  files without their bytes. Per item: `reactivated`; `unchanged` for a file
  that was not inactive; `not_found` (no such file, or a namespace the caller
  cannot read); `forbidden` (read but not write); `invalid_state` with
  `details.rule: storage_missing` when the object store no longer holds the
  bytes. One item's refusal never stops the rest of the batch.
- A revived file is `active` while documents still reference it (a file
  deleted with `force=true` keeps its reference count), `orphan` otherwise.
- New MCP tool `reactivate_file`; `@wip/client` `files.reactivateFile` /
  `files.reactivateFiles`.

### `PATCH /files` and `DELETE /files` report a namespace refusal per item

If one file in a batch was in a namespace the caller could not write, the
whole request answered 403 (or 404), although the files before it had already
been updated or deleted, and none of them was reported. Now the request
answers 200, and each refused item reports `status: "error"`:
- `error_code: "forbidden"` when the caller can read that namespace;
- `error_code: "not_found"`, worded exactly like a missing file, when the
  caller cannot see the namespace.

Every other item in the batch proceeds. Permissions are checked before any
item is written, so an authorization outage (503) now refuses the request
with nothing changed.
### Archive names cannot escape their paths; a restore always checks the namespaces it writes (wip-archive 0.36.0, WIP-Toolkit 0.26.2)

An archive's namespace prefixes, file ids and template values become
filesystem paths in the toolkit (transforms, `export-csv`, `split`,
`convert-archive`) and ZIP member names in their output. None was checked, so
a crafted archive could write or overwrite files anywhere the operator can
write, through `../` or an absolute path. Now:

- **Opening an archive refuses an unsafe member name** — an absolute path,
  a backslash, an empty, `.` or `..` segment, a `namespaces/<prefix>/` whose
  prefix is not one safe segment, or a `blobs/` member that is not
  `blobs/<file_id>`. The error is `UnsafePathError`, an `ArchiveError`, so
  the restore route answers 400 at upload.
- **The writer refuses an unsafe namespace prefix or file id** before it
  names a staging file, whoever supplies it (an archive, a remote instance's
  file records).
- **`export-csv` refuses a template value that is not one safe segment**
  instead of writing `<out>/<ns>/<value>.csv` outside `<out>`.
  A template whose value contains `/` cannot be exported to CSV until it is
  renamed; the error names it.
- **A manifest that is JSON but not a valid manifest** is
  `ManifestValidationError`, and **a corrupt entity line** is
  `EntityParseError` naming its member — both `ArchiveError`s, not raw
  pydantic or JSON errors.

The restore route (`POST /backup/namespaces/{ns}/restore` and
`POST /backup/jobs/{id}/restore`, modes restore and merge) admin-checks the
namespaces the engine will write: the manifest's list, or the zip's own
`namespaces/` tree when the manifest lists none. It used to check only the
manifest's list, so an archive with an empty list restored the zip's
namespaces with no permission check, and it skipped every check when the
manifest failed to parse. Now an invalid manifest is a 400, an archive that
names no namespace at all is a 400, and any other failure to read the
archive is a 500 — never a job.

### A backup of namespaces with no entities can be restored

A backup whose namespaces hold no entities is a zip with only its manifest,
because empty entity files are not written. Restore, fresh and merge refused
such an archive as malformed ("the zip has no namespaces/ tree"), so the
backup reported success but could never be restored. Now an archive whose
manifest lists its namespaces, all with zero counts, restores their settings
and nothing else. An archive missing the `namespaces/` tree is still refused
when its manifest counts entities or lists no namespaces.

### `WIP_RATE_LIMIT` limits nothing; the security audit says so

`WIP_RATE_LIMIT` is read by every service, but no service installs the
middleware that applies it, so no request has ever been rate-limited.
`reports/SECURITY-AUDIT.md` recorded C3 ("No rate limiting") as fixed; it now
says open, and the docstrings say not enforced. Enforcement is tracked in its
own case. Nothing changes at runtime.

### A file's `allowed_templates` is enforced, and stored as template ids

`allowed_templates` on `POST /api/document-store/files` and `PATCH /files`
was stored and returned, but validation never applied it: a file restricted
to `REPORT` could be attached to any template's document. Now:

- **Validation refuses a file whose list does not name the document's
  template** (`invalid_file`, naming both). It compares template ids, which
  are stable across template versions. A template inherits no permission
  from its parent.
- **Entries are resolved and stored as canonical template ids.** On upload
  and PATCH, each entry may be a template's value, `NS:VALUE`, id or a
  synonym, resolved in the file's namespace. An entry that names no template
  is refused (upload: 400; PATCH: per-item `validation_failed`, rule
  `unresolved_template`), and nothing is stored. File responses return the
  ids, not the spellings sent.
- **`PATCH` with `allowed_templates: []` removes the restriction.** It used
  to store an empty list that no template could match.
- **Files stored before this release hold values.** Document-store converts
  them to ids in the background at startup, through the Registry. An entry
  that names no template is kept and logged; it matches nothing, so the file
  stays restricted. If the Registry is unreachable, the conversion is retried
  at the next start, and until then such a file is refused everywhere. A
  restore from an older archive brings values back, which the next start
  converts.
- **Fresh and merge restores rewrite a file's `allowed_templates`** onto the
  ids its templates get on the target. A plain restore keeps ids.

### Document-store: import bounds, hidden reads, filter operands, empty pages, cursor start, integrity counts; `@wip/client` 0.95.0

- **`POST /import` and `/import/preview` refuse an upload over
  `WIP_MAX_UPLOAD_SIZE` with 413**, read in chunks as file uploads are, and
  `/import` backs off between row batches while the reporting consumer lags,
  as every other write does. There is no row cap.
- **A document or file in a namespace the caller cannot read answers exactly
  as an id that names nothing**: 404 `Could not resolve document_id '<id>' in
  namespace '<ns>'` (or `file_id`). It answered `Namespace not found`, which
  confirmed that the id exists.
- **An ill-formed filter operand is 422**, not 500: `in` / `nin` without a
  list, `exists` without true/false, `regex` without a string or with a
  pattern MongoDB cannot compile.
- **`pages` is 0 for an empty result** on `GET /documents`, `POST
  /documents/query`, `GET /files` and `GET /files/{id}/documents`, as the
  documented `ceil(total / page_size)` says (it was 1). Cursor mode also
  reports 0.
- **Cursor pagination can start**: pass `cursor=start` for the first page,
  then each response's `next_cursor`. A cursor was only ever handed out by a
  cursor-mode page, so there was no way in.
- **Namespace validation**: a document pinned to a template version the
  template store does not have is reported (`template_version_not_found`).
  `identity_hash_mismatches` now counts only hashes compared and found
  different; a document whose identity could not be compared is counted in
  the new **`identity_unverified`**.
- **Table view and CSV**: a flattened array item that is an object or array
  is written as JSON (CSV carried its Python repr, `{'a': 1}`), and an empty
  array kept as JSON is `[]`, not an empty cell.
- reporting-sync refusing a batch sync or schema drop is logged.
- **`@wip/client` 0.95.0**: `NamespaceIntegritySummary.identity_unverified`.

### Declared constraints that did not hold now hold

- **NaN and ±Infinity are refused anywhere in a document's data**
  (`non_finite_number`, per field path). JSON parsing accepted them, NaN
  passed every minimum/maximum check, and a stored NaN read back as `null`.
- **`pattern` and `range` rules are evaluated.** Template-store accepted
  them and document-store skipped them. A pattern rule matches the whole
  value, like a field's `validation.pattern`; a range rule's bounds are
  inclusive. Both apply when their `conditions` hold (none: always) and pass
  an absent target. Documents that violated such a rule are refused on
  their next write.
- **Template-store refuses a rule document-store could not evaluate**, per
  item (`validation_failed`, `details.rule: "invalid_rule"`): no
  `target_field` (or `allowed_values`, or a `dependency`'s `conditions`),
  fewer than two `target_fields`, a pattern that does not compile, a range
  without a finite bound or with minimum above maximum, an `in` / `not_in`
  condition whose value is not a list. **An unknown key in a rule or a
  condition is a 422**: `condition` (singular) used to be dropped, which
  made the rule apply to every document. A template stored before this can
  still carry such a rule; its document writes now fail with `invalid_rule`
  (they crashed with a 500, or the rule was skipped). Fix the template's
  rules.
- **`hash:<identity_hash>` document references resolve.** The prefix was
  sent to the Registry, which holds only the digest, so every one failed
  as `reference_not_found`. A digest two templates share is narrowed by the
  field's `target_templates`, and refused as `ambiguous_reference` when
  that leaves several.
- **Two query filters on one field both apply.** `POST /documents/query`
  kept only the last filter on a field (`gte 2` then `lte 3` returned
  everything up to 3); a filter on `template_id` or `status` replaced the
  request's own. Every filter is ANDed now.
- **A terminology reference resolves in the document's namespace and is
  asked of def-store on every write.** A bare value used to be looked up in
  every namespace and cached for five minutes under that bare value, so one
  namespace could receive another's terminology, and a retired terminology
  still validated. The terminology cache is gone, and with it the
  `terminology_cache` block of `GET /debug/cache` (now `def_store`, the
  term-resolution counters).
- **The integrity check reads the template version each document is pinned
  to**, not the latest, so a retired old version is reported for the
  documents that use it.

### Backups leave out inactive template versions nothing needs; a merge does not refuse over a template's status; `@wip/client` 0.94.0

**Backup** (`POST /api/document-store/backup/namespaces/{namespace}/backup`,
the Console, MCP `start_backup`) used to carry every template version. It
now leaves out an inactive version unless something in the archive needs
it. A version is still carried when:

- it is not inactive (drafts are carried);
- a document row of any status is pinned to it — including the superseded
  version rows of a document — so archives restore and `verify` cleanly, as
  before;
- a carried template version extends it;
- or it is the highest version of a template whose versions are all
  inactive. A template never disappears from an archive while other
  definitions and its Registry entry name it.

`include_inactive_templates: true` keeps every version. The job reports how
many versions it left out. The retired `include_inactive` flag still answers
400.

**Merge** no longer refuses a template over its status alone. An archive
version that is inactive where the target's is active (or the reverse), with
the same schema, is the same definition. The target keeps its own status,
and the difference is reported among the fields where the target's value
won. A schema difference still refuses.

Neither change makes a merge of two archives with drifted **versioned**
bootstrap templates work. `migrate` leaves each document's superseded row
pinned to the old version, so the backup still carries it and pass 1 still
refuses. `docs/design/restore-modes-merge-and-new-namespace.md` records this
as open; it no longer offers a recipe.

- **`@wip/client` 0.94.0:** `BackupRequest.include_inactive_templates`.
- **MCP:** `start_backup(include_inactive_templates=…)`.
- **The WIP-Toolkit CLI export is unchanged.** With `include_inactive` off
  (the default) it exports only active entities.

### `/relationships` and `/traverse` count only edge-type documents

- **Only documents of edge types (`usage: "relationship"`) are edges.** Both
  routes found edges by `data.source_ref` / `data.target_ref` alone, so a
  document of an entity template that declares a field of that name was
  listed, counted in `total`, and walked as an edge.
- **`/traverse` stops reading a hop once `max_nodes` is reached**, and reads
  a hop's endpoint documents in one query instead of one per node.
- `TraverseNode.depth` is documented as it is: 1 for a direct neighbour; the
  seed is not listed.

### Document validation: `enum` on every scalar type, `max_files`, exact email

- **`validation.enum` holds for number, integer and boolean fields**, not
  only strings (`invalid_enum`). A template that declared an enum on a
  number was never enforced; writes outside it are now refused.
- **`file_config.max_files` caps a multi-file field** (`max_files`, with
  `details.max_files` and `details.count`). It was declared and shown in
  forms but checked nowhere.
- **An `email` value must be the address alone**: a trailing newline was
  accepted.
- A reference resolved through the Registry answers its latest active
  version (or latest version) every time; with several active rows it could
  answer an older one.
- A template the template store does not have yet is no longer remembered as
  missing for up to 5 seconds: one created a moment later is found at once.

### CSV exports cannot carry spreadsheet formulas; the table view is bounded and counts documents; `wip-archive` 0.35.0

- **Formula escape in every CSV WIP writes:** document-store's table export,
  reporting-sync's `/export/csv`, def-store's terminology export, and
  `wip-toolkit export-csv` (wip-archive).
  - A string cell or column name starting with `=`, `+`, `-`, `@`, tab or CR
    gets a leading `'`, so a spreadsheet shows it as text instead of running
    it.
  - Numbers are written as they are.
  - A string that already starts with `'` followed by one of those characters
    gets one more `'`, so the escape stays reversible.
- **WIP's CSV importers reverse it:** the file import (`import_documents_csv`)
  and the terminology CSV import drop that one leading `'`, so exporting and
  re-importing gives back the original values. An ordinary `'90s` is never
  touched.
- **`GET /table/{template_id}` refuses a page that flattens to more than
  10,000 rows** (422). It used to reach `page_size` × `max_cross_product`, up
  to 10 million. Lower either knob.
- **`GET /table/{template_id}/csv` streams** from a cursor in chunks. It used
  to hold every document, every row and the whole file in memory at once.
- **Both show each document once, at its latest version.** A document with
  several active rows, as a merge restore with `older_versions: keep` leaves
  it, used to appear once per row and be counted once per row.
  - `total_documents` counts those documents.
  - `total_rows` is described as what it always was: the rows on this page.

### Replay names its namespace and its errors, and frees what it held; startup no longer waits on the integrity scan; `@wip/client` 0.93.0

- **`POST /replay/start` requires `filter.namespace`** (422 without it).
  - It used to replay `wip` when the filter or namespace was left out.
  - Every session response carries `namespace`, and a failed session carries
    `error`, where it used to say only `failed`.
  - `@wip/client` 0.93.0: `ReplayRequest.filter` and `ReplayFilter.namespace`
    are required, `startReplay()` takes no default request, and
    `ReplaySessionResponse` gains `namespace` and `error`.
- **The count no longer sorts**, and the publish scan may spill to disk, so a
  large namespace no longer hits MongoDB's in-memory sort limit. A MongoDB
  failure counting the documents is a 503 naming the cause, not a bare 500.
- **Replay streams expire unconsumed events after 24 hours** (`max_age`), so a
  stream nobody drains, or one a pod restart orphaned, no longer holds its
  documents in NATS memory. Finished sessions beyond the newest 50 are
  forgotten.
- **The startup integrity scan runs in the background.** It used to be awaited
  before the service served, contrary to its comment, and it counts every
  active document on the instance. Startup no longer waits on it.
- **`/debug/timing` statistics are bounded:**
  - counts, totals, min and max cover every run;
  - the percentiles cover the last 1,000 samples per stage, given as
    `percentile_window`.

  They used to keep one float per stage per call for the life of the pod.

### Namespace writes accept `deletion_mode` only as `retain` or `full`

`POST /api/registry/namespaces` and `PUT /api/registry/namespaces/{prefix}`
took `deletion_mode` as any string. The guards compare the exact value, so
`"FULL"`, `"Full"` or `" full"` got past both of them: the refusal to enable
deletion on `wip`, and the `confirm_enable_deletion` step for `retain` →
`full`. An upsert of an existing namespace then stored that value. The stored
model refuses it on read, so every later read of that namespace failed, as
did every namespace listing on the install, and no API call could repair it.

- **Any other value is a 422** on create, upsert-create and upsert-update,
  and nothing is stored. The narrow `PATCH` route already refused such
  values with a 400; it is unchanged.
- **No value was normalised** (no case-folding or trimming): the confirmed
  switch from `retain` to `full` is meant to be explicit.
- **An install that already stored such a value** still fails on reads of
  that namespace. Repair it in MongoDB: set the namespace's `deletion_mode`
  in `wip_registry.namespaces` to `retain` or `full`.

### Restores rehash each document against the template it is pinned to; restored Registry rows search by their new keys

- **Fresh restore**: a document's identity hash is recomputed against the
  template version it is pinned to, in whichever archived namespace that
  template lives. Before, the definition was looked up by template value in
  the document's own namespace: a document on another namespace's template
  kept its archived hash (the next write of that identity made a second
  document), and one whose namespace held a template of the same value was
  rehashed by the wrong definition.
- **Templates the archive does not carry** are read from this install, in
  fresh, merge and plain restores alike, so documents on them are rehashed
  too. A keyed document whose template is known nowhere keeps its hash; a
  fresh restore counts it (`planned.<ns>.unrehashable_documents`) and warns.
- **Merge and plain restores** rehash with the template's
  `referent_identity_paths`, so a dotted identity path into a nested template
  is recomputed instead of kept with a warning.
- **A fresh restore into a pre-created target with prefixed ids** keys each
  document on its template's new id. The id was mapped twice, and a new id
  equal to another template's old id keyed the document on the wrong
  template.
- **A merge redirected into another namespace** renames
  `references[].resolved.namespace`, so the post-merge validation no longer
  reports every in-archive document reference as `misplaced_document_ref`.
- **Restored Registry rows' `search_values`** are derived from their keys as
  written, so a value search finds a rehashed or re-namespaced entry by its
  new values and no longer by its old ones.

### Namespace deletion dry run, create races, prefix length

- **`DELETE /api/registry/namespaces/{prefix}?dry_run=true`** reports the
  delete's refusals: new `refusals: [{rule, message}]`
  (`default_namespace`, `deletion_mode`, `locked`), and `safe_to_delete` is
  false when any applies. It said `true` for `wip`, retain-mode and locked
  namespaces, which the delete refuses.
- **Two creates of one prefix at once** no longer answer 500 to the loser:
  `POST` answers the 409 an existing namespace gets, `PUT` applies the
  loser's settings as an update of the winner (under the update guards, so
  retain→full still needs `confirm_enable_deletion`), and `initialize-wip`
  answers the namespace.
- **A namespace prefix is at most 63 characters**, the longest PostgreSQL
  schema name reporting can hold (it refused longer ones at sync time).
  Existing namespaces keep their names.
- A deletion removes only the namespace's own ID counters, even for a name
  from before the prefix rule that holds a regex character.

### Document writes: bulk auto-synonym, `by-identity`, pinned versions, `document_id`, PATCH identity; `@wip/client` 0.91.0

- **Bulk creates register each new document's template-scoped
  auto-synonym**, as single creates do. A document whose synonym the
  Registry refuses fails with `registry_error` and is not written.
- **`GET /documents/by-identity/{hash}`** accepts a template synonym (its
  value) as `template_id`, searches only namespaces the caller can read,
  answers the latest version, and **answers 409 `ambiguous` with the
  candidates** when several documents match — pass `namespace` and
  `template_id`. It used to return an arbitrary one.
- **A pinned version (`document_id` + `version`) that is already taken is
  refused** with `already_exists` (`details.rule: version_taken`). It used to
  be written as the next version, retiring the document's current one.
- **A `document_id` without `version` must be the document the identity
  resolves to**, or the item is refused with `invalid_request`
  (`details.rule: document_id_mismatch`). The single-item path dropped it
  silently; bulk sent it as a restore id, so re-posting a fetched document
  with its own id failed — it now updates the document.
- **PATCH** accepts another alias of the same term (or reference) in an
  identity field; only a different referent is `identity_field_change`.
- **Migrate dry run** reports the document version the apply writes, and
  create, upsert and migrate results carry `template_version`.
- **`validate-candidate`** works out a candidate's nested identity paths
  instead of refusing them.
- **`@wip/client` 0.91.0**: result types carry `template_version`;
  `getDocumentByIdentity` takes `templateId`.

### `POST /documents`: a batch writes each document into its item's namespace

Each item of `POST /api/document-store/documents` names its namespace, but a
batch of two or more items wrote **every** document into the first item's
namespace: identity registered there, validated there, and reported
`created`. A one-item request with the same content went to the right
namespace, so the outcome depended on batch size and the response could not
show it.

- **Each document is now written into the namespace its item names.** The
  batch is processed in runs of consecutive items that share a namespace, in
  submission order. A single-namespace batch is one run, as before.
- **`continue_on_error=false`** stops the request at its first failing item,
  and every later item is `skipped`, whichever namespace it names.
- **An empty body (`[]`)** answers an empty `BulkResponse`. It used to be a
  500.
- **Documents a mixed batch misplaced before this fix stay where they were
  written.** They carry the first item's namespace and a document_id
  registered there; a later write of the same content to the intended
  namespace creates a separate document.

### A restore acts as its requester at the Registry, logins included

A restore creates and configures namespaces at the Registry with the
requester's own credential. A platform admin who logged in (OIDC) used to
have this done with document-store's own Registry key, so the Registry could
not tell who asked. Now:

- **An OIDC login's bearer token is passed on**, as an API key always was.
  The Registry validates it and judges the caller. A login can now also
  create a namespace by restore (a reservation's holder, as reserved), which
  used to need an API key.
- **A login a gateway forwarded is refused** wherever the restore would
  create or configure a namespace: its request carries the gateway's key,
  not the user's, so it has no credential to pass on. That covers every
  platform admin restore, since an admin's restore always applies the
  archive's settings, and any restore into a namespace that does not exist.
  The error says to use an API key or a direct login.
- **The Registry records who asked**: the upsert carries the requester as
  `updated_by`, so a namespace a restore creates has a `created_by`.
- document-store's own key is used only when the service acts as itself
  (tests and tooling inside the service), never for a request.

### `if_match` guards versioned:false documents: send `revision`; `@wip/client` 0.92.0

A `versioned: false` document stays at version 1, and `if_match` compared
against the version, so it guarded nothing: two PATCHes holding
`if_match: 1` both answered `updated`, and the second silently replaced the
first. Now:

- **Every document and every document write result carries `revision`**, the
  token to send as `if_match`. On versioned templates it equals `version`, so
  nothing changes there. On a `versioned: false` template each in-place write
  moves it on (PATCH, a POST that overwrites, a restore merge that replaces the
  row) while the version stays 1.
- **`if_match` compares against `revision`**, and the in-place write is
  conditional on the revision read. A write that lands between the check and
  the write is caught too. Of two writers holding one token, one wins and the
  other gets `concurrency_conflict`. A PATCH without `if_match` that loses the
  race re-reads and merges onto the winner; a POST overwrite re-reads and
  replaces it.
- **Action for callers of versioned:false documents:** set `if_match` from
  `revision`, not `version`. A caller still sending `if_match: 1` gets
  `concurrency_conflict` once the document has been written in place, where it
  used to succeed and overwrite blindly.
- **Stored rows need no migration.** `revision` is stored from a row's first
  in-place write on; until then it is served as the version.
- **`@wip/client` 0.92.0:** `Document.revision` and `BulkResultItem.revision`.
  `updateDocument(…, { ifMatch })` is unchanged; pass the revision.

### Registry: a composite key stays claimed by the entry that holds it

The claims collection is what keeps a composite key to one entry. Several
paths could leave an entry holding a key with no claim, after which another
entry could take the same key as a synonym and lookups by it became
ambiguous. They no longer can:

- **A refused claim rolls back only what its own request took.** An
  activation refused because one of its synonyms belongs to another entry
  used to delete the claim on the reservation's own key as well. Every
  pending claim now carries its request's token, and a rollback deletes only
  claims that still carry it.
- **Two requests on one entry no longer delete each other's claims.** A
  request that finds its entry already holding a key takes the claim over as
  its own in-flight claim (pending, dated now, no token). The request that
  inserted it can no longer release it, and a healer or audit running
  meanwhile leaves it alone.
- **`POST /entries/rekey-identity` moves the claim with the key.** It claims
  the new key before writing, confirms it after, and releases the old one.
  A target key another entry holds as a **synonym**, or is claiming right
  now, is a `conflict`. Before, only another entry's primary key was. The
  entry's `updated_at` now moves too.
- **A batch insert that fails part-way reports what it wrote.** The
  `register`, `reserve` and `provision` insert is ordered. Entries before the
  failing one are written: they keep their claims, and `register`/`reserve`
  report them `created`/`reserved` instead of `error`. Only the entries that
  were not written lose their claims. When the error does not say how far the
  insert got, every claim stays pending for the pending reconcile.
- **The orphan audit (`reconcile_orphan_claims`, run by
  `python -m registry.admin_backfill_claims`) skips pending claims younger
  than the grace window.** It deletes a claim only in the state and with the
  date it read, so it can run against a live Registry.

### Registry lows: export, activation errors, status filters, ID formats; one version per service

- **`POST /api/registry/entries/export`** answers `not_found` for an entry in a
  namespace the caller cannot read, exactly like a missing id, so the route no
  longer confirms that an id exists (as `/entries/lookup/by-id` already did).
  An entry the caller reads but does not administer is still `forbidden`, so an
  export key short of admin still fails loudly.
- **`POST /entries/activate`**: a database failure is now an error on the items
  it concerns. On the keyed path (reading the entries, the claim insert, the
  post-write re-read) it used to be a whole-request 500. On the keyless path, a
  failed flip marked every keyless item an error; now only the reserved items
  it was for. A failure confirming or releasing claims is logged; the claim
  stays pending for the startup reconcile.
- **`GET /entries` and `GET /entries/search`** refuse a `status` other than
  `active`, `reserved` or `inactive` (422). An unknown value used to return an
  empty page.
- **`/health`'s `auth_enabled`** is the mode the auth layer enforces
  (`WIP_AUTH_MODE` is not `none`). It used to echo `AUTH_ENABLED`, which nothing
  else read; that variable is gone.
- **ID formats (`/entries/reserve`):**
  - a UUID must be in canonical form: lowercase, hyphenated, no braces or
    `urn:uuid:`. Any version is accepted;
  - a prefixed ID is its prefix followed by one or more digits. IDs whose
    counter outgrew `pad`, or that were minted under an earlier `pad`, used to
    be refused;
  - a `pattern` must match the whole ID;
  - a nanoid may not carry a trailing newline.
- **`id_config` writes** refuse:
  - a prefix with characters other than letters, digits, `_`, `.` and `-`, or
    longer than 32;
  - `pad` above 20;
  - a nanoid `length` outside 8–64.

  Configs already stored are re-accepted unchanged, as before. A field of
  another algorithm (`pad` on uuid7) is still accepted and has no effect.
- **`/entries/reactivate`** is documented as what it is: it reclaims synonyms
  and does not change the entry's status.
- **Versions:** registry, def-store, template-store and document-store serve
  one version, their `__version__`, at `/`, in build metadata and in OpenAPI
  (registry 0.4.0, the three stores 0.2.0). `/` used to report a different
  number.

### PATCH and migrate enforce the edge namespace rule

- **PATCH and migrate run the relationship constraints** that create runs.
  An edge lives in its source's namespace: a PATCH that re-points an edge's
  `source_ref` into another namespace is refused with
  `cross_namespace_relationship`, as POST refuses it. (Reachable only for an
  edge type whose `identity_fields` leave out `source_ref`; with it in the
  identity, PATCH already refused the change.) Migrate refuses to re-pin an
  edge whose source lies in another namespace.
- **Action:** an edge that already carries a source in another namespace
  (written before the rule, or landed by a restore) can no longer be patched
  or migrated; write it in its source's namespace.
- **The outbound gate takes a template's or terminology's namespace from
  validation**, which resolved it, instead of looking it up in the Registry.
  No behaviour changes today: a retired template's Registry entry stays
  active, so the lookup already placed it and a strict namespace's
  allow-list already applied. The gate no longer depends on that lookup for
  these referents (a referent whose entry is inactive would otherwise be
  skipped).

### The table view and its CSV export show only documents the caller can read

`GET /api/document-store/table/{template_id}` and `…/csv` checked read on the
template's namespace and then returned every document on the template. A
document's namespace is independent of its template's, so a key that could
read a shared template's namespace got other namespaces' rows, identity hashes
and document counts. Now:

- **Both routes apply the caller's namespace filter** to the rows and to
  `total_documents`: the documents of every namespace the caller can read, as
  `GET /documents` already does. Read on the template's namespace is still
  required.
- **New `namespace` parameter** limits the documents to one namespace; naming
  one the caller cannot read is refused (403/404). It is also the namespace a
  bare template value resolves in, as on `GET /documents`: a shared template
  is named `wip:PATIENT` (or by id), never found by falling back.
- **The MCP `get_table_view` and `export_table_csv` tools** forward their
  `namespace` to it. Before, it only chose where the template value was
  resolved.
- **`@wip/client` 0.90.0:** `documents.getTableView`'s `TableViewParams` and
  `documents.exportTableCsv`'s params take `namespace`.

### A stuck namespace deletion can always be finished

- **`POST /api/registry/namespaces/{prefix}/resume-delete`** now resumes a
  deletion that stopped on a failed step (`failed`) or finished with
  degraded best-effort steps (`completed_with_warnings`), not only one still
  in progress; completed steps are skipped. A namespace left locked with no
  journal (the deletion stopped before writing it) gets one built at resume.
  A deletion already running answers 409. There is no abandon: a partly
  deleted namespace is finished, not undone. The background loop still
  retries only deletions in progress.
- **Platform admins see a locked namespace** in `GET /namespaces/{prefix}`
  and in listings, as `status: locked`, so a stuck deletion is visible.
  Nobody gets access to its data.
- **Large namespaces**: stored-file keys are split into steps of 1,000, so
  the journal stays under MongoDB's document size limit.
- **Retries**: a connection reset or any transport error from
  document-store or reporting-sync defers the step for retry instead of
  recording it as degraded; an unreadable 200 from document-store is a
  degraded step, not a failed deletion.
- A journal records the deleted namespace's `namespace_id`, and later
  steps tell a namespace created again under the name by that id.

### File import converts each cell to its field's type

`POST /api/document-store/import` (and the MCP `import_documents_csv` tool)
passed every cell to validation as text, so a mapped number, integer or
boolean column failed every row, and an XLSX date cell (read as
`"1990-04-12 00:00:00"`) failed a date field. CSV date columns already worked.

- **Now:** each cell is converted to its template field's type first.
  Integer fields take whole numbers, number fields finite numbers (`1e3`
  works), boolean fields `true`/`false`, `yes`/`no` or `1`/`0` in any case,
  date fields `YYYY-MM-DD` or an XLSX date cell (refused if it carries a time
  of day), datetime fields ISO 8601 or an XLSX date-time cell. Every other
  field keeps the text as written; a whole-number XLSX cell in a text field
  reads without `.0`.
- **A cell its field's type cannot read fails its row**, reported in
  `errors` with the field and column (`age (column 'age'): 'thirty' is not an
  integer`), like any validation failure; the other rows import.
- **`/import/preview`** builds only the five rows it shows and counts the
  rest, instead of holding the whole file as rows.

### `/search/by-fields` matches only keys holding every field

`POST /api/registry/search/by-fields` (and `/search/across-namespaces`)
returned an entry when **any** queried field matched, even one field in the
primary key and another in a synonym, and labelled such an entry a
primary-key match. An entry now matches only when one key, its primary key
or one active synonym, holds every field at the given value. Values are
compared as values (a `{"$ne": …}` value no longer acts as a query
operator), field names cannot contain dots (composite keys are flat), and
empty `field_criteria` is refused with 422. Single-field searches behave as
before.

### `prune` and `filter` keep the templates and terminologies documents name; `wip-archive` 0.34.0

A document can name a template or a terminology through a reference field
whose `reference_type` is `template` or `terminology`. Archive transforms did
not see those references:

- `prune` could remove a template no document was pinned to but a document
  named, and a terminology only a pruned template declared but a document
  named, while the documents travelled;
- `filter` carried a kept document's pinned template and its terms'
  terminologies, but not a template or terminology the document named.

Now both follow those references: a named template is used (prune) or
carried (filter), with everything its schema needs, and a named terminology
is kept whole. `inspect`'s data closure lists them too. They are definitions,
so `filter --boundary strict` carries them rather than refusing. Re-run
`prune` or `filter` on an archive cut with an older version if its documents
use such fields; `verify` does not detect the cut (it checks stored term
references, not template- or terminology-typed ones).

### Query filters can't carry MongoDB operators; MongoDB runs without server-side JavaScript

`POST /api/document-store/documents/query` (and the MCP `query_documents` /
`query_by_template` tools, which use it) put a filter's `field` into the
MongoDB query as a key and its `value` in as the operand. A field of `$expr`
ran per-document computation, `$function` inside it JavaScript, on the
MongoDB all of WIP shares, for any key with read on one namespace; a value
like `{"$regex": …}` on an `eq` filter applied an operator around the
operator allowlist, and a bare `$where` reached MongoDB and came back as an
unhandled error. Now:

- **A field path segment may not start with `$`** (`$where`, `$expr`,
  `data.$x`): 422. Field paths stay free otherwise, so every name a template
  can declare (`data.first-name`, `data.größe`) still filters.
- **A filter value may not contain a `$`-prefixed key** at any depth, inside
  objects or list elements, for every operator: 422. A plain object value
  (`{"city": "Berlin"}`) still matches a sub-document exactly.
- **MongoDB serves with `--noscripting`.** No WIP query uses `$where`,
  `$function` or `$accumulator`. An existing install picks it up on its next
  `wip-deploy redeploy` (the MongoDB start script changes, so MongoDB
  restarts once). `wip-deploy verify --security` reports an install still
  rendered without it ("MongoDB server-side JavaScript").

### A namespace locked for deletion stays locked

A deletion locks its namespace before it removes anything, and its recovery
and clean-up steps read the lock. Archiving a locked namespace removed the
lock (the deletion then skipped its PostgreSQL rows, and document-store
refused its stored-file delete), and the upsert and the deletion-mode PATCH
changed a namespace mid-deletion. Now `POST …/{prefix}/archive`,
`PUT …/{prefix}` (an existing namespace, dry runs included) and
`PATCH …/{prefix}` answer **409** for a locked namespace. Those writes,
`POST …/{prefix}/restore` and the deletion's own lock are each a single
conditional update: a write that read the namespace before it was locked
answers 409 instead of undoing the lock, and two deletions cannot both take
it.

### Migrate refuses a document whose stored identity hash is stale; `wip-archive` 0.33.0

`POST /documents/migrate` carries each document's stored identity hash onto
the re-pinned version. Its per-document guard compared against a hash that
validation never computed, so it never fired: a document whose stored hash its
identity no longer produced was migrated, and forked into a second document on
its next create. That happens to a hash written before identity fed resolved
referent ids, or when the target version retypes an identity field under the
same name.

- **Now:** migrate recomputes each document's identity hash from its validated
  data under the target version (the value the Registry would store) and
  refuses a document whose stored hash differs: `error_code`
  `identity_hash_stale`, with `details.stored` and `details.expected`, in the
  dry run as in the apply. The error names the way out: re-hash the namespace
  (`POST /api/document-store/maintenance/rehash-identities`) if the hash
  predates the current rules, or create new documents if the target changes
  what identifies them. Healthy documents migrate as before.
- **Offline check agrees** (`wip-archive` migratability, behind
  `wip-toolkit migrate`): the same refusal with the same code (it was
  `identity_fields_changed`). An identity term whose vocabulary the archive
  does not carry (shared `wip` terms, for example) is taken from the
  document's stored resolution instead of failing every such document. A
  cohort whose source version's parent template is missing now says so,
  instead of "version N is not in the archive".
- **`verify --namespace`** on a multi-namespace backup no longer reports a
  term carried in another of the archive's namespaces as missing.
- **Action:** a script that matched the per-document `identity_fields_changed`
  code should match `identity_hash_stale`. The operation-level refusal
  (identity field names differ between versions) keeps
  `identity_fields_changed`. WIP-Toolkit's floor stays `wip-archive>=0.31.0`;
  install 0.33.0 to get the offline changes.

### A document hard delete releases only the files the document still holds

- **File references.** A document holds the files of its latest version
  while that version is active. The whole-document hard delete released
  every stored version's files again, so a file another document still used
  could reach a count of 0 and be deleted with its bytes; a soft-deleted
  document's files were released twice. Now it releases the latest version's
  files only when that version is active, and removes, once, each file any
  version referenced that nothing holds any more. Hard-deleting one version
  releases its files only when it is the active latest version.
- **Reporting.** The whole-document hard delete event carried one arbitrary
  version, so reporting-sync deleted only that version's row (and, under
  `latest_only`, often none). It now carries `version: null`, which removes
  the document's rows of every version.
- **Deleting in one request.** Documents hard-deleted whole in the same
  request do not block each other, but only those that will be deleted
  count now: one that is absent, not writable, in a `retain` namespace, or
  still referenced from outside the request no longer lets the documents it
  references go while it stays.

### `inspect --json` lists each outside reference once, with a count; `wip-archive` 0.32.0

The offline archive model kept one entry for every reference to a target
outside the archive, per document version. An app backup whose documents use
the shared `wip` vocabularies held memory in proportion to its references,
and `inspect --json` repeated each target once per occurrence. Now:

- **Aggregated.** An outside or dangling reference is one entry per (kind,
  target, reason, referring template), with `count` (occurrences) and
  `origin_template`. `referenced_by` is the first referrer, a sample. The
  model's memory is O(definitions + document ids + distinct outside targets
  per referring template).
- **Latest-only is latest-only.** `data_closure_latest_only.external` lists
  only what latest document versions reference. It used to repeat the
  with-history list.
- **Reasons for terms and files** follow the archive's referent table: a term
  or file recorded there in a namespace the archive does not carry is
  `other-namespace-not-in-archive`, as documents already were. They used to be
  `not-in-archive` always.
- **The findings' counts are unchanged:** `external_reference` and
  `dangling_document_reference` still count every occurrence.
- **JSON schema 1.3.** A consumer that took `len(external)` as an occurrence
  count must sum `count` instead.

### Archive transforms keep every row and field (wip-archive 0.31.0, WIP-Toolkit 0.26.1)

- **`migrate`** of a document with several active versions (legal after a
  merge with `older_versions: keep`) wrote every migrated row at the same
  version, an archive no restore accepts. Each now migrates into its own
  successor, numbered on from the document's highest version, as the live
  migration does. `documents_migrated` counts documents, not rows.
- **`migrate` `format` fills** resolve dotted paths (`{legacy.code}`) like
  `copy` does, with format specs and conversions; a pattern with an empty
  field, a field nested in a spec, a malformed brace or a spec that does not
  fit the value is refused before anything is written, instead of crashing.
- **`export-csv`**: a data field named `document_id`, `version`, `status`, or
  ending in `__term_id`, was overwritten by the export's own column and the
  header held the name twice. Such a field is now written as `data:<name>`;
  a column taken twice even so refuses the export (nothing written).
- **`split --by island`**: a file no document references was in no part, so
  its row, Registry entry and bytes were lost. It goes into `unattached`.

### The Registry's namespace export/import is gone; use backup/restore

`POST /api/registry/namespaces/{prefix}/export`,
`GET /api/registry/namespaces/exports/{export_id}` and
`POST /api/registry/namespaces/import` are removed, with `@wip/client`'s
`exportNamespace`, `downloadExport` and `importNamespace` (`@wip/client`
0.89.0; the `ExportResponse` and `ImportResponse` types go too). The import
posted one object per request to endpoints that take a list, so it restored
only the namespace record and still answered 200 with zero errors; the export
broke off silently at the first failed page. Back up and restore a namespace
with the document-store engine: `wip-toolkit backup` / `restore`, the MCP
`start_backup` / `start_restore` tools, or
`POST /api/document-store/backup/namespaces/{namespace}/backup` and
`…/restore`. `POST /api/registry/entries/export` (used by that engine) stays.

### Namespaces have an id of their own; archives without it are refused

A namespace was known only by its name, so a grant made for one namespace and
a later namespace that happened to have the same name could not be told
apart. Every namespace now has a `namespace_id` (UUID7) minted by the
Registry.

- **Lifecycle.** A reservation holds an id (the namespace's own while it
  exists); the namespace created under it takes it. A plain create mints one.
  Deleting a namespace ends its id, and a reservation that outlives it gets a
  new one. An existing namespace's id never changes.
- **Restores.** A platform admin's ID-preserving restore re-creates the
  namespace under its archived id, via
  `namespace_id` on `PUT /api/registry/namespaces/{prefix}`: platform admins
  only (403 otherwise), honoured only when the call creates the namespace.
  An id a live namespace already holds makes a copy with a new id (a
  warning, also on the response's new `warnings`). Onto a name reserved here
  for another id the create is refused with 409. A fresh restore is a copy
  and carries no id.
- **Orphaned grants** carry the id they were made for, and are activated when
  a namespace with that id is created, **by whoever creates it**, a
  reservation's holder included. A plain create no longer activates orphans
  by name. A system restore writes a grant orphaned when the namespace (or
  reservation) holding its name here has another id: an unrelated namespace
  of the same name no longer receives the archive's grants.
- **Hard cut-over: archives without namespace ids are refused.** That covers
  system archives (grants and reservations), namespace archives
  (`namespace_config.namespace_id`, wip-archive 0.30.0). To bring an old
  archive forward, restore it on a WIP
  from before this change, start this version, and back up again.
- **Startup migration**, once: every namespace and reservation without an id
  gets one; orphaned grants without one are deleted and logged, since nothing
  says which namespace they were made for. A count on six installs found
  none.
- `NamespaceResponse.namespace_id`; `@wip/client` 0.88.0 (`Namespace.namespace_id`,
  `warnings`); WIP-Toolkit 0.26.0 (its exporter writes the id).

### A deleted namespace clears every service's caches at once (where NATS runs)

Services cache Registry answers for up to 30 seconds: resolutions of names to
ids, and grants. The cache is keyed by namespace name, so after a namespace was
deleted and re-created under the same name, reads by name could resolve to the
deleted namespace's ids for up to 30 seconds and answer "not found" for an
entity that exists (reproduced live: 404 until 30 s, then 200).

- **Now:** when a namespace deletion runs, the Registry publishes
  `wip.cache.namespace.deleted` on NATS. def-store, template-store and
  document-store then clear their resolution and grant caches, and drop the
  namespace from their reference-policy and template caches. Every deletion
  run announces, whether it finished, failed or is waiting on a service.
- **Without NATS** nothing changes: no message, and the caches expire after
  their 30 s as before. A lost message (NATS briefly down) has the same
  fallback.
- **Deployer:** the Registry gets its own NATS user, allowed to publish only
  that subject (plain publish, stored by no stream); the three services may
  subscribe to it. The Registry image now includes `nats-py`, so it must be
  rebuilt.
- **Single entities too:** when an entry is hard- or soft-deleted, its
  synonyms are released (its entity retired), a synonym is removed, or it is
  merged into another entry (`merge_entries`, `merge_terms`), the Registry
  publishes `wip.cache.entries.changed` with the entry ids. Every service
  drops the resolutions it cached to them, under whatever spelling, so a name
  re-created for another entity, or moved to one, resolves to it at once
  instead of to the old entity for up to 30 s.

### A plain restore refuses a `target_namespace` it cannot honour

The upload restore (`POST /backup/namespaces/{namespace}/restore`,
`mode=restore`) took its destination from the archive and silently dropped a
`target_namespace` that differed, so a request naming `kb-copy` wrote into the
archive's own `kb` when that was absent or empty. It now answers 400 before
any job exists, naming the archive's namespace and the two modes that do move
data (`merge` into an existing namespace, `fresh` for a copy with new ids). The
archive's own name is still accepted, and omitting the field behaves as
before. A multi-namespace archive with a `target_namespace` is refused in
restore mode too, as it already was for merge. The endpoint's docstring and
the MCP and user docs now say that the URL namespace is the permission
anchor, not the destination.

### A prefixed ID sequence never mints an ID that already exists

`prefixed` IDs were counted per (namespace, entity type, prefix), and the
counter moved only by its own increments. After an ID-preserving restore the
next new ID was the first restored one, and every new entity of that type
failed (`E11000 … entry_id_unique_idx`) until the counter had passed them all.
A prefix that moved to another namespace or entity type restarted at 1, and a
caller-supplied ID in a prefix's range was minted again later. Now:

- **One counter per prefix, install-wide** (`@prefix:<PFX>` in `id_counters`).
  A prefix that moves continues its sequence, and namespaces still sharing a
  prefix from before prefixes became install-unique draw from one counter.
  Deleting a namespace leaves the counter: a restore of its IDs must not
  restart it.
- **IDs written with their number set raise the counter:** `/entries/register`
  and `/entries/reserve` raise it to the highest such ID before minting
  anything.
- **The first mint from a prefix starts above every ID the prefix has**, from
  entries and from the older per-namespace counters, which also repairs a
  counter that fell behind earlier. No migration step is needed.
- **A `prefixed` config without a prefix** (only possible in configs stored
  before writes refused it) now refuses to mint: a per-item error on
  register, 422 on provision, instead of bare numbers.
- **A failed batch insert no longer returns the database error.** A taken
  entry_id is `already_exists` (`details.rule` `entry_id_taken`, naming the
  ID); anything else is `internal_error` pointing to the Registry log.
- `/entries/register` still does not validate a caller's ID against the
  namespace's current `id_config`: a restore writes back IDs minted under
  earlier configs. `/entries/reserve` validates, as before.

### A composite key is held only in the namespace it names; `/resolve` and `/lookup/by-id` refuse instead of picking

The Registry claims a key once per namespace and entity type, but the keys the
platform builds name their namespace (`{"ns": ..., "type": "template",
"value": ...}`). A writer of one namespace could add another namespace's
template key as a synonym on its own template, and `/entries/resolve`, which
hashes the key without a namespace, then answered with whichever entry came
first. A reference written in the victim's namespace (a document's
`template_id: "PATIENT"`) could then bind to the writer's template. Now:

- **A key's `ns` must be its entry's namespace.** `/synonyms/add`, activation
  synonyms, `/entries/register`, `/entries/reserve` and `/entries/activate`
  refuse the item (`invalid_request`, `details.rule` `synonym_scope_mismatch`
  for a synonym, `key_scope_mismatch` for a primary key); `/entries/provision`
  answers 422. Keys with an `@`-qualified member (a document's identity
  synonym, written only by system callers) are exempt. A count on six installs
  found no stored key that breaks the rule.
- **`/entries/resolve`** looks a key up in `namespace` when given, else in the
  namespace the key names, else in every namespace the caller can read. Entries
  the caller cannot read never take part. When more than one readable entry
  holds the key, the item is `status: "ambiguous"` with `candidates` (up to 10)
  and the bulk response counts it under the new `ambiguous` field.
- **`/entries/lookup/by-id`**: a value that is no entry ID and that several
  readable entries carry is `ambiguous` with `candidates`, no longer one of
  them. An unreadable match no longer hides a readable one.
- **wip-auth** raises `AmbiguousEntityError` (the error an ambiguous value
  lookup already raised) for an ambiguous key, and does not fall back to the
  value lookup for it; a non-strict batch leaves it unresolved.
- **`@wip/client` 0.87.0**: `RegistryLookupResponse` gains `candidates` and
  `error_code`; the new `RegistryLookupCandidate` type. The MCP `lookup_entry`
  tool returns the same result.

### A merge retires target terms only under `carry_retirements`; `@wip/client` 0.86.0, WIP-Toolkit 0.25.9

A merge used to deprecate every active target term that the archive had
retired, even with every opt-in off. That changed the target's definitions,
which a merge otherwise does only when asked.

- **New default:** a term the archive retired stays active on the target.
  The merge reports it as kept (target wins), and the plan says how many and
  names the flag. The job result carries the count
  (`result.namespaces.<ns>.retirements_not_carried`). Dry runs report the
  same, since the decision is made in the plan.
- **To keep the old behavior,** pass `carry_retirements=true`. The term is
  retired with the archive's reason and replacement, as before. A term the
  target retired still stays retired either way.
- **Action:** if a merge script relies on deprecations travelling with the
  archive (a newer backup merged into an older state of the same
  namespace), add the flag.
- **Surfaces:** both restore routes (`carry_retirements` form field and
  request field; it is merge-only, like the other definition opt-ins), the
  MCP `start_restore` tool, `@wip/client` 0.86.0
  (`startRestore(…, { carry_retirements })`), and
  `wip-toolkit restore --mode merge --carry-retirements` (0.25.9).

### Restores upload file bytes before the records that need them

Plain, merge and fresh restores wrote file records first and uploaded the
bytes at the very end, so a failure in between left file records whose bytes
were never uploaded. The bytes now go up first (a merge's only for the files it
inserts, a fresh restore's under their new ids). A failure afterwards leaves at
most unreferenced bytes, which a retry overwrites. The blob step's progress
messages no longer carry a percentage, since the step now runs early in the job.

### A fresh restore folding several namespaces into one refuses key collisions before writing

When several archived namespaces are restored into one target
(`namespace_map` with a shared target), two of them may claim the same
Registry key, and the restore refuses rather than merge them. That check ran
per entity type as planning reached it, after the target was created and the
earlier types reserved in the Registry, so a collision found at documents left
a half-provisioned target that a retry then refused as not empty. It now runs
for every type before any target is created.

### A restore's dry run makes the same namespace check as the real run; `PUT /namespaces/{prefix}?dry_run=true`

A restore creates or updates its target namespace as the requester, and the
Registry decides who may (a reservation's holder with its settings, a platform
admin, nobody else). The dry run skipped that step, so it passed requests the
real restore then refused, after which a multi-namespace restore could already
have written its first namespace. Plain and fresh dry runs now ask the
Registry the same question with the upsert's new `dry_run` flag, which makes
every check the real call makes (permission, reservation settings, deletion
and inbound guards, id prefix rule and availability) and writes nothing.

### A fresh restore whose activation fails says what is live

A fresh restore makes its restored identities resolvable in one activation
step at the end, sent in batches of 500, target by target. A failure partway
left the earlier batches (and targets) live, but the error said the namespace
was "invisible" and suggested a "re-run activation" that no surface offers. The
error now gives, per target, how many identities are active, failed and not
activated, and the failed job's `result` carries the same counts
(`result_kind: activation_failed`). The recovery is unchanged: delete the target
namespaces and retry.

### A multi-namespace merge checks every namespace before writing any

A merge of an archive with several namespaces planned, checked and wrote them
one at a time. A refusal in a later namespace came after the earlier ones were
already merged into live data. A reference from one archived namespace into
another resolved in the real run (the earlier namespace was written by then)
but not in the dry run, which therefore refused what the real run accepted;
and a reference into a *later* namespace was refused even by the real run.

Now every namespace is planned and checked, against everything the whole
merge brings, before anything is written. A refusal leaves every target
untouched, the dry run refuses exactly what the real run refuses, and
references between the archive's namespaces resolve in either direction.

### A restore re-points term and terminology references and successor terms; an edge type keeps an endpoint in another namespace; `wip-archive` 0.29.0

- **Term-reference identity.** A fresh, plain-referent or merge restore
  rewrote `term_references[].term_id` but left `references[].resolved.term_id`
  and `.terminology_id` on the source's ids. The identity hash reads the
  `references` copy, so a restored document whose identity field is a
  term or terminology reference kept the source's hash. The next write of
  the same data then made a second document. Both ids now follow the id
  maps, as do terminology ids written as lookup or data strings.
- **Successor terms.** A restored term's `replaced_by_term_id` follows the
  term map, as its `parent_term_id` already does.
- **Edge-type endpoints outside the archive.** A fresh restore looked such
  an endpoint up as a whole string, inside the target namespace only, so a
  valid endpoint in another namespace (`other:ROLE`) was cleared with a
  warning. It now resolves through the shared resolver, which reads the
  namespace a qualified value names.

**Already-restored data.** Affected documents can be found exactly: for the
same field path, the term id in `term_references` differs from the one in
`references[].resolved`. Count them on each install before deciding on a
repair. Restored terms whose successor names a source term have no such
fingerprint; restore those namespaces again from their archives.

### `redact` clears fields inside arrays, checks every path segment, refuses a path that clears nothing; `wip-archive` 0.28.0

`redact` walked a dot path only through objects. `contacts.email`, where
`contacts` is a list, cleared nothing and kept the element snapshots
(`contacts[0].email`), while the archive's `derived_from` hop recorded the
path as redacted. Only a path's first segment was checked against the
template, and validation warnings quoting a redacted value were kept.

- A dot path names its field in every element of every array on the way;
  an array leaf (`tags`) is cleared whole. Snapshots under every element path
  go with it.
- A bracketed index (`contacts[0].email`) is refused.
- Every segment is checked: a nested one against the object's or array
  items' template, which must be in the archive; nothing may follow a field
  of another type. An object field without a template declares no keys, so
  paths into it are not checked there.
- A path that clears no value in any row is refused, naming it.
- A row the redaction changes drops its `metadata.warnings`.

**Archives redacted before this** with a path into an array still hold those
values, and their provenance says otherwise. Redact them again from their
source archives.

### A merge compares definitions as their models read them; a refusal lists every cause

- **Absent fields count as their defaults.** A merge restore compared the
  two sides' definitions as raw rows, so a field one side's row lacked
  (written before the field existed) differed from the other side's default.
  Merging an older install's archive into a newly bootstrapped one was
  refused for every such field (seen: five terminologies refused for
  `unique_names` absent vs `false`). Both sides are now read with absent
  fields at the model's default, nested models included. A real difference,
  such as `unique_names` `true` on one side and `false` on the other, still
  refuses.
- **A refused merge records every cause.** The error named five
  incompatibilities and the job recorded none. The failed job's `result` now
  holds all of them (`result_kind: merge_refused`), each with the target's
  and the archive's values; the message says where to look.
- **Template versions that do not line up:** templates are still matched by
  `(value, version)` by default, so an archive whose template history runs
  past the target's versions (a fresh bootstrap holds v1) is refused. The new
  `align_templates` option handles that case; see the next section.

### `align_templates`: merging an archive from another install; `@wip/client` 0.85.0, WIP-Toolkit 0.25.8

A merge option, off by default, for an archive whose template version numbers
do not line up with the target's.

- **Templates match by value.** Each archive version maps onto the target
  version with the same schema, or else onto the target's latest active
  version if its documents fit it: only added optional fields, and removed
  fields the arriving documents leave empty. A type change, a newly required
  or modified field, or a different identity, rules, usage, `versioned` or
  edge endpoints do not fit.
- **Documents are re-pinned** to the target version their version mapped
  onto. A document with any version row that maps nowhere is not merged and
  is listed on the job (`result.namespaces.<ns>.template_alignment
  .unplaceable_documents`); reshape those with `wip-archive migrate`,
  re-export and merge again.
- **Archive versions no document pins** (the source's retired history) are
  reported as history, never compared or added.
- **Refused** for a template both sides share (a merge from the same install,
  where versions already line up), and for a template whose identity fields,
  usage or `versioned` differ (a different template, not a version of it).
- **Surfaces:** both restore routes (`align_templates` form field and request
  field), the MCP `start_restore` tool, `@wip/client` 0.85.0
  (`startRestore(…, { align_templates })`), and
  `wip-toolkit restore --mode merge --align-templates` (0.25.8).
  `docs/design/merge-template-version-alignment.md` has the design and
  Peter's decisions.

### Dex's signing keys are trusted for at most an hour without a re-read

Each service's copy of Dex's signing keys (JWKS) used to be kept with no
limit while Dex was unreachable, so a key Dex had retired kept validating
tokens for as long as the outage lasted.

- **Refresh and limit:** keys are re-read every 5 minutes. A copy that could
  not be re-read for **one hour** is no longer used: bearer tokens are refused
  with 503 until Dex answers again.
- **Attempt limit:** fetches are at most one every 10 seconds, whether
  prompted by a failure, the schedule, or a token naming an unknown key id.
  Before, an unknown `kid`, which is read before any signature is checked,
  made every request fetch, and every request retried while Dex was down.
- **Bad responses:** a response that is not a JWKS document (not JSON, the
  wrong shape, no keys) is a failed fetch. It answers 503 instead of 500,
  and it no longer wipes the last good copy.

### Merges from another install compare references by what they name; `wip-archive` 0.27.0

- **The definitions check** compared a definition's references as raw ids: a
  template's `terminology_ref`, `template_ref`, `extends` and endpoint lists, a
  term's `parent_term_id`. Across installs those ids always differ, so a merge
  of identical definitions was refused as "different content" for any
  template that referenced something. References are now compared after
  mapping. That covers mappings the check discovers itself (same-type
  references included) and out-of-archive referents, such as a terminology in
  `wip`, through the archive's referent table.
- **Mappings for documents and files** the target holds under other ids now
  reach every incoming document before it is planned. Before, such a merge was
  refused ("reference … entities that neither the target holds nor this merge
  supplies") or a document whose identity hashes such a reference was inserted
  twice. Files are planned before documents, and documents until no new
  mapping appears.
- **`wip-archive` 0.27.0:** `IDRemapper.remap_term` maps `parent_term_id`. No
  restore path did before, so a term added by a merge or a fresh restore
  could point at a parent the target does not have.

### Every app is served at the root of its own origin; path mode is gone; `@wip/client` 0.84.0

**One cut-over, no compatibility mode.** Every app, deployed or attached, is
served at `https://<label>.<hostname>[:port]/`. There is no
`network.app_origins` setting, nothing is served under `/apps/` on WIP's
origin, and old `/apps/<name>/` URLs answer 404, with no redirect.

- **App manifests:** `app_metadata.route_prefix` (`/apps/rc`) is replaced by
  `app_metadata.label` (`rc`). Routes and the healthcheck are paths on the
  app's origin, declared from `/`. A manifest that declares a route or
  healthcheck under `/apps/`, sets `APP_BASE_PATH` or `VITE_BASE_PATH`, or
  routes a path covering the gateway's `/auth/handoff`, `/auth/renew`,
  `/auth/renewed`, `/auth/logout` or `/auth/userinfo` is refused. Every
  manifest in `apps/` is converted.
- **App images must be rebuilt** to serve at `/` (Vite's default base, the
  server's routes at the root). An image built for `/apps/<name>/` breaks on
  any install redeployed after this change. The manifests in `apps/` still
  pin the old tags until each app ships a new image; CASE-1115 tracks each
  app. Until then, run an app that hasn't moved on an install from an image
  set older than this change.
- **Hostnames:** an install that serves apps (or runs the app edge) needs a
  dotted hostname; a dotless one (`localhost`) or an IP address is refused.
  The dev target's default hostname is now `wip.localhost`.
- **Saved installs:** the deployer-state format is version 3. Loading a
  version 2 state drops `network.app_origins`; an install saved with `path`
  moves to app origins on its next apply. `wip-deploy install` and
  `redeploy` no longer take `--app-origins`.
- **Dex:** an app with its own OIDC client gets redirect URIs on its own
  origin (`https://<label>.<hostname>/<path>`).
- **The auth gateway:** `WIP_APP_ORIGINS` is gone. A handoff without a usable
  return path lands at the app origin's `/`, and a login without one at
  WIP's bare host (`DEFAULT_REDIRECT` defaults to `/`).
- **Attached apps:** served at the root of their origin, every path as the
  browser sent it. The attachment fields `strip_prefix`,
  `preserve_prefix_subpaths` and `redirect_bare_path`, and `path` in
  attachment responses and bulk results, are removed. Writes that send them
  answer 422. The Registry drops them from stored records once at startup. A
  system archive whose attachments carry them is refused: restore it on an
  install from before this change and back that install up again. An
  upstream that needed the prefix has to change. `@wip/client` 0.84.0 drops
  the fields from its types.
- **The app edge** names the app's own origin in `X-Forwarded-Host` toward
  the app.
- **k8s:** app sites behind the Ingress no longer carry a `tls` block (plain
  HTTP there). That was a latent bug with `tls internal` on k8s.
- **The scaffold (`--preset query`)** builds and mounts at `/`; its CI no
  longer passes `VITE_BASE_PATH`.
### Event consumers survive a cancelled run; retried events stay deduplicated for an hour

Four fixes in wip-auth's event bus, which every service with NATS uses:

- **A consumer run that ended cancelled** (cancelled from somewhere other than
  its supervisor) ended the supervisor for good. The reporting-sync or ingest
  worker then stayed down until the service restarted. It is now counted as
  a failure and restarted with backoff, like a run that raises.
- **Each stream's duplicate window** (how long JetStream remembers a
  `Nats-Msg-Id`) was the server default of 2 minutes, but queued events may
  wait up to an hour before a retry. Services now raise it to the queue's max
  age (default one hour), capped at the stream's own max age. An existing
  window is only ever raised. `WIP_EVENTS` converges on the first service
  start after the upgrade.
- **A publish that failed because the connection dropped** no longer counts
  toward the rejection limit, so it can't push an event over that limit and
  get it dropped as `rejected`.
- **`stop()`** awaits every task. Before, each finished task leaving the list
  made it skip the next.

### A Registry that does not answer is 503, never "not found" or "no access"

Several paths turned "the Registry did not answer" (unreachable, timed out, an
error status) into "the Registry said no". They now all answer **503** with
`error_code: "registry_unavailable"` and cache nothing:

- **Resolution** (`resolve_or_404`, the by-value fallback, the terminology
  lookup) answered 404 "Could not resolve". A value-form terminology ref in a
  document reported "Terminology not found".
- **Bulk writes** (`resolve_bulk_ids`, e.g. def-store term relations) passed
  every item on unresolved. The request is now refused.
- **Permission checks** treated any 4xx from the Registry, such as a 401 for a
  stale service key after a key rotation, as a real "none", cached it for 30 s,
  and answered 404 "Namespace not found" (or 403 / empty lists). Only a
  well-formed 200 is an answer now.
- **Hard delete** was refused with "deletion_mode 'retain'" when the
  namespace lookup failed.
- **Document writes** read a failed candidate lookup for a bare reference as
  "no such document". The item is now refused with `registry_error`
  (`details.rule: registry_unavailable`), the per-item code document writes
  already report for an outage, on the single-item and bulk paths alike.
- **Template writes** that resolve `extends` or field references during an
  outage are refused as a whole request (503), as a Registry refusal there
  already was (502).
- **The edge-type endpoint heal pass** counted every entry as unresolvable.
  It now fails.

An identifier that names several entities is now **409** at a route, with
`detail.candidates` and `detail.total`, instead of 404. Inside a bulk write the
per-item `ambiguous_reference` is unchanged.

**Who is affected:** callers that read a 404 during a Registry problem as
"absent". Retry a 503. Code built on wip-auth gets `RegistryUnavailableError`,
which is deliberately neither an `EntityNotFoundError` nor a `RegistryError`;
let it propagate to the handler `setup_auth` registers. Document-store and
template-store no longer define their own class of that name; they use this one.

### Nested identity paths follow the resolved-referent rule; `wip-archive` 0.26.0

A term- or reference-typed identity field feeds the identity hash the
canonical ID it resolves to. A dotted identity path into an object field
(`address.country`, where `country` is a term of the nested template) fed the
submitted spelling instead, so two aliases of one term made two documents. It
now feeds the resolved ID like a top-level field, and is fail-closed like one.

- **Templates** carry a new server-owned field, `referent_identity_paths`: the
  identity paths whose value is a term or a reference, worked out by the
  template store when a non-draft version is written (activation, for
  drafts). It is filled in at template-store startup for versions stored
  before. A write that sends it is refused (422).
- **Callers that compute identity hashes** pass it as `referent_paths` to
  `resolved_identity_values` / `referent_identity_fields` (wip-auth and
  `wip-archive` 0.26.0). Without it, a dotted path into a nested template is
  refused rather than guessed.
- **No existing documents change.** Before this change no template on dev-test,
  prod-test, kb, wip-k3s, wip-kubi or the local default install had a dotted
  identity path, so no hash moves and no re-hash is needed. An install with
  such a template should re-hash its documents with
  `POST /api/document-store/maintenance/rehash-identities` (admin on the
  namespace; dry run first) before writing under a second spelling.

### A read-only key can no longer migrate documents by omitting the namespace

`POST /api/document-store/documents/migrate` needs write on the cohort's
namespace. With `namespace` omitted, a single-namespace key used to pass that
check if it could merely read the namespace, and the migrate wrote a new
version of every document on `from_version`. It now gets 403, as it does when
it names the namespace. The shared namespace filter behind it
(`resolve_namespace_filter` in wip-auth) asked for write or admin without a
namespace now keeps only the namespaces that meet the requirement, and
answers 503 when the Registry cannot say. Keys with write are unaffected.

### A canonical id resolves only as its own entity type

Resolution sent a UUID to the Registry as a bare id, and the Registry checked
an id by existence and status only, so any active entry's id resolved as
whatever type was asked for: a template's id was accepted as a terminology.
wip-auth now sends the entity type with every resolution, and the Registry
applies it to canonical ids as well as to keys: an id of another type is not
found. For a non-UUID value, which is tried as an id and then as a key,
another type's id no longer wins; the value's own key answers. A Registry
from before this change still resolves the new client's requests (without
the id check), so the two can roll in either order.

### An unknown API key no longer stalls a service

A key value a service has not seen is checked with bcrypt against every key
it holds (about 170 ms each, and every service holds every runtime key).
That ran on the event loop, so one request with a random `X-API-Key` stalled
the whole service for N × 170 ms, and a stream of them stalled it outright.
The check now runs in a worker thread, at most two at a time per service;
values the caches already know (verified before, or recently rejected) are
answered inline as before. A flood of unknown keys now only delays other
first-time key checks. The per-address rate limit (`WIP_RATE_LIMIT`) is
still not enforced: no service installs slowapi's middleware.

### The app edge is off by default; the help names every optional module

The app edge (module `app-edge`, which serves apps attached through the
Registry's API) was in the `standard`, `full` and `analytics` presets, so
every new install ran it, plus, with app origins, a catch-all site for
unknown `<label>.<hostname>`, whether anyone attached an app or not.

- **No preset includes `app-edge`.** Turn it on deliberately: `--add
  app-edge` at install, or `wip-deploy add-module app-edge` later.
- **A new install without the edge** refuses attachment writes (`invalid_state`,
  `details.rule: no_app_edge`), and with app origins wip-caddy answers an
  unknown `<label>.<hostname>` with 404.
- **Existing installs are unchanged:** their saved spec lists their modules,
  so a redeploy keeps the edge where it runs.
- **Help:** `--add`, `--remove`, `add-module` and `remove-module` list the
  optional modules, one line each, read from their manifests. `--preset` says
  which modules each preset turns on, and `wip-deploy examples` shows how to
  serve attached apps. The note "preset … includes the app edge" is gone with
  the default it explained.

### App origins need a dotted hostname; `wip_session` is `Secure`; `@wip/react` 0.26.1

- **`localhost` (and any hostname without a dot, or an IP address) can't
  carry app origins.** `aa.localhost` and `localhost` are different sites, so
  the browser didn't send WIP's `SameSite=Lax` login cookie from the app's
  renewal frame: every renewal read as a logout and the app reloaded every
  five minutes. The deployer now refuses `subdomain` for such a hostname
  (`install --app-origins subdomain`, `redeploy --app-origins subdomain`),
  and a new install with one defaults to `path`. An install already saved
  that way still loads and warns on every verb: switch back with
  `wip-deploy redeploy --name <install> --app-origins path`, or re-install
  with a dotted hostname. For app origins locally use `--hostname
  wip.localhost`.
- **`wip_session` is `Secure`** whenever WIP's public URL is https (every
  install the deployer renders). Before, browsers stored it without the
  flag.
- **`@wip/react` 0.26.1:** if the first renewal after the default reload
  fails too, it no longer reloads in a loop. It stops and warns once that
  WIP's origin and the app's are probably different sites. A successful
  renewal re-arms it.

### Merge restores: reference counts, dates, partial failures, identical copies, the reporting gate

Five fixes to what a merge restore writes:

- **File reference counts follow the merge.** After the documents, every
  file's `reference_count` in the namespace is recounted from the latest
  version of each active document, as document writes keep it. Before, a
  file only merged-in documents used kept a count of 0 and could be deleted
  without `force`.
- **An overwrite in place stores dates as dates.** On a `versioned: false`
  template the replaced row kept the archive's timestamps as strings, which
  timestamp filters then skipped.
- **A batch that fails partway never leaves two active versions.** The
  older versions of the overwriting rows that did land are retired, and the
  error says how many landed.
- **An identical archive copy adds no version.** Under `overwrite` and
  `newer`, a clash whose data, resolved references and `metadata.custom`
  equal the target's is left alone and reported.
- **The reporting-table check runs before documents move,** as it does in a
  plain restore. A merge plans templates separately, so it never ran.
- **A template version a merge adds keeps the target's `template_id`.**
  Across installs one template carries different ids. With `add_missing`, a
  version new to the target was inserted under the archive's id while its
  earlier version matched under the target's, so the template had two ids
  and the merged documents pinned a version that did not exist under it.
### A backup or restore job needs its permission on every namespace it spans

A job may cover several namespaces, and creating it required admin on each.
Seeing it, following its events, listing it and downloading a backup's
archive checked only **read on its first namespace**, and deleting it only
admin there. So a read key on A could download a backup of `[A, B, C]` with
B's and C's data, and an admin of A alone could delete a backup of `[A, B]`.

- **Read on every namespace** is now needed to see, list, follow, or download
  a backup job. A restore job's input still needs admin on every namespace.
- **Admin on every namespace** is now needed to delete a job.
- Single-namespace jobs behave exactly as before.

### A fresh restore's files keep their own bytes; damaged rows are repaired at startup

A fresh restore re-mints every file id and copies the bytes under the new id,
but the file row kept the **source's** `storage_key`. On the same install a
restored file served the source's bytes, and deleting it deleted the
source's object. On another install it served nothing. A merge that
re-minted files did the same.

- **Every file row a restore writes** now has `storage_key = file_id`, on
  every restore mode.
- **At startup**, the document-store re-points rows written before the fix
  (`storage_key` ≠ `file_id`) at the object under their own id, when that
  object exists. A row without one is left as it is and named in the log. The
  repair runs once storage is reachable, and finds nothing on an install that
  never ran a fresh restore with files.

### Backups and restores check every file's bytes against its checksum; `wip-archive` 0.25.0

A file download that failed partway through a backup left its partial
bytes in the archive, and the backup completed. A restore uploaded whatever
bytes the archive held. Missing or unreadable blobs showed up only in the
server log.

- **Backup:** each file's bytes are hashed as they stream and checked against
  the file's SHA-256 `checksum`. A failed or mismatching download is dropped
  from the archive and **named as a job warning**. The backup still
  completes, so one lost object never blocks every backup.
- **Restore** (plain, merge, fresh): only bytes matching the file's checksum
  are uploaded. Every file restored without its bytes (missing from a backup
  made with files, unreadable, or mismatching) is named as a job warning.
- **`wip-archive` 0.25.0:** `ArchiveWriter.discard_blob(file_id)` drops a blob
  written earlier.

### Apps on origins of their own: `network.app_origins`, the new default; `@wip/react` 0.26.0

Served under `/apps/<name>` on WIP's origin, every app received the visitor's
session cookie, and an app's JavaScript could call any other app as the
visitor. With `network.app_origins: subdomain` each app is served at
`https://<label>.<hostname>[:port]/apps/<label>/` with a session of its own:

- **The gateway hands each app origin its own session.** `/auth/app-login` on
  WIP's origin mints a one-time code (30 s, single use, bound to the app);
  `/auth/handoff` on the app origin turns it into `wip_app_session`
  (host-only, HttpOnly, naming the app, 15 minutes by default,
  `APP_SESSION_MAX_AGE`). `/auth/verify` on an app origin accepts only that
  app's session, so an app's server cannot replay a visitor's session at
  another app. Logging in once still covers every app; Dex is unchanged.
- **wip-caddy** serves each deployer app on its own site, attached apps on a
  catch-all site handed to the app edge (certificates on demand, only for
  names the edge confirms), and answers `/apps/<label>/…` on WIP's origin
  with 301 to the app's origin.
- **`@wip/react` 0.26.0:** `WipProvider` renews the app session in the
  background (`useAppSessionRenewal`), so a long-open app's `fetch` calls keep
  working; a page reload signs in again when the WIP login has ended.
- **k8s:** wip-caddy behind the Ingress answers each origin on plain HTTP;
  the Ingress gets a `*.<hostname>` rule and TLS host, and a self-signed
  certificate the deployer mints names `*.<hostname>` too. An existing
  self-signed `wip-tls` without it is refused with the remedy (delete it and
  apply again); with `tls external`, the operator's certificate must cover
  `*.<hostname>`.
- **Defaults:** new installs use `subdomain` on every target. An existing
  install keeps its mode: switch with `wip-deploy redeploy --name <install>
  --app-origins subdomain`.
- **Action required for `subdomain`:**
  - `*.<hostname>` must resolve to the install (nothing to do for
    `localhost`);
  - apps must keep every browser request under their base path (the app
    contract's rule) and should move to `@wip/react` 0.26.0;
  - non-browser clients of app URLs (a CLI posting to an app's `server-api`)
    must use the app's origin: a `POST` does not follow the 301.
- `tls letsencrypt` with attached apps (on-demand certificates from a public
  CA) has not been tried yet.

### Registry: three fixes from the registry-auth review; `@wip/client` 0.83.0

- **A key-file change takes effect at the Registry without a restart.** An
  import that a reservation lookup ran (an app's namespace upsert, `GET
  /reservations`, a key create with `reserve`) updated MongoDB but not the
  Registry's own key provider, and later imports found nothing to change.
  Until a restart, a removed managed key (a `wip-admins` one included) kept
  authenticating at the Registry, a rotated key's old value worked and its
  new one did not, and a new key was refused there. Other services were not
  affected. Now every import uses the Registry's provider, and every import
  makes the provider agree with MongoDB's managed records, which also covers
  another replica's import.
- **No API key may be named `apikey:<name>`.** That spelling is how a key is
  addressed for callers on an older wip-auth, so a key named `apikey:ci` was
  read as the key `ci`: it held `ci`'s grants and reservations, and grants
  made to it were stored under `ci`. Key create refuses such a name (422);
  a key file declaring one is not imported (the previous import stands, and
  the reason is logged); a system restore skips such an archived key
  (`reason_code: invalid_record`). **A key already stored under such a name
  is named in a CRITICAL log line at Registry start: revoke it and create it
  again under another name.**
- **Attachment upstream hosts follow the app edge's rule.** The Registry
  accepted any host and the edge refused all but letters, digits, dots and
  hyphens, so an upstream like `http://my_app:3000` read `served: true`
  while `/apps/<name>` answered 404. An empty or over-long label
  (`a..b.example`) made the whole bulk write fail with 500 after earlier
  items were written. Now the host must be an IP literal or RFC 1123 DNS
  name, at both ends; a refused one fails its own item (`validation_failed`,
  `details.rule: upstream_invalid`). A record stored before the rule reads
  `served: false`, `not_served_reason: upstream_invalid`: re-attach it with a
  valid host. `@wip/client` 0.83.0 types the new reason.

### k8s: an install acts on the cluster it lives on; older installs must be pinned once

wip-deploy's k8s verbs ran plain `kubectl`, so they acted on whatever
context the shell pointed at. A `redeploy` run without the install's
`KUBECONFIG` once applied a whole install to another cluster. Now:

- **`install` records the cluster** in the saved spec (`platform.k8s.kube`):
  the kubeconfig (`$KUBECONFIG`, else `~/.kube/config`), the context, and
  the cluster's identity, the UID of its `kube-system` namespace. A server
  URL cannot tell clusters apart: local clusters and tunnels all serve on
  127.0.0.1.
- **Every verb runs kubectl with the recorded kubeconfig and context**, and
  first checks that the context still reaches the recorded cluster. If it
  reaches another one, the verb refuses before changing anything and names
  both clusters. A plain `wip-deploy redeploy --name <install>` works
  without setting `KUBECONFIG`.
- **Action required: an install made before this change records no
  cluster.** It refuses `redeploy`, `add-app`, `app-deploy`, `user`, `stop`,
  `start`, `nuke`, `secrets pull` and the other verbs that change the
  cluster until it is pinned once, naming the cluster it lives on:
  `wip-deploy redeploy --name <install> --kube-context <context>
  [--kubeconfig <path>]`. That command also re-pins after a kubeconfig
  moves or the cluster is rebuilt. `status` and `status --diff` keep
  working, with a warning. The first verb never records the context it
  happens to find: in the incident that prompted this, the first verb
  after the upgrade was the one aimed at the wrong cluster.
- `status`, `stop` and `start` given only `--namespace`, with no install on
  this host, use the shell's context and warn.
- **Rollback:** an older deployer refuses a saved spec that carries
  `platform.k8s.kube`. Remove that block from `deployment.deployer-state`
  before running an older deployer.

### `@wip/proxy` 0.7.0: the API key is trimmed, and a malformed one is refused at startup

An inline `apiKey` (typically from an environment variable fed by a
Secret) was sent exactly as given. A trailing newline, which `jq -r … >
file` plus `kubectl create secret --from-file` adds, made Node refuse the
`X-API-Key` header (`ERR_INVALID_CHAR`), so every proxied call returned 500
while the app's own server-side calls, which trimmed the key, worked.

- **Both key sources are trimmed**, the inline `apiKey` now like
  `apiKeyFile` already was. An inline key that is only whitespace counts as
  missing (the file fallback and the usual errors apply).
- **A key with whitespace or a control character inside it is refused when
  the proxy is created**, naming the option, instead of failing on the first
  request. With `onMissingKey: 'unavailable'` it gives the unavailable proxy.
  WIP keys are URL-safe base64 or hex and never contain either.
- **Apps pick this up when they re-vendor** `wip-proxy-0.7.0.tgz`.

### Attachment reads say whether the app is served; `@wip/client` 0.82.0

`GET /api/registry/attachments` and `GET /api/registry/attachments/{name}`
showed `status: active` for a record nothing serves (an install without the
app edge). Each attachment now carries:

- `served`: true when the record is active and an app edge runs;
- `not_served_reason` when false: `inactive`, `no_app_edge`, or
  `policy_unavailable`. An unusable attach policy file reads as not served,
  which is true, since the edge then serves nothing; a read never fails on it.

`served` is the install's configuration, not a reachability check: the Registry
never calls an upstream, and the edge can still refuse a record when it
resolves it. The app edge itself ignores the field. **`@wip/client` 0.82.0**
types both fields (optional, for older Registries) and adds
`AttachmentNotServedReason`.

### The app edge is an optional module; `@wip/client` 0.79.0

The app edge, which serves apps attached through the Registry's API, used to
run on every install with the auth gateway. It is now the optional module
`app-edge`:

- **New installs:** the gateway presets (`standard`, `full`, `analytics`)
  include it. `install --no-auth-gateway` leaves it out with a note; an
  explicit `--add app-edge` without the gateway is refused.
- **Existing installs keep their edge.** The saved spec's format moves to
  version 2. On first load, every version-1 install that ran the edge (the
  gateway on, core not suppressed) gets `app-edge` in its module list, so
  the next apply keeps serving its attached apps. Nothing else changes.
- **Turning it off:** `wip-deploy remove-module app-edge`. Attached apps go
  offline: their records stay, but nothing serves them. The Registry then
  refuses attachment writes (`invalid_state`, `details.rule: no_app_edge`),
  and a system restore skips active attachments
  (`reason_code: no_app_edge`). Reads and deactivation still work.
  `add-module app-edge` brings it back. See wip-guide §6.5.
- **`@wip/client` 0.79.0:** `SystemRestoreSkipReason` gains `no_app_edge`,
  and the attachment error codes document `invalid_state` /
  `no_app_edge`. Types only; no runtime change.
- **Upgrade the Registry and the deployer together before removing the
  edge.** The attach policy file carries `app_edge: false` only when the
  edge is off, so with the edge on, the file stays readable by an older
  Registry. Once you `remove-module app-edge`, an older Registry rejects the
  field it doesn't know and refuses **every** attach. This holds for every
  verb that renders, scoped ones (`add-app`, `app-deploy`, `user add`)
  included: run them against an install whose Registry image matches, or
  pass the image set in the same apply (`redeploy --tag …`).
- **Rollback:** an older deployer refuses a version-2 saved spec ("expects
  1"), so after any apply with this deployer, `redeploy` with an older
  checkout fails on that install. To roll back, set
  `"wip_deploy_format_version": 1` in `~/.wip-deploy/<name>/deployment.deployer-state`
  by hand; the extra `app-edge` entry is then reported as an unknown module
  by the older deployer, so remove it from `modules.optional` as well.

### The app edge never brings expired or deactivated attachments back

During a Registry outage past the expiry deadline, or while the attach
policy was unreadable, the edge loads a config that serves no attached app.
If Caddy refused that load (for example a busy admin API timing out), the
edge's fallback rebuilt the routes of the **last good poll**, so attachments
were served again past expiry, including one deactivated during the outage.
If every load failed, Caddy simply kept its old routes.

- **The fallback now uses only the plan the refused config was built from.**
  An unavailable config has none, so it can never fall back to routes.
- **When Caddy accepts no config at all, the edge process exits.** The
  container restarts it, and the edge always starts Caddy serving no
  attached app.
- **Needs the new app-edge image.**

### Group names that can't travel in `X-WIP-Groups` are dropped at login

The gateway joins a user's groups with `,` into `X-WIP-Groups`, and every
consumer splits on `,` and trims spaces. The reserved-group filter compares
whole names. So a federated provider's group named `staff,wip-admins`, or
` wip-admins` with a leading space, passed the filter and arrived downstream
as `wip-admins`.

- **At login, the gateway drops any group name** that is empty, has leading or
  trailing spaces, contains a comma or a control character, or holds
  characters outside latin-1. It logs a warning, and the user keeps their
  other groups. This applies to every user, local Dex users included.
- **An email the identity header can't carry** (outside latin-1, or with
  control characters) is refused at login with a 403 and a reason. It used
  to make every later request a 500.
- **Needs the new auth-gateway image.**

### Login returns only to this install; query strings survive login; `--tls external` is k8s-only

- **Login can't end on another site.** `/auth/login?return_to=…` used to send
  the browser wherever `return_to` pointed once the login completed. That
  turned any WIP login link into a phishing tool. It now accepts:
  - a same-origin path; or
  - an absolute `http(s)` URL on the install's own host (`WIP_HOSTNAME`, any
    port), which is what `AUTH_REDIRECT_MODE=redirect` builds.

  Anything else lands on the default page. Login and logout also refuse
  paths with control characters: browsers delete tabs and newlines, so
  `/\t/evil.example` would arrive as `//evil.example`.
- **The whole URL comes back after login.** The front door and the app edge
  put the original URI into the login redirect unescaped, so
  `…/search?q=foo&page=3` came back without `&page=3`. They now use Caddy's
  `{http.request.uri_escaped}`. The edge half needs the new app-edge image.
- **`--tls external` is refused on compose and dev.** There wip-caddy is the
  public listener, and the `tls off` it rendered is not a directive Caddy 2
  accepts, so wip-caddy never started with it. Use `internal` or
  `letsencrypt`. On k8s, `external` is unchanged.

### Client addresses survive the front door and the app edge

Every Caddy hop used to replace `X-Forwarded-For` with its own peer's
address. Backends therefore keyed their per-IP rate limit on the front
door's peer: on k8s that is the ingress controller pod, so every user shared
one bucket. Attached apps saw wip-caddy as their only client.

- **k8s:** wip-caddy trusts the cluster's pod ranges (`--cluster-cidr
  pods=…`), where the ingress controller runs, and keeps its
  `X-Forwarded-For`. Without pod ranges nothing changes. Set them on an
  existing install with `wip-deploy redeploy --cluster-cidr pods=…`.
- **Every target:** the app edge trusts private addresses (only wip-caddy
  reaches it) and passes the chain on to attached apps.
- **Compose and dev are unchanged at the front door:** wip-caddy faces the
  client and still trusts no one, so a client cannot choose its address.
- **The controller must see the client too.** A `LoadBalancer` Service with
  `externalTrafficPolicy: Cluster` (the Kubernetes default, which k3s's
  Traefik keeps unless its values set otherwise) rewrites the
  source address, so the controller and everything behind it see a node
  address. Set `externalTrafficPolicy: Local` on the controller's Service
  (for k3s, through a `HelmChartConfig` for `traefik`) to keep real client
  addresses.
- **Needs a new app-edge image** for the edge half; the wip-caddy half is
  configuration only.

### Scoped verbs after the k8s front-door change; cluster ranges on existing installs

- **The first scoped verb on a k8s install last applied before wip-caddy
  became the front door applies the whole install once,** with prune
  (`add-app`, `app-deploy`, `user add` and the like say so). The old
  per-route Ingresses would otherwise stay and shadow the front door.
- **A scoped verb also applies services new to the render** (an upgrade
  brought a new component, or a module was added), on compose and k8s; it
  used to leave them uncreated.
- **`remove-module` removes the container only after the apply succeeded,**
  so a refused or failed apply leaves the module running.
- **`redeploy --cluster-cidr kind=CIDR`** (repeatable) replaces an existing
  k8s install's saved cluster ranges. `--cluster-cidr` on a non-k8s target is
  refused instead of ignored, and an IPv4-mapped IPv6 range is refused (it
  never matched). The reach warning now names each missing kind: pods and
  Services without `nodes=` still leave the control plane reachable.

### A scoped system restore changes nothing outside its scope; `@wip/client` 0.80.0 and 0.81.0

A scoped system backup (`system-backup?namespaces=…&attachments=…`) promises
that its restore leaves everything outside the scope untouched. Four gaps
broke that promise:

- **A scoped mirror could remove keys that reach outside the scope.**
  `keys_left_out` named only keys with a grant or reservation in scope. It
  now also names every key the archive doesn't carry whose namespaces overlap
  the scope, or that has no namespace list. A scoped mirror never removes
  those keys. **Archives made before this change don't carry the wider list:
  take a new scoped backup before mirroring.**
- **A scoped overwrite or mirror could narrow a key.** A key the archive
  carries but which, on this stack, also holds namespaces outside the scope
  (or none) is no longer replaced. It is skipped with
  `reason_code: out_of_scope_here`.
- **An edited scoped archive could write past its scope.** Its keys, grants,
  reservations and attachments outside the declared scope are skipped with
  `reason_code: outside_archive_scope`.
- **An empty scope was a scope.** `?namespaces=` or `?attachments=` with no
  names is refused with 422. `@wip/client` 0.80.0's `systemBackup` sends any
  list it is given, so `{ namespaces: [] }` gets that refusal instead of a
  whole-stack archive. The type `SystemRestoreSkipReason` gains both new
  codes. `@wip/client` 0.81.0 also treats `namespaces: null` (or
  `attachments: null`) as not given, where 0.80.0 threw a `TypeError`.
- **A scoped mirror never removes a key with a grant outside the scope.**
  Such a grant gives no access while the key's namespaces lie inside the
  scope (an ordinary key's access is bounded by its own namespace list), but
  removing the key would delete the grant with it.
- **A key in a stack-wide group is never inside a scope.** A `wip-admins` or
  `wip-services` key (or one in a configured admin group) reaches every
  namespace whatever its namespace list says, yet a key with `namespaces:
  [a]` counted as inside a scope of `a`. A scoped backup carried it, a scoped
  mirror removed it (taking stack-wide admin away), and a scoped overwrite
  replaced it, adding or taking away the group. Now a scoped backup never
  carries such a key and names it in `keys_left_out`; a scoped restore skips
  it (`outside_archive_scope` from the archive, `out_of_scope_here` when this
  stack's copy holds the group) and a scoped mirror never removes it. **A
  scoped archive made before this change may carry such a key: its restore
  now skips it.**
- **A scoped mirror that skips one of this stack's keys never adopts the
  archive's hash salt** (it answers 409 `salt_mismatch`). Before, the skipped
  key kept its hash under a salt that no longer matched it, and stopped
  authenticating.

### k8s: wip-caddy is the front door, behind one Ingress; Traefik works

On k8s every route used to be an ingress-nginx Ingress, carrying the
gateway's `auth-url`, regex prefix-stripping and the root redirect as nginx
annotations. That tied k8s installs to ingress-nginx: on Traefik (k3s's
default) every route would have been served **without the auth gateway**.
It also could not remove client-sent identity headers on unprotected app
routes. Now:

- **One front door on every target.** k8s runs `wip-caddy`, the same public
  Caddy compose runs, generated by the same code, behind **one** Ingress,
  `wip-ingress`, path `/` to `wip-caddy`. Routing, `forward_auth`, the
  identity-header strip and the root redirect are the same on compose and
  k8s.
- **`--ingress-controller nginx|traefik`** (default `nginx`) picks that
  Ingress's few annotations. `--ingress-namespace` names the controller's
  namespace for the NetworkPolicy; the default is `ingress` for nginx and
  `kube-system` for Traefik. A stock ingress-nginx install, which runs in
  `ingress-nginx`, must pass it.
- **On Traefik,** raise the entry point's read timeout and set
  `aliasHeadersStrategy` on the controller itself (`docs/deploy/k8s/README.md`,
  "The ingress controller").
- **k8s apps no longer receive client-sent `X-WIP-User` / `X-WIP-Groups` /
  `X-WIP-Auth-Method` on unprotected routes.** The contract's k8s exception
  is gone. Read identity only under `auth_required: true` routes, as before.
- **NetworkPolicies:** the ingress controller now reaches `wip-caddy` only,
  not every pod. The app edge accepts traffic from `wip-caddy` only.
- **One-app verbs carry the front door.** `add-app`, `app-deploy` and a
  scoped `redeploy` apply `wip-caddy` alongside the named app, since every
  app's route is in its Caddyfile; a changed Caddyfile rolls the pod.
- **Upgrading an existing k8s install:** the redeploy replaces the
  per-route Ingresses (`wip-ingress`, `<app>-ingress`, `wip-root`, …) with
  the single `wip-ingress` and adds the `wip-caddy` Deployment; the apply's
  prune removes the old Ingresses. Pass `--ingress-namespace` first if your
  controller is not in `ingress`; otherwise the policy shuts it out and
  every request fails.

### The auth gateway no longer hands apps the master key

Every app behind the auth gateway received the install's master key as
`X-API-Key` with every logged-in user's request: the gateway's verify answer
carried it, and the proxy copied it onto every protected route. Now:

- **The gateway's answer carries the user's identity only**, `X-WIP-User` and
  `X-WIP-Groups`. The gateway no longer holds any API key (its `API_KEY`
  setting is gone).
- **App routes remove forged identity headers.** On compose and dev, a
  client-sent `X-WIP-User`, `X-WIP-Groups` or `X-WIP-Auth-Method` is removed on
  app routes the gateway does not protect, and `X-WIP-Auth-Method` on the
  protected ones. On protected routes the gateway's answer replaces the other
  two. **On k8s, ingress-nginx cannot remove request headers without a
  snippet:** an app must trust `X-WIP-User` only on routes it declares
  `auth_required: true`.

**Action:** apps call WIP with their own key (`WIP_API_KEY`, which wip-deploy
provides). An app whose server forwards the browser's request headers to WIP
without setting its own key would now get 401. `@wip/proxy` always sets the
app's own key. Checked: WIP-AA, WIP-Cooking, WIP-KB, WIP-RC, pbs-clintrial.
**Not checked:** brmri, dnd, song, wip-val; their owners should confirm before
upgrading. ClinTrial Explorer, which the 2026-10-12 rollout recorded as using
the gateway's injected key, is retired. Takes effect on a redeploy with a rebuilt
auth-gateway image.

### The app edge on k8s may not reach the cluster's own ranges: `--cluster-cidr`

The app edge's egress rule for attached apps outside the cluster (`ipBlock
0.0.0.0/0`, `::/0`) also reached the cluster's control plane and kubelets on
the node addresses and, depending on the network plugin, pod and Service IPs.
- **`--cluster-cidr pods=… services=… nodes=…`** (repeatable,
  `platform.k8s.cluster_ranges`) sets the cluster's own ranges. They are
  excepted from that rule.
- **The node ranges also join the attach policy** (`blocked_cidrs`). The
  Registry refuses an attachment, and the edge an upstream, that resolves
  into them. List the cluster's own node addresses, not a shared LAN.
- **Without ranges, `wip-deploy` warns.** The deployer cannot discover them.
- **Unchanged:** the edge still reaches every other Kubernetes namespace's
  pods. That is documented as part of "attaching is full trust".
- **Rollout:** rebuild the Registry and the app edge with the deployer that
  renders `blocked_cidrs`. An older Registry refuses a policy file carrying
  the new field, and attaching fails closed until it is upgraded. The file
  carries it only when node ranges are set.

### A system backup scoped to one app's namespaces; WIP-Toolkit 0.25.7, `@wip/client` 0.78.0

- `POST /api/registry/system-backup?namespaces=a,b&attachments=x` takes a
  **scoped** backup and declares the scope in `source.scope`. It carries:
  - the grants and reservations of those namespaces;
  - the keys whose namespaces all lie in them;
  - the named attachments. Attachments have no namespace, so only named ones
    are carried.
- `source.scope.keys_left_out` names the keys the scope touches but cannot
  carry: unscoped keys, keys with no namespaces, and keys spanning in and out
  of the scope. The archive still carries their grants inside the scope, and
  the restore warns about each.
- **A mirror of a scoped archive removes only within its scope.** The plan
  digest includes the scope.
- **A foreign hash salt** is adopted only by a stack with no keys of its own,
  as before. A key outside the scope blocks it.
- **Format:** the archive stays version 1. A Registry that predates scopes
  refuses a scoped archive (422) instead of reading it as a whole-stack one.
- **WIP-Toolkit 0.25.7:** `system-backup --namespace … --attachment …` prints
  the scope and the keys left out.
- **`@wip/client` 0.78.0:** `systemBackup({namespaces, attachments})` and
  `SystemArchive.source.scope`.

### Mirror mode for the system restore; WIP-Toolkit 0.25.6

A system restore could only add and replace records, so a key revoked since
the backup survived it. `POST /api/registry/system-restore` now takes
`mode=upsert|overwrite|mirror`; `overwrite=true` still means
`mode=overwrite`.

- **Mirror makes the archive the exact auth state, stack-wide:**
  - it replaces what differs;
  - it removes every key, grant and reservation the archive does not hold;
  - it deactivates attachments, which are never deleted.
- **No new kind of delete.** A key goes as a revoke takes it (with its grants);
  a grant and a reservation as their revokes do.
- **Never removed:**
  - records the install's files manage, and grants to keys whose grants the
    key file declares;
  - the bootstrap grants;
  - keys named like config keys;
  - user grants the archive's OIDC issuer would not restore;
  - the key the request authenticates with. A plan that removes it is listed
    under `refused`, and an apply answers 409 `restore_refused`. In `overwrite`
    and `mirror` mode, the same goes for a plan that would replace that key by
    the archive's different record of the same name.
- **The report** gains `deleted` (keys, grants, reservations), `deactivated`
  (attachments), `refused`, `mode`, and `plan_digest`. An apply passing
  `expect=<digest>` is refused (409) if the plan changed since the dry run.
- A grant the archive holds for a key the mirror removes is not written.
- **Removed keys keep working at the services until their next key sync (up
  to about 30 s).** The Registry drops them at once.
- **Not one transaction:** a mirror that fails part-way has done what its
  report lists, and re-running converges.
- **A mirror adopts an archive's other salt** even on a stack with keys of
  its own, since those are removed.
- **Not yet:** a namespace-scoped mirror. It needs a backup that declares its
  scope.
- **WIP-Toolkit 0.25.6:** `system-restore --mode mirror` and `--expect`. The
  output shows removals, refusals and the plan digest.

### The system backup carries app attachments; WIP-Toolkit 0.25.4

A restore used to leave attached apps off the umbrella until someone attached
them again by hand. The system backup now carries every attachment (still
archive format version 1, defaulting to none, since the format is
unreleased), and a restore puts them back:

- **Restored last,** after keys and grants, by name, with the archived status;
  `overwrite` for one that differs, as for the other records.
- **The attach rules hold:** a name an app of this install routes under, an
  invalid upstream, or one that resolves from the Registry to a blocked target
  is skipped and listed. An upstream that does not resolve yet is written and
  listed in the report's new `attachments.unresolved`. An unusable attach
  policy file skips every attachment.
- **Each entry is checked on its own.** One that fails the attach rules is
  skipped and listed; the backup and the rest of the restore go ahead. An
  entry the source's wip-deploy manages is skipped too: the install that
  deploys the app re-creates it.
- A record wip-deploy manages on the target is never replaced.
- **WIP-Toolkit 0.25.4:** `system-restore` prints the attachments section.

### Each install has its own API key hash salt, kept by the Registry; WIP-Toolkit 0.25.2

Every install hashed API keys under the same public default salt
(`wip_auth_salt`): wip-deploy never set `WIP_AUTH_API_KEY_HASH_SALT`. The
Registry now keeps the salt for runtime keys (Console, imported and restored
keys) in its own database and hands it to every service with key sync.

- **New installs** get a random salt on the Registry's first start.
- **Existing installs keep `wip_auth_salt`.** A Registry that finds keys
  already stored keeps the configured salt, so no existing hash breaks.
- **It never changes afterwards,** except when a system restore lands on a
  fresh stack: one holding no keys of its own, only keys its key file
  manages. That stack adopts the archive's salt and re-hashes its managed
  keys from the key file. Services verify under the new salt from their next
  key sync (within 30 s). A stack with keys of its own refuses another salt
  (409).
- **The archive carries the salt,** not only its fingerprint. The salt is not
  secret from whoever holds the archive. Its value is that stacks no longer
  share one constant. The archive was already sensitive, since it holds every
  key hash.
- **Config keys are unaffected.** Each process still hashes its own config
  keys under its configured `WIP_AUTH_API_KEY_HASH_SALT`.
- **Upgrade the Registry and the services together.** A service older than
  this ignores the sync's salt header. On an existing install that changes
  nothing, because the salt stays `wip_auth_salt`. On an install created
  after the upgrade, an old service would reject every runtime key.
- **Rolling back below this release** is safe only on installs whose stored
  salt is still `wip_auth_salt`: those that had keys before the upgrade and
  never adopted an archive's salt. On any other install, an older Registry
  or service checks runtime keys against `wip_auth_salt` and rejects every
  Console, imported and restored key.
- **Archives made before the salt travelled** carry only its fingerprint. A
  fresh stack adopts one when that fingerprint is the configured salt's (the
  old default); any other such archive is refused.
- The Registry warns at startup about any stored key with a legacy
  (non-bcrypt) hash.
- **WIP-Toolkit 0.25.2:** the `system-restore` help text states the
  adoption rule.

### A system backup of API keys, grants and reservations; WIP-Toolkit 0.25.0

Nothing backed up the Registry's auth state. After a wipe or a move to new
hardware, the following were lost and had to be recreated by hand:
- every Console-issued API key;
- every namespace grant;
- every runtime reservation.

Namespace backups do not carry these, by design.

There is now a separate system backup:
- `POST /api/registry/system-backup` returns one JSON archive: every stored
  API key (its bcrypt hash and metadata, never a plaintext, including keys
  imported from the install's key file), every grant and every reservation.
  Config keys, the master key among them, are not included.
- `POST /api/registry/system-restore` restores an archive. It is a dry run
  unless `dry_run=false` is sent.
- **Both need a platform admin key.** There is no MCP tool.
- **WIP-Toolkit 0.25.0** adds `wip-toolkit system-backup OUT.json`, which
  writes the file with mode 0600, and `wip-toolkit system-restore IN.json`,
  which reports only; `--apply` writes. It also takes `--overwrite` and
  `--include-user-grants`.

**How a restore behaves:**
- It upserts by identity and never deletes.
- Every restored record arrives unmanaged, with `origin: "restored"`. The
  key import re-adopts any record the install's files declare.
- A record that differs on the target is replaced only with `overwrite`.
  Records the install's files manage are never replaced.
- An archive made under another API key hash salt is adopted, salt and
  all, by a stack holding no keys of its own, and refused by one that does
  (see the next entry).
- User grants from another OIDC issuer are skipped unless
  `include_user_grants` is set.
- A grant on a namespace that does not exist, and is not reserved, is
  written **orphaned** (see the next section). The system and namespace
  restores work in either order.
- The dry-run report warns when the target already holds auth state of its
  own: restoring brings back access revoked since the backup.

The archive is sensitive and not encrypted. Store it like `~/.wip-deploy/`.
See `docs/wip-guide.md` §5.7.

### `@wip/client` 0.76.0 and 0.77.0: attachments, the system backup and its mirror mode, orphaned grants; a typed restore report

**Registry:** the system restore report is now typed for code, while the
system backup is still unreleased.
- Every skipped record carries a `reason_code`: `managed_by_deployer`,
  `managed_on_source`, `invalid_record`, `differs`, `orphaned_here`,
  `orphaned_in_archive`, `other_oidc_issuer`, `config_key_name`, `app_route`,
  `attach_policy_unavailable`, `upstream_invalid`, `upstream_blocked` or
  `concurrent_write`.
- It also carries its identity as fields: `name` (key, attachment),
  `prefix` (reservation), or `namespace`, `subject_type` and `subject`
  (grant). `id` and the human `reason` stay.
- A salt the stack cannot take answers 409 with
  `error_code: "salt_mismatch"` beside `detail`.

**`@wip/client` 0.76.0** (`registry`):
- `systemBackup()` and `systemRestore(archive, {dryRun, overwrite,
  includeUserGrants})`. A dry run is the default. The archive holds every key
  hash: offer it as a download, never keep it in browser storage.
- `upsertAttachments`, `deactivateAttachments`, `listAttachments`,
  `getAttachment`. A write replaces the whole record: `auth_required` and
  `allowed_groups` are required in the type. A malformed item is a 422 for
  the whole request.
- `activateGrants(prefix, …)` and `listAllGrants({orphaned})`. `Grant` gains
  `orphaned` and `orphaned_at`; `Namespace` gains `activated_orphan_grants`.
- Constants `SALT_MISMATCH` beside `MANAGED_BY_DEPLOYER`, and
  `wipErrorCode(err)`, which reads the `error_code` of any WIP HTTP error.
- **0.77.0:** the mirror mode (CASE-1056). `systemRestore` takes `mode`
  (`upsert` | `overwrite` | `mirror`) and `expect` (the dry run's
  `plan_digest`). The report gains `mode`, `refused` and `plan_digest`, and
  each section gains `deleted` and `deactivated`. The skip codes gain
  `mirror_removes_key` and `caller_key`, and the 409 codes `RESTORE_REFUSED`
  and `PLAN_CHANGED`. `overwrite: true` still works and is the same as
  `mode: 'overwrite'`.

### A restore creates namespaces as the person who asked; orphaned grants; WIP-Toolkit 0.25.1

**A namespace restore no longer bypasses the Registry's rules for creating
namespaces.** Document-store created and updated a restore's namespaces with
its own key, the master key. So the Registry never saw who asked:
- a key that reserved a prefix could restore an archive and get the
  namespace with the **archive's** settings, not the reservation's (for
  example `deletion_mode: full` where the reservation says `retain`);
- any namespace admin who restored into an existing namespace had its
  `isolation_mode`, `inbound_references` and `allowed_external_refs`
  rewritten from the archive, which otherwise only a platform admin can do.

Now:
- **A platform admin's restore** applies the archive's settings, as before.
- **Anyone else's restore never changes a namespace's settings.** An
  existing namespace keeps its own. A namespace that does not exist is
  created with the requester's own API key, so the Registry applies its
  normal rules: a reservation's holder gets the reservation's settings,
  anyone else is refused. Every archive setting that is not applied is a
  warning on the job.
- **Creating a namespace by restore needs the requester's own credential**
  — see "A restore acts as its requester at the Registry" above for what
  that is for a login.
- The credential is held in the restore job's memory only, never on the job
  record.

**Orphaned grants.** A system restore no longer skips a grant whose namespace
neither exists nor is reserved. It writes it with `orphaned: true`:
- **An orphan counts as no grant** until it is activated. (A key whose own
  `namespaces` list names the prefix still reads it, as any such key does
  without a grant.)
- **A prefix another owner reserves here counts as absent:** the archive's
  grants on it are written orphaned, so that owner's own create does not
  activate them. A platform admin's create does (see below).
  An archived namespace counts as existing. A grant the archive holds
  orphaned never replaces one that is effective here, even with
  `overwrite`.
- **It is activated** when a platform admin creates the namespace (a
  namespace restore an admin runs does this, as do `PUT`/`POST
  /namespaces` and the namespace import, which list them in the response's
  `activated_orphan_grants`), when a namespace admin activates it with `POST
  /api/registry/namespaces/{prefix}/grants/activate`, or when an admin
  grants it again.
- **A key that reserves the prefix later and creates the namespace does not
  inherit orphans**; they were made for another namespace of that name.
- **A platform admin's create activates every orphan on the name**, those
  written because another owner reserves it included: a namespace is known
  only by its name, so the two cannot be told apart. Before an admin creates
  such a namespace, list its orphans (`GET /api/registry/grants?orphaned=true`)
  and revoke any that should not apply.
- **Listing:** grants carry `orphaned` and `orphaned_at`. `GET
  /api/registry/grants?orphaned=true` lists them across namespaces for
  platform admins.
- The system backup carries `orphaned`; a grant orphaned in the archive
  is written orphaned. The restore report lists orphans in
  `grants.orphaned`.
- **WIP-Toolkit 0.25.1:** `system-restore` marks the orphaned grants it
  creates. **0.25.3:** its help text says when a grant is written orphaned.

**A runtime reservation stays its owner's approval to create the namespace.**
Deleting a namespace purges every grant on it, including the `admin` grant of
the key that reserved it, while the reservation stays. Such a key could then
not bootstrap its namespace again (403). Now the key that owns a runtime
reservation may create the namespace without a grant, and gets its `admin`
grant back when it does. Settings must still equal the reservation's.
Until the namespace exists, the owner also counts as `admin` on its prefix, so
it can restore the namespace's backup directly (a restore checks admin before
it starts). Both hold only while the prefix is in the key's own namespace
scope: a key narrowed off its namespace stays narrowed after the namespace is
deleted, even though narrowing kept the reservation. Reservations from the install's file are unchanged: their keys'
grants come back through the key import.

**Rollout:** rebuild the Registry and document-store images. See
`docs/wip-guide.md` §5.7.

### Every service resolves every key through the Registry; only the Registry holds the key file

Second step of one key model in the Registry. The install's key file (the
spec-declared keys, each app's key and the canary) is now read by the
Registry alone:

- **A directory of its own.** The file is rendered to `config/keys/api-keys.json`
  (on k8s, the Secret `wip-key-file`) and mounted only into the Registry, as
  `WIP_REGISTRY_KEY_FILE`. def-store, template-store, document-store,
  reporting-sync and ingest-gateway no longer mount it or declare
  `WIP_AUTH_API_KEYS_FILE`, so no other container holds a plaintext. The
  auth config directory (`config/auth/`, the Secret `wip-api-keys-config`)
  keeps the federated group policy and the namespace reservations.
- **The Registry's records are the keys.** The Registry imports the file as
  before and no longer loads it as config keys, so every imported key is
  served by key sync. The other services authenticate app keys from their
  synced copy and ask the Registry for their grants, like any other key.
  A key-file change now reaches them within one import and one key sync:
  about 40 s on compose and dev (it was about 10 s), longer on k8s while the
  Secret propagates.
- **If the Registry is down, app keys stop working** too, once a service's
  synced copy is 90 s old. That is the design ("if the Registry is down, WIP
  is down"). Before this, app keys kept working through any outage, because
  every service resolved them locally.
- **The key listing shows a managed key's declared grants** in `grants`, as
  it did for config keys, so a client can tell which keys' grants the file
  owns. A key that declares no grants shows `grants: null` and keeps grants
  you set through the API.
- **An apply no longer refuses a namespace a runtime key reserves.** It
  warns, naming the key, and the Registry replaces the key's reservation with
  the install's (logged CRITICAL; the key keeps its grants). An apply still
  refuses an ID prefix a runtime reservation already claims.
- **`wip-deploy verify --security`** has a new check: the key file is mounted
  only into the Registry. A render from before this change fails it, with a
  re-render hint.
- **Apps-only installs** (`--apps-only` against a remote WIP): the apps now
  run with the install's `api-key`, the key `wip-deploy import-bundle`
  brings from the remote WIP. Since apps got derived keys, they had run with
  a derived key the remote WIP never knew. That was found by reading the
  code; it has not been run.

**Action:** re-render every install with images that carry this change, in
one apply: `wip-deploy redeploy --name <install> --tag <tag>`. Run
`wip-deploy verify --security` afterwards. An install left on the old render
keeps working: the Registry still imports the file, and its records stay
dormant while it also loads the file as config keys.

### The Registry imports the install's key file and reservations file; `@wip/client` 0.75.0

First step of one key model in the Registry. The Registry now imports the
key file and the reservations file wip-deploy renders into its own records,
marked `managed_by: "wip-deploy"`:

- **Keys** go into `api_keys`, hashed with bcrypt; the plaintext is never
  stored.
- **The grants a key declares** go into `namespace_grants`. Its namespaces
  give read, its grants their level, as wip-auth resolves them for a config
  key.
- **Reservations** go into `namespace_reservations`.

**When it imports, and what it removes.** It imports at startup before
serving, every 10 s, and whenever a reservation lookup finds a file changed.
It removes managed records the files no longer declare, and never removes a
record it did not import.

**A file it cannot trust changes nothing.** That covers a key file that is
missing, unparseable, or without the `wip-canary` key, and a missing or
unparseable reservations file. The previous import stands, and the Registry
logs why.

**Nothing changes for authentication yet.** The services still resolve these
keys from the file. While the Registry also loads the file as config keys, an
imported key stays out of key sync, and the key listing shows it once
(`source: "config"`, `managed_by: "wip-deploy"`).

Behaviour changes:

- **The API refuses every write to a managed record** with 409 and
  `{"detail": "...", "error_code": "managed_by_deployer"}`; `detail` stays a
  string. That covers creating, updating, widening, narrowing or revoking a
  managed key; creating or revoking a managed grant, or any grant to a key
  whose grants the file declares; and reserving or releasing a prefix the
  file reserves. Bulk grant writes refuse per item, with the same
  `error_code`. Before this:
  - writes to a config key's name answered 400;
  - reserving a file-reserved prefix answered 409 with another message;
  - releasing a file reservation answered 400.
- **A collision is won by the file's record, loudly, logged CRITICAL:**
  - a runtime key with a file key's name is replaced, and its old value stops
    working;
  - an API grant to that key on a namespace the file declares is replaced;
  - a runtime reservation of a prefix the file reserves is replaced, and the
    key that made it keeps its grants.

  This replaces the previous rule, which refused a prefix reserved both ways
  (the namespace upsert answered 409 until an operator removed one).
  `GET /reservations` therefore lists one record per prefix, and its
  `conflict` field is always null.
- **A key that declares no grants keeps its API grants.** Spec-declared keys
  without `grants` still resolve through the Registry's grants, and the API
  may change those.
- **Listings carry the new fields.** `managed_by` and `origin` appear on API
  keys, and `managed_by` on grants and reservations.
- **`@wip/client` 0.75.0** adds:
  - `managed_by` on `APIKeyInfo`, `Grant` and `NamespaceReservation`, and
    `origin` on `APIKeyInfo`;
  - `error_code` on grant bulk results, and `'error'` as a revoke status;
  - the constant `MANAGED_BY_DEPLOYER`.

  A 409 refusal arrives as `WipConflictError`, with the code in
  `err.detail.error_code`.

**Action:** none. Nothing about the key file, the secrets or the deployer
changes.

### Runtime API keys stop working 90 s into a Registry outage

def-store, document-store, template-store, reporting-sync and ingest-gateway
each keep a copy of the Registry's runtime API keys, which key sync refreshes
every 30 s. When the Registry was unreachable, that copy was kept forever: a
runtime key went on authenticating for the whole outage. A runtime key in
`wip-admins` was also authorized for everything, because the superadmin check
needs no Registry call. That contradicted "if the Registry is down, WIP is
down".

The copy is now a cache with a lifetime. Each successful sync confirms the
runtime keys for 90 s, three poll intervals, so two missed polls do no harm.
A failed sync does not extend that. Once the 90 s have passed:

- **Every key that is not a config key gets 503** (`API keys cannot be
  verified: the Registry has not confirmed them within the last 90 s`) until a
  sync succeeds again. This covers runtime keys, including admin-group ones,
  and also **unknown and invalid keys**, which get 503 instead of 401. That is
  intended: the answer reveals nothing about which keys exist.
- **Config keys keep working through an outage of any length.** These are the
  keys a service loads from its own environment, which on a wip-deploy
  install is the master key (`MASTER_API_KEY`, also each service's own
  credential). They are not a copy of the Registry, so a Registry outage does
  not mean that no key works. The
  key file's keys are not config keys in these services: the Registry
  imports them and key sync serves them (see "Every service resolves every
  key through the Registry" above), so they stop after 90 s like every other
  runtime key.
- **At service start**, runtime keys get 503 until the first sync succeeds.
  Before, they got 401.

Nothing to configure. The Registry's own key handling is unchanged.

### Documents with a term inside an object field reach the reporting layer

reporting-sync never wrote a document whose object field held a term value
(an `object` field with a `template_ref` whose template has a `term` field,
e.g. `{"one": {"name": "o", "kind": "beta"}}`). The row named a column,
`one_kind_term_id`, that the table does not have, because the table stores
the whole object as one JSONB column. The insert failed and the event was
dropped after three deliveries, with `column "one_kind_term_id" of relation
… does not exist` in `recent_drops`. Batch sync, and so a fresh restore's
reporting sync, used the same row builder. Terms inside array items and
top-level terms were not affected.

Such rows are now written. The nested term's id is in the row's
`term_references_json` and its value inside the object's JSONB column.
There is still no `<object>_<field>_term_id` column.

- **After upgrading,** re-sync the templates that have such fields:
  `POST /api/reporting-sync/sync/batch/{template_value}?namespace=<ns>`, then
  check `GET /api/reporting-sync/parity?namespace=<ns>`. Rows the old release
  dropped are not replayed by themselves.

### Archives with bare identity synonyms are refused; the re-qualification pass is gone; `@wip/client` 0.74.0, `@wip/react` 0.25.0

A document's identity values are registered as a synonym qualified by its
template (`{"@template": <template value>, <field>: <value>, …}`). Data written
before that carries them bare (`{<field>: <value>, …}`), a form that claims the
spelling for every template in the namespace. The bare form is no longer
supported anywhere.

- **Restore refuses it.** Every restore mode (restore, merge, fresh) refuses an
  archive whose document entries carry a bare identity synonym, before
  anything is written, dry runs included. The message names the count per
  namespace. To bring such an archive forward, restore it with an earlier WIP
  release, which re-qualifies identity synonyms on restore, then take a new
  backup there.
- **The re-qualification pass is removed.** A fresh restore no longer runs
  it. `POST /api/document-store/maintenance/requalify-identity-synonyms`,
  `@wip/client`'s `documents.requalifyIdentitySynonyms()` with its
  `RequalifyIdentitySynonyms*` types, and `@wip/react`'s
  `useRequalifyIdentitySynonyms()` are removed. This supersedes the entries
  below on batched re-qualification and on the pass's typed response.
- **Before upgrading,** check every live namespace with the old release's dry
  run (`POST …/requalify-identity-synonyms` with `dry_run: true`) and apply
  it where it finds bare synonyms. Archives taken before that are refused
  afterwards.

### Fresh restores do their Registry work in bulk

A fresh restore's Registry work ran one item at a time: activating each
document identity, then adding each synonym. Both now run in bulk, and a
document's synonyms are written together with its activation. On a Mac Studio
a fresh restore of 240,474 document rows went from 12 min 55 s to 2 min 26 s
(activation about 6 min to under a minute, the separate synonym pass gone).
`/entries/register`, `/provision`, `/reserve`, `/entries/activate` and
`/synonyms/add` / `/remove` claim composite keys in one insert per request;
their per-item results are unchanged. `/entries/activate` items accept
`synonyms` (with `composite_key` only).

### Bulk document requests carry at most 1,000 items

`POST`, `PATCH` and `DELETE /api/document-store/documents` and
`POST /api/document-store/validation/validate-bulk` reject a request with
more than 1,000 items with **422**, before anything is validated or written.
A request is held in memory whole while it is processed, so an unbounded
list was an unbounded allocation; on a Raspberry Pi a single oversized
request could exhaust memory. **Callers sending more must split them** into
several requests: the WIP-Toolkit, ingest-gateway (100) and CSV import are
already well below the limit, while the MCP bulk tools and
`@wip/client.createDocuments` pass through what they are given.

### Term values from a closed namespace are now refused for writers who cannot read it

A namespace with `inbound_references: closed` (the default) accepts a
reference from another namespace only when the writer can read it. For term
values the check never ran: the resolver's results lacked the terminology id
the gate decides on, so a writer could store a term from a closed namespace
it cannot read. A value that exists was accepted while an absent one was
refused, so the vocabulary could be probed value by value.

Document writes (single, bulk, PATCH and validate) now refuse such a value
exactly like an absent one: `invalid_term`, no suggestion. A term the
document's previous version already held stays usable. **Writes that
succeeded before may now fail**: give the writer's key read on the
terminology's namespace, or open that namespace's `inbound_references`.
`wip_auth.term_resolution.resolve_term_values` results now carry
`terminology_id`. Within a bulk write the gate's Registry lookups are shared,
one per terminology.

### `wip-toolkit restore` exits 1 when the job outlives `--timeout`; WIP-Toolkit 0.24.1

`restore` follows its job for `--timeout` seconds (default 3600). When the
job was still running at the timeout, the command printed the last snapshot
and exited 0, so a script chaining on the exit code treated an unfinished
restore as done. It now exits 1 with "not finished within --timeout; the job
keeps running on the server.", the same rule `backup` already follows: exit
0 only for a complete job. With `--json` the snapshot is still printed on
stdout and the message goes to stderr. `--no-wait` is unchanged and exits 0.

### Document writes ask the Registry where references live only in strict namespaces

Before writing a document, document-store checks the source namespace's
`isolation_mode` against the namespaces of the document's new references.
It used to look those namespaces up in the Registry once per document,
although only a `strict` namespace can refuse a reference. Writes into any
other namespace now skip the lookup: create, bulk create and PATCH. A bulk
write into a strict namespace looks up the whole batch's references at once,
in requests of up to 500 ids. Refusals are unchanged.
The bulk response's `timing` gains `1b_reference_gates`, the time spent on
the relationship and namespace-isolation checks after validation.

A bulk write also resolves its term values up front. It used to send
def-store one request per document for the values the batch had not seen
yet; now the batch's distinct values go in one request per 1,000 pairs,
before validation, and every document is answered from them. def-store still
decides every value at write time, and if that request fails each document
resolves its own values as before. `timing` gains `0t_term_prefetch`.

Bulk create now writes its documents in bulk: one update retiring the
versions a batch replaces, then one insert, instead of a database round trip
per document. Items with inline synonyms, a pinned version, a revival or a
non-versioned template keep the per-item path, as does a request with
`continue_on_error=false`. Per-item results are unchanged. `timing` gains
`4a_retire_versions` and `4b_insert_documents`. The Registry's
`/entries/register` mints new ids from the namespace configuration it has
already read instead of reading the namespace once per new entry, and its
response gains an optional `timing` per phase.

### App keys for apps wip-deploy does not manage: runtime namespace reservations; `@wip/client` 0.73.0

Until now only wip-deploy could issue a key that bootstraps an app: a key that
is not a platform admin may create a namespace only if the install reserves it,
and reservations came only from the deployed apps' manifests. A platform admin
can now issue such a key through the Registry's API, for an external app or for
someone developing against WIP.

- **`POST /api-keys` takes `reserve`:** `[{prefix, settings}]`, each prefix also
  in `namespaces`. The key gets `admin` on each reserved namespace and creates it
  through the namespace upsert. The upsert applies the reserved settings: the
  bootstrap may omit them or send equal values, as for a wip-deploy app.
  Everything is checked before anything is written. Refused:
  - `wip` or an invalid prefix (400);
  - a prefix the install reserves for one of its apps, or another key's (409);
  - an ID prefix another reservation or namespace claims (409).
- **Widen and narrow:**
  - `PUT /api-keys/{name}/namespaces/{prefix}` adds a namespace. Send
    `reserve` to reserve it too; the grant is `admin` when reserving, else
    `permission` (default `read`).
  - `DELETE /api-keys/{name}/namespaces/{prefix}` removes it, revokes the grant,
    and releases the key's reservation if the namespace was never created.
  - `PATCH` can no longer drop a reserved namespace from `namespaces`. Use the
    `DELETE` above, which also handles the reservation and the grant.
- **Revoke** (`DELETE /api-keys/{name}`) handles the key's reservations:
  - it releases those whose namespace was never created
    (`released_reservations`);
  - it keeps those whose namespace exists (`kept_reservations`);
  - it never deletes a namespace.
- **`GET /reservations`** (admin) lists every reservation from both sources,
  with `source`, `namespace_exists` and `conflict`. `DELETE /reservations/{prefix}`
  releases a runtime reservation explicitly.
- **One prefix has one owner.** A wip-deploy apply asks the running Registry
  for its runtime reservations. It refuses an app reservation that collides
  with one on a namespace or an ID prefix, and names the key. When the Registry
  is unreachable (a fresh install) the apply proceeds with a note. If a prefix
  ends up reserved by both sources anyway, the namespace upsert refuses it
  (409) until one is removed, the listing marks the conflict, and the Registry
  logs it.
- **MCP:** `create_api_key` takes `reserve`. **`@wip/client` 0.73.0:**
  - `CreateAPIKeyRequest.reserve`;
  - `addAPIKeyNamespace`, `removeAPIKeyNamespace`, `listReservations`,
    `releaseReservation`;
  - `APIKeyInfo.reserved_namespaces`;
  - the reservation outcomes on `APIKeyRevokeResult`.

**Action:** none for existing installs. Apps deployed by wip-deploy keep their
derived keys; use these endpoints only for apps wip-deploy does not manage.

### One plaintext, one key; revoking a key revokes its grants; `@wip/client` 0.72.0

A presented key authenticates as the first record it matches, and each
service caches that match. Two keys with one plaintext therefore
authenticated as whichever key a service had matched first, so services
started at different times disagreed about the caller. This was seen when a
runtime key's plaintext was pasted into an app's key secret.

- **wip-deploy refuses an apply whose key secrets share a value.** That covers
  app keys, spec-declared keys, the canary and the master key. The error names
  the secrets and points to `rotate-key`. An edited `app-<app>-api-key` gets
  an error explaining that the deployer generates app keys, and naming
  `wip-deploy app-namespace add <app> <ns>` (to widen access) and
  `wip-deploy rotate-key app-<app>` (for a fresh key).
- **`verify --security --live` checks every data service per app key.** It
  also checks the Registry's `/my/namespaces`, so a per-service split fails.
- **Config keys that share a plaintext are not loaded,** and an error names
  them. The master key is the exception: it stays, and the keys sharing its
  value are dropped.
- **A runtime key with the same plaintext as a config key is shadowed.** The
  Registry checks each runtime key's hash against each config key's value, at
  startup and every 10 s. A matching runtime key no longer authenticates: the
  config key wins. It is left out of key sync, so every service converges on
  the config key within about 40 s. `GET /api-keys` lists it with
  `shadowed_by: <config key>`, and the Registry logs it once. The runtime key
  is kept so an operator can revoke it.
- **Revoking a runtime key revokes its namespace grants.** Grants are keyed by
  the key's name, and were left behind on revoke, so a later key created under
  the same name inherited them. `DELETE /api-keys/{name}` now deletes them and
  returns `revoked_grants` (the namespaces). Grants that earlier revokes left
  behind are not cleaned up.
- **`@wip/client` 0.72.0:** `APIKeyInfo.shadowed_by`, and `revokeAPIKey`
  returns `APIKeyRevokeResult` (with `revoked_grants`).

**Action:**
1. If a service logs "share one plaintext", or the Registry logs "has the
   same plaintext as config key", give one of the keys a new value.
2. To find grants left behind by earlier revokes, list each namespace's grants
   (`GET /namespaces/{prefix}/grants`). Revoke any `api_key` grant whose
   subject is not a current key name.

### Document validation: patterns match the whole value, and five gaps close; wip-archive 0.10.0

- **`pattern` must match the whole value.** It used to match at the start
  only, so `[A-Z]{3}` accepted `ABC-anything`. Now `[A-Z]{3}` means exactly
  three capitals; `^`/`$` anchors are no longer needed. A template whose
  pattern relied on prefix matching starts refusing values that match only a
  prefix. That includes a PATCH of an existing document, which re-validates
  the whole document. On the dev install every real pattern was already
  `^…$`-anchored, and no stored value depends on the difference.
- **Datetimes may carry any UTC offset.** `2026-10-31T12:00:00+02:00` was
  refused; any `Z` or `±HH:MM` offset is accepted now. The value is stored as
  submitted; reporting stores datetimes with their time zone. Datetimes without
  an offset are still accepted.
- **Array items of type `boolean`, `date` and `datetime` are validated** like
  their scalar fields, and a non-string `file` item is refused. They were
  accepted unchecked.
- **`number` refuses `true` / `false`**, as a field and as an array item.
  `integer` already did.
- **An ambiguous term name in an array that is an identity field is refused**
  (`ambiguous_term`) instead of resolving to the first bearer and feeding it
  into the identity hash. Ambiguous names elsewhere still warn.
- **A document with several bad fields reports all of them.** One field's type
  error no longer suppresses the semantic checks (email, url, …) of the fields
  after it.

The offline validator in `wip-archive` (0.10.0) applies the same rules, so a
pre-upload check and the server still agree.

**Action:** check templates whose `pattern` relies on matching a prefix only.

### Templates that extend another are served whole, or refused

Reading a template with inheritance resolved (`GET /templates/{id}`, the
by-value routes, document writes, reporting sync) flattened its parent's fields
into it but dropped the child's other properties: `usage`, `versioned`,
`source_templates` / `target_templates`, `header_fields`, `reporting`,
`extends_version` and `renames` came back as defaults. A relationship or
`versioned: false` template that extended another was therefore validated as an
ordinary entity template: edges skipped their endpoint checks and
`versioned: false` documents gained versions. Reporting sync ignored the
child's `reporting` settings. Now the resolved template is the child with only
`fields`, `rules` and `identity_fields` merged; every other property is the
child's own.

- **A chain that cannot be resolved is an error**, not the child's own fields
  served as if they were the schema. The causes are a missing or unpinned
  parent version, a cycle, or a chain deeper than 10. The resolving routes
  answer 409 `{"error_code": "inheritance_unresolved", "details": {"rule": …}}`;
  a caller who cannot read the template still gets 404. The `/raw` routes keep
  serving the stored template. A document write against such a template fails
  per item with `template_unresolvable`.
- **`GET /templates/by-value/{value}` resolves inheritance**, as its
  description always said. `@wip/client`'s `getTemplateByValue` therefore
  returns the inherited fields too.
- **reporting-sync no longer resolves inheritance itself.** It used the
  parent's latest version, not the pinned one, and fell back to the child's own
  fields when the parent was missing. It now reads templates resolved by
  Template Store, including when a template is created: an extending
  template's reporting table now has its inherited columns before its first
  document arrives. Tables created before this fix gain them on their first
  document or a forced batch sync; `GET /parity` lists them.
- **Writes refuse a chain that reads would refuse.** Creating a template, a new
  version of one, or activating a draft now walks the pinned parent chain
  the same way reads resolve it. A chain deeper than 10 is refused per item
  with `invalid_state` / `max_depth`, a cycle with `invalid_state` /
  `circular_inheritance`, and a missing ancestor with `reference_violation` /
  `missing_parent`. Before, a create could succeed and the template then be
  served only as a 409. The cycle check on a new version used each parent's
  latest version; it now follows the pinned versions. Restores are not
  checked: they trust the archive.

**Action:** none for templates without `extends`. A `versioned: false`
template that extends another may already hold documents above version 1, and
an extending edge type may hold edges whose endpoints its declaration excludes.
Both were written before this fix and are not changed by it.

### `allow_missing_identity` on every restore surface; `@wip/client` 0.71.0, WIP-Toolkit 0.10.0

An id-preserving restore refuses an archive that carries entities but no
registry identity rows (an export made without synonyms), because nothing in
the restored namespace would resolve by id. `allow_missing_identity` restores
it anyway, with a warning. Until now only the upload restore endpoint accepted
the option. It now reaches every surface:

- `POST /backup/jobs/{job_id}/restore` (restore from a retained archive):
  `allow_missing_identity` in the request body.
- MCP `start_restore`: `allow_missing_identity`.
- `@wip/client` 0.71.0: `RestoreOptions.allow_missing_identity`.
- WIP-Toolkit 0.10.0: `restore --allow-missing-identity`.

It applies to `mode: "restore"` only: both endpoints answer 400 for a merge
or a fresh restore, and the toolkit refuses the flag outside `--mode restore`.

**Action:** none.

### A restore refuses an archive whose own verification failed; `@wip/client` 0.70.0, WIP-Toolkit 0.7.2

An archive transform (filter, latest-only, …) verifies its own output and
records the result in the archive's `derived_from` chain. When the newest step
records `verdict: "fail"`, the archive is known to be broken, and every restore
mode — restore, merge and fresh, dry runs included — now refuses it before
writing anything. The job fails with a message naming the transform, its
toolkit version and the finding counts. The new restore option
`allow_failed_verify` restores it anyway, with a warning, and the namespace's
restore provenance records `accepted_failed_verify: true`.

Every other derived archive (`pass`, `warn`, or no verdict) restores with a
warning that names the newest transform and its verdict. Before, only the
id-preserving restore warned; fresh and merge said nothing.

The option is on both restore endpoints (`POST /backup/namespaces/{ns}/restore`
as a form field, `POST /backup/jobs/{id}/restore` in the JSON body), MCP
`start_restore`, `@wip/client` `RestoreOptions` (0.70.0), and
`wip-toolkit restore --force` (0.7.2).

**Action:** none today. No transform writes `derived_from` yet, so no existing
archive is affected.

### `@wip/proxy` 0.6.0: a missing key can cost `/wip` instead of the app

`wipProxy()` is usually called while the server module loads, and throws when
neither `apiKeyFile` nor `apiKey` yields a key. That throw stopped the process
before `listen()`, so the app's `/api/health`, `/api/app-config` and SPA went
down with the one mount that needed a key. The new option
`onMissingKey: 'unavailable'` makes the proxy's routes answer 503
`{"error": <reason>, "error_code": "wip_proxy_unavailable"}` instead, with one
startup warning; the reason is the exact message the throw would carry. The
returned router exposes it as `unavailableReason`, for an app's
`appConfigHandler`. The default stays `'throw'`; with a key, nothing changes.

The build card and the ship checklist now show the option. The proxy README
also warns that `apiKey: process.env.WIP_API_KEY || ''` passes no key (an empty
string is none), so it is not a fallback.

**Action:** none required. Apps adopt the option on their next scaffold refresh:
vendor 0.6.0 and add `onMissingKey: 'unavailable'` to the `wipProxy({...})`
call.

### Scoped applies restart every service whose rendered definition changed

A verb that applies with a narrow scope (`app-namespace`, `group-map`,
`connector`, `remove-app`, a scoped `redeploy`, …) used to widen it only to
services whose config hash changed: the values of secrets they read and files
mounted for them. A changed literal env value, image, port or mount left the
service running its old definition on every target, and on a dev install no
backend service or app carried a hash at all, so even a switched API key went
unnoticed. Widening now compares each service's whole rendered definition
(the compose / dev service block, the k8s pod template), and dev renders
carry the config hash on every service like compose.
**Action:** expect the first scoped apply after upgrading a dev install to
recreate most services once: the previous render has no hash labels, so
every definition differs. Run a plain `wip-deploy redeploy` at a convenient
time to take that hit deliberately.

### Namespace-scoped keys run reporting SQL over their own namespaces

`POST /api/reporting-sync/query`, query-shaped `POST /export/csv` and the MCP
`run_report_query` tool used to need an admin or service key, so apps on their
own keys could not run SQL at all. They now accept any key:

- **Admin and service keys** read every namespace, as before.
- **Every other key's SQL** runs on a PostgreSQL connection that logs in as
  a role able to read only the namespaces the key can read. A relation in
  another namespace fails with `permission denied`, and so does a read made
  through a function such as `query_to_xml`. A `namespace` the key cannot
  read answers 404.
- **Registered views read with their reader's privileges**
  (`security_invoker`). Through a view, a namespace-scoped key's SQL sees
  only what it could read directly. View rows (`/rows`) for such a key also
  run on its scoped connection. A view whose SQL used a function to reach
  another namespace no longer returns that namespace's rows to readers who
  cannot read it. Existing registered views are switched over when
  reporting-sync starts.

The scoped roles are created on demand by a new role-manager login,
`wip_reporting_roles`, whose password is a new deployment secret
(`postgres-role-manager-password`). It is used only to create those roles and
never serves requests. Without it, namespace-scoped SQL answers 503;
admin and service keys are unaffected.
**Action:** re-render the install (`wip-deploy redeploy`) so the deployer
generates the secret, as for the other reporting-sync role passwords.

### Operators add namespaces to an app per install: `wip-deploy app-namespace`

`wip-deploy app-namespace add <app> <namespace>` gives an app another
namespace on one install: owned (reserved for it with the given settings, its
key holds `admin`) or, with `--access read|write`, used. `remove` takes it
away; `list` shows the manifest's and the install's entries. The addition is
kept in the install's saved spec; the apply changes only the key and
reservations files, so nothing restarts. Apps never create unreserved
namespaces themselves.

The namespace upsert no longer refuses an app key's `description` on a
reserved namespace: a create stores the description sent, and an existing
namespace keeps its own. Every other setting must still match the
reservation.
**Action:** none.

### Edges may cross namespaces; relationship reads follow the reader

- **Writing.** An edge lives in its source's namespace: `source_ref` must be
  in the edge's own namespace (`cross_namespace_relationship` otherwise, as
  before). `target_ref` may now point into another namespace and passes the
  same gates as any reference (`isolation_mode` outbound; the target's
  `inbound_references` plus the writer's read inbound). An edge type's
  declared `source_templates` / `target_templates` pass those gates too at
  create, activation and `add_edge_type_endpoints`, which they never did.
- **`GET /documents/{id}/relationships`** without `namespace` now lists the
  edges in every namespace the caller can read (it listed only the seed's
  namespace), so incoming edges stored in other namespaces appear. An
  explicit `namespace` must be readable (404 otherwise; it was not checked).
- **`include=peers`:** a peer in a namespace the caller cannot read reports
  `peer_error_code: "not_found"` (it was `"forbidden"`, naming the namespace).
  A call with no caller identity no longer sees every peer.
- **`GET /documents/{id}/traverse`** without `namespace` walks edges in every
  namespace the caller can read and stops at a document in a namespace it
  cannot read (that document is not returned). With `namespace`, every hop
  stays in that namespace, as before.

**Action:** none for same-namespace data. Clients that branch on
`peer_error_code == "forbidden"` should treat `"not_found"` the same way.

### Apps run with a key of their own, not the master key

The deployer now derives one config-file key per enabled app, `app-<name>`
(secret `app-<name>-api-key`), and sets the app's `WIP_API_KEY` from it. The
key holds `admin` on the namespaces the app owns (`spec.namespaces`), `read`
or `write` on those it lists under the new `spec.namespace_access`, and `read`
on `wip`; no admin group. An app manifest naming `from_secret: api-key` is
refused unless it declares `privileged_key: true` (React Console only), and
`WIP_API_KEY` may not be declared by hand. All ten shipped app manifests are
migrated.

What an app can no longer do with its key: create a namespace that is not
reserved for it, and read any namespace it has not declared. Reporting SQL
(`POST /api/reporting-sync/query`, the MCP `run_report_query` tool) still
works; it runs over the namespaces the key can read (see "Namespace-scoped
keys run reporting SQL over their own namespaces"), and a relation in any other
namespace fails with `permission denied`. Known effects on the shipped apps:
- WIP-KB: corpora a user creates at runtime cannot be created or browsed
  with the app's key.
- clintrial, pbs-clintrial, dnd, song, wip-val, dev-kb: their namespaces
  are taken from their manifests, not checked against their source;
  clintrial declares none.
**Action:** after upgrading, check each app's namespaces and access in its
manifest; an app that needs another namespace lists it under
`spec.namespace_access`. Apply with `wip-deploy redeploy`, which recreates
every app with its new key.
### Reads no longer show another namespace's data to a caller who cannot read it

These read paths showed data from namespaces the caller had no read on:

- **Registered views.** `POST …/namespace/{ns}/views/{name}/rows` checked read
  on `{ns}` only, so a view a `wip-admins` / `wip-services` key had registered
  over another namespace's schema served that schema's rows to any reader of
  `{ns}`. Reading a view now takes read on **every** namespace it draws from
  (read live from the view's Postgres dependencies); a view over platform
  tables (`public`, `pg_catalog`) reads only with an admin or service key.
  A view the caller may not read answers `404`, like an unregistered one.
- **Registering a view** with a namespace-ADMIN key now succeeds when the view
  draws on other namespaces the key can read (it was refused for any other
  schema); platform tables still need an admin or service key.
- **`GET /api/reporting-sync/entity/{type}/{id}/references`** returned each
  referenced entity's label, value and status. A reference into a namespace the
  caller cannot read now keeps its id but reads as `broken` ("… not found"),
  and the counts move with it.
- **`GET /api/document-store/files/{id}/documents`** listed every document that
  references the file, in any namespace. It lists only documents in namespaces
  the caller can read.

**Who is affected:** apps whose namespace keys read views that draw on another
namespace (give the key read on it), and integrity views run with a key that
cannot read every referenced namespace (references there now show as broken).
Admin and service keys see everything, as before.


### The MCP server acts with each caller's key

The HTTP/SSE transports used to admit only the server's own key (the
install's master key) and then call the backend with it. They now admit any
key the Registry accepts and make every backend call with that same key:
an app's namespace-scoped key stays scoped through the MCP server, and the
server's own key never acts for a network caller. A valid key is
remembered for 30 seconds; if the Registry cannot be asked the request gets
503. The server no longer needs a configured key to start in network mode.
**Action:** none for callers that already send the master key; their calls
act exactly as before. A caller that relied on the server's key while
sending a weaker one of its own now gets that key's authority.

### Namespaces refuse references from writers who cannot read them

Every namespace, existing ones included, now has two reference settings:

- **Inbound — `inbound_references`** (new; default `closed`): a reference
  from another namespace is accepted only when the writing key can read this
  namespace. `open` accepts any writer. `wip` is always open and cannot be
  closed.
- **Outbound — `isolation_mode`** (existing, meaning changed): `open` (the
  default) may now reference **any** namespace, where it was limited to its
  own, `wip` and `allowed_external_refs`. `strict` is unchanged: only itself
  and `allowed_external_refs`.

Previously only the referencing namespace decided, and the referenced one had
no say. A reference must now pass both gates. Only references a write ADDS are
checked: references a document's or template's previous version already held
carry over unchecked, so existing data stays updatable. This also applies to
outbound: a document holding a reference its namespace's `strict` setting now
forbids no longer becomes un-PATCHable.

A refused inbound reference reports exactly like a reference whose target does
not exist: the same error code, message and details, in the same place among
the write's other errors (`validation_failed` with a `reference_not_found`
error on documents; on templates `reference_violation` with
`unresolved_reference`, or `parent_not_found` for a parent, and the same
`invalid_reference` errors at activation). The namespace is named only in the
service log (`Inbound reference refused: …`).

When several of a template's references fail to resolve, the one reported is
now the first in field order (edge-type endpoints: declaration order), not
whichever a batch lookup met first. A template update naming a new parent by
bare value now finds it in the template's own namespace only, as create
always did.

Term and file references on documents are now checked against both gates. They
carried no namespace before, so the outbound rule never saw them.

**Who is affected:** apps and loaders that write references (terms, documents,
templates, terminologies or files, in documents or templates) into another
namespace with a key that cannot read it. They were accepted before and are
refused now. Admin and service keys pass every inbound gate.

**Action:** for each namespace that others must reference, either give the
writing keys read on it, or open it (`PUT /api/registry/namespaces/{prefix}`
with `{"inbound_references": "open"}`, MCP `upsert_namespace`). After the
upgrade, "not found" on a cross-namespace reference whose target exists means
the writing key lacks read on it. Namespace exports and backups carry the
setting; restoring an archive from before it leaves the target namespace's
own setting (`closed` for a namespace the restore creates).

### Namespace settings move from the seeds to the app manifest

An app now declares the namespaces it owns, and their settings, under
`spec.namespaces` in its `wip-app.yaml` (role, name, the env var that
carries the name, settings). The deployer renders every enabled app's
declarations as namespace reservations, which the Registry reads from
`WIP_NAMESPACE_RESERVATIONS_FILE` beside the config-file API keys:

- A key holding `admin` on a reserved namespace may create it through
  `PUT /api/registry/namespaces/{prefix}`, without being a platform admin.
  Any setting it sends must equal the reservation's (403 naming the field);
  on an existing namespace it changes nothing.
- A namespace create, by any caller, takes the settings it leaves out from
  the reservation instead of the platform defaults.
- The deployer refuses two apps reserving one namespace and two
  reservations claiming one ID prefix.

The bootstrap template upserts with no settings and runs its phases across
roles (every namespace, then every role's terminologies, then every role's
templates). A seed `server/seed/namespaces.json` role that still carries
`config` fails the bootstrap before its first write.
**Action:** an app with a `config` in `namespaces.json` moves those settings
to `spec.namespaces` in its manifest and removes `config`, then refreshes
its bootstrap template. An app manifest that sets `WIP_NAMESPACE` (or a
role's env var) under `env` while also declaring it in `spec.namespaces` is
refused; drop the `env` entry.

### Deleting a namespace that other namespaces reference needs `force`

Namespace deletion's inbound-reference check looked at field names no
service stores (`extends_template`, a field's `terminology_id`), so it never
found a reference from another namespace, and a deletion never asked for
`force`. Deleting a namespace silently broke the templates, documents and
relations that depended on it.

The dry run now reports, and a deletion without `force=true` refuses with
400 on, any of these from another namespace:

- `template_extends`: a template whose parent is here;
- `terminology_reference`: a template field drawing on a terminology here
  (`terminology_ref`, `array_terminology_ref`, `target_terminologies`);
- `template_reference`: a template field or edge endpoint naming a template
  here (`template_ref`, `array_template_ref`, `target_templates`,
  `source_templates` / `target_templates` of an edge type);
- `document_reference`: documents referencing documents here, one entry per
  namespace and template with a count of document versions;
- `term_relation`: term relations touching terms here, one entry per
  namespace with a count.

Synonym links stay low severity and do not require `force`.

**Action:** a script or app that deletes namespaces may now get 400 where it
used to succeed. Delete the referencing namespace first, or pass
`force=true` once the dry run shows the breakage is intended.
`@wip/client`'s `deleteNamespace` sends no `force`, so it gets the 400.

### A new namespace's prefix must be lowercase letters, digits, `-` and `_`

Nothing checked a namespace prefix's format. A prefix is embedded in NATS
event subjects, PostgreSQL schema names and URL paths, where a dot, a NATS
wildcard (`*`, `>`) or whitespace shifts or widens the token it becomes.

- Creating a namespace (`POST /api/registry/namespaces`, the creating
  `PUT /api/registry/namespaces/{prefix}`, namespace import, and so every
  restore that creates one) refuses a prefix not matching
  `[a-z0-9][a-z0-9_-]*` with 400.
- An existing namespace keeps its name and stays readable and writable,
  whatever its prefix.
- App manifests' `spec.namespaces` and `spec.namespace_access` already
  required `[a-z][a-z0-9-]*`.

**Action:** a script that creates namespaces with uppercase letters or dots
gets 400. Restoring an archive of such a namespace onto an install that does
not have it fails the same way; use a fresh restore into a valid name.

### Retiring a template version counts only the documents on that version

Deactivating one template version was refused whenever the **template** had
active documents, with the template's total count, even when none were on
that version. So retiring an old version after `migrate_documents`, the
documented lifecycle, needed `force=true`, which also skips the check for a
version that still has documents. Now only active documents on the named
version block it. The refusal names the version and its count
(`details: {rule: has_documents, version, document_count}`). A count that
cannot be read still lets the deactivation through, as before: deactivation
is reversible.

`GET /api/document-store/documents` takes a new `template_version` filter.
**Action:** none. Callers that passed `force=true` only to get past the old
count can drop it.

### The app scaffold runs Node 24 in development and production

Node 20 reached end of life on 2026-04-30. The scaffold's `Dockerfile.dev`
was still `node:20-alpine` while its production `Dockerfile` was
`node:22-slim`, so an app was developed on one Node major and shipped on
another. Both now use Node 24 (active LTS until 2028-04-30):
`node:24-alpine` for development, and `node:24-slim` for both production
stages. The production image stays on slim (glibc), because it copies
`node_modules` (with esbuild's native binary) from the build stage, and it
installs `wget` for the deployer's healthcheck.

The dev entrypoint (`docker-entrypoint-dev.sh`) now re-runs `npm ci` when the
Node version or the libc changes, not only when `package-lock.json` does. A
named `node_modules` volume installed under another runtime used to be kept,
with native packages built for it. Expect one `npm ci` on the first start of
every existing dev volume, because the hash marker's format changed.

**Action (app owners):** a scaffold refresh does not rewrite an existing
app's Dockerfiles or entrypoint. Bump your own `FROM` lines to Node 24 and
copy the new `docker-entrypoint-dev.sh`. Rebuild and re-run your tests:
tooling such as Vitest 5 already requires Node 22.12+.

### New apps develop in their real namespace; the scaffold no longer creates `dev-<slug>`

An app used to be developed in `dev-<slug>` and deployed in `<slug>`, with
mechanisms that existed only to bridge the two names. Namespace reservations
and per-app keys work for any name, so the developer's install now uses the
same name as every other install.

- `create-app-project.sh` defaults the app's namespace to `<slug>` and no
  longer upserts a namespace with the install's master key. The namespace is
  created by the app's bootstrap (with its reservation) or by the developer.
- The generated CLAUDE.md has `## Namespace` instead of `## Dev Namespace`,
  and drops the "recreate the model in a production namespace" steps.
  `.claude/.app-meta` records `APP_NAMESPACE` (an existing `DEV_NAMESPACE`
  is still read), and the engine's `--dev-namespace` is now `--namespace`
  (the old flag is still accepted).
- A refresh keeps the namespace an app was set up with, read from
  `.app-meta` or from the old or new CLAUDE.md heading. No existing app is
  renamed, and no app manifest or namespace was changed.
- `authored_as` in seed namespace roles stays: it lets one app's seeds run
  under another name (a second copy of an app on one install).
- The core and model cards and the conventions text no longer say "dev
  namespace".

**Action:** none for existing apps. A new app's namespace is its slug.

### Event subscriptions and MongoDB `read-all` are for privileged apps only

`event-subscriber` and `replay-subscriber` receive every namespace's events,
because event subjects carry no namespace to scope a subscription by.
MongoDB `read-all` reads every service database. An app holding a key of its
own, scoped to its namespaces, could read past that scope through them.

- The deployer refuses an app manifest declaring any of the three unless it
  declares `privileged_key: true`. React Console qualifies; no other shipped
  app declares them.
- Apps read their own data through the WIP API and reporting-sync, where
  their key's namespace grants apply. `ingest-producer` and `nats-monitor`
  are unchanged.

**Action:** an app manifest outside this repo declaring one of these roles
fails to deploy until it declares `privileged_key` or drops the role.

### Hard-deleting a document that other documents reference needs `force`

In a namespace with `deletion_mode: "full"`, `hard_delete=true` removed a
document even while other documents referenced it, in any namespace and
whatever their own `deletion_mode`. Their references then pointed at nothing.

- A hard delete now first looks for other documents referencing it, of any
  namespace, version or status. If there are any, the item fails with
  `error_code: invalid_state`, `details.rule: "referenced"`, and
  `details.referrers` lists one entry per namespace and template with a
  count of documents. Up to five sample ids are given, only for namespaces
  the caller can read.
- Documents hard-deleted whole in the same request do not count, so a
  document and its edges can be deleted together.
- Hard-deleting one version of several is refused only by `pinned`
  references recorded against that version; deleting the last remaining
  version counts as deleting the document.
- `force=true` together with `hard_delete=true` deletes anyway. The item
  carries a warning and `details.broken_references`. `force` without
  `hard_delete` is still refused with `force_unsupported`.
- The MCP `delete_document` and `delete_documents_bulk` tools take `force`.

**Action:** a script or app that hard-deletes documents may now get a
per-item `invalid_state` error where it used to succeed. Delete the
referrers in the same request, or pass `force=true` once the referrers are
known. `@wip/client`'s `deleteDocument` and `deleteDocuments` send no
`force` yet.

### A `prefixed` ID prefix must be unique on the install

The Registry minted `prefixed` IDs from a counter per (namespace, entity type,
prefix), while every `entry_id` must be unique install-wide. Two namespaces
using one prefix minted the same IDs, and the second one's writes failed
intermittently with a raw MongoDB duplicate-key error.

Every namespace write (`POST /api/registry/namespaces`, `PUT …/{prefix}`,
`POST …/import`) now refuses, before storing anything, an `id_config` whose
`prefixed` prefix:
- another namespace or another entity type of the same namespace already
  uses → `409`, naming the holder;
- is empty, or ends in a digit (`DOC-00` and `DOC-` would both mint
  `DOC-000010`) → `422`.

Re-sending a namespace's stored `id_config` unchanged is never refused, so a
bootstrap resume or a same-name restore keeps working. At startup the
Registry records each namespace's prefixes and logs a warning for any two
namespaces that already share one.
**Action:** look for `id_config prefix conflict` in the Registry log after
the upgrade; give one side of each reported pair another prefix. An import
under a new name onto the install that still holds the source now needs its
own prefix (it would have minted the source's IDs). Apps whose seed
`namespaces.json` carries a prefixed `id_config` and runs on a dev and a
deployed namespace of one install get a 409 at the second namespace — drop
the literal prefix there.

### Upgrade by re-rendering the install, not by swapping image tags

reporting-sync now serves every request through two PostgreSQL service roles
whose passwords are deployment secrets (`postgres-sync-owner-password`,
`postgres-reporting-read-password`). The deployer generates them only when it
renders the install. An install that only gets new image tags has no such
passwords: reporting-sync then refuses to serve PostgreSQL and its `/health`
answers 503.
**Action:** upgrade with `wip-deploy redeploy` (or `install`), never with
`kubectl set image` or an edited compose file alone. On a dev install whose
source is bind-mounted, pulling the source clone is itself a partial rollout:
re-render immediately afterwards.

### reporting-sync requires authentication on every route

Every route answers 401 without an API key or session. The exceptions are the
health probes (`/health`, `/api/reporting-sync/health`) and the build-provenance
root `GET /api/reporting-sync/`. Before this release the whole service,
including raw SQL, answered anonymously.
**Action:** every caller must send `X-API-Key` (or come through the
auth-gateway). The platform's own callers — registry's namespace-deletion
cascade, document-store, the MCP server — are updated.

### Raw SQL and operator routes are admin/service-only; view writes take namespace ADMIN

These routes now require group `wip-admins` or `wip-services`; a
namespace-scoped key gets 403:
- `POST /query` and `POST /export/csv` (raw SQL);
- batch sync and its jobs (`/sync/batch…`), `/metrics…`, `/alerts…`,
  `/health/integrity`, `DELETE /namespace/{prefix}`.

Registering or deleting a view (`POST`/`DELETE /namespace/{prefix}/views…`)
takes ADMIN on the namespace **or** the privileged groups. A namespace-admin
registration is contained: the created view may only depend on relations in
the namespace's own schema (checked against the view Postgres actually
built — `pg_depend`, no SQL parsing); a cross-schema dependency rolls the
registration back with 403. Admin/service keys still register cross-schema
views deliberately. A key with only read or write on the namespace gets 403.

Namespace-scoped keys keep read access to their own namespaces through
`GET /export/csv`, `GET /namespace/{prefix}/views`, `/parity`, `/table-name`,
`/schema/{template}` — and read a registered view's rows through the new
structured endpoint `POST /namespace/{prefix}/views/{name}/rows` (namespace
read; filters as column/op/value triples validated against the view's
columns, bound parameters, sort, limit/offset; response shaped like
`POST /query`; `@wip/client` 0.55.0 ships it as `reporting.readViewRows`).
Another namespace answers 404.
**Action:** apps that ran raw SQL with a scoped key must use the install's
service key for those calls, or move view reads to the rows endpoint. An app
that registers its own views on a scoped key needs ADMIN on its namespace.

### Listings are scoped to the caller's namespaces

`POST /search`, `GET /activity/recent`, `/references/term/…/documents`,
`/entity/…/referenced-by` and `/tables` return only namespaces the caller may
read; `/entity/…/references` answers 404 for an entity in another namespace.
- an admin key sees everything, as before;
- a key with exactly one namespace uses it implicitly;
- a key with several namespaces must pass `namespace` (400 otherwise).

`/status` filters its `recent_drops` to readable namespaces.

### Raw SQL runs as a read-only role whose search path is `public`

`POST /query` runs as `wip_reporting_read` (read-only transaction, statement
timeout). Before, it ran as the bootstrap superuser `wip` with search path
`"$user", public` — so an **unqualified** table name in a query **without**
`namespace` silently resolved to the `wip` namespace's schema. That query now
fails with `relation "…" does not exist`.
**Action:** pass `namespace` (the search path then points at that namespace's
schema) or schema-qualify every table, as the API has always documented.

### Object storage: Garage replaces MinIO (now the default; MinIO retired)

MinIO's images are no longer publicly pullable (docker.io resolves to an
HTML page, quay.io answers 401 anonymously); the platform ran a frozen,
arm64-only copy. **Garage** (dxflrs/garage v2.1.0, arm64+amd64) is now
the platform's object store: the `standard` and `full` presets include
the `garage` module, document-store's manifest reads the Garage endpoint
and deployer-generated credentials directly, and the deployer provisions
the node at apply time (single-node layout, key import, attachments
bucket) — idempotently, with secrets on stdin.

**The `minio` module no longer exists.** An install whose saved spec
still lists it renders without an object store (file storage disabled)
until migrated. The proven migration recipe: full backup → delete the
file-bearing namespace(s) → `add-module garage` → restore → verify
checksums → `remove-module minio` (run against a repo state that still
had the module, or edit the saved spec). All platform installs were
migrated this way or as zero-blob flips (CASE-864 Phase 2: dev-test, kb,
prod-test with 207/207 blobs checksum-verified plus an in-app download,
wip-local). Service images need no change — the storage client is
S3-generic; images ≥ 20261017a carry the required SigV4 presigning.

Two cross-cutting changes ship with it:

- **Presigned download URLs are now SigV4** (they were legacy V2, which
  Garage rejects as anonymous). V4 signs the Host header, so the storage
  route (`/garage`) now forwards the upstream Host at the edge
  (`upstream_host_header` route primitive; Caddy `header_up Host`, NGINX
  `upstream-vhost` — restoring the full in-cluster FQDN on k8s, since
  that is the host services sign against).
  **Action:** presigned downloads require the re-rendered router config —
  one more reason this release upgrades by re-rendering, never by image
  swap alone. A dev install whose document-store hot-reloaded the new
  code serves V4 URLs that fail until the router is re-rendered.
- New route primitive `upstream_host_header` on component manifests,
  honored by both renderers.

### Backup archives reach the object store on Garage installs

Document-store keeps backup and restore archives in their own bucket,
`wip-backups`. Provisioning created only `wip-attachments`, and the files key
may not create buckets, so on every Garage install each archive stayed in
document-store's scratch storage: cleared after `WIP_BACKUP_RETENTION_HOURS`
(default 24) and lost with the container, while the job reported `complete`.

- Provisioning now creates `wip-backups` and grants the files key on it,
  idempotently, next to `wip-attachments`; document-store's manifest names the
  bucket explicitly (`WIP_BACKUP_BUCKET`).
- A backup or restore whose archive could not be stored in the object store —
  missing bucket, refused write, or a store that is down — still completes, but
  the job now carries a warning saying the archive is in scratch only, until
  when, and (for a backup) where to download it.

**Action:** run `wip-deploy redeploy` on each Garage install to create the
bucket. Until then, download any backup you need to keep
(`GET /api/document-store/backup/jobs/{id}/download`) before the scratch sweep
or a container restart removes it.

### Reporting catch-up after a restore no longer slows with the square of a template's size

A batch sync paged each template's documents by offset, and every page made
document-store sort all of the template's documents (the list's default order
cannot use an index): measured on 43,856 documents, the first and the fortieth
page each examined all 43,856. A 115k-document template took about four minutes.

- The batch sync now pages by cursor (`_id` order). A new index on the
  documents collection, `ns_template_status_id_idx`
  (`namespace, template_id, status, _id`), lets each page read one page of
  keys — a test checks the query plan against MongoDB. The template's total,
  for progress, comes from one single-document request.
- A restore's reporting count check waits while reporting rows keep landing,
  instead of a fixed 90 seconds: it gives up after 60 seconds without
  progress (or 30 minutes overall), and its warning names the batch-jobs and
  parity endpoints to watch. Meanwhile the job's progress message shows
  "reporting catching up: X of Y rows".

**Note:** document-store builds the new index at its first start after the
upgrade; on a large collection that start takes correspondingly longer.

### ingest-gateway requires authentication

Every ingest-gateway route requires an API key except
`/api/ingest-gateway/health`; `/status` and `/metrics` used to answer
anonymously. Its outbound key now also falls back to `MASTER_API_KEY`, and the
component manifest sets `API_KEY`, so it no longer writes with the dev default
key on deployed installs.

### MongoDB requires authentication; each client is its own MongoDB user

MongoDB accepted anonymous clients on every target. Any workload that could
reach it could read and write every service's database, bypassing every
guardrail WIP enforces.
- **Per-client users.** MongoDB now serves with `--auth`. Each client
  connects as its own user:

  | Client | Access |
  |---|---|
  | def-store, template-store | their own database |
  | registry | its own database, plus only `find` / `remove` on the seven collections namespace deletion clears in the other databases |
  | document-store | all four (backup reads them; restore writes raw rows) |
  | react-console | `read` on all four (admin scope, read-only) |

  Apps can never write to MongoDB: WIP data changes only through WIP's API.
- **Where it is declared.** Components declare a `mongo_client` block
  (`databases`, `collection_privileges`); apps declare `roles: [read-all]`.
  The deployer generates `mongo-password-<client>` and `mongo-root-password`,
  injects `MONGO_USER` / `MONGO_PASSWORD` into each client and the passwords
  into MongoDB, and renders MongoDB's start script
  (`config/mongodb/entrypoint.sh`; k8s ConfigMap `wip-mongodb-entrypoint`),
  which holds no secrets.
- **How users get there.** On every start, the script creates or updates
  root, the users and the custom role through a temporary `mongod` without
  `--auth` on 127.0.0.1:27018 (unreachable from other containers). It then
  restarts MongoDB with authentication. Users the step created that are no
  longer declared are dropped (an app removed).

  A fresh data directory, an existing one and a changed user set take the
  same path, so **an existing install comes up authenticated on its next
  start**. No migration step, and no backup → fresh → restore needed.
- **Credentials stay out of the logs.** The services print `MONGO_URI` at
  startup. Credentials therefore travel as client arguments (`wip_auth.mongo`),
  never inside the URI.

**Action:** re-render with `wip-deploy redeploy`.
- **Images first:** the credentials come from `wip_auth.mongo` and the
  services' own code, both baked into images. On k8s and compose, re-render
  together with images built from this release. With older images, MongoDB
  refuses every service, which leaves the platform down. Dev installs
  bind-mount the source and are not affected.
- **react-console** needs its own `MONGO_USER` / `MONGO_PASSWORD` support
  (WIP-ReactConsole). Until then its MongoDB page connects anonymously and is
  refused.
- **Verification:** `wip-deploy verify --security` now fails an install
  whose MongoDB runs without authentication.

### NATS requires authentication; each service is its own NATS user

NATS accepted anonymous clients on every target. Any workload that could reach
it could read every change event, publish forged events that reporting-sync
applied, delete the streams, and publish ingest work items that ingest-gateway
forwarded with the platform's admin key.
- **Per-service users.** NATS now refuses anonymous connections. Each NATS
  client (def-store, template-store, document-store, reporting-sync,
  ingest-gateway) connects as its own user and may publish only its own event
  subjects and use only the JetStream API it needs. No service can delete or
  purge the platform streams (`WIP_EVENTS`, `WIP_INGEST`,
  `WIP_INGEST_RESULTS`). document-store may create and delete streams in
  general, which replay needs (`WIP_REPLAY_<session>`); the platform streams
  are denied to it explicitly. Each user reads replies only on its own inbox
  prefix (`_INBOX_<user>`).
- **Where it is declared.** Components declare a `nats_client` block (their
  permitted subjects). The deployer generates a `nats-password-<component>`
  secret per client, injects `NATS_USER` / `NATS_PASSWORD` into the client
  and the passwords into NATS, and renders `config/nats/nats.conf` (k8s:
  ConfigMap `wip-nats-config`). The config holds no secrets: it references
  the passwords as environment variables.
- **JetStream store on its volume.** NATS kept its JetStream store in
  `/tmp/nats/jetstream` inside the container, and the 5 GiB volume mounted at
  `/data` stayed empty. Every NATS restart therefore lost all streams:
  unconsumed events, the ingest backlog, replay streams. The store is now
  `/data/jetstream` (CASE-891).
- **Monitoring on localhost.** The monitoring endpoint (port 8222, no
  authentication, `/connz` lists every client) listens on 127.0.0.1 only.
  It is no longer a Service port on k8s, and the healthcheck runs inside
  the container.
- **Ingest is written with the producer's key.** ingest-gateway forwarded
  every work item with the install's master key, and it had no outbound key
  of its own beyond that. Now a producer puts its WIP API key in the
  message's `X-API-Key` NATS header and the gateway writes with exactly that
  key, so the producer's namespace grants apply. **A message without the
  header is refused** (published as a failed result with
  `http_status_code: 401`) and nothing is written. The gateway no longer
  receives `API_KEY` / `DOCUMENT_STORE_API_KEY`.
- **Apps get NATS through roles.** An app manifest declares
  `nats_client: {roles: [...]}` with any of `ingest-producer`,
  `event-subscriber` (one durable on `WIP_EVENTS` named after the app, which
  sees every namespace), `replay-subscriber` (a plain subscription to
  `wip.replay.>`) and `nats-monitor` (read-only stream and consumer
  listings, no message contents). The deployer expands each role to fixed grants and injects
  `NATS_URL` / `NATS_USER` / `NATS_PASSWORD`. Raw subject lists are rejected
  in app manifests. react-console holds `nats-monitor` for its NATS page,
  which needs RC's credential support (WIP-ReactConsole `3c989bd`). An RC
  image without it connects anonymously and NATS refuses it. See
  `docs/wip-deployable-app-contract.md` ("NATS access is a role").
- **Clients outside an app manifest are refused.** Scripts and external
  services have no NATS user.
- **Diagnosing a refusal.** A publish the server refuses shows up in the
  client as a timeout. NATS logs `Publish Violation - Subject "<subject>"`
  with the user name.

**Action:** re-render with `wip-deploy redeploy`.
- **Images first:** the services send credentials from `wip_auth.events`,
  which is baked into their images. On k8s and compose, re-render together
  with images built from this release. Re-rendering while services still run
  older images makes NATS refuse all of them: no events, no reporting sync,
  no ingest. Dev installs bind-mount the wip-auth source and are not
  affected.
- **Rollout order:** a client with credentials can still connect to a NATS
  server that doesn't enforce them yet. So on k8s the order the pods roll in
  doesn't matter, apart from a brief window in which old, credential-less
  pods are refused. They queue their events (see below) until replaced.
- **Lost streams:** the first start with the store on the volume begins with
  empty streams, as every NATS restart did before. Run a reporting batch sync
  afterwards if reporting-sync had not consumed everything. Unprocessed
  ingest items are gone.
- **Verification:** `wip-deploy verify --security` now fails an install whose
  NATS accepts anonymous clients.

### NATS connections heal themselves; `WIP_EVENTS` has one definition

def-store, template-store, document-store, reporting-sync and ingest-gateway
share one NATS connection module (`wip_auth.events`). Before, a store whose
first connect failed published nothing until restarted, and any client gave
up after about two minutes of NATS outage — writes kept succeeding while
reporting silently diverged.
- **Startup never waits for NATS.** Services connect in the background and
  reconnect without limit; a closed client is rebuilt. ingest-gateway no
  longer crashes on start when NATS is down.
- **Events survive short outages.** While NATS is unreachable, events wait in
  a per-process retry queue (10,000 events, 1 hour) and are published in
  order once it is back. Beyond those bounds, or across a restart, events are
  dropped and counted; repair with a batch sync, as before.
- **Consumers are supervised.** The reporting-sync and ingest-gateway workers
  start whenever NATS is connected, and restart when their consumer loop
  dies (a missing stream, a deleted durable consumer).
- **`WIP_EVENTS` converges on every (re)connect** to one definition: subjects
  `wip.documents/files/templates/terminologies/terms/term_relations.>`,
  1,000,000 messages, 1 GiB, **7 days**. Missing subjects are added (file
  events had no stream on installs where reporting-sync created it first);
  subjects added by others are kept.
  **Action:** on such installs the stream had no age limit, so the first
  start purges stored events older than 7 days. reporting-sync normally
  processed them long before; if its consumer is days behind (check
  `/api/reporting-sync/metrics/consumer`), run a batch sync after upgrading.
- **Visibility.** `/health` reports `"nats"` as `connected`, `connecting`,
  `reconnecting` or `not_configured` (was `connected` / `disabled`).
  `GET /api/<service>/health/events` (wip-admins / wip-services) returns the
  connection state, published / queued / dropped-by-reason counters and the
  supervised consumers' restarts. `/health` stays HTTP 200 regardless: the
  event pipeline's state is for observability, not routing (a store with
  NATS down still accepts writes and queues their events).

### Terms carry typed names; aliases and translations are now projections

A term is now a code plus a set of **names**: each name carries `value`,
optional `type` (validated against the new seeded, extensible `_NAME_TYPES`
terminology — INN, TRADE_GLOBAL, TRADE_LOCAL, CODE, REGISTRY_ID, SYNONYM,
ABBREVIATION), optional `language` and `source`, and a `preferred` flag (at
most one per (type, language) group). The term API keeps accepting
plain-string `aliases` (stored as untyped names) and `translations` (stored
as language-bearing names), and responses serve derived `aliases` /
`translations` projections alongside `names` — but four behaviors change:

- **Translated labels now resolve.** Every name participates in validation
  and document term resolution; `translations` used to be display-only
  payload, invisible to validation, document-store and reporting. A value
  that collided with a translated label and was invalid before is valid now.
- **Exact-duplicate names are rejected loudly** (per-item
  `error_code='invalid_names'`), as are a name equal to the term's own
  value, an unknown `type`, and two preferred names in one group.
  Case-variant names (`'MR.'` vs `'Mr.'`) stay legal. Seeds that submitted
  literal duplicate aliases must deduplicate.
- **The `aliases` projection covers every name** — including translated
  labels — so consumers keying on it see more entries than before.
- **Term resolution gained value precedence**: every term's canonical value
  is checked before any term's names (def-store validation and
  document-store's local lookup both), so a name can no longer shadow
  another term's canonical value — duplicate-alias resolution used to
  depend on storage/pagination order. The validate response carries the
  matched name (with type and language) as `matched_name`.

Reporting gains a **`term_names` table** per namespace schema (one row per
name: `value`, `type`, `language`, `source`, `preferred`), synced by event
and batch paths, exported and listed like the other fixed tables. OBO
import keeps each synonym's predicate in the name's `source` and no longer
caps synonyms at 10 silently — `max_synonyms` defaults to unlimited and
reports `dropped_synonyms` when a cap is passed. `@wip/client` 0.56.0
ships the `TermName` types and `matched_name`.
**Action:** none for most installs — the stored data migrates itself (see
first start below) and legacy API shapes keep working. Review seeds for
literal duplicate aliases, and any consumer that assumed `aliases`
excluded translations.

### Ambiguous term names are visible, and identity fields refuse them

A name borne by several terms used to resolve silently to whichever term
storage order served first. Three changes make the ambiguity loud:

- **Validation reports every bearer.** `/validate` responses carry
  `matches` (how many distinct terms bear the submitted value) and
  `candidates` (each bearer with its matched name). A single hit keeps
  the previous shape; a value match is unambiguous by construction.
- **Document writes fail closed on ambiguous identity fields.** A term
  identity field resolving an ambiguous name is rejected (per-field code
  `ambiguous_term`) — the arbitrary pick would otherwise feed the
  identity hash. On ordinary fields the first bearer still resolves,
  now with a warning naming all candidates.
- **Authoring warns at the source.** Creating or updating a term whose
  value or names collide with another active term's value or names (per
  the terminology's case sensitivity, including collisions inside one
  bulk import) reports them in the per-item `details.name_collisions`.
  A terminology declaring `unique_names: true` rejects such writes with
  `error_code='name_collision'` instead.

**Action:** none — defaults warn, nothing previously accepted is rejected
except ambiguous names on identity fields, which were producing
storage-order-dependent identities and should be corrected, not relied on.

### Template writes that change nothing report `unchanged`

- **`PUT /templates`** reports `status: "unchanged"` (with the current
  `version` and `is_new_version: false`) when the update mints no version. It
  used to report `"updated"` for every successful item, so a caller acting on
  `updated`, for example by deactivating the previous version, acted on a
  no-op.
- **An identical edge-type re-post** (create-as-upsert) returns `unchanged`.
  An edge type's `source_ref` / `target_ref` constraint is derived from its
  endpoint declaration when the template is served, so the copy a caller
  sends on those fields is neither compared nor stored. A bootstrap that
  re-posts the served shape used to mint a new version on every run.

**Action:** none. Installs where a bootstrap already minted duplicate
edge-type versions keep them; deactivate the extras by hand if wanted.

### Conditional rules and nested fields are validated on write

Two declarative checks that passed silently now apply to every document
write and to `/validate`:

- **`conditional_required` rules fire.** Template-store serves every
  optional rule key, an unset `required` as `null`, and document-store read
  that null as "not required", so the rule never applied. Unset now means
  required; only an explicit `required: false` turns a rule off. Rules with
  a null `allowed_values` (`conditional_value`) or `target_fields`
  (`mutual_exclusion`) no longer raise.
- **Fields inside nested templates are resolved like top-level ones.** Term,
  reference and file fields of an object field's `template_ref` or an object
  array's `array_template_ref` used to be type-checked only: an unknown term
  was accepted, a reference was never existence-checked, and nothing was
  recorded. They now go through the same checks as top-level fields, and
  their resolutions are stored under the nested path
  (`ingredients[0].ingredient`, `address.country`) in `term_references`,
  `references` and `file_references`. Reporting keeps them in
  `term_references_json`; nested fields get no per-field `_term_id` column.
  Identity hashing is unchanged: a dotted identity path into an object field
  still hashes its submitted value.

**Action:** documents already stored with a value these checks reject (an
unknown nested term, a dangling nested reference, an imported document
missing a conditionally required field) stay as they are, but their next
update is rejected until the value is corrected. To find them first, run
`POST /api/document-store/validation/validate` over the existing documents
of each namespace that uses nested templates or conditional rules.

### Explicit null clears optional term and terminology fields

Term updates align with the platform's RFC 7396 vocabulary: an EXPLICITLY
submitted `null` clears `description`, `label` (display falls back to the
value) and `parent_term_id`, and resets `sort_order` to 0 — while an
OMITTED field keeps meaning "unchanged", so nothing existing breaks.
`value` and `names` reject explicit null loudly (`names` clears with
`[]`). Terminology `description` clears the same way. Reconcile snapshots
may carry explicit nulls with the same meaning, idempotently — and an
explicit null also normalizes the historical `""` workaround to a truly
cleared field. The MCP `update_term` tool exposes this as a `clear`
parameter. Until now there was NO way to remove a term's description;
consumers treating `""` as absent can drop that shim once their data is
normalized.

### Merging a newer backup into an older namespace works; merges carry term deprecations

A merge (`mode: merge`) of a newer backup of a namespace into an older state
of it was refused whenever a term had been added since. The terminology's
`term_count`, a count def-store derives from its active terms, was compared
as content. `extend_terminologies`, the flag the refusal suggested, could
not get past that comparison. A term deprecated in the newer backup was
refused too, because its lifecycle fields differed.

Now:
- `term_count` is not compared, and the merge recounts it with def-store's
  rule (active terms) after it adds or retires terms. A merged terminology
  ends with the right count.
- **A term the archive retired** (deprecated or inactive) that the target
  still has active is retired on the target, with the archive's reason and
  replacement (mapped to the target's term ids).
- **Retirement travels one way:** a term the target retired stays retired,
  and the difference is reported like other target-wins fields.

A new template version in the newer backup was not affected; it merges with
`add_missing`.

### A merge asks what happens to the target's older versions; `@wip/client` 0.65.0, WIP-Toolkit 0.7.1

A merge with `on_clash: overwrite` or `newer` appends the archive's latest
version on top of the target's. It used to leave the target's older version
`active` beside it, unlike a normal write, which retires the version it
supersedes. The document then had two active versions, and the post-merge
parity check waited its full 90 seconds and warned.

The new merge option `older_versions` makes it a choice:
- `retire` (default): the target's older versions are soft-deleted, as a
  normal write does.
- `keep`: they stay active beside the new head.

It is accepted by the upload restore and restore-from-job endpoints, the MCP
`start_restore` tool, and `@wip/client` 0.65.0 (`RestoreOptions.older_versions`,
type `OlderVersionsPolicy`). It is recorded in the restore provenance record's
`merge` block. Outside `mode: merge` a non-default value is rejected, like the
other merge options.

WIP-Toolkit 0.7.1 adds `wip-toolkit restore`, a thin client of the restore
endpoint: it uploads the archive, starts the job and follows it, with every
mode and merge option as a flag (`--older-versions retire|keep` among them).
It also fixes `wip-toolkit update-document`, which sent its PATCH to
`/api/document-store/api/document-store/documents` (the service prefix
twice), a path the document-store does not serve.

**Action:** callers that relied on the old behaviour (older versions staying
active after an overwrite merge) pass `older_versions: keep`. Apps re-vendor
`wip-client-0.65.0.tgz` to get the typed option.

### A restore keeps document-store responsive while it plans; identity synonyms re-qualify in batches

- **Planning off the event loop.** A merge restore, dry run included, read and planned the whole archive synchronously on document-store's event loop, in two full passes. The single worker then answered nothing, `/livez` included, for as long as planning took: about 45 minutes on a 463,529-version archive before CASE-959. A fresh restore's planner did the same per entity type. These passes now run on a worker thread. The engine's progress events, whose sink is an `asyncio.Queue` and not thread-safe, are handed back to the loop from there.
- **Batched re-qualification.** The identity-synonym re-qualification every restore runs (and `/maintenance/requalify-identity-synonyms`) made two single-item Registry requests per document: about 90 documents a second, roughly 40 minutes for 222k documents. It now sends one add request and one remove request per batch of 500. A bare synonym is still removed only after its qualified form was added. A failed remove is now reported as an error: before, it counted as re-qualified and left the bare synonym in place. A restore reports the step as `phase_requalify` with per-batch progress, instead of sitting silently in `phase_synonyms`.

### `wip-deploy install`: a wrong repo root, a registry URL and a missing `--hostname` stop with the cause

One k8s install hit three silent defaults at once:

- **Wrong repository root.** Run from another git repository, `install` (and every verb that builds a fresh spec, such as `validate` and `render`) took that repository as the WIP checkout. Validation then reported every preset module as `unknown component (known: [])`. These verbs now stop with the same message the additive verbs already gave: `discovery found no components/apps under <root> — this is not a World-in-a-Pie checkout`, followed by the ways to point at the right one. The check is one helper shared by every path.
- **Registry with a scheme.** `--registry https://ghcr.io/x` was taken verbatim, and every image reference (`<registry>/<name>:<tag>`) would have failed to pull. `images.registry` now refuses a URL scheme, a trailing slash or surrounding spaces, naming the value to use (`use 'ghcr.io/x'`). A port is fine: `localhost:5000/x`. Saved specs are checked when they load.
- **No `--hostname`.** `install --target compose|k8s` silently used `wip.local` as the Ingress host, the OIDC issuer and the TLS certificate's name. The result was an install that looked healthy and could not be logged into. It is now refused. `--target dev` keeps `localhost`, and the preview verbs keep `wip.local`. A re-run over an existing install still shows the existing-install guard first; `--reconcile` without `--hostname` is refused too, so a reconcile cannot move a live install to the placeholder.

**Action:** scripts that run `wip-deploy install` on compose or k8s without `--hostname` now pass it explicitly. Fix any `--registry` value with `https://` or a trailing `/`.

### Dex users keep their existing passwords: `user add --password-hash-file`, `user import`

Every redeploy-family verb renders Dex's users from the spec, so users
created by hand in the rendered `config.yaml` were lost on the next render.
The only supported way to declare them was `user add`, which generates a new
password, so every existing user had to be given one.

A user can now be declared with an existing bcrypt hash. In the spec this is
`auth.users[].password: supplied` (default `generated`; saved specs load
unchanged).
- Only the hash secret `dex-password-<username>.bcrypt` exists for such a
  user. It is operator-supplied: the deployer never generates it, and an
  apply that finds it missing stops and names the user.
- `wip-deploy user add … --password-hash-file <file>` declares one user.
- `wip-deploy user import <file>` declares many in one apply, from a Dex
  config (its `staticPasswords`) or a CSV file (`username,email,hash` and an
  optional `groups` column). Groups come from the CSV column, then
  `--groups-file` (YAML username → groups), then `--group`.
  - Users already declared are left as they are; any other problem refuses
    the whole import.
  - `--dry-run` previews it.
  - If the apply fails, the stored hashes are removed again.
- `user list` shows such users as `supplied hash`.

**Action:** an install with hand-made Dex users runs `wip-deploy user import
<saved dex config> --group … --groups-file …` once; after that, re-renders
keep them.

### The app scaffold no longer demands a version bump for a rebuilt, unchanged wheel

The scaffold refuses to ship a vendored wheel (`wip-toolkit`, `wip-archive`)
whose content changed under an unchanged version, because pip never reinstalls
a version it already has. It compared raw bytes. A newer build backend stamps
its own version into the wheel (`Generator:` in `*.dist-info/WHEEL`), so an
unchanged `wip-archive` 0.3.0 rebuilt with hatchling 1.33.0 instead of 1.32.4
aborted an app refresh and asked for a version bump nobody owed.

- The check compares what a wheel ships: every member byte for byte, except the
  `Generator:` line and `RECORD` (which only hashes the members). A real code or
  `METADATA` change under an unchanged version is still fatal. When only the
  stamp differs, the app keeps the wheel it has.
- Both wheel projects pin `hatchling==1.33.0` as their build backend, so an
  unchanged tree rebuilds to identical bytes.

**Action:** an app whose refresh stopped on `wip_archive-0.3.0 … different
content` re-runs `create-app-project.sh .`; no version bump is needed.

### Reporting parity counts live documents on both sides

The parity check (`check_reporting_parity`, and the count phase at the end of
every restore) compared the number of active document VERSION rows in MongoDB
with every row in the reporting tables. Two correct states failed it:
- **A document soft-deleted after reporting had synced it:** its row stays,
  with status `deleted`, and was counted. Such a namespace reported a
  mismatch, and a merge into it waited the full 90 seconds and warned.
- **A document with several active versions** (left by a merge with
  `older_versions: keep`): counted once per version on the MongoDB side.

Both sides now count live documents: documents whose latest version is active
(the `latest_only=true` count), against reporting rows that are not `deleted`.
`all_versions` templates are still not compared.

The expected side also counts only the checked namespace's documents. A
template's documents created by another namespace (a cross-namespace template
reference) live in that namespace's reporting schema, so counting them here
reported a gap that no sync could close.

### Reporting parity covers term tables and registered views, and repairs gaps by itself

The parity check only looked at document tables. A namespace whose
`term_relations` and `term_closure` were empty after a restore, while its
terms were back, reported `ok: true`. Two changes:
- **Parity checks everything reporting derives.** Every registered view must
  exist (`views`). With counts, `derived_tables` compares active `terms` and
  `term_relations` rows with def-store's active totals, checks that every
  named term has `term_names` rows, and checks `term_closure`'s reflexive rows
  against what its rebuild writes. Failures are counted in `derived_issues`
  and named by an issue code. `ok` is true only when all of it passes, so the
  count phase at the end of a restore now waits for, and warns about, these
  tables too.
- **The namespace batch sync (`POST /sync/batch`) syncs term relations.** Its
  definitions companion synced terminologies and terms but not relations,
  which is how a restore left `term_relations` and `term_closure` empty. It
  now syncs relations after the terms, ending with a closure rebuild.

**Reporting-sync now checks parity on its own and repairs gaps.** Every 300
seconds it runs the check over every active namespace. A gap seen on two
consecutive passes is repaired with the narrowest sync that closes it: the
template's batch sync, the term or term-relation sync, a closure rebuild, or
re-applying registered views. Each gap gets at most 3 repairs, and a
namespace with a batch sync running is skipped. A gap that does not heal, or
that has no safe repair (a legacy table, old-shape bookkeeping), is logged
once at error level. `GET /api/reporting-sync/parity/status` (admin or
service key) shows the last pass and the recent repairs. To turn it off, set
`PARITY_CHECK_INTERVAL_SECONDS=0` in reporting-sync's environment.

**A restore's job says whether reporting was verified.** A restore that gave
up waiting for reporting used to end `complete` like a verified one, the
difference only in a free-text warning. A completed restore or merge now
carries `result.reporting_verification` on its job snapshot: per namespace
written, `verified`, `incomplete` (with the gaps), `unavailable` or
`not_checked`, and the worst of them as `status`. A fresh restore
(`namespace_map`) reports `not_checked`: it does not wait for reporting.

**Action:** a script that treats parity `ok` as its pass condition may now
see `false` on a namespace that passed before. A script that waits for a
restore can now check `result.reporting_verification.status == "verified"`. Read `derived_tables` and
`views`; a re-run of the namespace batch sync repairs the term tables, and
re-registering repairs a missing view.

### `latest_only` + `status` filters on each document's latest version

With `GET /api/document-store/documents?latest_only=true`, a `status` filter
ran before the latest version of each document was picked. So
`status=inactive` returned each document's highest *inactive* version: the
superseded versions of live documents came back next to the soft-deleted
ones, and `total` and `pages` counted them. The status filter now applies to
each document's latest version, so `latest_only=true&status=inactive` lists
exactly the soft-deleted documents. `status=active` and calls without
`latest_only` are unchanged. This also affects the MCP `list_documents`
tool, which defaults to `latest_only=true`.

`latest_only=true` with a `cursor` used to ignore the cursor silently and
page by `page`. It is now refused with 422.

### Document archive is removed; `@wip/client` 0.63.0, `@wip/react` 0.23.0

Document "archive" is gone. It was designed as policy-driven tiering of
superseded versions, which was never built; what shipped archived the latest
version on demand and behaved as a second soft delete, with rules that
contradicted delete's. Soft delete replaces it.

**Removed, in one step (no deprecation window):**
- `POST /api/document-store/documents/archive` and the `document.archived`
  event;
- the `archived` document status and the PATCH `archived` error code;
- `archived_relationship_endpoint`;
- the MCP `archive_document` tool;
- `@wip/client` `documents.archiveDocument` (0.63.0) and `@wip/react`
  `useArchiveDocument` (0.23.0).

Namespace archive (`POST /namespaces/{prefix}/archive`) is a different
feature and is unchanged.

**Existing data:**
- document-store rewrites every `archived` document row to `inactive` at
  startup;
- reporting-sync rewrites `status = 'archived'` reporting rows to `deleted`
  at startup;
- a restore writes an `archived` row from an older backup as `inactive`.

File reference counts need no fix-up: archive released them exactly as soft
delete does.

**Relationships:** an edge whose endpoint is soft-deleted is now accepted
with a warning, the same rule a plain reference to an inactive document
follows. Before, an archived endpoint was refused while a deleted one was
accepted.

**Action:**
- **Callers of the removed endpoint, method, hook or tool break on upgrade.**
  Use soft delete instead: `DELETE /api/document-store/documents`,
  `documents.deleteDocument`, `useDeleteDocument`, `delete_document`.
- **Apps re-vendor** `wip-client-0.63.0.tgz` and `wip-react-0.23.0.tgz`.
  WIP-RC must remove its Archive action and Active/All archive toggle.
- **Replayed `document.archived` events.** If an install's NATS stream still
  holds one, reporting-sync now handles it like any document write and stores
  the payload's `archived` status. Startup rewrites existing rows only.

### Re-posting a deleted document brings it back as a proper successor

Re-posting the identity of a soft-deleted document always brought it back,
but by accident. The create path looked only for an active version, took
the write for a brand-new document, collided with version 1, and the
duplicate-key retry pushed it to the next version. The revived document
reported `updated`, but its `created_at` was reset to the re-post time and
its `metadata.custom` was dropped.

A write now follows the identity's latest version, whatever its status. A
soft-deleted document comes back as its successor:
- it keeps the entity's `created_at`;
- it carries `metadata.custom` forward unless the caller supplies it;
- it reports `updated`;
- it does not release the deleted version's file references a second time.

The revival happens even when the data is identical. On a `versioned: false`
template the document comes back in place, at the same version. PATCH on a
deleted document still answers `not_found`: bringing a document back takes
a full re-post.

### A retired entity releases its synonyms; a soft-deleted document no longer blocks a new identity

A soft-deleted document kept its Registry synonyms claimed. A new document
whose identity spellings matched it, but whose identity resolved differently,
was refused with `Composite key already registered under different entry`.
The typical case is a term identity field whose term was hard-deleted and
re-created. Deleting the documents too worked only in a `deletion_mode: full`
namespace.

Retiring an entity now releases its synonyms. Retiring means:
- soft-deleting a document or a term;
- deprecating a term;
- soft-deleting a terminology, which retires its terms with it;
- deactivating the last active version of a template.

A released synonym stays recorded on the entity's Registry entry with
`status: "inactive"`. It no longer resolves, and it no longer blocks another
entity from registering the same key.

What is not released:
- the entity's canonical ID;
- its primary key;
- its own `{ns, type, …}` value synonym.

So a retired entity stays addressable by ID and by value, and re-posting a
deleted document's identity still revives it.

Reviving an entity claims its synonyms again. Reviving means:
- re-posting a document's identity;
- reactivating, re-posting or activating a template version;
- restoring a terminology.

A synonym that another entity took in the meantime stays with that entity.
The revived document's write result carries a warning naming the holder.

**Registry:**
- `POST /api/registry/entries/deactivate` (with `dry_run`) and
  `/entries/reactivate` are new. They are bulk and privileged, and the stores
  call them.
- Lookups, `/resolve`, search and registration dedup ignore inactive
  synonyms.
- A merge moves an inactive synonym as a record only. It never takes over
  the claim, which may belong to another entry by then.
- A term merge does not release the absorbed term's synonyms: they move to
  the survivor and stay live.

**Restores:**
- Every restore mode ends by releasing the synonyms of the entities it
  restored as retired. This also covers archives taken before this change.
- An id-preserving restore claims no inactive synonym.
- A fresh restore does not re-add a synonym the archive holds inactive. On
  that path the record of an already-released synonym is not carried over.
- `/maintenance/requalify-identity-synonyms` keeps a retired document's
  synonym released in its new qualified form.

**Action:** documents, templates and vocabularies retired before this release
still hold their synonyms. Run the new pass per namespace, dry run first:
`POST /maintenance/reconcile-synonym-lifecycle` with
`{"namespace": …, "dry_run": true}`. It exists on document-store,
template-store and def-store, and requires namespace admin. `revive: true`
also reclaims the synonyms of active entities; it is applied only, never on a
dry run.

A failed Registry call during a delete or revival does not fail the write:
- document-store reports it as a warning on the item;
- template-store and def-store log it.

The pass repairs both.

### A value that names several entities is refused, never resolved to one of them

A bare value used to resolve through the Registry's `search_values` fallback
with a single `find_one`, which returned whichever matching entry came first.
`search_values` holds every value of an entry's active keys. So a value was
ambiguous whenever several entries carried it:
- the same identity spelling under two templates;
- a template's value, which every one of its documents carries in its
  auto-synonym.

In those cases a reference resolved to an arbitrary entity.

**Registry:** `POST /api/registry/entries/lookup/candidates` is new, bulk, and
gated by read access. For each value it returns every entry the value
resolves to, with `matched_via` and `matched_composite_key` (the qualifier),
plus `total` and `truncated`. An exact entry ID is the only candidate.
`/lookup/by-id` is unchanged.

**Document reference fields:** a bare string reference resolves through the
candidates. The field's `target_templates` narrow them. One survivor resolves.
Several fail the write with the per-field code `ambiguous_reference`, which
lists `{document_id, template_value, matched_composite_key}` per candidate.

**Other callers:** `resolve_entity_id` (`wip_auth.resolve`) raises
`AmbiguousEntityError`, a subclass of `EntityNotFoundError`, so callers that
answer 404 still do, with a message naming the candidates. The non-strict
batch path leaves an ambiguous value unresolved.
`RegistryClientBase.resolve_identifier` raises `AmbiguousIdentifierError`, a
`RegistryError`.

**Action:** a write or lookup that relied on the arbitrary pick now fails. Use
the canonical ID or a qualified value, or give the reference field
`target_templates` that single out the intended document.

### Merge and fresh restores stream the documents

Only a plain `restore` streamed the archive. A `merge` and a `fresh` restore
read every document version row into memory before writing, so their peak
memory grew with the archive's document payload and large restores crashed
memory-limited pods. Both now plan from a compact row per document and read
each document's version chain again, one at a time, where it is checked and
written.

Measured on synthetic archives (Python heap): a merge's planning input for
40 000 documents x 2 versions went from 201.9 to 43.1 MiB; a fresh restore's
planning phase from 106 to 20.4 MiB, and with ten times larger documents
from 449 to 20.5 MiB. What remains grows with the number of documents and
registry entries, not with their size. Definitions (terminologies, terms,
templates) are still held whole.

**Action:** none. An archive whose documents are not sorted by
`document_id` (exported before the backup sorted them, July 2026) cannot be
streamed this way; it restores as before, holding the documents in memory,
with a warning naming the archive. A new backup of the source streams.

### Operator-supplied secrets reach their apps (`ANTHROPIC_API_KEY`)

An optional env var with `from_secret` was filled only when the deployer
collected that secret, and it collects for required vars only, so
`ANTHROPIC_API_KEY` never reached react-console, wip-kb, wip-aa, wip-cooking,
wip-val, dnd, song or dev-kb, even with `secrets/anthropic-api-key` in the
install's store, and nothing said so. A manifest now marks such a var
`supplied_by: operator`:

- the deployer never generates the secret; it fills the var whenever the
  secret is in the store, and every apply and `render` prints a `note:`
  naming the app, the var and the expected file when it is absent;
- deployer-generated optional secrets (the Garage keys on document-store)
  keep the old rule, so a key file left behind by `remove-module garage` is
  not handed to document-store;
- operator-supplied secrets are exempt from the secret-store drift check:
  the operator adds, rotates and removes them on purpose.

**Action:** to give an app its Anthropic key, put it in
`~/.wip-deploy/<install>/secrets/anthropic-api-key` and re-apply
(`wip-deploy redeploy`). App manifests outside
this repo that read an operator-placed secret need `supplied_by: operator`.

### Group mappings and connectors no longer restart the backend

The federated group policy reached the six token-validating services and
the auth-gateway as `WIP_AUTH_FEDERATED_GROUPS`, read at start, so every
`wip-deploy group-map` and `connector` change applied the whole install.
Now:

- the policy is a file, `federated-groups.json`, next to `api-keys.json` in
  the mounted `/etc/wip` directory, rendered on every install (empty
  without connectors). The services take it from
  `WIP_AUTH_FEDERATED_GROUPS_FILE` and re-read it every 10 s
  (`WIP_AUTH_FEDERATED_GROUPS_FILE_RELOAD_SECONDS`); the gateway re-reads
  it when it changes. A file that does not parse keeps the previous policy;
- `group-map add|remove` restarts nothing; `connector add|remove` restarts
  only Dex. auth-gateway mounts `/etc/wip` again, for this file.

**Action required:** as for the declared keys, the re-read is in the
service images. Re-render with this release's image set; an install that
keeps older images loses its federated group policy, because those services
still look for `WIP_AUTH_FEDERATED_GROUPS`, which the deployer no longer sets.

### Declared API keys apply without restarting the backend

Adding, removing or rotating a declared key (`auth.api_keys`) used to
restart every backend service, because each one's config hash covered the
rendered `api-keys.json`. Now:

- every service that checks keys (registry, def-store, template-store,
  document-store, reporting-sync, ingest-gateway) re-reads the file while
  running, every 10 s (`WIP_AUTH_API_KEYS_FILE_RELOAD_SECONDS`, `0` turns it
  off), and swaps the config keys in one step. A file that does not parse
  keeps the previous keys and logs CRITICAL;
- the file is mounted through its directory (`/etc/wip`), not as a single
  file or k8s `subPath`, so a running container sees it change. On k8s the
  kubelet refreshes the mount first: about 80 s measured on the platform
  cluster;
- `api-keys.json` is no longer part of the config hash, so nothing restarts
  for a declared key. auth-gateway and mcp-server no longer mount the file;
  they never read it;
- `rotate-key` names the secret file instead of printing the new key;
  `--show-key` prints it. For a declared key it says how the key arrives,
  and its post-rotation check waits up to 150 s;
- an MCP server configured with `WIP_API_KEY_FILE` follows a rotated key
  file: outbound calls use the new key and the network transports accept it,
  without a reconnect.

**Action required:** the reload is in the service images. An install whose
images predate this release gets the new mounts and hashes on its next
re-render, but its services read the key file only at start, so a declared
key change would reach them only on their next restart. Re-render with the
image set of this release (`wip-deploy redeploy --tag <this release>`), the
same rule as every rendering change.

### Every restore leaves a provenance record; `wip-archive` 0.3.0

Restored data was only partly identifiable: a fresh (remap) restore
appended a sentence to the namespace description, and an id-preserving
restore or a merge left nothing in the namespace. Every restore mode now
writes one structured record per namespace it wrote. The record holds the
restore job and mode; when and by whom; the archive's SHA-256 and versions;
the source namespace, host and export time; the archive's `derived_from`
transform chain; and, for a merge, the clash policy and planned counts.
Records are only added, never replaced. A backup carries the namespace's
records in its manifest (`NamespaceEntry.provenance`, a new optional field
in `wip-archive` 0.3.0; archive format stays 3.1). A restore of that backup
copies them in, marked `inherited`, before adding its own. Each record
links the validation job its restore started.

Read it with `GET /api/document-store/backup/provenance?namespace=<prefix>`
(oldest first, paginated, namespace read permission). An empty list means
no restore has written into the namespace. The id-preserving restore
still leaves every restored entity an exact copy; the record is a separate
row.

**Action:**
- **Re-render the install** (`wip-deploy redeploy`). The registry's
  MongoDB user gains find and remove on the new
  `wip_document_store.restore_provenance` collection, which namespace
  deletion now clears. Registry code from this release on an install
  rendered before it fails every namespace deletion, dry runs included,
  with "not authorized": deletion counts each collection it will clear
  before it starts.
- **Apps that vendor `wip-archive`** pick up 0.3.0 on their next
  scaffold refresh.

### Scaffolds keep local config out of git; `--kb` needs a key file

App repos had committed per-machine files, and before key files existed
those included literal WIP API keys. Two scaffold changes stop this:

- **App scaffold, every run:** `.mcp.json` and `.claude/kb.json` join `.env`
  and the session files in `.gitignore`. Before this, the entries were added
  only when a project was created, and the two config files were not among
  them. A tier-3 app gets a tracked `.claude/kb.json.example` with
  placeholder values. A new `.githooks/pre-commit` gate refuses staged local
  config, the value of any secret file on the machine, and known key
  shapes. The scaffold enables it unless the clone already sets
  `core.hooksPath`, and leaves an existing `.githooks/pre-commit` alone. A
  refresh prints the `git rm --cached` command for any local-config file
  the repo still tracks; it does not commit.
- **Both scaffolds:** `--kb` no longer falls back to
  `~/.wip-deploy/kb/secrets/api-key`. The KB key is issued per installation,
  so `--kb-key <path>` is required unless `.claude/kb.json` already records a
  key file. A missing or unreadable key file is an error, raised before
  `kb.json` is written.

**Action:** pass `--kb-key` when enabling tier 3. In an existing app that
still tracks `.mcp.json` or `.claude/kb.json`, run the scaffold refresh,
then the `git rm --cached` command it prints, and commit. Repos whose
history holds a literal key should retire that key.

### App phase commands read rule cards instead of the depth documents

Four short rule cards now live in `docs/cards/` and are bundled into every
app's `docs/cards/` by the scaffold: `core.md` (what an app is, the model
rule, the approval gate, the eight PoNIFs in one line each), `model.md`
(Phase 2–3), `build.md` (Phase 4) and `ship.md` (the deployable-app
contract as a checklist). The phase commands' mandated reading points at
them: `/wip-explore` reads `core.md` and `model.md` instead of the four MCP
resources, `/wip-design-model` and `/wip-implement` read `model.md` instead
of `WIP_PoNIFs.md`, the build playbook reads `build.md` instead of
`WIP_DevGuardrails.md`, both READMEs and the PoNIFs, and `/wip-deploy` and
the deploy playbook point at `ship.md`. The depth documents stay bundled
and are opened when a card does not answer a question. `/wip-setup` and
`/wip-wake` keep their full baseline reload.

Each card records a hash of every source it condenses (the served
`wip://` resource text for the MCP resources). A test in the mcp-server
suite fails when a source changes after the card's last review, and names
the `git diff` to read before the hash is updated.

**Action:** none for the platform. An app picks the cards up on its next
scaffold refresh. When editing Vision, the PoNIFs, the served resources,
the guardrails, the technology stack, the UI guidance, the deployable-app
contract or the app CLAUDE.md template, expect the card drift test to name
the card to re-review.

### A scaffold refresh keeps MCP servers the project added to `.mcp.json`

The scaffolds rewrote `.mcp.json` whole, so a refresh silently dropped any
MCP server a project had added itself (WIP-Cooking lost its Playwright
server for browser QA). Only the `wip` server is the scaffold's now: it is
regenerated, and every other server and top-level key is kept. A file with
nothing else in it is written exactly as before.

**Action:** a project that already lost a server on an earlier refresh
re-adds it once, e.g. from its git history.

### A slim app profile for agents with a small working set

`scripts/create-app-project.sh --profile slim` scaffolds an app for an agent
that must stay well inside its context (a local model): it reads short rule
cards instead of the depth documents, and scripts do its bookkeeping.

- **CLAUDE.md** is `scaffold/templates/claude-md/app-slim.md` (~1k tokens,
  against ~8k for the full one): the app line, the dev namespace, one line
  per phase command, the session protocol, the 300k-token stop rule, an
  empty app-gotchas section, and where the cards and depth documents are.
- **Commands:** the shared phase commands, without `/wip-analyst`,
  `/wip-add-app`, `/wip-bootstrap`, `/wip-fireside` and `/wip-status`, and
  with short `/wip-wake`, `/wip-setup` and `/wip-report` from
  `docs/slash-commands/app-slim/`. The slim wake is one script call
  (`wake-rollover.py`, then the new `wake-brief.py`), the core card, the
  phase card, and a WIP check only when the next task touches WIP data.
- **`wake-brief.py`** prints the recorded phase and its age, the previous
  session's summary, recent commits, uncommitted files, the brief's open
  cases and decisions, and a warning when the commit hook is not active.
- **`wake-rollover.py --close FILE`** closes the current session: the agent
  writes only the summary text; the script sets `status: closed` and
  `ended_at`, keeps an existing summary, replaces a stub, and mirrors to kb.
  Both profiles can use it; the slim `/wip-report session-end` does.
- **Compaction** keeps the session: the slim re-anchor hook prints the core
  card and the recorded phase instead of sending the agent to `/wip-wake`.
- **Status line:** the recorded profile is shown next to the context figure;
  `slim` turns red at 300k tokens used, `full` at 80%. The tokens are what
  the context holds now: `current_usage` when the harness sends it (Qwen
  Code), else the fill percentage times the window (Claude Code). Qwen
  Code's `total_input_tokens` is the sum over every request of the session
  and would trip the 300k rule within a few dozen requests. The activity
  part reads Qwen Code's prompt hook too: Qwen sends a slash command
  expanded into the command's text with the typed command as the last line,
  and fires the hook with an empty prompt on its own turns, which no longer
  clears the activity. Qwen Code's tool hooks are recognised as well
  (`skill`, `run_shell_command` next to Claude Code's `Skill`, `Bash`); a
  skill Qwen's agent calls itself shows by name only, since Qwen sends no
  arguments with it.

The profile is recorded as `PROFILE` in `.claude/.app-meta` (every app now
records it; existing apps get `PROFILE="full"`). A refresh keeps the recorded
profile unless `--profile` is given again; an unknown value is refused.

**Action:** none for existing apps. To move an app to the slim profile, run
the scaffold with `--profile slim`. Its existing CLAUDE.md is kept and the
slim one is written as `CLAUDE.md.refresh`: move the app's own sections into
the slim file's "App-specific gotchas", then replace CLAUDE.md with it (or
pass `--force-claude-md`).

### Apps record their phase and log their commits from git

Two pieces of app-agent bookkeeping no longer depend on the model:

- **Phase:** each phase command (`/wip-explore`, `/wip-design-model`,
  `/wip-implement`, `/wip-build-app`, `/wip-improve`) first runs
  `.claude/scripts/set-phase.py <phase>`, which writes a tracked
  `.claude/.app-phase` ("3 implement"). Committed with the phase's work, its
  `git log` is the phase history.
- **Commit log:** a new `.githooks/post-commit` runs
  `.claude/scripts/log-commit.py`, which appends each commit's entry to
  `reports/<session-id>/commits.md` from git: hash, time, author, files and
  lines, the subject as heading, the body as the Why, and the `Tests:`,
  `PoNIF:` and `Discovered:` commit-message trailers. An already-listed hash
  is not logged twice, and a commit that changes only files under `reports/`
  (a session close, the commit log itself) is not logged at all — so the
  commit that sweeps up the last entry does not leave a new one behind. The app CLAUDE.md's "After Every Commit" now asks only
  for those trailers.

**Action:** an app picks both up on its next scaffold refresh. The hook is
written only when `.githooks/post-commit` is absent, and runs only when
`core.hooksPath` is `.githooks` — a clone with its own hooks path gets no
entries until it calls `.claude/scripts/log-commit.py` from its own hook.

### Query-preset production images build for their base path and pass the healthcheck

The `--preset query` production `Dockerfile` declared no `VITE_BASE_PATH`
ARG, so the `--build-arg VITE_BASE_PATH=/apps/<slug>/` its CI workflow
passes never reached `vite build`: the SPA built with base `/` and a
deployed app's assets 404. Its `node:22-slim` runtime also had neither
`curl` nor `wget`, so the deployer's HTTP healthcheck probe exited 127 and
the app reported permanently unhealthy. The scaffold's `Dockerfile` now
declares the ARG and installs `wget` in the runtime stage.

**Action:** the scaffold creates this file only for a new app, and a
refresh does not rewrite it. In an existing query-preset app, add
`ARG VITE_BASE_PATH=/` plus `ENV VITE_BASE_PATH=${VITE_BASE_PATH}` before
`npm run build`, and install `wget` (or `curl`) in the runtime stage — or
copy both from `scripts/scaffold-query/Dockerfile`.

### App guidance matches the platform again

Writing the rule cards turned up app-facing documents that taught code the
platform rejects. Corrected:

- **Bootstrap** (`/wip-bootstrap`, the bootstrap playbook): relations are
  created with `create_term_relations`, not `create_relations`, and the
  seed files' 2-part `TERMINOLOGY:TERM_VALUE` must be prefixed with the
  namespace (`ns:TERMINOLOGY:TERM_VALUE`) — the 2-part form is rejected with
  422. `create_terms` takes a terminology value as well as a UUID, and a
  cross-namespace terminology reference is `wip:COUNTRY`, not a UUID lookup.
- **`baseUrl`**: `technology-stack.md` and the app CLAUDE.md template use
  `` `${import.meta.env.BASE_URL}wip` ``; a bare `'/wip'` works only for an
  app served at `/`.
- **`WIP_DevGuardrails.md`**: Guide 4's client example uses the real
  options, hooks and error classes; Guide 6 states the client's actual retry
  policy (GET only, 2 retries from 500 ms); Guide 3's container skeleton
  points at the deployable-app contract instead of a superseded Caddy
  image and `VITE_WIP_*` variables; Guide 2's React and Router versions
  match `technology-stack.md`.
- **The build playbook**: term dropdowns use `useTerms`, references
  `resolveReference(client, …)`, document lists `useDocuments({ … })`.
- **The deployable-app contract**: `wip-deploy check-app-deployability` is
  a shipped CLI verb, and the scaffold-gaps table says what the query
  scaffold does today.
- **PoNIFs #3 and #8** (served `wip://ponifs` and `WIP_PoNIFs.md`):
  changing `identity_fields` is refused (`identity_fields_immutable`), and
  `versioned: false` is available on any template with identity fields,
  not only edge types.

**Action:** none. Apps pick up the corrected documents on their next
scaffold refresh.

### `@wip/client` browser writes over 64 KB work (0.60.1)

Up to 0.60.0 the client sent every request with Fetch's `keepalive` flag,
mistaking it for HTTP connection reuse. The flag actually lets a request
outlive the page, and browsers cap the combined body size of in-flight
keepalive requests at 64 KiB. In a browser, a write above that (a file
upload, a long text field, or several concurrent writes or `bulkImport`
batches that together pass it) failed with `TypeError: Failed to fetch`
before leaving the page, so no server log ever showed it. Node callers were
unaffected, and connection reuse needs no flag in either runtime. 0.60.1
drops the flag.
**Action:** apps that use `@wip/client` in the browser re-vendor
`wip-client-0.60.1.tgz`.

### CSV export: query export takes `namespace`; SQL errors are HTTP errors

- `POST /api/reporting-sync/export/csv` accepts `namespace`, with the same
  meaning as on `POST /query`: unqualified table names resolve in that
  namespace's schema. Before, the request model rejected the field with 422,
  so "export what I just queried" worked only for schema-qualified SQL.
- Both export routes prepare the SQL before the response starts. An invalid
  statement or an unknown relation or column is now a **400**, and a missing
  table on `GET /export/csv` is a **404**. Before, these came back as **200
  with an empty body**, because the error was raised after streaming had
  begun. A failure that only appears mid-stream (e.g. a statement timeout on
  a large export) can still truncate the body.
- MCP `export_report_csv` gains `namespace`. Table mode requires it; before,
  table mode always failed with 422 because the route requires a namespace.

**Action:** callers that relied on an empty CSV for a missing table must
handle the 404.

### `@wip/client` 0.61.0: parity and CSV export on `client.reporting`

`reporting.getParity(namespace, { includeCounts? })` returns the typed
`NamespaceParityResult`. `reporting.exportTableCsv(table, namespace, …)` and
`reporting.exportQueryCsv(sql, { params?, namespace?, … })` return a `Blob`;
query export requires an admin or service key. Apps that call these
endpoints with plain `fetch` can switch to the typed methods after
re-vendoring `wip-client-0.61.0.tgz`.

### `@wip/react` 0.21.0: template hooks take a namespace

A template value is unique only within a namespace, but the hooks looked
templates up by value alone. With a key that can see more than one namespace
(an admin or multi-namespace key), `useTemplateByValue` and `useFormSchema`
returned whichever namespace's template came first. 0.21.0 adds the option:

- `useTemplateByValue(value, { namespace? })` and
  `useFormSchema(value, { namespace? })`. Pass `namespace` whenever the key
  can see more than one namespace. A scoped lookup does not fall back to the
  `wip` namespace. Omitting `namespace` stays right for a single-namespace key,
  whose namespace the server derives.
- `useTriggerBatchSync` variables gain `namespace?`. It picks the template when
  its value exists in several namespaces and scopes the sync to that
  namespace's documents. The hook documents it as required together with
  `force`.
- **Query-key shape.** `wipKeys.templates.byValue(value, namespace?)` now
  returns `['wip', 'templates', 'by-value', value, namespace ?? null]`; the
  unscoped lookup carries `null`. Code that builds this key by hand with the
  old four-element form must append the namespace (or `null`); use the
  `wipKeys` helper instead of a hand-built array.

No newer `@wip/client` is needed: the client methods behind these hooks
(`templates.getTemplateByValue`, `reporting.triggerBatchSync`) already accepted
`namespace` in 0.60.0.
**Action:** apps that read templates by value through these hooks, or force a
batch sync, with a key that can see more than one namespace pass `namespace`.
Re-vendor `wip-react-0.21.0.tgz`.

### `@wip/react` 0.22.0: `useReportQuery` owns its query key

`useReportQuery(sql, params, options)` typed its options as
`Omit<UseQueryOptions, 'queryFn'>`, which left `queryKey` required: the hook's
own documented call, `useReportQuery(sql, undefined, { namespace })`, failed
`tsc` with "Property 'queryKey' is missing". The hook builds its key itself
(`wipKeys.reporting.query(sql, params, namespace)`), and the options were spread
after it, so a caller who passed a `queryKey` to satisfy the type silently
replaced the hook's namespace-scoped key. The options type now omits
`queryKey` as well.
**Action:** drop any explicit `queryKey` from `useReportQuery` options (it no
longer compiles; the hook's key is the same one `wipKeys` builds). Re-vendor
`wip-react-0.22.0.tgz`.

### `@wip/client` 0.65.0 / `@wip/react` 0.24.0: the maintenance passes have typed clients and hooks

`POST /api/document-store/maintenance/rehash-identities` and
`POST /api/document-store/maintenance/requalify-identity-synonyms` answered a
bare JSON object, so the OpenAPI snapshot had no response type and `@wip/client`
had no method for either (an app had to use a raw `fetch`). Both endpoints now
declare typed responses (`RehashIdentitiesResponse`,
`RequalifyIdentitySynonymsResponse`); the response bodies themselves are
unchanged.

- `@wip/client` (added in 0.64.0, which 0.65.0 replaced):
  `documents.rehashIdentities(request)` and
  `documents.requalifyIdentitySynonyms(request)`, with typed requests and
  responses. The namespace goes in the request body. Both passes default to a
  dry run on the server, need admin permission on the namespace, and are
  synchronous (no job to poll).
- `@wip/react` 0.24.0: `useRehashIdentities()` and
  `useRequalifyIdentitySynonyms()`. They invalidate caches only after an
  applied pass (`dry_run: false`).

**Action:** none for existing callers; this is additive. Apps that want the
methods or hooks re-vendor `wip-client-0.65.0.tgz` and `wip-react-0.24.0.tgz`.

### The identity-hash migration converges after a failed Registry leg; `applied` means fully migrated; `@wip/client` 0.66.0

`POST /api/document-store/maintenance/rehash-identities` writes MongoDB first,
then re-keys the Registry's claims. When the Registry leg failed, the
documents' stored hash was already the new one, so every later run counted
them unchanged and never asked the Registry again: their claims stayed on the
old hash for good. `applied` had counted them as migrated anyway.

- Every apply now re-sends the Registry re-key for documents whose stored hash
  is already right. The Registry answers `unchanged` for a claim that is
  current, and re-keys one an earlier run left behind. Re-running the apply
  after a Registry outage now completes it.
- `applied` counts only documents whose rows were rewritten **and** whose
  Registry re-key succeeded. New counts: `registry_failed` (re-key did not
  succeed, from this run or an earlier one) and `registry_repaired` (stored
  hash already right, claim re-keyed by this run). Such documents are listed
  in `items` with `status: "unchanged"` and their `registry_status`; the item
  status enum gains `unchanged`.
- The re-key is sent in batches of 500; a failed batch marks only its own
  documents.
- The collision `detail` no longer tells the caller to archive a document
  (archive was removed).
- `@wip/client` 0.66.0 types the new fields and status, and its README's
  browser `baseUrl` example is `` `${import.meta.env.BASE_URL}wip` `` (a bare
  `'/wip'` works only for an app served at `/`).

**Action:** a caller that read `applied` as "rows rewritten" now gets "fully
migrated"; read `registry_failed` too. If an earlier apply reported any
`registry_status` starting with `error`, run the apply again. Apps using the
typed client re-vendor `wip-client-0.66.0.tgz`.

### Namespace validation checks a reference's namespace and names what its lookup resolves to; `@wip/client` 0.67.0

The namespace validation job (`POST /api/document-store/backup/namespaces/{namespace}/validate`,
and the job every fresh or merge restore starts) checked only that a
document reference's held id exists. A fresh restore maps a reference's
recorded namespace to the new one even when its target was not in the
archive and so kept its old id. The result names the new namespace and an
old id, and on the source install that id still exists, so the check passed
it.

- A held id that lives in a different namespace than the reference records
  is a new error, `misplaced_document_ref`. The summary gains
  `misplaced_document_refs`.
- An orphaned or misplaced reference whose `lookup_value` differs from the
  held id now says what that lookup resolves to on this install, or that it
  could not be resolved there. That is the identifier the reference was
  written with, and the one a repair would use.
- `@wip/client` 0.67.0 types `misplaced_document_refs` on
  `NamespaceIntegritySummary`.

**Action:** none; a validation result may now report errors it missed
before. Apps using the typed client re-vendor `wip-client-0.67.0.tgz`.

### A merge rehashes documents whose identity it repoints

A merge points an archived document's term and reference resolutions at
this install's entities: a definition matched by content, or a referent
outside the archive. When the field is an identity field, the document's
identity changes (it hashes the canonical id the resolution names), but the
merge kept the archived hash. The merged row, its Registry key and its
uniqueness claim described an identity no write computes, so the next write
of that identity created a second document. Planning on the stale hash could
also miss the target's own copy of the same document and insert the
archive's beside it.

- A merge now recomputes the hash of every document whose rewrite changed a
  resolution, before planning, from the archived template's identity
  definition, and re-keys its Registry row. The claim it writes is the new
  key's. Streamed merges read the archived chains once more for this, only
  when the merge maps anything.
- A merge that maps nothing writes rows exactly as archived.
- The merge result gains `rehashed_documents` per target namespace. Plain
  restore shares the same code.

**Action:** none for new merges. Documents merged before this release
keep their stale hash, and nothing repairs them automatically: re-posting
one creates the duplicate described above, and a PATCH keeps the stored
hash.

### Restores list the documents that reference what they could not find, and can leave such edges out

A restore that could not find a referenced entity on this install said
which entity it was, but not which documents pointed at it, and the restore
provenance did not record it at all.

- The job result's `referents` gains `unresolved_references`. For each
  unresolved referent and referencing namespace, it gives the number of
  documents that keep its archived id and up to 20 (document_id,
  field_path) samples. Each referent's warning now says how many documents
  reference it. A dry run reports the same.
- New restore option `drop_unresolved_edges`, for every mode (upload
  restore, restore from a job, MCP `start_restore`). With it, an edge
  document whose `source_ref` or `target_ref` points at a document that is
  neither in the archive nor on this install is not restored, with all its
  versions and its registry entry. `referents.dropped_edges` lists every
  dropped edge in full. Plain reference fields always keep their value.
- A merge still refuses any reference that resolves to nothing. With
  `drop_unresolved_edges`, edges to a missing document no longer cause the
  refusal; a plain reference to one still does.
- Each restore provenance record gains `referents`: the counts, and the
  unresolved references and dropped edges of that namespace's documents.

**Action:** none. Without the option, restores write what they wrote before.
`@wip/client`'s restore options do not carry `drop_unresolved_edges` yet.

### A plain restore onto another install points references outside the archive at that install

A plain (id-preserving) restore did not read the archive's referent table.
Restored onto another install, the disaster-recovery case, every reference
to an entity the archive does not carry kept the source install's id. That
includes every reference into the `wip` namespace, whose vocabularies each
install mints at its own bootstrap: template fields drawing on them, and
documents' `term_references` and `references`.

- The restore now resolves the referents as fresh and merge do, and rewrites
  only those references. The archive's own ids are kept, as before.
- A document whose identity field is such a term or reference is rehashed,
  and its Registry row and uniqueness claim are re-keyed to the new hash, so
  the next write of that identity versions it instead of creating a second
  document.
- On the install the archive came from, every referent resolves to itself
  and every row is written exactly as archived.
- The job result gains `referents`, `repointed_referents` and
  `rehashed_documents`; a dry run reports the first two.
- An unresolved referent keeps its id, and its warning says to restore the
  namespace holding it first. Namespaces backed up separately must be
  restored in dependency order: a reference into a namespace restored
  later is not revisited.

**Action:** none. Archives taken before the referent table existed restore
as before, and say so.

### Fresh and merge restores point references outside the archive at this install's entities; `wip-archive` 0.5.0

A fresh or merge restore rewrote references to the entities it carried, and
passed every other reference through with the source install's id. On
another install that id named nothing or the wrong thing. On the same
install, a reference to a same-namespace document the archive did not carry
came out naming the new namespace beside the old id.

- The restore now reads the archive's referent table (`referents.jsonl`)
  and asks this install's Registry which entity each referent is, through
  its own keys (primary key, then synonyms; retired entities included,
  reserved ones not). The old id is never used to look it up, since a
  prefixed id can name a different entity elsewhere. One entity found: every
  reference to the referent is rewritten to it by the same passes that
  rewrite archived entities. A dry run does the same lookup, so its preview
  is exact.
- A referenced document takes its namespace, template id and identity hash
  from the document on this install, not from the archive's maps.
- None or several found: the reference keeps its archived id, and the
  restore job carries a warning naming the referent's key (the first 20,
  then a count). The namespace validation job then reports each affected
  reference.
- The job result gains `referents`: `{recorded, total, resolved,
  unresolved}`, per target for a merge. An archive taken before the table
  existed reports `{recorded: false}`. Its references keep their source ids
  as before, and the restore says so as a progress message, not a warning.
- `wip-archive` 0.5.0: `IDRemapper.add_external_document_mapping`.

**Action:** none. Restores of archives taken with the referent table repair
these references automatically; take a new backup to get the table.

### Backups record what they reference but do not carry; `wip-archive` 0.4.0

An archived reference to an entity outside the archive stored only that
entity's id: a template field drawing from a `wip` vocabulary, a document
pointing into a sibling namespace, a term relation into another ontology.
On another install that id names nothing, or the wrong thing, and nothing in
the archive said which entity was meant.

- A backup now writes `referents.jsonl` at the archive root: the Registry
  entry of every entity the archived rows reference but the archive does not
  carry, with its primary key and synonyms. Those are the entity's identity
  in a form another install can resolve, for example `{ns: wip, type: term,
  terminology: _TIME_UNITS, value: seconds}`, or a document's
  `{@template: …, <identity field>: …}` synonym.
- `Manifest.referents` counts the rows. `0` means the archive references
  nothing outside itself. An archive from before this change has no key and
  reads as `None`, so a restore can tell "nothing external" from "not
  recorded".
- Additive: older readers ignore the file and the key, and the archive
  format stays 3.1.
- Fresh and merge restores use the table to repair those references; see
  the `wip-archive` 0.5.0 section.

**Action:** none. Backups taken from now on carry the table. Apps that
vendor `wip-archive` pick up 0.4.0 on their next scaffold refresh.

### Platform-maintained term closure in reporting

Each namespace schema gains a `term_closure` table: one row per
(ancestor, descendant, relation_type) with the shortest-path depth,
reflexive rows included, `parent_term_id` links counted as `is_a` (the
same union the ontology traversal uses). "Documents whose term is
anywhere in the subtree of X" becomes one indexed subquery — no app-side
recursive CTEs, and correct under multiple parents, which fixed-depth
joins silently are not (926 multi-parent terms measured in one real
vocabulary). Closures are per relation type, never blended, and are
rebuilt wholesale per namespace (debounced from term and term-relation
events; immediately after a batch sync) inside one transaction, so
readers never see a half-built closure. The table is listed by `/tables`
and exportable like the other fixed tables.
**Action:** none. Apps using the CASE-849-style registered closure view
can switch their joins to `term_closure` and drop the view at their own
pace.

### Terminology snapshot reconcile

`POST /api/def-store/terminologies/{id}/reconcile` (MCP tool
`reconcile_terminology`, `@wip/client` 0.59.0
`defStore.reconcileTerminology`) takes a terminology's complete desired
term set and a policy, and answers with the diff — created, updated with
changed fields, deprecated, errors; unchanged terms counted, not listed.
`dry_run` is the **default**: writing is the explicit choice. Built for
externally sourced vocabularies that refresh periodically, replacing each
app's hand-rolled diff script. Absent active terms are deprecated (never
deleted) and only under `on_missing='deprecate'`, optionally with
successor mappings; the snapshot is authoritative for label and names
while description/sort_order/parent apply only when set (reconcile never
clears a field, keeping re-runs idempotent); metadata is honored on
create and never rewritten; a snapshot value matching a retired term is a
per-item `exists_inactive` error — reconcile does not reactivate. Writes
route through the normal term doors, so Registry registration, names
validation, the collision guard, events and audit behave exactly like
individual writes.

### Ranked term search (typeahead); unified search actually finds terms

Def-store gains `GET /api/def-store/terms/search` (MCP tool
`search_terms`, `@wip/client` 0.57.0 `defStore.searchTerms`): literal,
case-insensitive search over a term's value, label and every name, with
match modes (`exact`/`prefix`/`word-start`/`substring`), ranked hits that
carry the exact matched string and — for name hits — the name's type and
language, and `rollup=root` to collapse hits onto their root `is_a`
ancestor (a trade name returns the molecule). Exact mode returns ALL
candidates, making it the inspection surface for ambiguous names.

The unified search (`POST /api/reporting-sync/search`) now delegates its
term type to that endpoint. Before, it fetched one page of 100 terms from
the first 20 terminologies and substring-matched in Python — any
vocabulary beyond 100 terms was effectively invisible and alias matches
were never found. Term results now cover full vocabularies and name
matches say what matched.

### Document-store write doors resolve against the Registry, not the cache

After an entity was hard-deleted and re-created under the same value,
document-store kept resolving the value to the dead canonical id from its
process-local cache (up to 5 minutes), so writes failed with
"Template '<old-uuid>' not found" until the process was restarted —
while the Registry answered correctly the whole time. Every document-store
write door (document create, PATCH, migrate, delete, archive, file
metadata/delete, CSV import) now resolves identifiers with the cache
bypassed, matching what def-store and template-store already did. Reads
keep the cache; its staleness stays bounded by the 5-minute TTL. The
"restart document-store after recycling same-valued entities" workaround
is obsolete. (`resolve_or_404` in wip-auth gained the `bypass_cache`
parameter the single-id doors needed.)

### Bearer tokens from Dex authenticate against the backend services

A Dex token sent as `Authorization: Bearer` to any backend service failed
on every `wip-deploy` install. The JWKS fetch went to a path Dex does not
serve (`/dex/.well-known/jwks.json`) and surfaced as HTTP 500. Past that,
the issuer was compared against Dex's internal URL while tokens carry the
public one, and the audience `wip` matched no Dex client. The deployer now
renders, from the same sources as Dex's own config:
- `WIP_AUTH_JWT_ISSUER_URL`: the public issuer (`https://<hostname>/dex`);
- `WIP_AUTH_JWT_JWKS_URI`: `http://wip-dex:5556/dex/keys`;
- `WIP_AUTH_JWT_AUDIENCE`: every Dex client the deployment registers
  (`wip-gateway` plus each app's own client).

wip-auth accepts a comma-separated `WIP_AUTH_JWT_AUDIENCE` (any listed client
matches) and refuses an empty one. An unreachable key endpoint now answers
503 instead of 500. The gateway and API-key paths are unchanged.
**Action:** re-render (`wip-deploy redeploy`). The six backend services'
environment changes, and image-based installs need images carrying the new
wip-auth.

### Install directories are private; mounted secret files stay container-readable

The spec-declared API-keys file (`config/auth/api-keys.json`) was rendered
0600 and bind-mounted into the backend services. On Linux compose hosts,
registry and mcp-server run as a non-root user and read it through the
"other" permission bits. Under rootless podman, and under a root daemon
whenever the operator's uid differs from the container's, wip-auth failed
with `PermissionError` at startup. Meanwhile the Dex config, which carries
the gateway's client secret, was 0644 inside a 0755 install directory,
readable by every local user. Every apply and render now makes the directory
it writes 0700, and container-mounted files are 0644 inside it. The
directory, not the file mode, keeps other host users out, so non-root
containers can still read their mounts. `.env` and Garage's config stay
0600: no non-root container reads them.
**Action:** none. Existing installs become 0700 on their next apply.

### `wip-deploy render` is a true dry run

`render` shared install's secret handling, so a preview persisted every
secret it resolved into the install's store (`~/.wip-deploy/<name>/secrets`)
whatever `--output-dir` said. It created credential directories for names
that were never installed, and it added missing secrets to a live install's
store. Without `--output-dir` it also wrote a tree built from command-line
flags over the install's own rendered files, which `rebuild`, `restart` and
`start` act on. Now `render` reads an existing store without writing to it
(missing secrets are generated in memory only), refuses any target directory
that holds a `deployment.deployer-state`, and looks for an imported CA in the
secret store rather than the output directory.
**Action:** to refresh a live install, use `wip-deploy redeploy`; to preview
one, pass `--output-dir <scratch dir>`. Earlier previews may have left
secret-only directories under `~/.wip-deploy/` (no `deployment.deployer-state`
inside). Nothing reads them, and they can be deleted.

### Terms: one resolver for document writes, a real merge, hard deletes that stick

**Document writes resolve term values through def-store.** document-store used
to match term fields against its own cached copy of each terminology. That copy
included deprecated and inactive terms permanently, and was up to 5 minutes
stale for deleted terms and edited names. So documents could store a deprecated
or deleted term's id with no warning, while def-store's own `validate` gave a
different answer. Document writes now ask def-store, by the same rule as
`POST /api/def-store/validate`:
- only active terms match;
- a term's value beats any term's name;
- the terminology's case rule applies.

A vocabulary change applies to the very next write. Within one bulk write or CSV
import, each distinct value is resolved once.

The way to ask is the shared `wip_auth.term_resolution` module
(`resolve_term_values`, `term_resolution_batch`), the term-value counterpart of
`wip_auth.resolve`. Any service resolving term values uses it rather than
matching against a vocabulary of its own. def-store's bulk resolution is
now one indexed query per terminology per request: two case-insensitive
indexes on `terms` are built on first start.
**Action:** a write submitting a deprecated or inactive term's value is now
rejected (`invalid_term`), unless an active term carries that value as a name.

**Merging terms: `POST /api/def-store/terms/merge`** (MCP `merge_terms`,
`@wip/client` 0.62.0 `defStore.mergeTerms`). It absorbs one term into another of the
same terminology:
1. the absorbed term's value and names become names of the survivor;
2. it is deprecated with `replaced_by_term_id` = survivor;
3. its Registry entry is merged, so its id and `ns:terminology:value` key
   resolve to the survivor.

Existing documents keep the absorbed term's id. A merge that cannot complete
changes nothing.
**Action:** the Registry's `merge_entries` (and the MCP tool) now **refuses term
entries**, because a Registry-only merge left the absorbed term's value still
resolving to it. Use the term merge.

**Hard-deleting from a mutable terminology removes the Registry entry.** Before,
the Registry refused the deletion in `retain` namespaces, as a per-item error
nobody read. The deleted value kept resolving to the dead id, and re-creating it
reused that id. Now the entry goes too, and re-creating the value mints a new
id. If the Registry still refuses, the delete reports
`error_code: registry_entry_not_removed`.
- Entries left behind by earlier deletes: list them with
  `POST /api/def-store/maintenance/registry-ghosts?namespace=<ns>` (namespace
  admin; a dry run by default), and remove them with `dry_run=false`.
  Entries from mutable or deleted terminologies are removed; a term missing from
  an immutable terminology is only reported.
- The shared Registry client's `hard_delete_entry` now raises when the Registry
  refuses an item, instead of returning quietly.
- Rollout order: update the Registry before def-store. A def-store that already
  sends the exemption to an older Registry gets the deletion refused and
  reports `registry_entry_not_removed`; nothing breaks silently, and the
  ghost-cleanup endpoint removes those entries afterwards.

### Edge-type filters resolve in the request's namespace; an unknown value is 404

`GET /documents/{id}/relationships?template=` and `GET /documents/{id}/traverse?types=`
resolved a bare edge-type value through template-store without a namespace.
When the same value existed in more than one namespace, the lookup could
select another namespace's edge type, and the call returned nothing for
edges that exist. Both filters now resolve through the Registry, like every
other identifier on these routes:
- a bare value names the edge type in the request's namespace (the
  `namespace` parameter, else the seed document's);
- `NS:VALUE` names another namespace;
- a template ID is accepted.

**Behavior change:** a filter value that resolves to nothing used to return
an empty page (or zero traversal nodes). It now returns **404** naming the
value, so a misspelled or wrong-namespace edge type can no longer pass for
"no edges".
**Action:** callers that filter on an edge type that may not exist yet must
handle the 404.

---

### Ingest producers read their own outcomes over HTTP

A producer holding the `ingest-producer` NATS role could submit work items
but had no supported way to learn whether they were created, updated or
rejected. The results stream carries every producer's outcomes, so the role
cannot read it. ingest-gateway now serves each producer its own outcomes:
- `GET /api/ingest-gateway/results/{correlation_id}` returns an item's
  outcome, or 404;
- `POST /api/ingest-gateway/results/lookup` with `{"correlation_ids": [...]}`
  returns one `found` / `not_found` entry per ID, in request order.

The caller sees only the outcomes of items submitted with its own API key:
the gateway resolves the key in each message's `X-API-Key` header to its
principal and records it as `submitted_by` on the outcome. Admins may pass
`?submitted_by=apikey:<name>` to read another producer's outcomes, and
`?submitted_by=` (empty) for items that carried no usable key. Outcomes
last only as long as the results stream keeps them, so poll soon after
submitting.

**Behavior change:** outcomes are published on
`wip.ingest.results.<owner>.<correlation>`, where both tokens are hashes,
instead of `wip.ingest.results.<correlation_id>`. A subscriber to
`wip.ingest.results.>` still receives every outcome, and the payload still
carries `correlation_id` verbatim, now with `submitted_by` alongside.
A correlation ID containing whitespace used to make its outcome
unpublishable: NATS refused the subject and dropped the gateway's
connection, and the outcome was lost. It now publishes normally.
**Action:** none for producers. A subscriber that filtered on a single
`wip.ingest.results.<correlation_id>` subject must switch to the HTTP lookup.
ingest-gateway's NATS user gains read access to its own results stream
(`$JS.API.STREAM.MSG.GET.WIP_INGEST_RESULTS`), so the install must be
re-rendered (`wip-deploy redeploy`) for the lookup to work.

---

### Readiness can report a down dependency; liveness and startup never kill a waiting pod

On k8s, readiness and liveness probed the same endpoint, and there was no
startup probe. Two consequences:
- A store still retrying MongoDB at startup (up to 120 s, during which
  uvicorn serves nothing) was killed by liveness after about 80 s
  (registry) or 95 s (the other stores): three failed checks 30 s apart,
  starting at `start_period_seconds + 5`. The restart reset its retry, so a
  MongoDB that stayed down that long kept the stores in a restart loop.
- No endpoint could report a down dependency without turning it into a
  restart, so a store whose MongoDB was unreachable stayed `Ready`.
  reporting-sync's `/health`, which already answered 503 on unusable
  PostgreSQL service roles, crash-looped its pod instead.

Now:
- `healthcheck.liveness_endpoint` (new, optional) is what the k8s
  livenessProbe and a new startupProbe check; `endpoint` stays the
  readiness check and compose's healthcheck. Without it, every probe uses
  `endpoint`, as before.
- The startupProbe checks every 5 s for `start_period_seconds + 150` s.
  k8s runs no other probe until it first succeeds, so a pod waiting for a
  dependency at startup is no longer killed.
- `healthcheck.retries` becomes every probe's `failureThreshold` (the
  default, 3, is the k8s default).
- Services serve `GET /livez`: 200, no I/O, no credentials (wip-auth
  `install_liveness`).
- registry, def-store, template-store and document-store use `/ready`
  (pings MongoDB, 503 while it is unreachable) for readiness and `/livez`
  for liveness. reporting-sync keeps `/health` for readiness and uses
  `/livez` for liveness.

**Behavior change:** on k8s, a store whose MongoDB is unreachable is taken
out of its Service's endpoints until MongoDB answers, instead of staying
`Ready` and failing requests. Every Deployment has one replica, so for that
time callers reach no pod at all rather than the store's own errors. On compose, the
stores' container health now reflects MongoDB, so `wip-deploy`'s wait for
"all healthy" also waits for MongoDB. `/health` keeps its always-200
contract, and `wip-deploy verify --security --live` is unaffected.
**Action:** re-render the install (`wip-deploy redeploy`); the services
must run this release's images, since the manifests now probe `/livez`.

---

### MCP: registered reporting views (108 tools)

Registered reporting views could be managed only over HTTP, and an agent
with a namespace-scoped key could not read one through MCP at all (raw SQL
via `run_report_query` needs an admin or service key). Four new tools wrap
reporting-sync's view endpoints:
- `register_reporting_view(name, sql, namespace?, registered_by?)` —
  register or replace; returns `created` and `schema_qualified`;
- `list_reporting_views(namespace?)`;
- `read_reporting_view(name, namespace?, filters?, sort?, limit?, offset?)`
  — rows through structured filters, READ on the namespace;
- `delete_reporting_view(name, namespace?)`.

`namespace` is a URL path segment on these endpoints, so it is not derived
from the key: pass it, or set `WIP_MCP_DEFAULT_NAMESPACE`. Register and
delete are write tools, removed in read-only mode (now 51 write, 57
read-only tools). `@wip/client` already had the same four methods.

---

### Terminology deletion checks its template dependencies for real

The guard "a terminology referenced by templates cannot be deleted without
`force`" was silently off on every install: def-store's manifest never
handed the service `TEMPLATE_STORE_URL` or a key, the check's call went to
`localhost:8003` inside def-store's own container, and any failure — an
unreachable Template Store and a non-200 answer alike — collapsed to a
count the guard read as "no dependencies". A successful delete therefore
never meant "nothing depended on this".

The manifest now wires both variables, and the guard fails closed:

- A non-forced **soft delete** is refused while active templates reference
  the terminology, **and** while that cannot be verified ("Could not verify
  template dependencies … retry, or use force=true"). `force=true` keeps
  its meaning — the deactivate stays reversible via restore.
- A **hard-delete** (`hard_delete=true`, namespace `deletion_mode='full'`)
  is refused while templates of **any** status reference the terminology —
  an inactive template still validates the documents pinned to it — or when
  that cannot be verified. `force` does not apply to hard-delete.
- `GET /terminologies/{id}/dependencies` reports an incomplete check as
  `template_count: -1` with `has_dependencies: null` (unknown), never
  `false`.

**Action:** re-render the install (`wip-deploy redeploy --name <install>`)
so def-store receives `TEMPLATE_STORE_URL` and `TEMPLATE_STORE_API_KEY`.
Without them every non-forced terminology delete is now refused instead of
silently succeeding.

---

### Namespace deletion removes the namespace's stored files

Until now, deleting a namespace removed its file records but left every
stored file object in the bucket, and still reported `completed`. The
Registry's object-store step was only built when `MINIO_URL` was set (no
deployer ever set it), and its request used an authentication scheme MinIO
and Garage both reject. The objects of every namespace deleted so far are
still in storage, with no record pointing at them.

The step now always runs when the namespace has files. The Registry sends
the storage keys from its deletion journal to a new document-store route,
`POST /api/document-store/files/storage/delete` (wip-admins / wip-services
only), which deletes them with document-store's own storage client, so it
follows whichever object store the install runs. The route refuses a
namespace that is not being deleted (409), refuses a key whose file record
belongs to another namespace, and answers 503 if the Registry cannot
confirm the namespace's state. A failed or refused key leaves the journal
`completed_with_warnings` with the keys kept on the step.

**Action:** re-render the install (`wip-deploy redeploy --name <install>`)
so the Registry receives `DOCUMENT_STORE_URL`. Without it, deletions record
the storage step as degraded instead of deleting the objects. The Registry
no longer reads `MINIO_URL`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY` or
`MINIO_BUCKET`. The impact report and journal summary keep the key name
`minio_objects` for compatibility; it counts objects in any object store.

---

### reporting-sync's startup backfill retries until it is complete

At startup reporting-sync backfills terminologies, terms, term relations and
template metadata into PostgreSQL. It waited for def-store only and ran each
step once: template-store was never waited for, and a listing that failed (a
service still starting, or answering 500 while its database restarted) was
logged as `synced: 0` and never retried. The tables then stayed empty until
each entity's next write. This was seen after redeploys, Podman VM restarts
and image rollouts.

Each step now waits for its own source and retries, every 5 s doubling to
every 60 s with no deadline, until its listing completes. It logs WARNING
while incomplete and INFO once complete. The def-store steps and the
template-store step run independently, so one source being down does not hold
back the other.

The batch-sync endpoints (`POST /api/reporting-sync/sync/batch/terminologies`,
`…/terms`, `…/term_relations`, `…/templates`) now also return
`"complete": true|false`: `false` means a listing failed, so a `synced: 0`
can be told apart from an empty namespace.

**No action required.**

---

### Writes resolve names against the Registry, never the cache

Services cached name-to-id resolutions for 5 minutes. A namespace deleted and
re-created under the same name kept resolving `ns:VALUE` to the deleted
entities for those minutes, so writes failed with "<deleted id> not found"
(for example creating a term in a re-created terminology). The rule that
writes bypass the cache was opt-in per route, and about a dozen write routes
had not opted in.

wip-auth now marks every request other than GET, HEAD and OPTIONS as a write,
and resolution in a write always asks the Registry (the fresh answer also
replaces the stale cache entry, so reads heal with it). Reads still use the
cache, now for 30 seconds instead of 5 minutes, so a read can lag a
re-created namespace by at most 30 seconds (less when a write refreshes the
entry first).

**No action required** beyond running images with this wip-auth.

---

### App routes can name the groups allowed through the gateway

Behind the auth-gateway, any logged-in user could open any app. The in-app
`ALLOWED_GROUPS` check runs only in apps that have their own OIDC client, and
gateway-mode apps have none, so nothing enforced it.

A manifest route can now declare `allowed_groups`:

```yaml
routes:
  - {path: /apps/rc, auth_required: true, allowed_groups: [wip-admins]}
```

The deployer renders the list into that route's verify URL (Caddy
`forward_auth` on compose and dev, NGINX `auth-url` on k8s). The gateway
answers 403 to a logged-in user in none of the groups: a short "no access"
page for browsers, JSON for API callers. Behind NGINX only the status reaches
the browser. No session is still the login redirect, and an empty or absent
list keeps today's behaviour. React Console's route now allows `wip-admins`
only.

**Action:** re-render the install (`wip-deploy redeploy --name <install>`) so
the proxy configuration carries the groups, and run an auth-gateway image with
this change. Apps that relied on `ALLOWED_GROUPS` while running behind the
gateway can move the list to their route.

---

### A deleted namespace's reporting schema stays deleted

Deleting a namespace dropped its PostgreSQL reporting schema, but an event
still in flight could create it again: a document or term event processed
after the drop, a retry, or the term-closure rebuild that runs a few seconds
after term changes. The result was an orphan schema holding only the late
rows, left behind for good (dozens on long-lived dev installs). A namespace
later re-created under the same prefix would start next to that stale data.

reporting-sync now records every namespace it drops in
`public._wip_deleted_namespaces`, in the same transaction as the drop. A
missing schema is created only when no such record exists, or when the
Registry says the namespace is `active` again (re-created under the same
prefix), in which case the record is cleared. Events for a deleted namespace
are acknowledged and counted in the sync status as
`events_skipped_deleted_namespace`, not retried and not reported as drops.
Registering a view in a deleted namespace answers 404. If the Registry
cannot be reached when a deleted namespace's schema would be created, the
event is retried rather than the schema created.

**Action:** remove the orphans earlier versions left, once per install.
`POST /api/reporting-sync/maintenance/orphan-schemas` (wip-admins /
wip-services) lists schemas whose namespace the Registry does not know
(`dry_run=true`, the default) and drops them with `dry_run=false`. Only
schemas holding nothing but tables reporting-sync creates are dropped; any
other schema is listed under `skipped`. If the Registry cannot answer for a
schema, nothing is dropped.

---

### The Registry enforces namespace authorization on entries, synonyms and search

Until now the Registry's entry, synonym and search routes accepted any valid
API key and never checked the caller's namespaces. A key scoped to one
namespace could read another namespace's entries, attach synonyms to them,
and claim a composite key inside it. That namespace's own later
registration of the same value then silently adopted the foreign entry.

Now:
- **Entry allocation is privileged-only.** `POST /entries/register`,
  `/provision`, `/reserve`, `/activate`, `PUT /entries` and `DELETE /entries`
  answer 403 to any key outside `wip-admins` / `wip-services`, including in
  the key's own namespace. These are the stores' internal protocol; app keys
  create terms, templates, documents and files through their store.
- **Synonym writes need write on every namespace they touch.**
  `/synonyms/add` and `/synonyms/remove` need write on the entry's namespace
  and on the synonym's; `/synonyms/merge` needs write on both entries'
  namespaces. A key with only read gets a per-item `forbidden`.
- **Reads see only readable namespaces.** `GET /entries/{id}`,
  `/entries/lookup/by-id`, `/lookup/by-key`, `/resolve` and the three
  `/search/*` routes report an entry in a namespace the caller cannot read as
  not found (404 for the single get), so its existence is not leaked.
  Isolation rules (`open`, `allowed_external_refs`) govern references, not
  reads, as in the stores: a cross-namespace reference keeps working,
  because the service validating it resolves it with its own key.
- **Spec-declared keys keep their write grants at the Registry.** The
  Registry now honours a config-file key's declared grants, as every other
  service already did; before, it answered only read for them.

The master key, every store and every deployer-managed app call the Registry
as privileged callers and are unaffected.
**Action:** a tool that registered or deleted Registry entries with a
namespace-scoped key must use the install's service key, or create the
entity through its store.

---

### Every install has a canary key; `verify --live` checks authorization, not only authentication

`wip-deploy verify --security --live` checked that a request **without**
credentials is refused. Nothing checked that a running install refuses a
caller who is authenticated but has no permission. Three things that unit
tests cannot see could open that gap: a misconfigured admin group in an
install's env, a route made public at the edge, or a roll whose images do
not carry the fix.

Now:
- **Every install carries a canary key.** The deployer adds `wip-canary` to
  every install's config-file keys. It has no groups and no grants, and it
  is scoped to a namespace that is never created. Its secret
  `wip-canary-api-key` is generated on first apply, beside the master key.
  The name is reserved, so a spec may not declare a key called
  `wip-canary`.
- **`verify --live` adds a check, "authenticated callers without permission
  refused".**
  - The canary must get a JSON 403 or 404 from one privileged and one
    namespace-scoped route per active service; a 401 fails, because it
    means a service does not know the canary.
  - The admin key must pass the same requests.
  - Write routes are probed with an invalid body, so nothing is written.
  - The canary must see no namespace and no Registry entry in `wip`.
- **The release checklist runs `verify --security --live`** on the
  release-candidate install after the roll.

**Behavior change:** every install now renders `api-keys.json` and mounts
it into the services that declare `WIP_AUTH_API_KEYS_FILE`, including
installs whose spec declares no keys. The next redeploy restarts those
services once.
**Expect a failure on older images:** before this release, the Registry
let any valid key call the allocation routes. Against those images the
Registry probes fail until the install runs this release's images.

---

### A term looked up by terminology + value accepts the terminology's id and `ns:VALUE`

Every def-store endpoint that takes a term as `?terminology=` plus a raw
value worked only when the terminology was given as its bare value:
- `GET /terms/{term}`
- bulk term updates
- the ontology endpoints
- term audit

The terminology's canonical id, the form templates store, answered 404 for
a term that exists, and so did `ns:VALUE`. The id also demanded a
`?namespace=` it does not need.

Now:
- **Any terminology form works.** The terminology may be its id,
  `ns:VALUE`, or its bare value.
- **Only the bare value needs a namespace.** It is still read in the
  request's namespace, and without one the answer is still 422.
- **The term value stays opaque** and is never colon-parsed.
- **Search follows the same rule.** `GET /terms/search` takes its namespace
  from a terminology given as id or `ns:VALUE`, so it needs no
  `?namespace=` either.

Read access is still checked on the term's own namespace.
**No action required.** `@wip/client` `getTerm` and the MCP term tools pass
the parameter through and pick this up with the new images.

### `wip-toolkit` 0.6.2 and `wip-archive` 0.2.0: the wheels were behind their sources

Both packages changed after 0.5.1 and 0.1.1 were set (2026-07-23) without a
version bump, so one version name covered two different sets of bytes. An app
that scaffolded in that window holds the old build; `pip` treats the old
version as satisfied. The scaffold refresh guard noticed and aborted: a
refresh built the 0.5.1 wheel from this repo and found it differed from the
copy the app vendors. Both are minor bumps because both gained features:
- **Archives declare format 3.1**, and restore remaps resolved edge-endpoint
  halves and qualified cross-namespace references through a fresh restore.
- **`inspect` runs on a shared archive analysis core.**
- **A default export says what it drops.** Its manifest is honest about the
  omission and the toolkit warns loudly.
- **The toolkit health check probes the prefixed route.** In proxy mode it
  answered 404 for every service behind an ingress.

- **The toolkit requires `wip-archive>=0.2.0`.** It imports archive modules
  that 0.1.1 does not have. 0.6.0, briefly on `develop`, declared the
  dependency without a floor: installed over an existing 0.1.1 it was accepted
  and then failed at startup with `No module named 'wip_archive.model'`.
  0.6.2 differs from 0.6.1 only by a lint cleanup (unused imports); its
  version moved because its bytes did.

**Action:** apps vendoring the wheels re-run the scaffold refresh to pick up
`wip_toolkit-0.6.2` and `wip_archive-0.2.0`. A refresh that aborted on the
wheel mismatch no longer trips that guard. Install both wheels in one command,
`pip install libs/wip_archive-*.whl libs/wip_toolkit-*.whl`: `wip-archive` is
not on PyPI, so the toolkit wheel alone fails to resolve its dependency. The
regenerated app CLAUDE.md says the same.

### A Registry outage answers 503, not "Namespace not found"

Namespace permission checks go to the Registry. When the Registry could not be
reached, or answered 5xx, the check used to report "no access":
- a namespace-scoped request answered **404 "Namespace not found"**;
- a listing across namespaces answered **403 "No accessible namespaces"**;
- that wrong answer was cached for 30 s, so it kept coming after the Registry
  was back.

Now the check answers **503 "Authorization service unavailable"**, caches
nothing, and access returns on the next request once the Registry is up.
Access is still refused during the outage. A `PATCH /documents` item reports
`error_code: "registry_error"` instead of `forbidden`. A Registry answer of "no
access" is unchanged: still 404 or 403, still cached.

**Action:** callers that treat 404 as "this namespace does not exist" should
treat 503 as "try again", not as a missing namespace.

---

### File routes: an unreachable object store answers 503; refusals get their own status

With the object store down, a file upload used to escape the connection error
to the server and answer a bare **500 "Internal Server Error"**, with no detail.
An unreachable Registry did the same during an upload. Now:

- an object store that cannot be reached (connection refused, DNS, timeout)
  answers **503** with `detail` "Object store unreachable at … — retry
  shortly", on every document-store route that touches storage;
- an upload whose Registry cannot be reached answers **503**; a Registry that
  answered but refused the file ID answers **502**;
- `POST /files`, `GET /files/{id}/download` and `DELETE /files/{id}/hard`
  map the refusal to a status instead of one status for all:
  a file in the wrong state (`invalid_state` — deleted, not yet inactive)
  is **409**, a fault on the server is **500**.

**Behavior changes:** a hard-delete of a file that is still active, a
download URL for a deleted file, and `GET /files/{id}/content` for a deleted
file answered **400** and now answer **409**. A hard-delete while the store is
down answers 503 and keeps the file record, as it always kept it — retry once
the store is back.

**Action:** callers that branch on 400 for these cases should branch on 409;
treat 503 as "try again".

---

### Registry: a synonym takes its entry's namespace and type; an unknown entity type is an error

`/synonyms/add` used to store whatever `synonym_namespace` and
`synonym_entity_type` the caller sent, and report `added`:
- an unknown type (the singular `document`) made a synonym no lookup could
  ever match;
- a valid but foreign one made a synonym that resolved to the wrong thing —
  another namespace's key answering with this entity, or a type's lookup
  returning an entity of another type.

Now:
- **`/synonyms/add`** stores the synonym in its entry's namespace with its
  entry's entity type. The two fields are optional — omit them. Sent values
  that differ are refused per item: `error_code: "invalid_request"`,
  `details.rule: "synonym_scope_mismatch"` (or `invalid_entity_type` for a
  type that does not exist). A synonym is not a cross-namespace link; to
  reference another namespace's entity, address it as `NS:VALUE`.
- **`/synonyms/remove`** defaults both fields to the entry's, and still
  honours explicit ones as sent, so a synonym stored under another scope
  before this release can be removed.
- **`/synonyms/merge`** refuses two entries of different namespace or entity
  type (`merge_scope_mismatch`).
- **Lookups** (`/entries/lookup/by-key`, `/lookup/by-id`, `/entries/resolve`,
  `/search/by-fields`, `/search/by-term`) report an unknown entity type as a
  per-item `invalid_entity_type` error instead of `not_found`; the
  `GET /entries` and `GET /entries/search` filters answer it with **400**.
  A key lookup never returns an entry whose own namespace or type differs
  from the one asked for: such a synonym can only predate this release, and
  it is logged and reported `not_found`.

No install checked had a synonym scoped outside its entry (wip-local,
dev-test, prod-test, kb.internal), and every platform service already sends
the entry's own scope. The MCP `add_synonym` / `remove_synonym` tools now
take the two fields as optional.

**Action:** callers that send `synonym_namespace` / `synonym_entity_type`
should send their entry's own values or drop them. A restore of an archive
holding a synonym scoped outside its entry now fails loudly instead of
re-creating it.

---

### `inspect` shows term names again; `wip-archive` 0.9.0

`wip-toolkit inspect`'s vocabulary dive listed no aliases for any term in an
engine backup: the archive model read an `aliases` key, while engine
archives carry the term's `names`. It now reads `names`, and still accepts
`aliases` from older toolkit archives.

`wip-archive` 0.7.0 to 0.9.0 also add, for the coming `verify` verb
(CASE-827 M2):

- a definition index holding every template, terminology and term row of an
  archive and the identity rows of entities it references but does not
  carry, which resolves template inheritance as template-store does, from
  the archive alone;
- an offline copy of the document identity feed and hash, so a document's
  `identity_hash` can be recomputed without a running platform. A test in
  document-store keeps it identical to `wip_auth`'s, source and output;
- an offline document validator (0.8.0) that checks a document against the
  archive's templates and vocabularies as document-store does: field types
  and constraints, semantic types, term resolution, cross-field rules. It
  reproduces the live validator's current behaviour exactly, including the
  quirks filed as CASE-1009, and a comparison suite in the toolkit's
  integration tests holds the two together. Reference and file checks are
  not part of it. What the archive cannot answer (a vocabulary or nested
  template it does not carry) is reported as unverifiable, never guessed;
- a document check (0.9.0), opt-in on `ArchiveModel.load(check_documents=True)`:
  every document row against its pinned template version (fields and rules,
  without re-resolving stored terms, which existing data keeps resolving
  after they are retired), its stored term references and its recomputed
  identity hash. New findings: `template_version_missing` and
  `missing_term_reference` (errors), `document_invalid`,
  `identity_hash_mismatch` and `unverifiable_dependency` (warnings). The
  `--json` output moves to schema 1.1, adding `document_checks` (null unless
  the check ran); nothing is removed.

**Action:** none. Apps that vendor `wip-archive` pick up 0.9.0 on their next
scaffold refresh. Consumers of `inspect --json` see schema 1.1.

### `wip-toolkit verify`, and `restore` verifies before it uploads; WIP-Toolkit 0.8.0

`wip-toolkit verify <archive>` checks an archive offline, without an
instance: `inspect`'s findings, plus every document against the template
version it is pinned to, its stored term references and its recomputed
identity hash (the `wip-archive` 0.9.0 document check). It answers `fail`
(errors), `warn` or `pass`, exits 1 on `fail`, and with `--strict` also on
`warn`. `--json` carries the verdict, the findings and the per-template
document results (schema 1.1).

**Behaviour change:** `wip-toolkit restore` now runs the same check before
uploading. An error refuses the upload (`✗ not uploaded — fix the archive, or
pass --force`); warnings are printed and the upload continues. `--force`
uploads anyway and also passes the server's refusal of an archive whose own
verification failed, so one flag covers both gates.

WIP-Toolkit 0.8.0 requires `wip-archive` 0.10.0 or later, the first whose
offline validator matches the live one after CASE-1009. (`wip-archive`
0.10.1 changes a docstring only.)

**Action:** scripts calling `wip-toolkit restore` on archives they know to
be incomplete add `--force`. Apps that vendor the toolkit pick up 0.8.0 on
their next scaffold refresh.

### `inspect --target-version`: would a migration succeed? WIP-Toolkit 0.9.0

`wip-toolkit inspect <archive> TEMPLATE --target-version N` assesses, from the
archive alone, migrating that template's active documents to version N: what
`POST /documents/migrate` with `dry_run` would answer on the instance it came
from. It follows the live rules: the migration is refused when N is missing or
inactive, or when its `identity_fields` differ from a source version's (a fork);
declared renames are applied; each document is validated as a write against N,
so a term deprecated since the document was written fails it. Results are
grouped by source version, with counts per failing field and code and samples.
Reference and file fields are not checked (the report says so). An integration
test holds it to the live dry run.

`inspect --json` moves to schema 1.2, adding `migratability` (null unless
requested). WIP-Toolkit 0.9.0 requires `wip-archive` 0.11.0, which carries the
assessment (`wip_archive.migratability`).

**Action:** none. Apps that vendor the toolkit pick up 0.9.0 on their next
scaffold refresh.

### `wip-toolkit filter`: an archive cut down to some templates; WIP-Toolkit 0.11.0

`wip-toolkit filter <archive> <out> TEMPLATE…` writes a new archive holding
every version of those templates' documents and, unless `--no-relationships`,
every relationship document whose source is one of them. Definitions always
travel complete: the templates the kept documents pin, everything those
declare (parents, reference targets, edge-type endpoints) and their
terminologies, whole. `--definitions-only` keeps definitions and no
document.

Kept rows are copied unchanged. A reference from a kept document to a
document left out is never rewritten: `--boundary drop` (the default)
records the left-out document as a referent (its Registry row, keys and
synonyms), which a fresh or merge restore resolves on the target install
like anything else an archive references but does not carry. `strict`
refuses instead and writes nothing; `stubs` keeps the targets one hop out;
`closure` follows references until none cross, and asks for `--yes` when
that keeps more than half the archive's documents.

The output is verified before it is written. The verdict, the parameters,
the SHA-256 of the input and the kept/dropped counts are recorded in the
output's `derived_from` chain; a `fail` verdict still writes the archive
(the restore door then refuses it unless forced) and exits 1. An existing
output is refused without `--force`. `--json` carries the hop (schema 1.0).

`wip-archive` 0.13.0 carries the plan (`wip_archive.filter`) and the core
every shaping transform shares (`wip_archive.transform`, 0.12.0). A
transform's output lists only the namespaces something of travelled. One
fix there: `inspect` and `verify` no longer report a reference as dangling
(an error) when the archive records its target as a referent.

**Action:** none. Apps that vendor the toolkit pick up 0.11.0 on their next
scaffold refresh.

### `inspect` refuses a name two namespaces carry; WIP-Toolkit 0.12.0

`wip-toolkit inspect <archive> TEMPLATE` and `--terminology NAME` used to
dive into whichever namespace's entity they found first when a bare value
was carried by several namespaces of a multi-namespace archive. They now
exit 2 naming the candidates, and accept `ns:VALUE` to pick one, as
`filter` does. Ids and values unique in the archive work as before.
(`wip-archive` 0.14.0: `ArchiveModel.resolve_template` and
`resolve_terminology` raise `AmbiguousNameError`.)

**Action:** scripts that inspect a multi-namespace archive by a bare value
carried by several namespaces name it as `ns:VALUE`.

### `wip-toolkit prune`: an archive without the templates nothing uses; WIP-Toolkit 0.13.0

`wip-toolkit prune <archive> <out>` writes a new archive without the
templates nothing uses: no document is pinned to them, and no schema of a
template with documents needs them as a parent, reference target or
edge-type endpoint. Unused templates that only declare each other go too.
A terminology goes only when a pruned template declared it and nothing kept
uses it (no kept template declares it, no kept document holds its terms, no
term relation from a kept terminology points into it); a terminology no
template ever declared stays, since an app may use it through validation
alone. Every document and file travels.

As with `filter`, the output is verified before it is written, the pruned
names are recorded in its `derived_from` chain, an existing output needs
`--force`, and `--json` carries the hop. `wip-archive` 0.15.0 carries the
plan (`wip_archive.prune`).

**Action:** none.

### `wip-toolkit latest-only`: each document's newest version; WIP-Toolkit 0.14.0

`wip-toolkit latest-only <archive> <out>` writes a new archive holding each
document's highest version, whatever its status: a document retired in its
newest version stays retired. A version that a `pinned` reference names
(`version_strategy: "pinned"` with the `resolved.version` it recorded)
travels too, followed through kept versions that pin further ones: the
platform refuses to hard-delete such a version, so dropping it would break
a reference the platform guards. Every template version stays. A file only
dropped versions referenced leaves with them, with its blob. The output
manifest records `include_all_versions: false`, the first archive that
says so truthfully (the backup API refuses `latest_only`).

As with `filter` and `prune`: verified before it is written, counts in the
`derived_from` chain (`versions_dropped`, `pinned_versions_kept`), `--force`,
`--json`. `wip-archive` 0.16.0 carries the plan (`wip_archive.latest_only`).

**Action:** none.

### `wip-toolkit strip-blobs`: an archive without file content; WIP-Toolkit 0.15.0

`wip-toolkit strip-blobs <archive> <out>` writes a new archive without file
blobs, and without the file rows: a fresh restore of a file row whose blob
is missing still creates the file, which then lists but has no content.
Instead, each file a kept document references is recorded as a referent.
Files are keyed by the checksum of their bytes within their namespace, so a
fresh or merge restore points the documents at the target install's own
copy of the same file, and reports the reference as unresolved where there
is none. A file nothing references leaves no trace. Everything else travels
unchanged.

As with the other transforms: verified before it is written, recorded in
the `derived_from` chain, `--force`, `--json`. `wip-archive` 0.17.0.

**Action:** none.

### `wip-toolkit backup`, and `export` is deprecated; WIP-Toolkit 0.16.0

`wip-toolkit backup <namespace> <out.zip>` runs the server's backup engine
(`POST /backup/namespaces/{namespace}/backup`), follows the job and
downloads the archive to `out.zip` (through a `.partial` file, renamed when
complete). The archive is the engine's: complete and restorable, every
entity in every status and version, with the Registry rows and referents.
`--also NS` adds namespaces to the same archive, `--all-namespaces` takes
every one, `--include-files` adds the blobs, `--skip-documents` keeps
definitions only. Admin is required on every namespace backed up. Exit
code 1 when the job fails or does not finish within `--timeout` (the job
keeps running on the server).

**Behaviour change:** `wip-toolkit export` now prints a deprecation warning.
It is the client-side collector the server engine replaced, and it is
removed in the next release. What it did selectively is done offline now:
`backup`, then `filter` (templates, boundary policy), `latest-only`, `prune`
or `strip-blobs`.

**Action:** scripts calling `wip-toolkit export` move to `wip-toolkit
backup` plus the transform they need before the next release.

### Transforms take `--dry-run`; WIP-Toolkit 0.17.0

`filter`, `prune`, `latest-only` and `strip-blobs` take `--dry-run`. It runs
the transform itself on scratch space, the copy and the verify of the
would-be output included, and skips only the final write: it reports the
counts and the verdict the real run would record, and leaves the output
path untouched (an existing one is not refused). `filter --boundary closure
--dry-run` needs no `--yes`; `strict` still refuses. Transform `--json`
moves to schema 1.1: `dry_run`, and `output` null in a dry run.
`wip-archive` 0.18.0 (`run_transform(dry_run=True)`).

**Action:** none.

### `wip-toolkit diff`: what differs between two archives; WIP-Toolkit 0.18.0

`wip-toolkit diff <a> <b>` compares two archives row by row. Rows match by
their identity per namespace and entity type (a template or document
version by id and version, a term relation by its terms and type, the rest
by id); a matched row is changed when any field differs, compared as
canonical JSON, and for a sample of changes the differing fields are named.
Referents, blobs (by content) and both `derived_from` chains are compared
too. Exit 0 when identical, 1 when they differ, 2 on an unreadable archive;
`--json` (schema 1.0) carries every count and sample. `wip-archive` 0.19.0
(`wip_archive.diff`).

**Action:** none.

### `wip-toolkit split`: an archive's parts as separate archives; WIP-Toolkit 0.19.0

`wip-toolkit split <archive> <out-dir>` writes one archive per namespace
(`--by namespace`, the default: `<ns>.zip`) or one per island (`--by island`:
`island-<n>.zip`, the templates a connected component of references joins,
with every version of their documents, the files those reference and the
terminologies the island uses; a terminology several islands use is copied
into each). Terminologies no island uses, and documents pinned to a
template the archive does not carry, go into `unattached.zip`, so every row
lands in some part. A reference from one part into another becomes a
referent. Each part is verified and records its own `derived_from` hop
(`transform: split`, `params: {by, part}`); an existing part archive
refuses the whole run without `--force`, before anything is written.
`--dry-run` and `--json` as for the other transforms. `wip-archive` 0.20.0
(`wip_archive.split`).

**Action:** none.

### `wip-toolkit export-csv`: an archive as spreadsheets; WIP-Toolkit 0.20.0

`wip-toolkit export-csv <archive> <out-dir>` writes one CSV per template,
`<out-dir>/<namespace>/<TEMPLATE>.csv`, holding each document's latest
version (`--all-versions`: every version row), from the archive alone.
Columns: `document_id`, `version`, `status`, then the data fields flattened
by dot path (`parent.child`), in the order of the template's latest version
with inheritance resolved, undeclared paths after them sorted; arrays are
JSON text, booleans `true`/`false`, null and absent fields empty. A term
field is the value the document holds plus a `<path>__term_id` column (a
JSON list in item order for an array). An existing CSV refuses the run
without `--force`, before anything is written; `--dry-run` lists the files
and row counts; `--json` (schema 1.0). `wip-archive` 0.21.0
(`wip_archive.export_csv`).

**Action:** none.

### `wip-toolkit migrate`: a migration done on the archive, with fills; WIP-Toolkit 0.22.0

`wip-toolkit migrate <archive> <out> --mapping map.json` does what live
`POST /documents/migrate` does, on an archive and without an instance, and
fills what the target version needs that live migration would refuse a
document for. The mapping (a JSON object) names `template` (id, value or
`ns:VALUE`), `to_version`, optionally `from_versions`, and `fill` rules per
field: a constant, `{"copy": "path"}`, `{"format": "{a}-{b}"}`, or
`{"value": ...}` for a constant that looks like a rule. Renames apply
first, then the fills in order; a fill only sets an absent or null field.
Reference and file fields cannot be filled.

The cohort and the rows written are live migration's: the template's
active documents on another version, each validated as a write against the
target (terms re-resolved, so a term deprecated since fails); its old
version retired and the next version written, pinned to the target, with
the same `document_id`, identity hash and entity `created_at`; a
`versioned: false` template overwritten in place. The rows record
`wip-toolkit:migrate` as their writer. An integration test holds the
offline rows to a live migration's, field for field, apart from when and by
whom they were written. Refused with nothing written when the target is
missing or inactive, when identity fields differ (a fork), or when any
document would fail (`--skip-failing` leaves those on their version). The
output is verified with an identity mismatch counting as an error, and the
mapping is recorded verbatim in its hop. `wip-archive` 0.23.0
(`wip_archive.migrate`, `wip_archive.surgery`).

From WIP-Toolkit 0.24.0 a mapping file may also be YAML (`.yaml` / `.yml`);
other names are read as JSON. WIP-Toolkit now depends on `pyyaml>=6.0`.

**Action:** none.

### `wip-toolkit redact`: fields cleared from an archive's whole history; WIP-Toolkit 0.23.0

`wip-toolkit redact <archive> <out> --field TEMPLATE=path[,path…]` sets the
named fields to null in every version row of the template's documents,
whatever their status, so no history keeps the value. Paths are dot paths
(`contact.phone`). A redacted term, reference or file field drops its
snapshots; a file no remaining document references leaves the archive with
its blob, while a file another document still uses stays. Refused, with
nothing written, for an identity field (its values are also in the
document's Registry row, and changing identity is a fork) and for a path
the template never declared.

Registry rows are not touched: a document's `search_values` come from its
primary key and synonyms, never its data. A synonym a caller registered by
hand with the same value is not redacted. Rows keep their timestamps and
writers; the hop records `params_redacted: true` with the template names
and paths, never the values. The output is verified with an identity
mismatch counting as an error. `wip-archive` 0.24.0 (`wip_archive.redact`).

**Action:** none.

## What happens on first start after the upgrade

def-store converts stored terms to the unified names shape before serving
traffic (legacy `aliases`/`translations` fields fold into `names`,
exact duplicates deduped first-occurrence-wins, the legacy alias index is
dropped). Idempotent and fast — look for `Term-name migration:` in the log.
Restoring a pre-upgrade archive is also covered: the restore engine
converts term rows at write time.

reporting-sync does this once, idempotently, while still connected as the
bootstrap login:

1. **Bookkeeping repair.** Copies of `_wip_schema_migrations` /
   `_wip_sync_status` that the old `"$user"` search path created in the `wip`
   schema are merged into `public` (the `wip` copy wins on conflict, being the
   newer one) and dropped. A copy with a different shape is renamed aside and
   logged as CRITICAL. Look for `Bookkeeping repair:` in the log.
2. **Service roles.** `wip_sync_owner` and `wip_reporting_read` are created
   (`LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE`), every schema and relation
   owned by the bootstrap role is re-owned to `wip_sync_owner`, and the reader
   gets read grants plus default privileges. Look for
   `PostgreSQL roles ensured … re-owned N schemas, M relations`.
3. **Self-check.** Neither pool may run as a superuser or role creator; if one
   does, or a password is missing, `/health` answers 503 and `/status` reports
   `postgres_service_roles_ok: false`.

**Rollback caution:** an older reporting-sync image connects as `wip` again and
recreates empty unqualified bookkeeping tables in the `wip` schema. Prefer
redeploying forward; if you do roll back, reconcile with `/parity` and a batch
sync afterwards.

---

## Deployment changes

- **App attachments in the Registry (not served yet).** `POST
  /api/registry/attachments` records an app put under the umbrella through
  the API: name, upstream, gateway protection and proxy options. Also
  `GET /attachments[/{name}]` and `POST /attachments/deactivate`. Writes are
  platform admin only; reads are also open to the app edge's key group
  `wip-app-edge`. Never hard-deleted. A write replaces the whole record, so
  `auth_required` and `allowed_groups` are required on every write.
  **Served by a new component, `app-edge`** (rendered whenever the auth
  gateway is on): it reads the attachments every 10 s with its own key
  `wip-app-edge` (reads attachments only) and serves `/apps/<name>` behind
  the gateway. It dials the address it checked, removes browser-sent
  identity headers and the gateway's session cookie, and answers 503 for
  every attached app at the first poll (every 10 s) that finds the Registry
  silent for more than 30 s, in practice 30 to about 45 s. The public
  router hands it every `/apps` path no deployed app's route claims. On k8s
  a NetworkPolicy, `app-edge-egress`, limits its egress. New install files:
  none. New secret: `wip-app-edge-api-key`, generated on the next apply. The deployer renders a new file
  beside the namespace reservations, `config/auth/attach-policy.json`
  (`WIP_ATTACH_POLICY_FILE`): the install's own hosts, which an attachment may
  not proxy to, and its apps' route names, which it may not take. An apply
  that would add an app on an attachment's route is refused. Existing installs
  gain the file and the Registry's env var on their next redeploy; until
  then, the Registry refuses only loopback, link-local, unspecified and
  multicast upstreams. See `docs/wip-guide.md` §6.5.
- **Scoped applies on k8s carry the key file and the namespace
  reservations.** `add-app`, `app-deploy` and `app-namespace` on a k8s
  install applied a fixed list of shared manifests that did not include
  `api-keys-secret.yaml`, so an app added that way ran with a key no service
  knew (401 everywhere) and its namespaces were not reserved. A scoped apply
  now sends every top-level rendered manifest. Services pick up a new key
  once the kubelet refreshes the mounted Secret, typically within a minute or
  two. **Action:** on a k8s install that gained an app through a scoped verb,
  run one `wip-deploy redeploy` (or any scoped verb) with the new deployer.
- **`verify --security --live` checks each app's own key.** For every
  enabled app it sends the app's key across its namespace boundaries (another
  app's documents and registry entries, a view in another namespace, a
  namespace outside its reservations, its own namespace against the
  reservation, a terminology in `wip`) and requires a refusal, each paired
  with a request inside its own namespaces that must pass. A refused registry
  entry must read exactly like a missing one. Write probes carry
  recognisable values (`wip-verify-unreserved`, `WIP_VERIFY_PROBE`,
  `v_wip_verify_probe`) that a failure names if one got through. A static
  check reads the rendered install: only an app declaring `privileged_key`
  holds the master key. The canary's namespace-upsert probe now sends a valid
  body: the route checks permission after validating the body, so its old
  invalid-body probe failed the check on every install.
- **`wip-deploy images` shows which image each service runs, and why.**
  Read-only, from the saved spec. One row per enabled component and app, with
  its image and SOURCE: the rule that decided its tag. The rules are `pin` (a
  per-service `--image-tag`, also written by `add-app --tag` and `app-deploy
  --tag`), `base` (the deployment-wide `--tag`), `manifest`, `latest`,
  `upstream` (an infra image's own version, which a base tag never touches)
  and `source` (dev target: built locally as `<name>:dev`, tags do not
  apply). `--tag` and `--image-tag` preview exactly the roll `redeploy` makes
  with the same flags, without applying it. Each row says whether it moves or
  stays, the apps that stay on their current images are named, and the
  preview ends with the `redeploy` command that applies it. `--format json`
  is available.
- **`redeploy --tag` no longer moves apps.** Apps are built and tagged in
  their own repos, so a new platform tag rarely names an image that exists
  for them. Before this change, every app without its own pin rendered at
  the new tag and failed to pull. Now each such app is pinned to the image it
  runs today, and the command names it. To move an app, pass `--image-tag
  APP=TAG` in the same call, or use `app-deploy`. A redeploy that does not
  change the base tag pins nothing.
- **`nuke --remove-data` also removes the saved spec.** After a teardown
  that destroyed an install's data, the next `install` was refused as a
  reshape of an install that no longer existed: "install 'default' already
  exists and this run would reshape it". The cause was
  `~/.wip-deploy/<name>/deployment.deployer-state`, which no `nuke` removed.
  Now `--remove-data` deletes it too: for the one install in the scoped
  modes (compose and k8s), and for every install under `~/.wip-deploy/`
  with `--purge-all`. The report and the confirmation prompt both say so.
  Without `--remove-data` the saved spec stays, so `redeploy` brings an
  install back on its surviving volumes. Secrets still go only with
  `--remove-secrets`, so the next install reuses them by default.
- **External identity providers (Dex connectors).** `wip-deploy connector
  add|set-secret|remove|list` declares Google, GitHub, a corporate OIDC or
  LDAP provider in the spec (`auth.connectors`), rendered into Dex on every
  target. The redirect URI is derived (`<issuer>/callback`); the client
  secret is operator-supplied into the secret backend (`dex-connector-<id>`)
  and never generated, so an apply with a missing one stops with
  instructions. `wip-deploy group-map add|remove|list` grants WIP groups to
  federated users by verified email or domain (`auth.group_mappings`).
  `wip-admins` and `wip-services` are never granted to a federated login,
  neither by mapping nor by the provider's own groups: every token-validating
  service and the auth-gateway drop them (`WIP_AUTH_FEDERATED_GROUPS`, set
  only when a connector is declared). See wip-guide §4.6.
- **Dex static users hold several groups.** A spec user carries `groups`
  (a list) instead of `group`; `wip-deploy user add --group` takes a
  comma-separated list, and the new `wip-deploy user set-groups <user>
  --group a,b` replaces a user's groups in place, keeping the password.
  Saved installs whose users still say `group` load unchanged. The first
  user verb or re-render writes `groups` into `deployment.deployer-state`,
  which an older `wip-deploy` then refuses to load: upgrade the deployer on
  every machine that drives the install.
- **A stale or incomplete secret store stops the apply.** Every apply now
  records a fingerprint of each secret it ran with (`secret_digests` in
  `deployment.deployer-state`, never the values) and checks the store
  against it, and on k8s against the live `wip-secrets`, before generating
  anything. A known secret that is missing or different stops the apply
  with the names. Previously the deployer generated fresh datastore
  passwords from an old copy of the install folder and wrote them into the
  cluster while the pods kept the old ones. New verbs: `wip-deploy secrets
  pull` (k8s: adopt the running values) and `wip-deploy secrets accept`
  (record the folder as right). Every apply names the secrets it generated.
  See wip-guide §7.2.
- **Scoped verbs also restart what they change elsewhere.** `add-app`,
  `app-deploy`, `remove-app` and a scoped `redeploy` compare every
  workload's config hash with the previous render and add the out-of-scope
  services whose configuration changed — a secret they read, the router's
  routes, MongoDB's user set — printing their names. Before, those changes
  went live only at the services' next unrelated restart.
- **A failed dev-target build no longer takes the stack down.** A full dev
  apply removed every `wip-*` container and then built the images inside
  `compose up --build`, so a failed build left nothing running. The apply
  now builds first and touches containers only after the build succeeded.
  podman-compose also runs with `--parallel 4`: unbounded, a dozen
  simultaneous builds each opened an SSH session into the macOS podman
  machine, and sshd's MaxStartups limit (10) dropped the surplus builds
  with `ssh: handshake failed: EOF`.
- **The MCP server's network transports authenticate on every install.**
  They enabled their key check only when `API_KEY` or
  `WIP_AUTH_LEGACY_API_KEY` was set; the deployer provides the key as
  `MASTER_API_KEY`, so on k8s installs any workload in the namespace got an
  MCP session — acting with the server's admin key — without credentials
  (compose/dev were covered only because their `.env` happens to carry
  `API_KEY`). The transports now require `X-API-Key` equal to the key the
  server itself uses (`WIP_API_KEY` > `MASTER_API_KEY` > `API_KEY` >
  `WIP_API_KEY_FILE`), compare it in constant time, no longer accept it as a
  `?api_key=` query parameter, and **refuse to start without a key** unless
  `MCP_ALLOW_UNAUTHENTICATED=1` is set for local development. In-cluster
  clients (WIP-KB's query agent) already send `X-API-Key`. Takes effect with
  the next mcp-server image.
- **A rotated key now reaches the services that read it, and the master
  key can be rotated.** Services read their secrets and `api-keys.json`
  only at start, and a change to that content alone left every rendered
  workload identical — k8s rolled nothing (and `subPath` mounts never
  refresh in a running pod), compose recreated nothing for a changed key
  file. Each non-storage workload now carries a hash of those inputs (k8s
  pod-template annotation, compose label `wip.dev/config-hash`), so an
  apply restarts exactly the services whose key changed. `wip-deploy
  rotate-key --secret api-key` rotates the install's master key;
  `rotate-key` names the services it restarted and then probes the install
  (new key accepted, old refused; `--no-check` skips it). Database
  passwords and other generated secrets are refused — they need a
  store-side change. **First apply after upgrading the deployer:** the
  hash is new on every non-storage workload, so that one apply rolls all
  Deployments (k8s) or recreates those containers (compose) once.
- **Dev installs mount the checkout they were installed from, whatever
  the cwd.** A dev redeploy (or any other mutation verb) used to bind-mount
  the backend source of whichever git checkout the command ran in, while
  the deployer-state kept recording the original one — running wip-deploy
  from a second clone silently re-pointed a running dev install at that
  clone. The mounts now follow the same root as discovery: `--repo-root` /
  `WIP_REPO_ROOT`, else the root stamped at install, else the cwd. An
  explicit `--repo-root` that differs from the stamped root still moves the
  install, and now prints a `note:` naming both. `render` and `install`
  with `--repo-root` also mount from the flag (before, always from the cwd).
  `status --diff` discovers from the stamped root before the cwd.
- **Compose and dev containers receive only the secrets they declare.**
  Every service or app that referenced any secret also got the install's
  whole `.env` as `env_file`, so each such container — apps included —
  held the PostgreSQL superuser and MinIO root passwords, the Dex user
  passwords and the master key, whatever its manifest declared. `.env` is
  now only the `${VAR}` interpolation source (as it already was for the
  values themselves), so a container sees exactly its manifest's
  `from_secret` variables — the same set k8s gives it. Code that read an
  undeclared variable worked only through the leak; none was found in the
  platform services or the local apps. The next apply recreates every
  container that referenced a secret.
- **MinIO image** is `ghcr.io/peterseb1969/minio:RELEASE.2025-09-07T16-13-09Z`.
  MinIO no longer publishes pullable images (Docker Hub answers with HTML,
  quay.io with 401); this is a copy of the last release in use. It is
  **arm64-only** — an amd64 cluster needs a multi-arch copy before a fresh
  install.
- **`wip-deploy verify --security --live`** probes a running install through
  its public URL: every protected route must refuse a request without
  credentials (JSON 401/403/404), every health probe must answer 200, and
  reporting-sync must report `postgres_service_roles_ok: true`. Certificates
  are verified only for `tls=letsencrypt`.

---

## Operational notes

- **Identity-hash migration after restoring an older archive.** Identity
  fields that are term or reference fields now hash the resolved IDs, not the
  submitted spelling. An archive written before that rule restores the old
  hashes; run `POST /api/document-store/maintenance/rehash-identities`
  (dry run first) on each restored namespace. Then rebuild the affected
  reporting tables with `POST /api/reporting-sync/sync/batch/{template}?namespace=…&force=true`
  — a batch sync without `force` keeps existing rows and does not refresh
  their `identity_hash`. On large namespaces the pass can answer 500 from a
  client timeout while the Registry still completes; check a sample of
  Registry entries against document-store before re-running.

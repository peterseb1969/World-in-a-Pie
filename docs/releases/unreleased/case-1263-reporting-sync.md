---
section: fixes
cases: [1263]
packages: [wip-client]
---

### Reporting-sync says what it could not check instead of answering "nothing there"

- **`GET /activity/recent`** answered as though nothing had happened when a
  service it reads was down. The response now carries `incomplete_sources`:
  the entity types whose service could not be read, whose activity is missing
  from the list. `@wip/client`'s `ActivityResponse` type carries the field.
- **`GET /entity/{type}/{id}/references`** reported every reference as broken
  ("not found") when the store holding the referenced entity answered an
  error or could not be reached. That is now a 503. A reference the store
  answers 404 for is still broken.
- **Parity** (`GET /parity`, the restore's structure gate, the parity
  monitor): a failure while listing the shared templates a namespace's
  documents use aborted the whole check with no template rows. The
  namespace's own templates are now still checked, and the result says the
  shared ones were not. A shared template that cannot be read is a row with
  the reason. Two shared templates with the same value (whose documents share
  one set of tables) are one row, counted against all their documents, where
  two rows could never match.
- **The parity monitor** repairs a shared template's gap by reading the
  template in its own namespace; it looked it up in the documents' namespace,
  failed, and gave up after its attempts on a gap one batch sync closes.
- **Batch sync:** `rows_written` counts rows actually written, so a re-run
  over current rows reads 0. A `force` run on a template with no active
  version and no documents no longer drops its relations (nothing would have
  rebuilt them), and an empty run no longer calls the namespace's own
  template "foreign".
- **`POST /alerts/test`** no longer consumes the error-rate samples of the
  periodic check.
- **`GET /tables`** counts a template's default view as the version tables it
  actually reads: under a `cross_version_view` that selects some versions, it
  reported every version's rows.
- **A template field with a dot in its name** (possible before template-store
  refused them) got a column named with the dot while every row carried an
  underscore, so none of its documents synced. Its column now takes the
  underscore; an existing table gains it on the next sync.

---
section: fixes
cases: [1266]
packages: [wip-client, wip-auth, wip-deploy]
---

### API keys, def-store error codes and restore gates say and do what they promise

- **A disabled config key is listed.** The Registry's key list and
  `GET /api-keys/{name}` showed a config-file key only while it was enabled;
  a disabled one that was never imported was missing from the list and
  answered 404. Every config key is now listed, disabled or not, as
  `source: "config"` with `reserved_namespaces: null`.
- **A refused key create no longer changes another request's reservation.**
  A create with `reserve` replaced the settings of a reservation already held
  under the new key's name (another request creating the same name, or a key
  of that name before), and undid only what it had created. A key being
  created now reserves only namespaces nobody holds: an existing reservation
  of the prefix under its name is refused with 409 and left as it was.
- **The master key's name is the master key's.** A key in
  `WIP_AUTH_API_KEYS_JSON` or the key file named `legacy` replaced the
  master key (`WIP_AUTH_LEGACY_API_KEY`) in every service that loaded it. While
  the master key is set, such a key is now not loaded (an error names it), and
  `wip-deploy` refuses a spec key named `legacy`.
- **def-store's bulk term create and reconcile report a missing `_NAME_TYPES`
  vocabulary as `name_types_unavailable`** (`error_code: internal_error`), as
  the single-item path does, instead of `invalid_names`.
- **`GET /terminologies/{id}/dependencies` answers `can_deactivate: false`
  when the template check could not run**: a delete refuses an unverifiable
  terminology whether forced or not.
- **A restore's foreign-scope gate skips what the restore leaves out.** With
  `skip_documents` / `skip_files`, a document's or file's archived synonym key
  naming another namespace no longer refuses the restore. The bare-identity
  gate no longer counts synonyms the archive holds as inactive (they are not
  restored as keys). The gate runs off the event loop.
- **Descriptions corrected:** `@wip/client`'s `reconcileTerminology` (what an
  explicit null clears) and `searchTerms` (exact mode returns every candidate
  up to `limit`); the MCP `delete_terminology` tool and def-store's manifest
  (an unverifiable delete is refused whatever `force` says); the Registry's
  `SourceInfo.endpoint_url` (stored as data, never requested); def-store's
  `ValidateValueResponse.error_code` (`invalid_request` is a bulk item's).

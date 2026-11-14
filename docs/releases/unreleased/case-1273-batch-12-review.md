---
section: fixes
cases: [1273]
packages: [wip-client]
---

### A file's bytes cannot be reclaimed under a document that is being written

A document write counts its files before the document is written, not after.
A hard delete that reclaimed a file's bytes could run between a write's
validation and its insert, and the document then referenced bytes that were
gone. Now the reclaim keeps a file a write has counted, and a write whose file
went inactive since validation is refused (`validation_failed`,
`details.rule: file_deleted`).

- A file the reclaim keeps because active documents still reference it gets
  the count of the references they hold, not the number of documents (capped
  at ten).
- A file a document hard delete released but whose bytes the object store
  refused to delete is recorded, and the file integrity check reports it as
  `unreclaimed_file` (`@wip/client`: the `FileIntegrityIssue.type` union gains
  it). A file soft-deleted on purpose is not reported.
- The file listing's `content_type` filter matches as the upload allowlist
  does: case ignored, parameters (`; charset=utf-8`) not part of the type.
- The orphan listing and the integrity check, which read files by status
  across namespaces, have an index.
- A stored filename is at most 255 characters (the extension kept), and a name
  that is not valid text is made safe instead of failing the upload.
- An upload cancelled mid-stream leaves no partial archive, and a refused
  fresh restore removes its uploaded archive.
- The object-store health check says what it does with a missing bucket: it
  creates it only when the key may; a wip-deploy install's key may not.

### A Registry key is never left without its claim

Removing a synonym, retiring an entity, re-keying an identity or activating a
reservation releases the claim on the key the entry let go. A request on the
same entry that took that claim over after the write (adding the synonym back,
say) lost it to the release, and the key was then free for another entry while
still active on this one. A release now keeps a claim taken over since its
write.

- Keyed activation writes only while the entry still has the revision it read:
  a synonym added meanwhile is kept, and the item answers
  `concurrency_conflict`.
- A re-key of an entry deleted meanwhile answers `not_found` (it answered
  `rekeyed`), and one another request already applied answers `unchanged`. A
  retirement of an entry deleted meanwhile answers `not_found`, and a merge
  into an entry deleted meanwhile is refused with nothing moved.

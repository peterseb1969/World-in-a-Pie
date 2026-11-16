---
section: fixes
cases: [1275]
---

### Re-uploading stored bytes with a different restriction is refused

Identical bytes are one file per namespace, so uploading them again returned the
file that already exists — including when the upload asked for an
`allowed_templates` restriction that file does not carry. The restriction was
silently not applied while the upload reported success. It is now refused
(`409`, `details.rule: restriction_mismatch`), naming the stored file and both
restrictions: reference that file as it stands, or upload without
`allowed_templates` to take it unchanged. An upload that does not address the
restriction behaves as before.

**Action:** apps that re-post known bytes with a restriction should expect a
refusal when the existing file differs, and reuse the file it names.

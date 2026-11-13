---
section: fixes
cases: [1140]
---

### A revival whose write fails leaves the document deleted

When a new version's insert fails, document-store gives the document its
active version back. A write to a deleted document's identity (a revival)
went through the same repair, and the row it "gave back" was the deleted
head: the document came back to life while the caller was told the write
failed, and the Registry entry the revival had recorded stayed revived.
The repair now restores only a row the failed write took from active, so a
failed revival leaves the document deleted and its Registry entry retired,
on the single-document and bulk paths alike.

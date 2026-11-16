---
section: fixes
cases: [1363]
---

### A namespace restored under its old id while a deletion resumes keeps its data

`resume-delete` refuses to run while a namespace holds the prefix again, but it
checked once, before it claimed the deletion. An ID-preserving restore that
landed between that check and the claim gives the new namespace the deleted
one's namespace_id, and the steps, which compared ids only, deleted its stored
files and reporting rows. While a deletion's steps run, the only record that may
hold the prefix is the deletion's own lock, so every step now keeps the data of
any other record under the prefix, same id or not.

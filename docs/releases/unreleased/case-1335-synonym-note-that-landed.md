---
section: fixes
cases: [1335]
---

### An entry's synonym note is the caller's text of the change that landed

- **Add.** A request that added several synonyms to one entry took the entry's
  `actor_note` from the first synonym it *planned*. When another request added
  that synonym first, this request answered its item `already_exists` and still
  stamped the entry with that item's text, dropping the text of the addition
  that landed. The note is now picked from the same outcomes the items are
  answered from: the text of the entry's first change that landed.
- **Remove.** The note was the *last* item's text, so the same race could stamp
  the words of a removal answered `not_found`, and when all of an entry's
  removals landed it kept the last one's words where add kept the first's.
  Remove now follows add.

Nothing stored is rewritten and no request or response shape changes: only the
`actor_note` an entry is left with when one request changes several of its
synonyms.

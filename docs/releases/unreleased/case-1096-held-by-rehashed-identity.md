---
section: fixes
cases: [1096]
---

### An aligned merge under on_clash=skip recognises a held document by the identity it is written under

With `align_templates` and `on_clash: "skip"`, a merge leaves out of the version check every document the target already holds. It found those documents by the archive's own identity hash. A document whose identity field is a term (or a reference) that the merge maps onto the target's own copy gets a different hash once it is mapped. The merge then missed the target's copy and counted the document as arriving. A field only that skipped document filled could mark its archive template version unplaceable, so every document pinned to that version was left out. The held check now uses the identity the merge would write, as the document planner does.

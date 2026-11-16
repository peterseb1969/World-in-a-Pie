---
section: fixes
cases: [1300]
---

### An id whose record is gone answers every door as an id that was never issued

An id the Registry still answers but whose record is gone — a ghost left by a hard delete whose Registry entry could not be released — now answers every door exactly as an id that was never issued: "Could not resolve terminology_id … in namespace …" for a caller with a namespace, the door's own not-found for one without. Previously those doors answered "Terminology/Template/File/Document not found", an empty child list or validation, or a 400 from activate, which told a caller whether an id had ever been issued. Covered: def-store term and terminology doors including export, dependencies, restore and reconcile; template-store get, raw, dependencies, validate, activate, cascade, children and descendants; document-store document get, versions, latest, relationships and traverse, and file get, download, content, documents and hard delete. The audit doors still serve the change history of an entity whose row is gone; only an id with neither a record nor any audit row answers as one that names nothing.
